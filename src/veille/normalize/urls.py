"""Canonicalisation d'URL.

`url_canonical` sert a la fois de cle de deduplication ET de href affiche a
l'utilisateur. Toute transformation doit donc rester sur des cas ou l'URL
resultante reste joignable ; c'est la raison pour laquelle `www.` n'est PAS
retire, alors que `m.` et `amp.` le sont (ces prefixes ont, eux, toujours un
apex equivalent).

La fonction est pure et idempotente : canonicalize(canonicalize(u)) == canonicalize(u).
"""

from __future__ import annotations

import logging
import re
import string
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from veille.errors import InvalidUrlError

logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Prefixes de parametres a jeter (tout ce qui commence par).
TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "pk_", "piwik_", "_ga", "at_custom")

#: Parametres a jeter par egalite stricte.
TRACKING_EXACT: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "vero_id",
        "oly_enc_id",
        "ck_subscriber_id",
        "ref",
        "source",
        "cmpid",
        "ncid",
        "spm",
        "at_medium",
        "at_campaign",
        "amp",
        "outputtype",
    }
)

#: Prefixes de host retires. `www.` en est volontairement absent.
MOBILE_HOST_PREFIXES: tuple[str, ...] = ("m.", "amp.")

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
_CONTROL_OR_SPACE_RE = re.compile(r"[\x00-\x20\x7f]")
_PERCENT_RE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")


def canonicalize_url(raw: str | None) -> str:
    """Renvoie la forme canonique de `raw`. Leve InvalidUrlError si inexploitable."""
    # 1. nettoyage des blancs et caracteres de controle
    if raw is None:
        raise InvalidUrlError("URL absente")
    url = _CONTROL_OR_SPACE_RE.sub("", raw)
    if not url:
        raise InvalidUrlError("URL vide")

    # 2. garde-fou de longueur (au-dela c'est une data: ou un dechet, et on
    #    frole la limite de cle B-tree de Postgres)
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrlError(f"URL trop longue ({len(url)} > {MAX_URL_LENGTH})")

    # 3. schema : absent -> https, interdit -> rejet
    url = _ensure_scheme(url)

    parts = urlsplit(url)

    # 4-9. host : minuscules (via .hostname), point final, IDNA, userinfo, port
    host = _normalize_host(parts.hostname, original=raw)
    if not host:
        raise InvalidUrlError(f"host absent : {raw!r}")
    port = parts.port
    netloc = f"{host}:{port}" if port and port != 443 else host

    # 11-13. path : segments relatifs, doublons de /, encodage, suffixe amp
    path = _normalize_path(parts.path)

    # 14-16. query : suppression du tracking, tri deterministe, re-encodage
    query = _normalize_query(parts.query)

    # 17. fragment : toujours supprime, sans exception
    canonical = urlunsplit(("https", netloc, path, query, ""))

    if len(canonical) > MAX_URL_LENGTH:
        raise InvalidUrlError(f"URL canonique trop longue ({len(canonical)})")
    return canonical


def _ensure_scheme(url: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    match = _SCHEME_RE.match(url)
    if match is None:
        return f"https://{url}"
    scheme = match.group(1).lower()
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidUrlError(f"schema interdit : {scheme}")
    # 4. http -> https
    return "https" + url[len(match.group(1)) :]


def _normalize_host(hostname: str | None, *, original: str) -> str:
    # urlsplit.hostname a deja mis en minuscules et retire le userinfo et le port.
    if not hostname:
        return ""
    host = hostname.rstrip(".")

    # 8. IDNA seulement si necessaire ; en cas d'echec on garde la forme minuscule.
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            logger.warning("IDNA impossible sur %r, host conserve tel quel", host)

    # 10. prefixes mobiles. C'est le SEUL endroit ou la canonicalisation peut
    #     produire une URL morte : on le trace systematiquement.
    for prefix in MOBILE_HOST_PREFIXES:
        if host.startswith(prefix) and host.count(".") >= 2:
            stripped = host[len(prefix) :]
            logger.warning(
                "host transforme : %r -> %r (prefixe %r retire) sur %s",
                host,
                stripped,
                prefix,
                original,
            )
            return stripped
    return host


def _normalize_path(path: str) -> str:
    if not path:
        return ""

    segments = path.split("/")
    leading_slash = segments[0] == ""
    rest = segments[1:] if leading_slash else segments

    resolved: list[str] = []
    for segment in rest:
        if segment in ("", "."):
            # 11. ecrase les // consecutifs et le slash final ; le slash final
            #     serait de toute facon retire a l'etape 18.
            continue
        if segment == "..":
            if resolved:
                resolved.pop()
            continue
        # 12. ré-encodage percent stable, casse du path preservee
        resolved.append(_normalize_percent(segment))

    # 13. suffixe AMP
    if resolved and resolved[-1].lower() == "amp":
        resolved.pop()

    if not resolved:
        return ""
    prefix = "/" if leading_slash else ""
    return prefix + "/".join(resolved)


def _normalize_percent(segment: str) -> str:
    def replace(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        if char in _UNRESERVED:
            return char
        return "%" + match.group(1).upper()

    return _PERCENT_RE.sub(replace, segment)


def _normalize_query(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [(key, value) for key, value in pairs if not _is_tracking(key)]
    if not kept:
        return ""
    # 15. ordre deterministe, independant de celui du flux
    kept.sort()
    return urlencode(kept, quote_via=quote)


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    if lowered in TRACKING_EXACT:
        return True
    return lowered.startswith(TRACKING_PREFIXES)
