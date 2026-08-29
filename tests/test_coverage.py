"""Couverture temporelle : la page doit refuser de dire qu'il n'y a pas de trou."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import make_feed
from veille.models import Feed, FeedRun, MissedRun
from veille.web.coverage import build_coverage

pytestmark = pytest.mark.db

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
INTERVAL = timedelta(hours=6)


def add_run(
    session: Session, feed: Feed, when: datetime, *, status: str = "ok", gap: str = "none"
) -> None:
    session.add(
        FeedRun(
            feed_id=feed.id,
            started_at=when,
            finished_at=when,
            status=status,
            n_new=0,
            n_seen=0,
            gap_status=gap,
        )
    )


def regular_runs(session: Session, feed: Feed, *, start: datetime, count: int) -> None:
    """Des runs toutes les 6 h, sans trou."""
    for i in range(count):
        add_run(session, feed, start + i * INTERVAL)


def report(session: Session, now: datetime = NOW):
    return build_coverage(session, now=now, planned_interval=INTERVAL)


# ------------------------------------------------------- R1 : la fenetre finit a now


def test_le_trou_en_cours_est_visible(session: Session) -> None:
    """LE test de ce module. Des runs parfaitement reguliers puis plus rien :
    un calcul entre runs observes annoncerait un sans-faute."""
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=12), count=8)
    session.commit()

    item = report(session).feeds[0]
    assert item.has_ongoing_gap, "la machine est eteinte depuis 10 jours"
    assert len(item.gaps) == 1
    assert item.gaps[0].ongoing is True
    assert item.gaps[0].end == NOW
    assert item.worst_interval > timedelta(days=9)


def test_sans_la_regle_r1_le_bilan_serait_parfait(session: Session) -> None:
    """Documente le mensonge qu'on evite : les intervalles CLOS sont tous
    nominaux, seul celui qui va jusqu'a maintenant revele la panne."""
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=12), count=8)
    session.commit()

    item = report(session).feeds[0]
    assert item.median_interval == INTERVAL, "tous les intervalles clos sont nominaux"
    assert item.gaps, "et pourtant il y a un trou"


def test_collecte_a_jour_ne_signale_rien(session: Session) -> None:
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=2), count=9)
    session.commit()

    item = report(session).feeds[0]
    assert item.gaps == []
    assert item.has_ongoing_gap is False
    assert item.median_interval == INTERVAL


# --------------------------------------------- trou de 3 jours, periode exacte


def test_un_trou_de_3_jours_est_localise_exactement(session: Session) -> None:
    feed = make_feed(session, "sain")
    debut = NOW - timedelta(days=10)
    # 4 runs reguliers, puis 3 jours de silence, puis reprise jusqu'a maintenant
    for i in range(4):
        add_run(session, feed, debut + i * INTERVAL)
    reprise = debut + 3 * INTERVAL + timedelta(days=3)
    n = int((NOW - reprise) / INTERVAL) + 1
    for i in range(n):
        add_run(session, feed, reprise + i * INTERVAL)
    session.commit()

    item = report(session).feeds[0]
    assert len(item.gaps) == 1
    trou = item.gaps[0]
    assert trou.start == debut + 3 * INTERVAL
    assert trou.end == reprise
    assert trou.duration == timedelta(days=3)
    assert trou.ongoing is False


# ------------------------------------------ R2 : un run en erreur ne ferme rien


def test_un_run_en_erreur_ne_ferme_pas_le_trou(session: Session) -> None:
    """Sinon une semaine de pannes ressemblerait a une semaine de collecte."""
    feed = make_feed(session, "sain")
    debut = NOW - timedelta(days=6)
    add_run(session, feed, debut)
    # trois jours d'echecs au milieu du trou
    for i in range(1, 4):
        add_run(session, feed, debut + timedelta(days=i), status="error")
    add_run(session, feed, NOW - timedelta(hours=1))
    session.commit()

    item = report(session).feeds[0]
    assert item.n_runs == 2, "seuls les runs reussis comptent"
    assert len(item.gaps) == 1
    assert item.gaps[0].duration > timedelta(days=4)


def test_un_304_ferme_le_trou(session: Session) -> None:
    """Un 304 prouve qu'on a joint le flux et qu'il n'avait rien de neuf."""
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=2), count=9)
    for run in session.query(FeedRun).all():
        run.status = "not_modified"
    session.commit()

    item = report(session).feeds[0]
    assert item.n_runs == 9
    assert item.gaps == []


# ---------------------------------------------------- R3 : aucun pourcentage


def test_la_page_n_affiche_aucun_pourcentage(session: Session, client: TestClient) -> None:
    """Un « 97 % » se lit comme une note et invite a arrondir a « bon »."""
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=2), count=9)
    session.commit()

    body = client.get("/coverage").text
    assert "%" not in body.replace("100%", ""), "aucun pourcentage de couverture"


# ------------------------------------------------------------- seuil et config


def test_le_seuil_vient_de_la_configuration(session: Session) -> None:
    """Piege E : le seuil ne doit pas etre code en dur."""
    feed = make_feed(session, "sain")
    # un seul intervalle anormal (20 h), tout le reste nominal, jusqu'a NOW
    for when in (
        NOW - timedelta(hours=32),
        NOW - timedelta(hours=12),  # <- 20 h apres le precedent
        NOW - timedelta(hours=6),
        NOW,
    ):
        add_run(session, feed, when)
    session.commit()

    serre = build_coverage(session, now=NOW, planned_interval=timedelta(hours=6))
    large = build_coverage(session, now=NOW, planned_interval=timedelta(hours=24))
    assert len(serre.feeds[0].gaps) == 1
    assert large.feeds[0].gaps == [], "avec un intervalle planifie de 24 h, 20 h n'est pas un trou"


def test_la_mediane_est_absente_quand_il_y_a_trop_peu_de_runs(session: Session) -> None:
    """Sur deux runs espaces de 7 secondes, une mediane n'a aucun sens et ne
    doit pas pouvoir se lire comme si elle en avait."""
    feed = make_feed(session, "sain")
    add_run(session, feed, NOW - timedelta(seconds=7))
    add_run(session, feed, NOW)
    session.commit()

    item = report(session).feeds[0]
    assert item.n_runs == 2
    assert item.observed is False


def test_un_flux_jamais_lance_n_a_pas_de_trou_fabrique(session: Session) -> None:
    """Avant le premier run il n'y a pas de fenetre d'observation, donc pas de
    trou : c'est une absence d'instrumentation."""
    make_feed(session, "jamais")
    session.commit()

    item = report(session).feeds[0]
    assert item.n_runs == 0
    assert item.gaps == []
    assert item.first_run is None


# -------------------------------------------------- annotation par missed_run


def test_les_tentatives_avortees_annotent_le_trou(session: Session) -> None:
    feed = make_feed(session, "sain")
    debut = NOW - timedelta(days=6)
    add_run(session, feed, debut)
    add_run(session, feed, NOW)
    for i in range(1, 15):
        session.add(
            MissedRun(
                attempted_at=debut + i * timedelta(hours=6),
                reason="docker_unavailable",
                detail="demon injoignable",
                drained_at=NOW,
            )
        )
    session.commit()

    item = report(session).feeds[0]
    assert len(item.gaps) == 1
    assert item.gaps[0].missed_attempts == 14
    assert report(session).missed_total == 14


def test_les_trous_suspectes_sont_comptes(session: Session) -> None:
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=2), count=9)
    for run in list(session.query(FeedRun).all())[:2]:
        run.gap_status = "suspected"
    session.commit()

    assert report(session).feeds[0].n_suspected == 2


# ------------------------------------------------------------------- la route


def test_la_route_repond_et_montre_le_trou(session: Session, client: TestClient) -> None:
    feed = make_feed(session, "sain")
    regular_runs(session, feed, start=NOW - timedelta(days=40), count=4)
    session.commit()

    response = client.get("/coverage")
    assert response.status_code == 200
    assert "trou en cours" in response.text
    assert "sain" in response.text


def test_la_route_repond_sans_aucun_run(client: TestClient) -> None:
    assert client.get("/coverage").status_code == 200
