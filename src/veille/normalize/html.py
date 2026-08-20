"""Sanitisation du HTML de flux.

Le HTML des flux est du contenu non fiable. `raw_summary` conserve l'original
pour audit, `summary_clean` est la seule valeur que les templates ont le droit
d'afficher, et aucun `|safe` ne doit apparaitre ailleurs que sur cette colonne.
"""

from __future__ import annotations

import nh3

from veille.normalize.text import fix_double_escaping, normalize_ws

#: Allowlist stricte : mise en forme minimale, aucun media, aucun conteneur
#: exploitable pour du CSS d'exfiltration. Ni <img>, ni <iframe>, ni <style>.
ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "code",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "li",
    "a",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {"a": {"href", "title"}}

ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}

LINK_REL = "noopener noreferrer nofollow"


def sanitize(raw: str | None) -> str | None:
    """Renvoie le HTML nettoye, ou None s'il ne reste rien d'affichable.

    Renvoyer None plutot qu'une chaine vide est deliberé : le template teste
    l'absence de resume, il ne doit pas avoir a distinguer None de "" ni de
    "<p></p>" (les items Hacker News sont concernes).
    """
    if raw is None:
        return None
    prepared = fix_double_escaping(raw)
    if not prepared.strip():
        return None

    cleaned = nh3.clean(
        prepared,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel=LINK_REL,
        strip_comments=True,
    ).strip()

    if not cleaned or not strip_tags(cleaned):
        return None
    return cleaned


def strip_tags(value: str | None) -> str:
    """Texte brut d'un fragment HTML, blancs normalises. Sert au hash de contenu."""
    if not value:
        return ""
    return normalize_ws(nh3.clean(value, tags=set(), attributes={}, strip_comments=True))
