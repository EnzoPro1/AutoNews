"""Etape 1 : reseau.

Seul module du projet autorise a importer httpx. Il ne parse rien, ne persiste
rien, et ne connait pas les modeles SQLAlchemy. Il rend des octets bruts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from veille.config import settings
from veille.errors import FetchError
from veille.schemas import FeedSpec, FetchResult

logger = logging.getLogger(__name__)

#: Un flux qui ne repond pas deux fois de suite ne repondra pas la troisieme.
#: On ne martele pas les serveurs des editeurs.
DEFAULT_MAX_ATTEMPTS = 2

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def fetch_feed(
    spec: FeedSpec,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    client: httpx.Client | None = None,
) -> FetchResult:
    """Telecharge un flux en requete conditionnelle.

    Renvoie un FetchResult pour 200 et 304. Leve FetchError pour tout le reste :
    c'est le pipeline qui isole le flux, pas cette fonction.

    `client` est injectable pour que les tests n'aient jamais besoin de reseau.
    """
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(settings.http_timeout),
        follow_redirects=True,
    )
    try:
        response = _request_with_retries(client, spec, headers)
    finally:
        if owns_client:
            client.close()

    fetched_at = datetime.now(tz=UTC)

    if response.status_code == 304:
        logger.info("%s : 304 Not Modified", spec.id)
        return FetchResult(
            url=spec.url,
            http_status=304,
            fetched_at=fetched_at,
            body=None,
            etag=etag,
            last_modified=last_modified,
        )

    if response.status_code != 200:
        raise FetchError(
            f"{spec.id} : HTTP {response.status_code}", http_status=response.status_code
        )

    if not response.content:
        raise FetchError(f"{spec.id} : corps vide (HTTP 200)", http_status=200)

    return FetchResult(
        url=str(response.url),
        http_status=200,
        fetched_at=fetched_at,
        body=response.content,
        # On conserve les validateurs verbatim : les reformater casse les 304.
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )


def _request_with_retries(
    client: httpx.Client, spec: FeedSpec, headers: dict[str, str]
) -> httpx.Response:
    max_attempts = max(1, settings.http_max_attempts)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.get(spec.url, headers=headers)
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning("%s : tentative %s/%s echouee (%s)", spec.id, attempt, max_attempts, exc)
            continue

        if response.status_code in RETRYABLE_STATUS and attempt < max_attempts:
            logger.warning(
                "%s : tentative %s/%s -> HTTP %s, nouvelle tentative",
                spec.id,
                attempt,
                max_attempts,
                response.status_code,
            )
            last_error = FetchError(
                f"{spec.id} : HTTP {response.status_code}", http_status=response.status_code
            )
            continue

        return response

    raise FetchError(f"{spec.id} : {last_error}") from last_error
