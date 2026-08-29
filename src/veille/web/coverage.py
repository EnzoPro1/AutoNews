"""Calcul de couverture temporelle.

Trois regles portent cette page, et elles sont la raison d'etre du module :

R1 — La fenetre se termine a `now`, jamais au dernier run. Mesurer les
     intervalles entre runs observes rend le trou courant structurellement
     invisible, et le bilan est toujours parfait juste apres une panne. Verifie
     sur les donnees reelles : 22 runs espaces de 7 secondes, machine eteinte
     depuis 9 jours, un calcul naif annonçait "intervalle maximal 7 s, 0 trou".

R2 — Seuls les runs 'ok' et 'not_modified' ferment un trou. Un run en 'error'
     prouve qu'on a essaye, pas qu'on a collecte. Sinon une semaine de pannes
     ressemblerait a une semaine de collecte reguliere.

R3 — Aucun pourcentage de couverture. Un "97 %" se lit comme une note et invite
     a arrondir a "bon". On ne rend que des intervalles et des periodes datees.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from veille.models import Feed, FeedRun, MissedRun

#: R2 : la liste des statuts qui prouvent qu'on a effectivement joint le flux.
CLOSING_STATUSES = ("ok", "not_modified")


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """Une periode sans aucun run reussi, au-dela du seuil."""

    start: datetime
    end: datetime
    ongoing: bool
    missed_attempts: int

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class FeedCoverage:
    feed: Feed
    n_runs: int
    first_run: datetime | None
    last_run: datetime | None
    median_interval: timedelta | None
    worst_interval: timedelta | None
    gaps: list[CoverageGap]
    n_suspected: int

    @property
    def has_ongoing_gap(self) -> bool:
        return any(gap.ongoing for gap in self.gaps)

    @property
    def observed(self) -> bool:
        """A-t-on assez de runs pour que la mediane veuille dire quelque chose ?"""
        return self.n_runs >= 3


@dataclass(frozen=True, slots=True)
class CoverageReport:
    feeds: list[FeedCoverage]
    planned_interval: timedelta
    threshold: timedelta
    now: datetime
    missed_total: int

    @property
    def window_start(self) -> datetime | None:
        starts = [f.first_run for f in self.feeds if f.first_run]
        return min(starts) if starts else None

    @property
    def n_feeds_with_ongoing_gap(self) -> int:
        return sum(1 for f in self.feeds if f.has_ongoing_gap)


def build_coverage(
    session: Session, *, now: datetime, planned_interval: timedelta
) -> CoverageReport:
    """Construit le rapport. `now` est injecte : la page ne lit pas l'horloge."""
    threshold = 2 * planned_interval

    feeds = list(session.scalars(select(Feed).order_by(Feed.is_active.desc(), Feed.slug)))

    runs_by_feed: dict[int, list[datetime]] = {feed.id: [] for feed in feeds}
    rows = session.execute(
        select(FeedRun.feed_id, FeedRun.started_at)
        .where(FeedRun.status.in_(CLOSING_STATUSES))
        .order_by(FeedRun.feed_id, FeedRun.started_at)
    )
    for feed_id, started_at in rows:
        runs_by_feed.setdefault(feed_id, []).append(started_at)

    suspected_by_feed: dict[int, int] = dict(
        session.execute(
            select(FeedRun.feed_id, func.count())
            .where(FeedRun.gap_status == "suspected")
            .group_by(FeedRun.feed_id)
        ).all()
    )

    missed = list(session.scalars(select(MissedRun.attempted_at)))

    return CoverageReport(
        feeds=[
            _feed_coverage(
                feed,
                runs=runs_by_feed.get(feed.id, []),
                n_suspected=suspected_by_feed.get(feed.id, 0),
                missed=missed,
                now=now,
                threshold=threshold,
            )
            for feed in feeds
        ],
        planned_interval=planned_interval,
        threshold=threshold,
        now=now,
        missed_total=len(missed),
    )


def _feed_coverage(
    feed: Feed,
    *,
    runs: list[datetime],
    n_suspected: int,
    missed: list[datetime],
    now: datetime,
    threshold: timedelta,
) -> FeedCoverage:
    if not runs:
        # Jamais aucun run reussi : ce n'est pas un trou dans une fenetre
        # d'observation, c'est l'absence de fenetre. On ne fabrique pas de
        # periode a partir de rien.
        return FeedCoverage(feed, 0, None, None, None, None, [], n_suspected)

    # R1 : la borne de droite est `now`, pas le dernier run. L'intervalle de
    # queue est un trou en cours, pas une absence d'information.
    bounds = [*runs, now]
    intervals = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]

    closed = intervals[:-1]
    gaps = [
        CoverageGap(
            start=bounds[i],
            end=bounds[i + 1],
            ongoing=(i == len(intervals) - 1),
            missed_attempts=sum(1 for m in missed if bounds[i] < m <= bounds[i + 1]),
        )
        for i, delta in enumerate(intervals)
        if delta > threshold
    ]

    return FeedCoverage(
        feed=feed,
        n_runs=len(runs),
        first_run=runs[0],
        last_run=runs[-1],
        # La mediane porte sur les intervalles clos : celui en cours n'est pas
        # termine, l'inclure ferait baisser artificiellement la mesure.
        median_interval=(
            timedelta(seconds=statistics.median(d.total_seconds() for d in closed))
            if closed
            else None
        ),
        worst_interval=max(intervals),
        gaps=gaps,
        n_suspected=n_suspected,
    )
