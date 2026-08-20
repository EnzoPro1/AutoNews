"""Orchestration : fetch -> parse -> store, un flux a la fois.

Le seul endroit ou les trois etapes se rencontrent. Chaque flux a son propre
try/except ET sa propre transaction : un flux mort, un XML malforme ou un timeout
ne doit jamais interrompre le run ni faire perdre ce que les flux precedents ont
ecrit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from veille.config import settings
from veille.errors import FetchError, VeilleError
from veille.ingest.fetch import fetch_feed
from veille.ingest.parse import parse_feed
from veille.ingest.store import store_entries
from veille.models import Feed, FeedRun
from veille.schemas import FeedRunOutcome, FeedSpec

logger = logging.getLogger(__name__)


def run_ingestion(
    session: Session,
    specs: list[FeedSpec],
    *,
    client: httpx.Client | None = None,
) -> list[FeedRunOutcome]:
    """Ingere chaque flux de `specs`. Ne leve jamais a cause d'un flux."""
    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(settings.http_timeout), follow_redirects=True
    )

    outcomes: list[FeedRunOutcome] = []
    try:
        for spec in specs:
            feed = session.scalar(select(Feed).where(Feed.slug == spec.id))
            if feed is None:
                logger.error("flux %s absent de la base : lancer `veille seed`", spec.id)
                continue
            outcomes.append(_ingest_one(session, spec, feed, client=client))
    finally:
        if owns_client:
            client.close()

    return outcomes


def _ingest_one(
    session: Session, spec: FeedSpec, feed: Feed, *, client: httpx.Client
) -> FeedRunOutcome:
    feed_id = feed.id
    etag, last_modified = feed.etag, feed.last_modified
    outcome = FeedRunOutcome(
        feed_id=feed_id, feed_slug=spec.id, started_at=datetime.now(tz=UTC)
    )

    try:
        fetched = fetch_feed(spec, etag=etag, last_modified=last_modified, client=client)
        outcome.http_status = fetched.http_status

        if fetched.not_modified:
            outcome.status = "not_modified"
            logger.info("%s : inchange", spec.id)
        else:
            assert fetched.body is not None  # noqa: S101 - garanti par fetch_feed
            parsed = parse_feed(fetched.body, fetched_at=fetched.fetched_at)
            stored = store_entries(
                session, feed_id=feed_id, entries=parsed.entries, now=fetched.fetched_at
            )
            outcome.n_new = stored.n_new
            outcome.n_seen = stored.n_seen
            outcome.status = "ok"

            # Un bozo benin n'est pas un echec, mais il doit rester visible sur
            # /feeds : c'est gratuit et ca rend le tableau de bord utile.
            if parsed.bozo_message:
                outcome.warnings.append(f"flux degrade : {parsed.bozo_message}")
            if parsed.n_skipped:
                outcome.warnings.append(f"{parsed.n_skipped} entree(s) ignoree(s)")

            feed.etag = fetched.etag
            feed.last_modified = fetched.last_modified
            feed.updated_at = datetime.now(tz=UTC)

            logger.info("%s : %s nouveaux / %s vus", spec.id, stored.n_new, stored.n_seen)

    except VeilleError as exc:
        session.rollback()
        outcome.status = "error"
        outcome.error_message = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, FetchError):
            outcome.http_status = exc.http_status
        logger.warning("%s : echec (%s)", spec.id, exc)

    except Exception as exc:  # noqa: BLE001 - un flux ne doit jamais tuer le run
        session.rollback()
        outcome.status = "error"
        outcome.error_message = f"{type(exc).__name__}: {exc}"
        logger.exception("%s : erreur inattendue", spec.id)

    outcome.finished_at = datetime.now(tz=UTC)
    _record_run(session, outcome)
    return outcome


def _record_run(session: Session, outcome: FeedRunOutcome) -> None:
    """Ecrit la trace du passage. Commit immediat : le statut d'un flux ne doit
    pas dependre du sort des flux suivants."""
    message = outcome.error_message
    if message is None and outcome.warnings:
        message = " | ".join(outcome.warnings)

    session.add(
        FeedRun(
            feed_id=outcome.feed_id,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
            status=outcome.status,
            http_status=outcome.http_status,
            n_new=outcome.n_new,
            n_seen=outcome.n_seen,
            error_message=message,
        )
    )
    session.commit()
