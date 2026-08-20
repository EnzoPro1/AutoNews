"""Modele relationnel.

Trois invariants portes par le schema lui-meme, pas par du code Python :
  - `article.url_canonical` est UNIQUE globalement (dedup inter-flux) ;
  - `article.first_seen_at` n'est jamais dans le SET d'un ON CONFLICT ;
  - toutes les colonnes temporelles sont TIMESTAMPTZ, jamais naive.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from veille.db import Base
from veille.schemas import DATE_SOURCES, LANGS, RUN_STATUSES, SOURCE_TYPES, TOPICS

EMBEDDING_DIM = 1024


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class Feed(Base):
    __tablename__ = "feed"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_feed_slug"),
        UniqueConstraint("url", name="uq_feed_url"),
        CheckConstraint(_in_list("lang", LANGS), name="ck_feed_lang"),
        CheckConstraint(_in_list("topic", TOPICS), name="ck_feed_topic"),
        CheckConstraint(_in_list("source_type", SOURCE_TYPES), name="ck_feed_source_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: identifiant fonctionnel issu de feeds.yaml ; un renommage est un UPDATE.
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(String(2), nullable=False)
    topic: Mapped[str] = mapped_column(String(8), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)

    #: un flux retire de feeds.yaml est desactive, jamais supprime : sinon la
    #: cascade emporterait ses articles.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: renvoyes verbatim en If-None-Match / If-Modified-Since. Le Last-Modified est
    #: stocke en TEXT et non en timestamptz : le reformater casserait les 304.
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    runs: Mapped[list[FeedRun]] = relationship(back_populates="feed")

    def __repr__(self) -> str:
        return f"<Feed {self.slug}>"


class Article(Base):
    __tablename__ = "article"
    __table_args__ = (
        UniqueConstraint("url_canonical", name="uq_article_url_canonical"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_article_hash_len"),
        CheckConstraint(_in_list("date_source", DATE_SOURCES), name="ck_article_date_source"),
        Index("ix_article_published", "published_at", "id"),
        Index("ix_article_first_feed_published", "first_feed_id", "published_at"),
        Index("ix_article_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    #: flux qui a vu l'article en premier. L'ensemble des flux l'ayant relaye est
    #: dans article_feed : cette colonne ne pretend pas etre exhaustive.
    first_feed_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("feed.id", ondelete="CASCADE"), nullable=False
    )

    url_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: HTML brut du flux : contenu non fiable, stocke pour audit, jamais rendu.
    raw_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: sortie de nh3, seul champ que les templates ont le droit d'afficher.
    summary_clean: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 'published' | 'updated' | 'fetched'. Une seule colonne : un booleen
    #: date_is_fallback en plus finirait par la contredire.
    date_source: Mapped[str] = mapped_column(String(16), nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: V1. Nullable, jamais lue ni alimentee en V0, pas d'index vectoriel.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    first_feed: Mapped[Feed] = relationship()
    observations: Mapped[list[ArticleFeed]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )

    @property
    def is_fallback(self) -> bool:
        """La date affichee ne vient pas du flux."""
        return self.date_source == "fetched"

    @property
    def was_revised(self) -> bool:
        """Contenu modifie depuis la premiere observation (CERT-FR revise en place)."""
        return self.updated_at > self.first_seen_at

    def __repr__(self) -> str:
        return f"<Article {self.url_canonical}>"


class ArticleFeed(Base):
    """Une observation : ce flux a relaye cet article, sous ce guid, a cette URL.

    Le guid et l'URL d'origine sont des proprietes de l'observation, pas de
    l'article : deux flux relayant le meme papier n'ont ni le meme guid ni
    forcement la meme URL brute.
    """

    __tablename__ = "article_feed"
    __table_args__ = (Index("ix_article_feed_feed", "feed_id"),)

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("article.id", ondelete="CASCADE"), primary_key=True
    )
    feed_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("feed.id", ondelete="CASCADE"), primary_key=True
    )
    guid: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_original: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    article: Mapped[Article] = relationship(back_populates="observations")
    feed: Mapped[Feed] = relationship()


class FeedRun(Base):
    """Trace d'un passage d'ingestion sur un flux. Ecrite meme (surtout) en echec."""

    __tablename__ = "feed_run"
    __table_args__ = (
        CheckConstraint(_in_list("status", RUN_STATUSES), name="ck_feed_run_status"),
        Index("ix_feed_run_feed_started", "feed_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    feed_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("feed.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    n_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: porte aussi les avertissements non bloquants (bozo_exception sur un run 'ok').
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    feed: Mapped[Feed] = relationship(back_populates="runs")
