"""Etape 2 : octets -> objets.

Pas de reseau, pas de base, pas d'horloge implicite : `fetched_at` est injecte.
La normalisation (canonicalisation, sanitisation, hash) est faite ici parce
qu'elle est pure ; `store` ne recalcule rien.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from xml.sax import SAXException

import feedparser

from veille.errors import FeedParseError, InvalidUrlError
from veille.normalize.dates import resolve_published
from veille.normalize.html import sanitize, strip_tags
from veille.normalize.text import clean_title, content_hash
from veille.normalize.urls import canonicalize_url
from veille.schemas import ParsedEntry, ParseResult

logger = logging.getLogger(__name__)


def parse_feed(body: bytes, *, fetched_at: datetime) -> ParseResult:
    """Transforme le corps d'un flux en entrees normalisees.

    Leve FeedParseError si le document est irrecuperable. feedparser ne leve
    jamais de lui-meme : il positionne `bozo` et rend ce qu'il a pu sauver. La
    regle est donc explicite ici, sinon un XML tronque passerait pour un flux
    vide et le run serait compte comme un succes.

    Un `bozo` benin (encodage declare faux, namespace non declare) avec des
    entrees exploitables est accepte : sinon la moitie des flux FR echouerait.
    Le message est remonte pour finir dans feed_run.error_message.
    """
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at doit etre timezone-aware")

    parsed = feedparser.parse(body)
    raw_entries = list(parsed.get("entries") or [])
    bozo_exception = parsed.get("bozo_exception")
    is_bozo = bool(parsed.get("bozo"))

    if is_bozo and (not raw_entries or isinstance(bozo_exception, SAXException)):
        raise FeedParseError(f"flux illisible : {bozo_exception!r}")

    bozo_message = repr(bozo_exception) if is_bozo else None
    if bozo_message:
        logger.warning("flux degrade mais exploitable : %s", bozo_message)

    entries: list[ParsedEntry] = []
    skipped = 0
    for raw in raw_entries:
        entry = _build_entry(raw, fetched_at=fetched_at)
        if entry is None:
            skipped += 1
            continue
        entries.append(entry)

    return ParseResult(entries=entries, bozo_message=bozo_message, n_skipped=skipped)


def _build_entry(raw: Any, *, fetched_at: datetime) -> ParsedEntry | None:
    url_original = _extract_link(raw)
    if not url_original:
        logger.info("entree sans lien exploitable, ignoree")
        return None

    try:
        url_canonical = canonicalize_url(url_original)
    except InvalidUrlError as exc:
        logger.info("entree ignoree, URL inexploitable (%s) : %r", exc, url_original)
        return None

    # Un titre manquant ne justifie pas de perdre l'article : l'URL fait un
    # libelle mediocre mais honnete.
    title = clean_title(raw.get("title") or "") or url_canonical

    raw_summary = _extract_summary(raw)
    summary_clean = sanitize(raw_summary)

    published_at, date_source = resolve_published(
        raw.get("published_parsed") or raw.get("published"),
        raw.get("updated_parsed") or raw.get("updated"),
        fetched_at,
    )

    return ParsedEntry(
        url_canonical=url_canonical,
        url_original=url_original,
        title=title,
        content_hash=content_hash(title, strip_tags(summary_clean)),
        published_at=published_at,
        date_source=date_source,
        # Le GUID est conserve pour information : il est instable d'un flux a
        # l'autre, ce n'est jamais une cle.
        guid=(raw.get("id") or None),
        author=(clean_title(raw.get("author") or "") or None),
        raw_summary=raw_summary,
        summary_clean=summary_clean,
    )


def _extract_link(raw: Any) -> str | None:
    link = (raw.get("link") or "").strip()
    if link:
        return link
    for candidate in raw.get("links") or []:
        href = (candidate.get("href") or "").strip()
        if href and candidate.get("rel") in (None, "alternate"):
            return href
    return None


def _extract_summary(raw: Any) -> str | None:
    """Prend le contenu le plus riche disponible, sans jamais aller le chercher
    ailleurs que dans le flux (pas de scraping en V0)."""
    contents = raw.get("content") or []
    for content in contents:
        value = (content.get("value") or "").strip()
        if value:
            return value
    summary = (raw.get("summary") or "").strip()
    return summary or None
