"""Etape reseau. Aucun test ne sort de la machine : httpx.MockTransport partout."""

from __future__ import annotations

import httpx
import pytest

from veille.errors import FetchError
from veille.ingest.fetch import fetch_feed
from veille.schemas import FeedSpec

SPEC = FeedSpec(
    id="demo",
    name="Demo",
    url="https://example.com/feed.xml",
    lang="fr",
    topic="sec",
    source_type="media",
)

BODY = b"<?xml version='1.0'?><rss version='2.0'><channel></channel></rss>"


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_200_returns_body_and_validators() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=BODY,
            headers={"etag": '"abc"', "last-modified": "Wed, 19 Aug 2026 13:26:29 GMT"},
        )

    with client_for(handler) as client:
        result = fetch_feed(SPEC, client=client)

    assert result.http_status == 200
    assert result.body == BODY
    assert result.etag == '"abc"'
    assert result.last_modified == "Wed, 19 Aug 2026 13:26:29 GMT"
    assert result.not_modified is False
    assert result.fetched_at.tzinfo is not None


def test_conditional_headers_are_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    with client_for(handler) as client:
        fetch_feed(SPEC, etag='"abc"', last_modified="Wed, 19 Aug 2026 13:26:29 GMT", client=client)

    assert seen["if-none-match"] == '"abc"'
    assert seen["if-modified-since"] == "Wed, 19 Aug 2026 13:26:29 GMT"
    assert "veille/" in seen["user-agent"]


def test_304_keeps_previous_validators_and_has_no_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    with client_for(handler) as client:
        result = fetch_feed(SPEC, etag='"abc"', last_modified="hier", client=client)

    assert result.not_modified is True
    assert result.body is None
    assert result.etag == '"abc"'
    assert result.last_modified == "hier"


def test_conditional_headers_absent_on_first_run() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=BODY)

    with client_for(handler) as client:
        fetch_feed(SPEC, client=client)

    assert "if-none-match" not in seen
    assert "if-modified-since" not in seen


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_client_errors_raise_without_retry(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with client_for(handler) as client, pytest.raises(FetchError) as excinfo:
        fetch_feed(SPEC, client=client)

    assert excinfo.value.http_status == status
    assert calls == 1


def test_server_error_is_retried_once_then_raises() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with client_for(handler) as client, pytest.raises(FetchError):
        fetch_feed(SPEC, client=client)

    assert calls == 2, "pas plus de 2 tentatives"


def test_retry_recovers_on_second_attempt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502)
        return httpx.Response(200, content=BODY)

    with client_for(handler) as client:
        result = fetch_feed(SPEC, client=client)

    assert calls == 2
    assert result.body == BODY


def test_timeout_is_wrapped_in_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("trop lent")

    with client_for(handler) as client, pytest.raises(FetchError):
        fetch_feed(SPEC, client=client)


def test_empty_body_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    with client_for(handler) as client, pytest.raises(FetchError):
        fetch_feed(SPEC, client=client)
