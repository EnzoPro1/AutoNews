"""Idempotence de l'ingestion et table d'observations article_feed."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import NOW, make_feed, read_fixture
from veille.ingest.parse import parse_feed
from veille.ingest.store import store_entries
from veille.models import Article, ArticleFeed, Feed

pytestmark = pytest.mark.db

LATER = NOW + timedelta(hours=6)
FAILLE_URL = "https://exemple-sain.test/2026/08/faille-routeur"


def ingest(session: Session, feed: Feed, fixture: str, when=NOW):
    parsed = parse_feed(read_fixture(fixture), fetched_at=when)
    result = store_entries(session, feed_id=feed.id, entries=parsed.entries, now=when)
    session.commit()
    return result


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_first_run_inserts_everything(session: Session, feed: Feed) -> None:
    result = ingest(session, feed, "rss20_ok.xml")
    assert (result.n_new, result.n_seen) == (3, 3)
    assert count(session, Article) == 3
    assert count(session, ArticleFeed) == 3


def test_second_run_creates_nothing(session: Session, feed: Feed) -> None:
    ingest(session, feed, "rss20_ok.xml")
    second = ingest(session, feed, "rss20_ok.xml", when=LATER)

    assert (second.n_new, second.n_seen) == (0, 3)
    assert count(session, Article) == 3
    assert count(session, ArticleFeed) == 3


def test_first_seen_at_never_moves(session: Session, feed: Feed) -> None:
    ingest(session, feed, "rss20_ok.xml")
    before = dict(session.execute(select(Article.url_canonical, Article.first_seen_at)).all())

    ingest(session, feed, "rss20_ok.xml", when=LATER)
    after = dict(session.execute(select(Article.url_canonical, Article.first_seen_at)).all())

    assert before == after
    assert all(value == NOW for value in after.values())


def test_last_seen_at_moves(session: Session, feed: Feed) -> None:
    ingest(session, feed, "rss20_ok.xml")
    ingest(session, feed, "rss20_ok.xml", when=LATER)

    values = set(session.scalars(select(Article.last_seen_at)))
    assert values == {LATER}


def test_unchanged_content_does_not_bump_updated_at(session: Session, feed: Feed) -> None:
    ingest(session, feed, "rss20_ok.xml")
    ingest(session, feed, "rss20_ok.xml", when=LATER)

    article = session.scalar(select(Article).where(Article.url_canonical == FAILLE_URL))
    assert article is not None
    assert article.updated_at == NOW
    assert article.was_revised is False


def test_revision_updates_content_but_freezes_first_seen_and_published(
    session: Session, feed: Feed
) -> None:
    """CERT-FR revise ses avis en place : on veut la derniere version."""
    ingest(session, feed, "rss20_ok.xml")
    original = session.scalar(select(Article).where(Article.url_canonical == FAILLE_URL))
    assert original is not None
    published_before = original.published_at

    result = ingest(session, feed, "rss20_revised.xml", when=LATER)
    session.expire_all()

    assert result.n_new == 0
    assert count(session, Article) == 3

    revised = session.scalar(select(Article).where(Article.url_canonical == FAILLE_URL))
    assert revised is not None
    assert "mise a jour" in revised.title
    assert "Exploitation active" in (revised.summary_clean or "")
    assert revised.first_seen_at == NOW
    assert revised.published_at == published_before
    assert revised.updated_at == LATER
    assert revised.was_revised is True


def test_same_article_in_two_feeds_is_one_row_and_two_observations(session: Session) -> None:
    source = make_feed(session, "source")
    relay = make_feed(session, "relais")

    ingest(session, source, "rss20_ok.xml")
    ingest(session, relay, "relay.xml", when=LATER)

    # 3 articles du premier flux + 1 exclusif du second ; l'article relaye est partage.
    assert count(session, Article) == 4

    article = session.scalar(select(Article).where(Article.url_canonical == FAILLE_URL))
    assert article is not None
    assert article.first_feed_id == source.id, "first_feed_id = le flux qui l'a vu en premier"

    observations = session.scalars(
        select(ArticleFeed).where(ArticleFeed.article_id == article.id)
    ).all()
    assert {obs.feed_id for obs in observations} == {source.id, relay.id}


def test_each_observation_keeps_its_own_guid_and_raw_url(session: Session) -> None:
    source = make_feed(session, "source")
    relay = make_feed(session, "relais")
    ingest(session, source, "rss20_ok.xml")
    ingest(session, relay, "relay.xml", when=LATER)

    article = session.scalar(select(Article).where(Article.url_canonical == FAILLE_URL))
    assert article is not None
    by_feed = {
        obs.feed_id: obs
        for obs in session.scalars(select(ArticleFeed).where(ArticleFeed.article_id == article.id))
    }

    assert by_feed[source.id].guid == "urn:exemple:1"
    assert by_feed[relay.id].guid == "https://exemple-relais.test/item?id=90210"
    assert "utm_source" in by_feed[source.id].url_original
    assert "fbclid" in by_feed[relay.id].url_original


def test_observation_first_seen_at_is_per_feed(session: Session) -> None:
    source = make_feed(session, "source")
    relay = make_feed(session, "relais")
    ingest(session, source, "rss20_ok.xml")
    ingest(session, relay, "relay.xml", when=LATER)

    article = session.scalar(select(Article).where(Article.url_canonical == FAILLE_URL))
    assert article is not None
    by_feed = {
        obs.feed_id: obs
        for obs in session.scalars(select(ArticleFeed).where(ArticleFeed.article_id == article.id))
    }
    assert by_feed[source.id].first_seen_at == NOW
    assert by_feed[relay.id].first_seen_at == LATER


def test_store_refuses_naive_datetimes(session: Session, feed: Feed) -> None:
    from datetime import datetime

    parsed = parse_feed(read_fixture("rss20_ok.xml"), fetched_at=NOW)
    with pytest.raises(ValueError, match="naif"):
        store_entries(
            session,
            feed_id=feed.id,
            entries=parsed.entries,
            now=datetime(2026, 8, 19, 12, 0),  # noqa: DTZ001
        )


def test_empty_entry_list_is_a_noop(session: Session, feed: Feed) -> None:
    result = store_entries(session, feed_id=feed.id, entries=[], now=NOW)
    assert (result.n_new, result.n_seen) == (0, 0)
    assert count(session, Article) == 0


def test_embedding_stays_empty_in_v0(session: Session, feed: Feed) -> None:
    ingest(session, feed, "rss20_ok.xml")
    assert all(embedding is None for embedding in session.scalars(select(Article.embedding)))
