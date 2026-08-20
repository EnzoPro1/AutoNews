"""Parsing : 4 fixtures obligatoires (RSS 2.0 sain, Atom, dates cassees, XML tronque)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import NOW, read_fixture

from veille.errors import FeedParseError
from veille.ingest.parse import parse_feed


def parse(name: str):
    return parse_feed(read_fixture(name), fetched_at=NOW)


# --------------------------------------------------------------------- RSS 2.0


def test_rss20_yields_every_item() -> None:
    result = parse("rss20_ok.xml")
    assert len(result.entries) == 3
    assert result.n_skipped == 0
    assert result.bozo_message is None


def test_rss20_urls_are_canonicalised() -> None:
    urls = [entry.url_canonical for entry in parse("rss20_ok.xml").entries]
    assert urls == [
        "https://exemple-sain.test/2026/08/faille-routeur",
        "https://exemple-sain.test/2026/08/modele-ouvert",
        "https://exemple-sain.test/2026/08/bulletin",
    ]


def test_rss20_keeps_the_original_url_alongside() -> None:
    first = parse("rss20_ok.xml").entries[0]
    assert "utm_source=rss" in first.url_original


def test_rss20_dates_are_utc_aware() -> None:
    first = parse("rss20_ok.xml").entries[0]
    assert first.published_at == datetime(2026, 8, 19, 6, 30, tzinfo=UTC)
    assert first.date_source == "published"
    assert first.published_at.tzinfo is not None


def test_rss20_double_escaped_entities_are_fixed() -> None:
    second = parse("rss20_ok.xml").entries[1]
    assert second.title == "Le modèle ouvert qui inquiète les éditeurs"
    assert "&amp;" not in (second.summary_clean or "")


def test_rss20_content_encoded_is_preferred_over_description() -> None:
    third = parse("rss20_ok.xml").entries[2]
    assert third.summary_clean is not None
    assert "résumé de la semaine" in third.summary_clean


def test_rss20_author_and_guid_are_kept() -> None:
    first = parse("rss20_ok.xml").entries[0]
    assert first.author == "Alice Durand"
    assert first.guid == "urn:exemple:1"


# ------------------------------------------------------------------------ Atom


def test_atom_yields_every_entry() -> None:
    result = parse("atom_ok.xml")
    assert len(result.entries) == 2
    assert result.bozo_message is None


def test_atom_published_wins_over_updated() -> None:
    first = parse("atom_ok.xml").entries[0]
    assert first.published_at == datetime(2026, 8, 19, 9, 15, tzinfo=UTC)
    assert first.date_source == "published"


def test_atom_falls_back_to_updated() -> None:
    second = parse("atom_ok.xml").entries[1]
    assert second.published_at == datetime(2026, 8, 18, 22, 0, tzinfo=UTC)
    assert second.date_source == "updated"


def test_atom_link_is_read_from_the_href_attribute() -> None:
    first = parse("atom_ok.xml").entries[0]
    assert first.url_canonical == "https://exemple-atom.test/2026/08/local-models"


# ---------------------------------------------------------------- dates cassees


def test_broken_dates_never_crash_and_are_all_utc() -> None:
    result = parse("dates_broken.xml")
    assert len(result.entries) == 6
    assert all(entry.published_at.tzinfo is not None for entry in result.entries)


def test_broken_dates_use_the_documented_fallbacks() -> None:
    by_url = {entry.url_canonical.rsplit("/", 1)[-1]: entry for entry in parse("dates_broken.xml").entries}

    # pubDate absent -> fetched_at
    assert by_url["a-sans-date"].date_source == "fetched"
    assert by_url["a-sans-date"].published_at == NOW

    # format francais non standard -> lu quand meme
    assert by_url["b-format-maison"].date_source == "published"
    assert by_url["b-format-maison"].published_at == datetime(2026, 8, 19, 8, 30, tzinfo=UTC)

    # fuseau absent -> interprete en UTC, pas rejete
    assert by_url["c-sans-fuseau"].date_source == "published"
    assert by_url["c-sans-fuseau"].published_at == datetime(2026, 8, 18, 7, 0, tzinfo=UTC)

    # illisible -> fetched_at
    assert by_url["d-illisible"].date_source == "fetched"

    # epoch -> rejete
    assert by_url["e-epoch"].date_source == "fetched"

    # futur lointain -> rejete
    assert by_url["f-futur-lointain"].date_source == "fetched"


def test_fallback_dates_are_flagged_not_hidden() -> None:
    fallbacks = [e for e in parse("dates_broken.xml").entries if e.date_source == "fetched"]
    assert len(fallbacks) == 3


# ---------------------------------------------------------------- XML tronque


def test_truncated_xml_raises_cleanly() -> None:
    with pytest.raises(FeedParseError):
        parse("truncated.xml")


def test_truncated_fixture_actually_triggers_the_rule() -> None:
    """Verifie que la fixture est bien construite : feedparser doit signaler une
    erreur SAX ou ne rendre aucune entree. Sans ca le test precedent ne teste rien."""
    import feedparser
    from xml.sax import SAXException

    parsed = feedparser.parse(read_fixture("truncated.xml"))
    assert parsed.get("bozo")
    assert not parsed.get("entries") or isinstance(parsed.get("bozo_exception"), SAXException)


def test_empty_body_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"", fetched_at=NOW)


def test_html_page_instead_of_a_feed_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"<!DOCTYPE html><html><body>404</body></html>", fetched_at=NOW)


# ------------------------------------------------------------------- sanitisation


def test_hostile_summary_is_sanitised_at_parse_time() -> None:
    first = parse("hostile.xml").entries[0]
    cleaned = first.summary_clean or ""
    assert "<script" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()
    assert "<!--" not in cleaned
    assert "javascript:" not in cleaned.lower()
    assert "Texte légitime" in cleaned
    # l'original est conserve tel quel pour audit
    assert "<script>" in (first.raw_summary or "")


def test_summary_is_none_when_nothing_survives() -> None:
    second = parse("hostile.xml").entries[1]
    assert second.summary_clean is None
    assert second.raw_summary is not None


# ------------------------------------------------------------------------ divers


def test_parse_refuses_a_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_feed(read_fixture("rss20_ok.xml"), fetched_at=datetime(2026, 8, 19, 12, 0))  # noqa: DTZ001


def test_parse_is_deterministic() -> None:
    first = parse("rss20_ok.xml").entries
    second = parse("rss20_ok.xml").entries
    assert [e.content_hash for e in first] == [e.content_hash for e in second]


def test_revision_changes_the_content_hash_but_not_the_url() -> None:
    original = parse("rss20_ok.xml").entries[0]
    revised = parse("rss20_revised.xml").entries[0]
    assert revised.url_canonical == original.url_canonical
    assert revised.content_hash != original.content_hash
    assert revised.guid != original.guid, "le guid est instable, c'est pour ca qu'il n'est pas cle"
