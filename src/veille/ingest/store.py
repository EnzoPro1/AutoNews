"""Etape 3 : persistance.

Ne fait aucun reseau et ne reparse rien : les ParsedEntry arrivent deja
normalises. L'idempotence est portee par le schema (UNIQUE + ON CONFLICT), pas
par de la logique Python : `first_seen_at` n'apparait dans aucun SET, donc il ne
peut structurellement pas bouger.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from veille.models import Article, ArticleFeed
from veille.schemas import ParsedEntry, StoreResult

logger = logging.getLogger(__name__)

#: Colonnes ecrasees uniquement si le contenu a reellement change. CERT-FR
#: revise ses avis en place : on veut la derniere version, sans toucher a
#: first_seen_at ni a published_at.
_REVISABLE_COLUMNS = ("title", "author", "raw_summary", "summary_clean", "content_hash")


def store_entries(
    session: Session,
    *,
    feed_id: int,
    entries: list[ParsedEntry],
    now: datetime,
) -> StoreResult:
    """Ecrit les entrees et renvoie le compte de nouveaux / vus.

    `now` est injecte : cette fonction ne lit jamais l'horloge.
    """
    _reject_naive(now, "now")
    if not entries:
        return StoreResult()

    unique = _dedupe(entries)
    urls = [entry.url_canonical for entry in unique]

    already_known = set(
        session.scalars(select(Article.url_canonical).where(Article.url_canonical.in_(urls)))
    )

    for entry in unique:
        _reject_naive(entry.published_at, f"published_at de {entry.url_canonical}")
        article_id = _upsert_article(session, feed_id=feed_id, entry=entry, now=now)
        _upsert_observation(session, article_id=article_id, feed_id=feed_id, entry=entry, now=now)

    session.flush()

    return StoreResult(
        n_new=sum(1 for url in urls if url not in already_known),
        n_seen=len(unique),
    )


def _upsert_article(session: Session, *, feed_id: int, entry: ParsedEntry, now: datetime) -> int:
    statement = pg_insert(Article).values(
        first_feed_id=feed_id,
        url_canonical=entry.url_canonical,
        title=entry.title,
        author=entry.author,
        raw_summary=entry.raw_summary,
        summary_clean=entry.summary_clean,
        content_hash=entry.content_hash,
        published_at=entry.published_at,
        date_source=entry.date_source,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
    )

    revised = Article.content_hash.is_distinct_from(statement.excluded.content_hash)
    updates = {
        column: case((revised, getattr(statement.excluded, column)), else_=getattr(Article, column))
        for column in _REVISABLE_COLUMNS
    }
    updates["updated_at"] = case((revised, statement.excluded.updated_at), else_=Article.updated_at)
    # Seule colonne mise a jour inconditionnellement : elle dit "vu a nouveau",
    # pas "modifie".
    updates["last_seen_at"] = statement.excluded.last_seen_at
    # first_feed_id, first_seen_at et published_at sont volontairement absents.

    statement = statement.on_conflict_do_update(
        index_elements=[Article.url_canonical],
        set_=updates,
    ).returning(Article.id)

    return session.execute(statement).scalar_one()


def _upsert_observation(
    session: Session, *, article_id: int, feed_id: int, entry: ParsedEntry, now: datetime
) -> None:
    statement = pg_insert(ArticleFeed).values(
        article_id=article_id,
        feed_id=feed_id,
        guid=entry.guid,
        url_original=entry.url_original,
        first_seen_at=now,
        last_seen_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[ArticleFeed.article_id, ArticleFeed.feed_id],
        set_={
            # Le guid peut etre regenere a chaque edition par certains flux :
            # on garde le dernier vu, il n'a valeur que d'information.
            "guid": statement.excluded.guid,
            "url_original": statement.excluded.url_original,
            "last_seen_at": statement.excluded.last_seen_at,
        },
    )
    session.execute(statement)


def _dedupe(entries: list[ParsedEntry]) -> list[ParsedEntry]:
    """Un meme flux peut publier deux fois la meme URL canonique (variantes de
    tracking). Le premier gagne, comme entre flux."""
    seen: set[str] = set()
    unique: list[ParsedEntry] = []
    for entry in entries:
        if entry.url_canonical in seen:
            logger.info("doublon intra-flux ignore : %s", entry.url_canonical)
            continue
        seen.add(entry.url_canonical)
        unique.append(entry)
    return unique


def _reject_naive(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} : datetime naif refuse a la frontiere de store")
