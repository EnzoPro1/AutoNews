"""Etape 2 : octets -> objets.

Pas de reseau, pas de base, pas d'horloge implicite : `fetched_at` est injecte.
La normalisation (canonicalisation, sanitisation, hash) est faite ici parce
qu'elle est pure ; `store` ne recalcule rien.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any
from xml.sax import SAXException

import feedparser

from veille.errors import FeedParseError, InvalidUrlError
from veille.normalize.dates import has_degenerate_span, resolve_published
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
    if not body.strip():
        raise FeedParseError("corps vide")

    # sanitize_html=False : feedparser nettoie le HTML par defaut, ce qui viderait
    # raw_summary de son contenu reel et ferait dependre la securite du rendu de
    # SON allowlist au lieu de la notre. Le brut est conserve pour audit, nh3
    # reste le seul assainisseur.
    parsed = feedparser.parse(body, sanitize_html=False)
    raw_entries = list(parsed.get("entries") or [])
    bozo_exception = parsed.get("bozo_exception")
    is_bozo = bool(parsed.get("bozo"))

    if is_bozo and (not raw_entries or isinstance(bozo_exception, SAXException)):
        raise FeedParseError(f"flux illisible : {bozo_exception!r}")

    # feedparser laisse `version` vide quand il n'a pas reconnu un format de flux
    # (une page HTML d'erreur renvoyee en 200, par exemple). Un flux valide mais
    # momentanement vide, lui, a bien une version : les deux cas restent distincts.
    if not parsed.get("version") and not raw_entries:
        raise FeedParseError("document non reconnu comme un flux RSS ou Atom")

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

    entries = _demote_degenerate_dates(entries, fetched_at=fetched_at)

    return ParseResult(entries=entries, bozo_message=bozo_message, n_skipped=skipped)


def _demote_degenerate_dates(
    entries: list[ParsedEntry], *, fetched_at: datetime
) -> list[ParsedEntry]:
    """Requalifie en 'fetched' les dates d'une page qui n'en sont pas.

    Certains flux horodatent toutes leurs entrees a l'heure de generation du
    document. `date_source` vaudrait alors 'published' pour une valeur qui n'a
    rien d'une date de publication : la colonne mentirait, et `/` trierait ces
    articles en tete comme s'ils etaient frais.

    On ne conserve pas l'horodatage d'origine : le garder en le declarant
    'fetched' ferait mentir la colonne dans l'autre sens. C'est une heure de
    collecte, on l'ecrit comme telle.
    """
    from_feed = [e for e in entries if e.date_source != "fetched"]
    if not has_degenerate_span([e.published_at for e in from_feed]):
        return entries

    logger.warning(
        "page sans chronologie exploitable (%s entrees dans un intervalle de %s) : "
        "dates requalifiees en 'fetched'",
        len(from_feed),
        max(e.published_at for e in from_feed) - min(e.published_at for e in from_feed),
    )
    return [
        replace(entry, published_at=fetched_at, date_source="fetched")
        if entry.date_source != "fetched"
        else entry
        for entry in entries
    ]


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
