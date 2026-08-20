"""Chargement et validation de feeds.yaml. Aucun acces base ici."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from veille.config import settings
from veille.errors import VeilleError
from veille.schemas import FeedSpec, Lang, SourceType, Topic

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class FeedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=SLUG_PATTERN, min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    lang: Lang
    topic: Topic
    source_type: SourceType

    @field_validator("url")
    @classmethod
    def _https_or_http(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme not in ("http", "https"):
            raise ValueError("le schema doit etre http ou https")
        return v


class FeedsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeds: list[FeedEntry] = Field(min_length=1)


def load_feeds(path: Path | None = None) -> list[FeedSpec]:
    """Lit le YAML, valide, et renvoie des FeedSpec. Leve VeilleError si le
    fichier est absent, illisible, ou contient des doublons d'id/url."""
    path = path or settings.feeds_path
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VeilleError(f"feeds.yaml introuvable : {path}") from exc
    except yaml.YAMLError as exc:
        raise VeilleError(f"feeds.yaml illisible : {exc}") from exc

    parsed = FeedsFile.model_validate(raw)

    _reject_duplicates([e.id for e in parsed.feeds], "id")
    _reject_duplicates([str(e.url) for e in parsed.feeds], "url")

    return [
        FeedSpec(
            id=e.id,
            name=e.name,
            url=str(e.url),
            lang=e.lang,
            topic=e.topic,
            source_type=e.source_type,
        )
        for e in parsed.feeds
    ]


def _reject_duplicates(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise VeilleError(f"feeds.yaml : {label} en double -> {v}")
        seen.add(v)
