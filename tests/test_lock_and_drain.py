"""Verrou consultatif et drainage des tentatives avortees."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from conftest import make_feed, make_spec, read_fixture
from veille.db import SessionLocal
from veille.errors import IngestLockedError
from veille.ingest.lock import advisory_lock
from veille.ingest.missed import drain_missed_runs
from veille.ingest.pipeline import run_ingestion
from veille.models import FeedRun, MissedRun

pytestmark = pytest.mark.db

SPEC = make_spec("sain", url="https://sain.test/feed.xml")
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def serving(body: bytes) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )


def write_queue(path: Path, *lines: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# ----------------------------------------------------------------- drainage


def test_deux_lignes_en_attente_donnent_deux_lignes_en_base(
    session: Session, tmp_path: Path
) -> None:
    queue = tmp_path / "missed-runs.jsonl"
    write_queue(
        queue,
        {
            "attempted_at": (NOW - timedelta(hours=12)).isoformat(),
            "reason": "docker_unavailable",
            "detail": "demon injoignable apres 120 s",
        },
        {
            "attempted_at": (NOW - timedelta(hours=6)).isoformat(),
            "reason": "docker_unavailable",
            "detail": "demon injoignable apres 120 s",
        },
    )

    assert drain_missed_runs(session, queue) == 2

    rows = session.scalars(select(MissedRun).order_by(MissedRun.attempted_at)).all()
    assert len(rows) == 2
    assert {r.reason for r in rows} == {"docker_unavailable"}
    assert rows[0].attempted_at == NOW - timedelta(hours=12)
    assert all(r.detail and "injoignable" in r.detail for r in rows)
    assert all(r.drained_at is not None for r in rows)


def test_le_fichier_est_vide_apres_drainage(session: Session, tmp_path: Path) -> None:
    queue = tmp_path / "missed-runs.jsonl"
    write_queue(queue, {"attempted_at": NOW.isoformat(), "reason": "docker_unavailable"})

    drain_missed_runs(session, queue)

    assert queue.exists(), "le fichier reste en place, seul son contenu part"
    assert queue.read_text(encoding="utf-8").strip() == ""


def test_rejouer_le_drainage_ne_cree_aucun_doublon(session: Session, tmp_path: Path) -> None:
    """L'idempotence est portee par la contrainte d'unicite, pas par la logique
    de vidage du fichier : meme si le fichier n'etait pas vide, rien ne double."""
    ligne = {"attempted_at": NOW.isoformat(), "reason": "docker_unavailable"}
    queue = tmp_path / "missed-runs.jsonl"

    write_queue(queue, ligne)
    assert drain_missed_runs(session, queue) == 1
    write_queue(queue, ligne)  # on rejoue exactement la meme ligne
    drain_missed_runs(session, queue)

    assert count(session, MissedRun) == 1


def test_un_horodatage_sans_fuseau_est_refuse(session: Session, tmp_path: Path) -> None:
    """Piege D : accepter un naif reviendrait a l'interpreter differemment selon
    qu'on collecte depuis l'Europe ou le Quebec."""
    queue = tmp_path / "missed-runs.jsonl"
    write_queue(queue, {"attempted_at": "2026-08-29T12:00:00", "reason": "docker_unavailable"})

    assert drain_missed_runs(session, queue) == 0
    assert count(session, MissedRun) == 0


def test_le_format_z_est_accepte_et_converti(session: Session, tmp_path: Path) -> None:
    """PowerShell ecrit volontiers un suffixe Z."""
    queue = tmp_path / "missed-runs.jsonl"
    write_queue(queue, {"attempted_at": "2026-08-29T12:00:00Z", "reason": "docker_unavailable"})

    assert drain_missed_runs(session, queue) == 1
    row = session.scalars(select(MissedRun)).one()
    assert row.attempted_at == NOW


def test_une_ligne_illisible_n_empeche_pas_les_autres(session: Session, tmp_path: Path) -> None:
    queue = tmp_path / "missed-runs.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        "ceci n'est pas du json\n"
        + json.dumps({"attempted_at": NOW.isoformat(), "reason": "docker_unavailable"})
        + "\n",
        encoding="utf-8",
    )

    assert drain_missed_runs(session, queue) == 1


def test_un_motif_inconnu_est_ramene_a_wrapper_error(session: Session, tmp_path: Path) -> None:
    queue = tmp_path / "missed-runs.jsonl"
    write_queue(queue, {"attempted_at": NOW.isoformat(), "reason": "quelque_chose_de_nouveau"})

    drain_missed_runs(session, queue)
    assert session.scalars(select(MissedRun.reason)).one() == "wrapper_error"


def test_un_fichier_absent_n_est_pas_une_erreur(session: Session, tmp_path: Path) -> None:
    assert drain_missed_runs(session, tmp_path / "jamais-ecrit.jsonl") == 0


def test_le_drainage_ne_bloque_jamais_l_ingestion(session: Session, tmp_path: Path) -> None:
    """Un fichier d'attente illisible ne doit pas empecher de collecter."""
    queue = tmp_path / "missed-runs.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_bytes(b"\xff\xfe\x00 octets binaires invalides")

    assert drain_missed_runs(session, queue) == 0, "echoue proprement"


def test_le_drainage_a_lieu_avant_la_boucle(session: Session, tmp_path: Path) -> None:
    """Le trou doit etre explique des le run qui le referme."""
    make_feed(session, "sain", url=SPEC.url)
    queue = tmp_path / "missed-runs.jsonl"
    write_queue(queue, {"attempted_at": NOW.isoformat(), "reason": "docker_unavailable"})

    from veille.config import settings

    ancien = settings.missed_runs_path
    settings.missed_runs_path = queue
    try:
        with serving(read_fixture("rss20_ok.xml")) as client:
            run_ingestion(session, [SPEC], client=client)
    finally:
        settings.missed_runs_path = ancien

    assert count(session, MissedRun) == 1
    assert count(session, FeedRun) == 1, "et l'ingestion a bien eu lieu"


# ------------------------------------------------------------------- verrou


def test_deux_pipelines_concurrents_le_second_sort_proprement(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    session.commit()

    autre = SessionLocal()
    try:
        with advisory_lock(autre) as tenu:
            assert tenu, "la premiere session prend le verrou"

            avant = count(session, FeedRun)
            with serving(read_fixture("rss20_ok.xml")) as client, pytest.raises(IngestLockedError):
                run_ingestion(session, [SPEC], client=client)

            assert count(session, FeedRun) == avant, "aucun feed_run en erreur"
    finally:
        autre.close()


def test_le_chevauchement_est_trace_dans_missed_run_pas_dans_feed_run(session: Session) -> None:
    """Ce n'est pas une panne : aucun flux n'a ete contacte."""
    make_feed(session, "sain", url=SPEC.url)
    session.commit()

    autre = SessionLocal()
    try:
        with advisory_lock(autre) as tenu:
            assert tenu
            with serving(b"") as client, pytest.raises(IngestLockedError):
                run_ingestion(session, [SPEC], client=client)
    finally:
        autre.close()

    assert count(session, FeedRun) == 0
    row = session.scalars(select(MissedRun)).one()
    assert row.reason == "lock_held"


def test_le_verrou_est_relache_en_sortie(session: Session) -> None:
    with advisory_lock(session) as tenu:
        assert tenu

    autre = SessionLocal()
    try:
        with advisory_lock(autre) as encore:
            assert encore, "le verrou doit etre reprenable apres liberation"
    finally:
        autre.close()


def test_le_verrou_est_relache_meme_si_le_corps_leve(session: Session) -> None:
    with pytest.raises(RuntimeError), advisory_lock(session) as tenu:
        assert tenu
        raise RuntimeError("panne au milieu")

    autre = SessionLocal()
    try:
        with advisory_lock(autre) as encore:
            assert encore
    finally:
        autre.close()


def test_une_ingestion_normale_prend_et_rend_le_verrou(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [SPEC], client=client)

    autre = SessionLocal()
    try:
        with advisory_lock(autre) as encore:
            assert encore, "le verrou ne doit pas rester pris apres un run reussi"
    finally:
        autre.close()
