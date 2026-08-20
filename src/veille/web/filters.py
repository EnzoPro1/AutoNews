"""Filtres Jinja. La conversion UTC -> Europe/Paris se fait ICI et nulle part
ailleurs : la base ne contient que de l'UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from veille.normalize.html import strip_tags

PARIS = ZoneInfo("Europe/Paris")

LANG_LABELS = {"fr": "FR", "en": "EN"}
TOPIC_LABELS = {"ai": "IA", "sec": "Cyber", "both": "IA + Cyber"}
SOURCE_LABELS = {
    "media": "média",
    "vendor": "éditeur",
    "official": "officiel",
    "community": "communauté",
}


def to_paris(value: datetime) -> datetime:
    return value.astimezone(PARIS)


def datetime_attr(value: datetime) -> str:
    """Valeur de l'attribut datetime= d'un <time>, en ISO 8601 local."""
    return to_paris(value).isoformat(timespec="minutes")


def absolute_date(value: datetime) -> str:
    return to_paris(value).strftime("%d/%m/%Y %H:%M")


def relative_date(value: datetime, now: datetime | None = None) -> str:
    """Date relative en francais. `now` est injectable pour les tests."""
    now = now or datetime.now(tz=UTC)
    seconds = (now - value.astimezone(UTC)).total_seconds()

    if seconds < 0:
        return "à l'instant"
    minutes = seconds / 60
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {int(minutes)} min"
    hours = minutes / 60
    if hours < 24:
        return f"il y a {int(hours)} h"
    days = hours / 24
    if days < 2:
        return "hier"
    if days < 7:
        return f"il y a {int(days)} jours"
    if days < 60:
        return f"il y a {int(days / 7)} sem."
    return absolute_date(value)


def excerpt(value: str | None, length: int = 240) -> str:
    """Extrait en texte brut du resume nettoye.

    Le rendu ne fait PAS confiance au HTML de flux : meme apres nh3, aucun
    `|safe` n'est applique a du contenu de flux dans les templates. On affiche
    donc le texte, echappe par Jinja comme le reste.
    """
    text = strip_tags(value)
    if not text:
        return ""
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + "…"


def lang_label(value: str) -> str:
    return LANG_LABELS.get(value, value.upper())


def topic_label(value: str) -> str:
    return TOPIC_LABELS.get(value, value)


def source_label(value: str) -> str:
    return SOURCE_LABELS.get(value, value)


FILTERS = {
    "to_paris": to_paris,
    "datetime_attr": datetime_attr,
    "absolute_date": absolute_date,
    "relative_date": relative_date,
    "excerpt": excerpt,
    "lang_label": lang_label,
    "topic_label": topic_label,
    "source_label": source_label,
}
