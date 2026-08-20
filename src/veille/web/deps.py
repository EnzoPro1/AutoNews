"""Dependances FastAPI."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from veille.db import SessionLocal


def get_session() -> Iterator[Session]:
    """Une session par requete, en lecture seule cote web."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
