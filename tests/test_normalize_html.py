"""Sanitisation et double-encodage d'entites."""

from __future__ import annotations

import pytest

from veille.normalize.html import sanitize, strip_tags
from veille.normalize.text import clean_title, content_hash, fix_double_escaping, normalize_ws

HOSTILE = (
    "<p>Bonjour<!-- commentaire cache --></p>"
    '<script>alert("xss")</script>'
    '<img src=x onerror="alert(1)">'
    '<a href="javascript:alert(2)">clic</a>'
    '<iframe src="https://evil.example"></iframe>'
    '<div style="position:fixed">bloc</div>'
)


def test_sanitize_removes_script_onerror_and_comments() -> None:
    cleaned = sanitize(HOSTILE)
    assert cleaned is not None
    lowered = cleaned.lower()
    assert "<script" not in lowered
    assert "alert(" not in lowered
    assert "onerror" not in lowered
    assert "<!--" not in lowered
    assert "commentaire cache" not in lowered
    assert "<img" not in lowered
    assert "<iframe" not in lowered
    assert "javascript:" not in lowered
    # le texte legitime survit
    assert "Bonjour" in cleaned


def test_sanitize_keeps_allowlisted_markup_and_adds_rel() -> None:
    cleaned = sanitize('<p>Voir <a href="https://example.com/a">la source</a>.</p>')
    assert cleaned is not None
    assert "<p>" in cleaned
    assert 'href="https://example.com/a"' in cleaned
    assert "noopener" in cleaned and "noreferrer" in cleaned


def test_sanitize_returns_none_when_nothing_displayable_remains() -> None:
    """Les items Hacker News n'ont pas toujours de resume exploitable."""
    assert sanitize(None) is None
    assert sanitize("") is None
    assert sanitize("   \n  ") is None
    assert sanitize("<script>alert(1)</script>") is None
    assert sanitize("<p>   </p>") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("&amp;eacute;", "&eacute;"),
        ("&amp;#233;", "&#233;"),
        ("&amp;#x00E9;", "&#x00E9;"),
        ("&amp;amp;eacute;", "&eacute;"),
        # un & seul echappe reste echappe : ce n'est pas du double-encodage
        ("Fnac &amp; Darty", "Fnac &amp; Darty"),
        # et surtout : on ne reintroduit pas de balise
        ("&amp;lt;script&amp;gt;", "&lt;script&gt;"),
    ],
)
def test_fix_double_escaping(raw: str, expected: str) -> None:
    assert fix_double_escaping(raw) == expected


def test_double_escaped_entity_never_reaches_the_template() -> None:
    cleaned = sanitize("<p>Cybers&amp;eacute;curit&amp;eacute; renforc&amp;eacute;e</p>")
    assert cleaned is not None
    assert "&amp;eacute;" not in cleaned
    assert "Cybersécurité renforcée" in strip_tags(cleaned)


def test_double_escaped_tag_stays_inert_after_sanitize() -> None:
    cleaned = sanitize("<p>&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;</p>")
    assert cleaned is not None
    assert "<script" not in cleaned.lower()


def test_clean_title_unescapes_and_collapses_whitespace() -> None:
    assert clean_title("  Cybers&amp;eacute;curit&amp;eacute;\n  renforc&eacute;e ") == (
        "Cybersécurité renforcée"
    )


def test_strip_tags() -> None:
    assert strip_tags("<p>a <strong>b</strong>  c</p>") == "a b c"
    assert strip_tags(None) == ""
    assert strip_tags("") == ""


def test_normalize_ws() -> None:
    assert normalize_ws("  a \n\t b  ") == "a b"


def test_content_hash_is_stable_and_sensitive() -> None:
    a = content_hash("Titre", "resume")
    assert a == content_hash("  Titre  ", "resume\n")
    assert len(a) == 64
    assert a != content_hash("Titre", "resume modifie")
    assert a != content_hash("Titre modifie", "resume")


def test_content_hash_separator_prevents_collisions() -> None:
    assert content_hash("ab", "c") != content_hash("a", "bc")


def test_content_hash_ignores_cosmetic_markup_changes() -> None:
    """Le hash porte sur le texte : rebalisage != revision."""
    first = content_hash("Titre", strip_tags("<p>Un <b>resume</b></p>"))
    second = content_hash("Titre", strip_tags("<div>Un <strong>resume</strong></div>"))
    assert first == second
