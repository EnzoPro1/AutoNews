"""Fixtures partagees.

Les tests de fonctions pures ne touchent pas la base. Ceux marques @pytest.mark.db
utilisent un vrai Postgres : SQLite ne sait pas executer CREATE EXTENSION vector,
on testerait un schema different de la production.

Ce module bascule la connexion vers une base dediee (<db>_test) AVANT le premier
import de veille.config : la suite efface des tables entieres, elle n'a rien a
faire dans la base de developpement.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"

DEFAULT_URL = "postgresql+psycopg://veille:veille@db:5432/veille"


def _redirect_to_test_database() -> str:
    url = make_url(os.environ.get("VEILLE_DATABASE_URL", DEFAULT_URL))
    if not (url.database or "").endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    rendered = url.render_as_string(hide_password=False)
    os.environ["VEILLE_DATABASE_URL"] = rendered
    return rendered


def _create_database_if_missing(rendered: str) -> None:
    url = make_url(rendered)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()


_TEST_DATABASE_URL = _redirect_to_test_database()
_create_database_if_missing(_TEST_DATABASE_URL)

# Ces imports doivent suivre la bascule ci-dessus : veille.config lit
# l'environnement a l'import et ne le relit jamais.
from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from veille.db import SessionLocal  # noqa: E402
from veille.models import Article, ArticleFeed, Feed, FeedRun  # noqa: E402
from veille.schemas import FeedSpec  # noqa: E402
from veille.web.app import app  # noqa: E402

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


#: En CI, un skip silencieux est pire qu'un echec : si CREATE EXTENSION vector
#: echouait (image postgres standard au lieu de pgvector), tous les tests marques
#: db seraient sautes et la CI resterait verte. Un faux vert sur la moitie de la
#: suite. VEILLE_REQUIRE_DB=1 transforme donc le skip en echec.
REQUIRE_DB = os.environ.get("VEILLE_REQUIRE_DB", "").strip().lower() in ("1", "true", "yes")


@pytest.fixture(scope="session")
def _database() -> None:
    """Applique les migrations sur la base de test.

    On passe par Alembic et non par create_all : c'est la migration qui est
    livree, c'est donc elle qui doit etre testee, extension vector comprise.
    """
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        command.upgrade(config, "head")
    except Exception as exc:  # pragma: no cover - depend de l'environnement
        if REQUIRE_DB:
            raise
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


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """Client HTTP de test. Depend de `session` : la base est nettoyee avant."""
    with TestClient(app) as test_client:
        yield test_client
