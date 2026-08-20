"""Requetes de lecture. Isolees du routing pour rester testables sans client HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from veille.models import Article, Feed, FeedRun

#: Un filtre ?topic=ai doit ramener aussi les flux classes 'both', sinon LeMagIT
#: et Next disparaissent de toutes les vues thematiques.
_TOPIC_EXPANSION = {"ai": ("ai", "both"), "sec": ("sec", "both"), "both": ("both",)}


@dataclass(frozen=True, slots=True)
class ArticleRow:
    article: Article
    feed: Feed


@dataclass(frozen=True, slots=True)
class Page:
    rows: list[ArticleRow]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


@dataclass(frozen=True, slots=True)
class FeedHealth:
    feed: Feed
    last_run: FeedRun | None
    n_articles: int


def list_articles(
    session: Session,
    *,
    lang: str | None = None,
    topic: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Page:
    """Liste paginee, tri published_at decroissant.

    lang et topic sont lus sur le flux qui a vu l'article en premier : ils ne
    sont pas dupliques sur article, feeds.yaml reste la source de verite.
    """
    page = max(1, page)

    statement = select(Article, Feed).join(Feed, Article.first_feed_id == Feed.id)
    statement = _apply_filters(statement, lang=lang, topic=topic)

    total_statement = _apply_filters(
        select(func.count()).select_from(Article).join(Feed, Article.first_feed_id == Feed.id),
        lang=lang,
        topic=topic,
    )
    total = session.scalar(total_statement) or 0

    statement = (
        statement.order_by(Article.published_at.desc(), Article.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = [ArticleRow(article=article, feed=feed) for article, feed in session.execute(statement)]

    return Page(rows=rows, total=total, page=page, page_size=page_size)


def _apply_filters(statement: Select, *, lang: str | None, topic: str | None) -> Select:
    if lang:
        statement = statement.where(Feed.lang == lang)
    if topic:
        statement = statement.where(Feed.topic.in_(_TOPIC_EXPANSION.get(topic, (topic,))))
    return statement


def feed_health(session: Session) -> list[FeedHealth]:
    """Dernier run de chaque flux, y compris ceux qui n'ont jamais tourne."""
    latest = (
        select(FeedRun.feed_id, func.max(FeedRun.started_at).label("started_at"))
        .group_by(FeedRun.feed_id)
        .subquery()
    )
    article_counts = (
        select(Article.first_feed_id, func.count().label("n"))
        .group_by(Article.first_feed_id)
        .subquery()
    )

    statement = (
        select(Feed, FeedRun, func.coalesce(article_counts.c.n, 0))
        .outerjoin(latest, latest.c.feed_id == Feed.id)
        .outerjoin(
            FeedRun,
            (FeedRun.feed_id == latest.c.feed_id) & (FeedRun.started_at == latest.c.started_at),
        )
        .outerjoin(article_counts, article_counts.c.first_feed_id == Feed.id)
        .order_by(Feed.is_active.desc(), Feed.slug)
    )

    return [
        FeedHealth(feed=feed, last_run=run, n_articles=n)
        for feed, run, n in session.execute(statement)
    ]


def latest_article_at(session: Session) -> datetime | None:
    return session.scalar(select(func.max(Article.published_at)))
