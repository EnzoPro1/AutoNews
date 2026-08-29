"""Parsing : 4 fixtures obligatoires (RSS 2.0 sain, Atom, dates cassees, XML tronque)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    by_url = {
        entry.url_canonical.rsplit("/", 1)[-1]: entry for entry in parse("dates_broken.xml").entries
    }

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
    # a-sans-date, d-illisible, e-epoch, f-futur-lointain
    assert len(fallbacks) == 4


# ---------------------------------------------------------------- XML tronque


def test_truncated_xml_raises_cleanly() -> None:
    with pytest.raises(FeedParseError):
        parse("truncated.xml")


def test_truncated_fixture_actually_triggers_the_rule() -> None:
    """Verifie que la fixture est bien construite : feedparser doit signaler une
    erreur SAX ou ne rendre aucune entree. Sans ca le test precedent ne teste rien."""
    from xml.sax import SAXException

    import feedparser

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
    # L'original est conserve tel quel pour audit. C'est aussi ce qui garantit
    # que nh3 recoit la charge : avec l'assainisseur de feedparser actif, il ne
    # voyait qu'un fragment deja desamorce et ces assertions passaient sans rien
    # exercer.
    raw = first.raw_summary or ""
    assert "<script>alert('xss')</script>" in raw
    assert 'onerror="alert(1)"' in raw
    assert 'href="javascript:alert(2)"' in raw


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


# ------------------------------------------------- pages sans chronologie


def test_degenerate_page_dates_are_demoted_to_fetched() -> None:
    """Un flux qui horodate tout a son heure de generation ne fournit pas de
    dates d'articles. Observe sur Actu IA : 15 items en 20 secondes."""
    result = parse("degenerate_dates.xml")
    assert len(result.entries) == 6
    assert {e.date_source for e in result.entries} == {"fetched"}
    assert {e.published_at for e in result.entries} == {NOW}


def test_demotion_does_not_keep_the_bogus_timestamp() -> None:
    """Conserver l'horodatage d'origine en le declarant 'fetched' ferait mentir
    la colonne dans l'autre sens."""
    for entry in parse("degenerate_dates.xml").entries:
        assert entry.published_at == NOW
        assert entry.published_at.year == 2026 and entry.published_at.month == 8


def test_a_normal_feed_is_never_demoted() -> None:
    """Le risque de cette regle est le faux positif : verifie sur les fixtures
    dont les dates sont legitimes."""
    for fixture in ("rss20_ok.xml", "atom_ok.xml"):
        sources = {e.date_source for e in parse(fixture).entries}
        assert sources != {"fetched"}, fixture


def test_a_short_burst_is_not_demoted() -> None:
    """En dessous de 5 entrees, une fenetre etroite peut etre une vraie rafale
    de publication : on ne requalifie pas."""
    from veille.normalize.dates import has_degenerate_span

    base = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
    burst_of_4 = [base + timedelta(seconds=10 * i) for i in range(4)]
    assert has_degenerate_span(burst_of_4) is False

    burst_of_6 = [base + timedelta(seconds=10 * i) for i in range(6)]
    assert has_degenerate_span(burst_of_6) is True


def test_span_just_over_the_window_is_kept() -> None:
    from veille.normalize.dates import has_degenerate_span

    base = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
    assert has_degenerate_span([base + timedelta(minutes=i) for i in range(6)]) is False
    assert has_degenerate_span([base + timedelta(seconds=50 * i) for i in range(6)]) is True


def test_degenerate_rule_is_shared_with_gap_detection() -> None:
    """Le seuil est defini une seule fois : parse et la detection de trou ne
    doivent pas pouvoir diverger."""
    from veille.normalize import dates

    assert timedelta(minutes=5) == dates.DEGENERATE_SPAN
    assert dates.DEGENERATE_MIN_ENTRIES == 5
