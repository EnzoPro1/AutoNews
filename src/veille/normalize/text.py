"""Normalisation de texte et hash de contenu."""

from __future__ import annotations

import hashlib
import html
import re

#: `&amp;eacute;` : une entite HTML qui a ete echappee une fois de trop.
#: Le motif est volontairement etroit (nom, decimal ou hexa suivi d'un ;) pour ne
#: PAS toucher a `&amp;` isole ni a `&amp;lt;script&amp;gt;` dans du HTML : un
#: html.unescape() global sur du contenu de flux reintroduirait des balises.
_DOUBLE_ESCAPED_RE = re.compile(
    r"&amp;(#[0-9]{1,7};|#[xX][0-9a-fA-F]{1,6};|[a-zA-Z][a-zA-Z0-9]{1,31};)"
)

_WHITESPACE_RE = re.compile(r"\s+")

MAX_UNESCAPE_PASSES = 3

#: separateur d'unite ASCII, absent des contenus reels : evite que
#: ("ab", "c") et ("a", "bc") produisent le meme hash.
_HASH_SEPARATOR = "\x1f"


def fix_double_escaping(value: str) -> str:
    """Ramene `&amp;eacute;` a `&eacute;`, sans desechapper le reste du HTML.

    Applique jusqu'a MAX_UNESCAPE_PASSES passes : certains flux FR sont
    triple-echappes (`&amp;amp;eacute;`).
    """
    current = value
    for _ in range(MAX_UNESCAPE_PASSES):
        following = _DOUBLE_ESCAPED_RE.sub(r"&\1", current)
        if following == current:
            break
        current = following
    return current


def unescape_text(value: str) -> str:
    """Desechappe completement une valeur *texte* (titre, auteur).

    Sur du texte, contrairement au HTML, un desechappement total est sans danger :
    le rendu passe par l'autoescape de Jinja. Repete jusqu'a stabilite pour
    absorber le double-encodage.
    """
    current = value
    for _ in range(MAX_UNESCAPE_PASSES):
        following = html.unescape(current)
        if following == current:
            break
        current = following
    return current


def normalize_ws(value: str) -> str:
    """Ecrase les suites de blancs en une espace simple et enleve les bords."""
    return _WHITESPACE_RE.sub(" ", value).strip()


def clean_title(value: str) -> str:
    return normalize_ws(unescape_text(value))


def content_hash(title: str, summary_text: str) -> str:
    """SHA256 du titre et du resume normalises.

    `summary_text` doit etre du texte brut (balises deja retirees) : une
    modification purement cosmetique du balisage ne doit pas compter comme une
    revision de l'article.
    """
    payload = f"{normalize_ws(title)}{_HASH_SEPARATOR}{normalize_ws(summary_text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
