"""Synchronisation feeds.yaml -> table feed.

Idempotente : c'est ce qui permet de la lancer a chaque demarrage du conteneur.
Un flux retire du YAML est desactive, jamais supprime : la cascade emporterait
ses articles et ses observations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from veille.models import Feed
from veille.schemas import FeedSpec

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SeedReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


_SYNCED_FIELDS = ("name", "url", "lang", "topic", "source_type")


def seed_feeds(session: Session, specs: list[FeedSpec]) -> SeedReport:
    report = SeedReport()
    now = datetime.now(tz=UTC)

    existing = {feed.slug: feed for feed in session.scalars(select(Feed))}

    for spec in specs:
        feed = existing.get(spec.id)
        if feed is None:
            session.add(
                Feed(
                    slug=spec.id,
                    name=spec.name,
                    url=spec.url,
                    lang=spec.lang,
                    topic=spec.topic,
                    source_type=spec.source_type,
                    is_active=True,
                )
            )
            report.created.append(spec.id)
            continue

        changes = {
            column: getattr(spec, column)
            for column in _SYNCED_FIELDS
            if getattr(feed, column) != getattr(spec, column)
        }
        if not feed.is_active:
            changes["is_active"] = True

        if changes:
            for column, value in changes.items():
                setattr(feed, column, value)
            feed.updated_at = now
            report.updated.append(spec.id)
        else:
            report.unchanged.append(spec.id)

    declared = {spec.id for spec in specs}
    for slug, feed in existing.items():
        if slug not in declared and feed.is_active:
            feed.is_active = False
            feed.updated_at = now
            report.deactivated.append(slug)

    session.commit()

    logger.info(
        "seed : %s crees, %s mis a jour, %s desactives, %s inchanges",
        len(report.created),
        len(report.updated),
        len(report.deactivated),
        len(report.unchanged),
    )
    return report
