"""Table de cas de la canonicalisation d'URL."""

from __future__ import annotations

import pytest

from veille.errors import InvalidUrlError
from veille.normalize.urls import canonicalize_url

# (libelle, entree, sortie attendue)
CASES: list[tuple[str, str, str]] = [
    (
        "https force + host en minuscules, path preserve",
        "http://Example.COM/Path/To/Article",
        "https://example.com/Path/To/Article",
    ),
    (
        "utm_* retires, parametre utile conserve",
        "https://example.com/a?utm_source=x&utm_medium=y&id=42",
        "https://example.com/a?id=42",
    ),
    (
        "fbclid retire, le ? disparait avec lui",
        "https://example.com/a?fbclid=abc",
        "https://example.com/a",
    ),
    (
        "gclid, ref et source retires",
        "https://example.com/a?gclid=1&ref=hn&source=rss",
        "https://example.com/a",
    ),
    ("slash final retire", "https://example.com/a/", "https://example.com/a"),
    ("slash racine retire", "https://example.com/", "https://example.com"),
    ("fragment retire", "https://example.com/a#section-2", "https://example.com/a"),
    ("prefixe m. retire", "https://m.example.com/a", "https://example.com/a"),
    (
        "suffixe amp/ retire",
        "https://example.com/article/amp/",
        "https://example.com/article",
    ),
    (
        "parametres tries de facon deterministe",
        "https://example.com/a?b=2&a=1",
        "https://example.com/a?a=1&b=2",
    ),
    ("port 443 retire", "https://example.com:443/a", "https://example.com/a"),
    ("port 80 retire et https force", "http://example.com:80/a", "https://example.com/a"),
    (
        "segments relatifs et // resolus",
        "https://example.com/a//b/./c/../d",
        "https://example.com/a/b/d",
    ),
    (
        "UTF-8 deja encode laisse intact",
        "https://example.com/caf%C3%A9",
        "https://example.com/caf%C3%A9",
    ),
    (
        "caractere non reserve inutilement encode, decode",
        "https://example.com/%7Euser",
        "https://example.com/~user",
    ),
    ("schema absent, https ajoute", "example.com/a", "https://example.com/a"),
    (
        "www. CONSERVE : url_canonical est aussi le href affiche",
        "https://www.example.com/a",
        "https://www.example.com/a",
    ),
    (
        "hexa percent normalise en majuscules",
        "https://example.com/a%2fb",
        "https://example.com/a%2Fb",
    ),
    (
        "point final du host retire, userinfo retire",
        "https://user:pass@Example.com./a",
        "https://example.com/a",
    ),
    (
        "cumul : http + m. + utm + amp + fragment + slash",
        "http://m.example.com/story/amp/?utm_campaign=news&id=7#top",
        "https://example.com/story?id=7",
    ),
    (
        "URL avec espaces parasites en bord de champ",
        "  https://example.com/a  ",
        "https://example.com/a",
    ),
    (
        "parametre de recherche conserve (flux hnrss)",
        "https://hnrss.org/newest?q=AI&points=100",
        "https://hnrss.org/newest?points=100&q=AI",
    ),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(raw, expected) for _, raw, expected in CASES],
    ids=[label for label, _, _ in CASES],
)
def test_canonicalize(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(raw, expected) for _, raw, expected in CASES],
    ids=[label for label, _, _ in CASES],
)
def test_canonicalize_is_idempotent(raw: str, expected: str) -> None:
    """Repasser la sortie dans la fonction ne doit plus rien changer."""
    once = canonicalize_url(raw)
    assert canonicalize_url(once) == once == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "mailto:a@b.c",
        "ftp://example.com/a",
        "",
        "   ",
        "https://" + "a" * 3000 + ".com/",
    ],
)
def test_canonicalize_rejects(raw: str) -> None:
    with pytest.raises(InvalidUrlError):
        canonicalize_url(raw)


def test_canonicalize_rejects_none() -> None:
    with pytest.raises(InvalidUrlError):
        canonicalize_url(None)


def test_variants_converge() -> None:
    """Les variantes d'un meme article doivent produire une seule cle."""
    variants = [
        "https://www.example.com/2026/08/titre",
        "http://www.example.com/2026/08/titre/",
        "https://www.example.com/2026/08/titre?utm_source=twitter&utm_medium=social",
        "https://www.example.com/2026/08/titre#commentaires",
        "https://www.example.com/2026/08/titre/amp/",
        "https://www.example.com/2026/08/titre?fbclid=IwAR123",
    ]
    canonical = {canonicalize_url(v) for v in variants}
    assert canonical == {"https://www.example.com/2026/08/titre"}


def test_m_prefix_only_when_a_real_apex_exists() -> None:
    """`m.` n'est retire que s'il reste un domaine complet derriere."""
    assert canonicalize_url("https://m.co/a") == "https://m.co/a"
    assert canonicalize_url("https://m.example.com/a") == "https://example.com/a"


def test_host_transformation_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Seul cas ou la canonicalisation peut produire une URL morte : on trace."""
    with caplog.at_level("WARNING", logger="veille.normalize.urls"):
        canonicalize_url("https://m.example.com/a")
    assert any("host transforme" in record.getMessage() for record in caplog.records)
