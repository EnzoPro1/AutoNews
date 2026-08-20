"""initial : extension vector, feed, article, article_feed, feed_run

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-19

"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    # V1 : la colonne existe des maintenant pour ne pas avoir a reecrire une
    # table de plusieurs centaines de milliers de lignes plus tard. Elle reste
    # NULL en V0 et n'a pas d'index vectoriel (un HNSW sur du 100 % NULL ne sert
    # a rien et son cout de build appartient au lot V1).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "feed",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=2), nullable=False),
        sa.Column("topic", sa.String(length=8), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feed"),
        sa.UniqueConstraint("slug", name="uq_feed_slug"),
        sa.UniqueConstraint("url", name="uq_feed_url"),
        sa.CheckConstraint("lang IN ('fr', 'en')", name="ck_feed_lang"),
        sa.CheckConstraint("topic IN ('ai', 'sec', 'both')", name="ck_feed_topic"),
        sa.CheckConstraint(
            "source_type IN ('media', 'vendor', 'official', 'community')",
            name="ck_feed_source_type",
        ),
    )

    op.create_table(
        "article",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("first_feed_id", sa.BigInteger(), nullable=False),
        sa.Column("url_canonical", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("raw_summary", sa.Text(), nullable=True),
        sa.Column("summary_clean", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_source", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_article"),
        sa.ForeignKeyConstraint(
            ["first_feed_id"], ["feed.id"], name="fk_article_first_feed", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("url_canonical", name="uq_article_url_canonical"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_article_hash_len"),
        sa.CheckConstraint(
            "date_source IN ('published', 'updated', 'fetched')", name="ck_article_date_source"
        ),
    )
    op.create_index("ix_article_published", "article", ["published_at", "id"])
    op.create_index("ix_article_first_feed_published", "article", ["first_feed_id", "published_at"])
    op.create_index("ix_article_content_hash", "article", ["content_hash"])

    op.create_table(
        "article_feed",
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("feed_id", sa.BigInteger(), nullable=False),
        sa.Column("guid", sa.Text(), nullable=True),
        sa.Column("url_original", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("article_id", "feed_id", name="pk_article_feed"),
        sa.ForeignKeyConstraint(
            ["article_id"], ["article.id"], name="fk_article_feed_article", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["feed_id"], ["feed.id"], name="fk_article_feed_feed", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_article_feed_feed", "article_feed", ["feed_id"])

    op.create_table(
        "feed_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("feed_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("n_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_feed_run"),
        sa.ForeignKeyConstraint(
            ["feed_id"], ["feed.id"], name="fk_feed_run_feed", ondelete="CASCADE"
        ),
        sa.CheckConstraint("status IN ('ok', 'not_modified', 'error')", name="ck_feed_run_status"),
    )
    op.create_index("ix_feed_run_feed_started", "feed_run", ["feed_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_feed_run_feed_started", table_name="feed_run")
    op.drop_table("feed_run")
    op.drop_index("ix_article_feed_feed", table_name="article_feed")
    op.drop_table("article_feed")
    op.drop_index("ix_article_content_hash", table_name="article")
    op.drop_index("ix_article_first_feed_published", table_name="article")
    op.drop_index("ix_article_published", table_name="article")
    op.drop_table("article")
    op.drop_table("feed")
    # L'extension n'est pas supprimee : elle peut servir a d'autres schemas.
