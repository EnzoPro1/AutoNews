"""Contrats entre les trois etapes du pipeline.

Ces dataclasses sont la frontiere : `fetch` ne connait que FeedSpec et FetchResult,
`parse` ne connait que des octets et ParsedEntry, `store` ne connait que ParsedEntry.
Aucune ne porte de comportement d'I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Lang = Literal["fr", "en"]
Topic = Literal["ai", "sec", "both"]
SourceType = Literal["media", "vendor", "official", "community"]
DateSource = Literal["published", "updated", "fetched"]
RunStatus = Literal["ok", "not_modified", "error"]

LANGS: tuple[str, ...] = ("fr", "en")
TOPICS: tuple[str, ...] = ("ai", "sec", "both")
SOURCE_TYPES: tuple[str, ...] = ("media", "vendor", "official", "community")
DATE_SOURCES: tuple[str, ...] = ("published", "updated", "fetched")
RUN_STATUSES: tuple[str, ...] = ("ok", "not_modified", "error")


@dataclass(frozen=True, slots=True)
class FeedSpec:
    """Une entree de feeds.yaml, validee."""

    id: str
    name: str
    url: str
    lang: Lang
    topic: Topic
    source_type: SourceType


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Sortie de l'etape reseau. `body` est None si et seulement si not_modified."""

    url: str
    http_status: int
    fetched_at: datetime
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.http_status == 304


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    """Un article, entierement normalise, pret a etre persiste tel quel.

    La canonicalisation, la sanitisation et le hash sont faits dans `parse` :
    ce sont des fonctions pures, elles n'ont rien a faire dans la couche de
    persistance. `store` ne recalcule rien.
    """

    url_canonical: str
    url_original: str
    title: str
    content_hash: str
    published_at: datetime
    date_source: DateSource
    guid: str | None = None
    author: str | None = None
    raw_summary: str | None = None
    summary_clean: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Entrees exploitables + defauts non bloquants rencontres en chemin."""

    entries: list[ParsedEntry]
    bozo_message: str | None = None
    n_skipped: int = 0


@dataclass(slots=True)
class StoreResult:
    n_new: int = 0
    n_seen: int = 0


@dataclass(slots=True)
class FeedRunOutcome:
    """Ce que le pipeline ecrit dans feed_run pour un flux."""

    feed_id: int
    #: slug du flux, pour l'affichage CLI ; feed_run ne stocke que feed_id.
    feed_slug: str
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = "ok"
    http_status: int | None = None
    n_new: int = 0
    n_seen: int = 0
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
