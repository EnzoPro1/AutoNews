"""Fixtures partagees.

Les tests de fonctions pures ne touchent pas la base. Ceux marques @pytest.mark.db
utilisent un vrai Postgres (le service `db` de docker compose) : SQLite ne sait
pas executer CREATE EXTENSION vector, on testerait un schema different de la
production.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from veille.db import SessionLocal, engine
from veille.models import Article, ArticleFeed, Feed, FeedRun
from veille.schemas import FeedSpec

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Horloge de reference. Toutes les etapes prennent leur temps en parametre :
#: aucun test n'a besoin de geler datetime.now globalement.
NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def make_spec(feed_id: str = "demo", **overrides: object) -> FeedSpec:
    values: dict[str, object] = {
        "id": feed_id,
        "name": feed_id.title(),
        "url": f"https://{feed_id}.test/feed.xml",
        "lang": "fr",
        "topic": "sec",
        "source_type": "media",
    }
    values.update(overrides)
    return FeedSpec(**values)  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def _database() -> None:
    try:
        with engine.connect() as connection:
            connection.close()
    except Exception as exc:  # pragma: no cover - depend de l'environnement
        pytest.skip(f"Postgres indisponible : {exc}")


@pytest.fixture
def session(_database: None) -> Iterator[Session]:
    """Session isolee : tout est efface avant chaque test marque db."""
    with SessionLocal() as db:
        for model in (ArticleFeed, FeedRun, Article, Feed):
            db.query(model).delete()
        db.commit()
        yield db
        db.rollback()


@pytest.fixture
def feed(session: Session) -> Feed:
    return make_feed(session, "demo")


def make_feed(session: Session, slug: str, **overrides: object) -> Feed:
    values: dict[str, object] = {
        "slug": slug,
        "name": slug.title(),
        "url": f"https://{slug}.test/feed.xml",
        "lang": "fr",
        "topic": "sec",
        "source_type": "media",
    }
    values.update(overrides)
    row = Feed(**values)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
