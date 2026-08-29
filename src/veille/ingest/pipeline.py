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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from veille.errors import FetchError, VeilleError
from veille.ingest.fetch import build_client, fetch_feed
from veille.ingest.gaps import assess_gap
from veille.ingest.parse import parse_feed
from veille.ingest.store import store_entries
from veille.models import Article, ArticleFeed, Feed, FeedRun
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
    client = client or build_client()

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
    outcome = FeedRunOutcome(feed_id=feed_id, feed_slug=spec.id, started_at=datetime.now(tz=UTC))

    try:
        fetched = fetch_feed(spec, etag=etag, last_modified=last_modified, client=client)
        outcome.http_status = fetched.http_status

        if fetched.not_modified:
            outcome.status = "not_modified"
            logger.info("%s : inchange", spec.id)
        else:
            assert fetched.body is not None  # garanti par fetch_feed
            parsed = parse_feed(fetched.body, fetched_at=fetched.fetched_at)

            # LU AVANT store_entries, imperativement : les articles qu'on
            # s'apprete a ecrire inflateraient cette borne et la detection ne
            # declencherait jamais.
            prev_max = _previous_max_published(session, feed_id)

            stored = store_entries(
                session, feed_id=feed_id, entries=parsed.entries, now=fetched.fetched_at
            )
            outcome.n_new = stored.n_new
            outcome.n_seen = stored.n_seen
            outcome.status = "ok"

            assessment = assess_gap(
                status="ok",
                entries=parsed.entries,
                prev_max_published=prev_max,
                n_new=stored.n_new,
            )
            outcome.gap_status = assessment.status
            outcome.oldest_in_page = assessment.oldest_in_page
            outcome.prev_max_published = assessment.prev_max_published
            if assessment.status == "suspected":
                logger.warning(
                    "%s : trou suspecte, rien entre %s et %s (%s)",
                    spec.id,
                    assessment.prev_max_published,
                    assessment.oldest_in_page,
                    assessment.reason,
                )
            else:
                logger.debug(
                    "%s : couverture %s (%s)", spec.id, assessment.status, assessment.reason
                )

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

    except Exception as exc:  # un flux ne doit jamais tuer le run
        session.rollback()
        outcome.status = "error"
        outcome.error_message = f"{type(exc).__name__}: {exc}"
        logger.exception("%s : erreur inattendue", spec.id)

    outcome.finished_at = datetime.now(tz=UTC)
    _record_run(session, outcome)
    return outcome


def _previous_max_published(session: Session, feed_id: int) -> datetime | None:
    """Date de publication la plus recente deja connue POUR CE FLUX.

    Jointure par article_feed et non par article.first_feed_id : un article
    relaye par ce flux mais vu d'abord ailleurs a bien ete observe ici, et
    l'exclure fabriquerait des trous fantomes sur les flux qui relaient.

    Restreint aux dates venant reellement du flux : une date 'fetched' vaut
    ~l'heure du run et transformerait cette borne en "maintenant" perpetuel.
    """
    return session.scalar(
        select(func.max(Article.published_at))
        .select_from(ArticleFeed)
        .join(Article, Article.id == ArticleFeed.article_id)
        .where(ArticleFeed.feed_id == feed_id, Article.date_source != "fetched")
    )


def _record_run(session: Session, outcome: FeedRunOutcome) -> None:
    """Ecrit la trace du passage. Commit immediat : le statut d'un flux ne doit
    pas dependre du sort des flux suivants.

    Le format "<NomDException>: <message>" est un CONTRAT, pas un hasard de
    formatage : /feeds s'en sert pour distinguer une panne transitoire (reseau,
    502, timeout) d'un flux reellement casse. Sans cette distinction le tableau
    de bord serait rouge en permanence a cause de hnrss, et on cesserait de le
    regarder. Voir veille.web.filters.error_kind.
    """
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
            gap_status=outcome.gap_status,
            oldest_in_page=outcome.oldest_in_page,
            prev_max_published=outcome.prev_max_published,
        )
    )
    session.commit()
