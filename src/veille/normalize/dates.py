"""Resolution des dates de publication.

`pubDate` est du chaos : absent, format non standard, fuseau absent, date dans
le futur, date a l'epoch. L'ordre de repli est published -> updated -> fetched_at,
et `date_source` dit laquelle a servi. La distinction entre "date absente" et
"date aberrante" va dans les logs, pas dans le schema.

Toutes les sorties sont timezone-aware en UTC : aucun datetime naif ne franchit
la frontiere de `store`.
"""

from __future__ import annotations

import calendar
import logging
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from veille.schemas import DateSource

logger = logging.getLogger(__name__)

#: Une date de publication ne peut pas devancer l'heure de collecte de plus que
#: la derive d'horloge plausible d'un serveur mal regle.
MAX_FUTURE_SKEW = timedelta(hours=2)

#: En dessous, c'est un timestamp a zero ou un champ vide mal interprete.
MIN_PLAUSIBLE = datetime(2000, 1, 1, tzinfo=UTC)

#: Une page entiere dont les dates tiennent dans cette fenetre ne porte pas des
#: dates d'articles mais l'heure de generation du flux. Observe sur Actu IA :
#: 15 items horodates entre 04:54:53 et 04:55:13, soit 20 secondes.
DEGENERATE_SPAN = timedelta(minutes=5)

#: En dessous de ce nombre d'items, une fenetre etroite peut etre une vraie
#: rafale de publication. Au-dela, c'est un horodatage unique replique.
DEGENERATE_MIN_ENTRIES = 5


def has_degenerate_span(values: list[datetime]) -> bool:
    """La page a-t-elle une chronologie exploitable ?

    Sert a deux endroits qui doivent rester d'accord : `parse`, qui refuse de
    faire passer un horodatage de generation pour une date d'article, et la
    detection de trou, qui ne peut rien deduire d'une page sans chronologie.
    Un seul seuil, defini ici, pour eviter qu'ils divergent.
    """
    if len(values) < DEGENERATE_MIN_ENTRIES:
        return False
    return (max(values) - min(values)) < DEGENERATE_SPAN


_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
)


def parse_datetime(value: str | time.struct_time | None) -> datetime | None:
    """Convertit une valeur de flux en datetime UTC aware, ou None.

    Accepte le struct_time de feedparser (deja en UTC) et les chaines brutes.
    Ne leve jamais : une date illisible est une date absente.
    """
    if value is None:
        return None

    if isinstance(value, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)

    raw = value.strip()
    if not raw:
        return None

    # RFC 822 / RFC 2822, le format nominal de RSS 2.0.
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None:
        return _as_utc(parsed, raw)

    for fmt in _FORMATS:
        try:
            # strptime rend un naif pour les formats sans %z : c'est exactement
            # le cas "fuseau absent", que _as_utc traite explicitement.
            return _as_utc(datetime.strptime(raw, fmt), raw)  # noqa: DTZ007
        except ValueError:
            continue

    logger.info("date illisible, ignoree : %r", raw)
    return None


def resolve_published(
    published: str | time.struct_time | None,
    updated: str | time.struct_time | None,
    fetched_at: datetime,
) -> tuple[datetime, DateSource]:
    """Applique l'ordre de repli published -> updated -> fetched_at.

    `fetched_at` est injecte : cette fonction ne lit jamais l'horloge.
    """
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at doit etre timezone-aware")

    candidates: tuple[tuple[str | time.struct_time | None, DateSource], ...] = (
        (published, "published"),
        (updated, "updated"),
    )
    for raw, source in candidates:
        parsed = parse_datetime(raw)
        if parsed is None:
            continue
        if _is_implausible(parsed, fetched_at, source):
            continue
        return parsed, source

    return fetched_at.astimezone(UTC), "fetched"


def _as_utc(value: datetime, raw: str) -> datetime:
    if value.tzinfo is None:
        # Fuseau absent : le seul choix qui ne ment pas systematiquement est UTC.
        logger.info("date sans fuseau, interpretee en UTC : %r", raw)
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_implausible(value: datetime, fetched_at: datetime, source: str) -> bool:
    if value > fetched_at + MAX_FUTURE_SKEW:
        logger.warning("date %s dans le futur (%s), repli", source, value.isoformat())
        return True
    if value < MIN_PLAUSIBLE:
        logger.warning("date %s aberrante (%s), repli", source, value.isoformat())
        return True
    return False
