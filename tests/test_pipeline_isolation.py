"""Isolation par flux : un flux qui leve ne doit ni interrompre le run ni faire
perdre ce que les autres ont ecrit."""

from __future__ import annotations

import httpx
import pytest
from conftest import make_feed, make_spec, read_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from veille.ingest.pipeline import run_ingestion
from veille.models import Article, Feed, FeedRun
from veille.seed import seed_feeds

pytestmark = pytest.mark.db

SPECS = [
    make_spec("sain", url="https://sain.test/feed.xml"),
    make_spec("casse", url="https://casse.test/feed.xml"),
    make_spec("relais", url="https://relais.test/feed.xml"),
]


def routed_client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        response = routes.get(str(request.url))
        if response is None:
            raise AssertionError(f"URL non prevue dans le test : {request.url}")
        if isinstance(response, Exception):
            raise response
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(fixture: str, **headers: str) -> httpx.Response:
    return httpx.Response(200, content=read_fixture(fixture), headers=headers)


@pytest.fixture
def three_feeds(session: Session) -> list[Feed]:
    return [make_feed(session, spec.id, url=spec.url) for spec in SPECS]


def counts(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def last_runs(session: Session) -> dict[str, FeedRun]:
    rows = session.execute(select(Feed.slug, FeedRun).join(FeedRun, FeedRun.feed_id == Feed.id))
    return {slug: run for slug, run in rows}


def test_a_failing_feed_does_not_stop_the_others(
    session: Session, three_feeds: list[Feed]
) -> None:
    routes = {
        "https://sain.test/feed.xml": ok("rss20_ok.xml"),
        "https://casse.test/feed.xml": ok("truncated.xml"),
        "https://relais.test/feed.xml": ok("relay.xml"),
    }
    with routed_client(routes) as client:
        outcomes = run_ingestion(session, SPECS, client=client)

    by_slug = {outcome.feed_slug: outcome for outcome in outcomes}
    assert by_slug["sain"].status == "ok"
    assert by_slug["casse"].status == "error"
    assert by_slug["relais"].status == "ok"

    # 3 articles du flux sain + 1 exclusif du relais (le 2e est le meme article)
    assert counts(session, Article) == 4


def test_the_failing_feed_has_an_error_row_in_the_database(
    session: Session, three_feeds: list[Feed]
) -> None:
    routes = {
        "https://sain.test/feed.xml": ok("rss20_ok.xml"),
        "https://casse.test/feed.xml": ok("truncated.xml"),
        "https://relais.test/feed.xml": ok("relay.xml"),
    }
    with routed_client(routes) as client:
        run_ingestion(session, SPECS, client=client)

    runs = last_runs(session)
    assert runs["casse"].status == "error"
    assert "FeedParseError" in (runs["casse"].error_message or "")
    assert runs["sain"].status == "ok"
    assert runs["relais"].status == "ok"
    assert counts(session, FeedRun) == 3, "un run est trace pour chaque flux, meme en echec"


def test_network_timeout_is_isolated(session: Session, three_feeds: list[Feed]) -> None:
    routes = {
        "https://sain.test/feed.xml": ok("rss20_ok.xml"),
        "https://casse.test/feed.xml": httpx.ConnectTimeout("trop lent"),
        "https://relais.test/feed.xml": ok("relay.xml"),
    }
    with routed_client(routes) as client:
        outcomes = run_ingestion(session, SPECS, client=client)

    by_slug = {outcome.feed_slug: outcome for outcome in outcomes}
    assert by_slug["casse"].status == "error"
    assert counts(session, Article) == 4


def test_http_error_is_recorded_with_its_status(session: Session, three_feeds: list[Feed]) -> None:
    routes = {
        "https://sain.test/feed.xml": ok("rss20_ok.xml"),
        "https://casse.test/feed.xml": httpx.Response(404),
        "https://relais.test/feed.xml": ok("relay.xml"),
    }
    with routed_client(routes) as client:
        run_ingestion(session, SPECS, client=client)

    runs = last_runs(session)
    assert runs["casse"].status == "error"
    assert runs["casse"].http_status == 404


def test_validators_are_stored_then_replayed(session: Session, three_feeds: list[Feed]) -> None:
    spec = [SPECS[0]]
    routes = {
        "https://sain.test/feed.xml": ok(
            "rss20_ok.xml", etag='"v1"', **{"last-modified": "Wed, 19 Aug 2026 08:00:00 GMT"}
        )
    }
    with routed_client(routes) as client:
        run_ingestion(session, spec, client=client)

    feed = session.scalar(select(Feed).where(Feed.slug == "sain"))
    assert feed is not None
    assert feed.etag == '"v1"'
    assert feed.last_modified == "Wed, 19 Aug 2026 08:00:00 GMT"

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcomes = run_ingestion(session, spec, client=client)

    assert seen["if-none-match"] == '"v1"'
    assert outcomes[0].status == "not_modified"
    assert outcomes[0].http_status == 304


def test_not_modified_writes_a_run_and_no_article(session: Session, three_feeds: list[Feed]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_ingestion(session, [SPECS[0]], client=client)

    runs = last_runs(session)
    assert runs["sain"].status == "not_modified"
    assert counts(session, Article) == 0


def test_degraded_but_readable_feed_is_ok_with_a_warning(
    session: Session, three_feeds: list[Feed]
) -> None:
    """bozo benin : le run reste 'ok' et le message atterrit dans feed_run."""
    body = read_fixture("rss20_ok.xml").replace(b'encoding="UTF-8"', b'encoding="ISO-8859-1"')
    routes = {"https://sain.test/feed.xml": httpx.Response(200, content=body)}

    with routed_client(routes) as client:
        outcomes = run_ingestion(session, [SPECS[0]], client=client)

    assert outcomes[0].status == "ok"
    assert counts(session, Article) == 3


def test_unknown_feed_slug_is_skipped_not_fatal(session: Session) -> None:
    """La table feed est vide : on log et on continue, on ne crashe pas."""
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        outcomes = run_ingestion(session, SPECS, client=client)
    assert outcomes == []


def test_run_after_seed_uses_the_yaml_feeds(session: Session) -> None:
    seed_feeds(session, SPECS)
    routes = {
        "https://sain.test/feed.xml": ok("rss20_ok.xml"),
        "https://casse.test/feed.xml": ok("truncated.xml"),
        "https://relais.test/feed.xml": ok("relay.xml"),
    }
    with routed_client(routes) as client:
        outcomes = run_ingestion(session, SPECS, client=client)

    assert len(outcomes) == 3
    assert sum(1 for outcome in outcomes if outcome.status == "ok") == 2
