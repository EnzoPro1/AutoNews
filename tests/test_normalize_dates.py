"""Resolution des dates : ordre de repli et rejet des valeurs aberrantes."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from veille.normalize.dates import parse_datetime, resolve_published

FETCHED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)


def test_parse_rfc822() -> None:
    assert parse_datetime("Wed, 19 Aug 2026 15:14:25 +0000") == datetime(
        2026, 8, 19, 15, 14, 25, tzinfo=UTC
    )


def test_parse_rfc822_with_offset_is_converted_to_utc() -> None:
    assert parse_datetime("Wed, 19 Aug 2026 15:14:25 +0200") == datetime(
        2026, 8, 19, 13, 14, 25, tzinfo=UTC
    )


def test_parse_iso8601() -> None:
    assert parse_datetime("2026-08-19T15:14:25+00:00") == datetime(
        2026, 8, 19, 15, 14, 25, tzinfo=UTC
    )


def test_parse_struct_time_from_feedparser() -> None:
    struct = time.struct_time((2026, 8, 19, 15, 14, 25, 0, 0, 0))
    assert parse_datetime(struct) == datetime(2026, 8, 19, 15, 14, 25, tzinfo=UTC)


def test_parse_naive_is_assumed_utc_and_aware() -> None:
    parsed = parse_datetime("2026-08-19 15:14:25")
    assert parsed == datetime(2026, 8, 19, 15, 14, 25, tzinfo=UTC)
    assert parsed is not None and parsed.tzinfo is not None


@pytest.mark.parametrize("raw", ["", "   ", "pas une date", "hier", None])
def test_parse_garbage_returns_none(raw: str | None) -> None:
    assert parse_datetime(raw) is None


def test_published_wins() -> None:
    value, source = resolve_published(
        "Wed, 19 Aug 2026 10:00:00 +0000", "Wed, 19 Aug 2026 11:00:00 +0000", FETCHED_AT
    )
    assert source == "published"
    assert value == datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def test_falls_back_to_updated() -> None:
    value, source = resolve_published(None, "Wed, 19 Aug 2026 11:00:00 +0000", FETCHED_AT)
    assert source == "updated"
    assert value == datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


def test_falls_back_to_fetched_at() -> None:
    value, source = resolve_published(None, None, FETCHED_AT)
    assert (value, source) == (FETCHED_AT, "fetched")


def test_unparseable_dates_fall_back_to_fetched_at() -> None:
    value, source = resolve_published("le 19 aout", "n'importe quoi", FETCHED_AT)
    assert (value, source) == (FETCHED_AT, "fetched")


def test_future_date_is_rejected_and_updated_is_used() -> None:
    future = (FETCHED_AT + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    value, source = resolve_published(future, "Wed, 19 Aug 2026 11:00:00 +0000", FETCHED_AT)
    assert source == "updated"
    assert value == datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


def test_small_clock_skew_is_tolerated() -> None:
    """Une heure d'avance est une horloge mal reglee, pas une date bidon."""
    slightly_ahead = (FETCHED_AT + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _, source = resolve_published(slightly_ahead, None, FETCHED_AT)
    assert source == "published"


def test_epoch_date_is_rejected() -> None:
    value, source = resolve_published("Thu, 01 Jan 1970 00:00:00 +0000", None, FETCHED_AT)
    assert (value, source) == (FETCHED_AT, "fetched")


def test_output_is_always_utc_aware() -> None:
    paris = timezone(timedelta(hours=2))
    fetched = datetime(2026, 8, 19, 14, 0, tzinfo=paris)
    value, _ = resolve_published(None, None, fetched)
    assert value.tzinfo is UTC
    assert value == datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def test_naive_fetched_at_is_refused() -> None:
    """Aucun datetime naif ne doit franchir la frontiere de store."""
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_published(None, None, datetime(2026, 8, 19, 12, 0))  # noqa: DTZ001
