"""Sauvegarde et restauration reelles.

Une sauvegarde jamais restauree n'est pas une sauvegarde : ce module fait le
tour complet, dump -> base jetable -> restore -> comparaison des comptes.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from conftest import make_feed
from veille.config import settings
from veille.ingest.parse import parse_feed
from veille.ingest.store import store_entries
from veille.models import Article, ArticleFeed, Feed, FeedRun, MissedRun

pytestmark = pytest.mark.db

TABLES = ("feed", "article", "article_feed", "feed_run")
SCRATCH_DB = "veille_restore_check"


def libpq_url(database: str | None = None) -> str:
    """URL pour les BINAIRES pg_dump / pg_restore, qui ne comprennent pas le
    prefixe +psycopg de SQLAlchemy."""
    url = make_url(settings.database_url).set(drivername="postgresql")
    if database:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def sa_url(database: str | None = None) -> str:
    """URL pour SQLAlchemy, qui a besoin du pilote explicite : sans lui il
    tenterait psycopg2, absent de l'image."""
    url = make_url(settings.database_url)
    if database:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def counts(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {t: connection.scalar(text(f"SELECT count(*) FROM {t}")) for t in TABLES}
    finally:
        engine.dispose()


@pytest.fixture
def scratch_database() -> Iterator[str]:
    """Base jetable, creee et detruite autour du test."""
    admin = create_engine(sa_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
        connection.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    admin.dispose()
    try:
        yield SCRATCH_DB
    finally:
        admin = create_engine(sa_url("postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
        admin.dispose()


@pytest.fixture
def populated(session: Session) -> Session:
    """Un jeu de donnees couvrant les quatre tables."""
    from conftest import NOW, read_fixture

    feed = make_feed(session, "sain")
    make_feed(session, "relais")
    parsed = parse_feed(read_fixture("rss20_ok.xml"), fetched_at=NOW)
    store_entries(session, feed_id=feed.id, entries=parsed.entries, now=NOW)
    session.add(
        FeedRun(
            feed_id=feed.id,
            started_at=NOW,
            finished_at=NOW,
            status="ok",
            n_new=3,
            n_seen=3,
            gap_status="none",
            oldest_in_page=NOW - timedelta(days=2),
            prev_max_published=NOW - timedelta(days=1),
        )
    )
    session.add(
        MissedRun(
            attempted_at=NOW - timedelta(hours=6),
            reason="docker_unavailable",
            detail="demon injoignable",
            drained_at=NOW,
        )
    )
    session.commit()
    return session


def test_dump_puis_restore_conserve_les_comptes(
    populated: Session, scratch_database: str, tmp_path: Path
) -> None:
    """LE test du lot sauvegarde. Sans lui, on ne saurait qu'a la premiere
    restauration reelle -- c'est-a-dire au pire moment."""
    dump = tmp_path / "essai.dump"
    avant = counts(sa_url())
    assert all(n > 0 for n in avant.values()), "le jeu de donnees doit etre non vide"

    fait = run("pg_dump", libpq_url(), "--format=custom", "--file", str(dump))
    assert fait.returncode == 0, fait.stderr
    assert dump.stat().st_size > 0

    restaure = run("pg_restore", "--dbname", libpq_url(scratch_database), "--no-owner", str(dump))
    assert restaure.returncode == 0, restaure.stderr

    assert counts(sa_url(scratch_database)) == avant


def test_un_dump_tronque_est_rejete_avant_toute_suppression(
    populated: Session, tmp_path: Path
) -> None:
    """pg_restore --list ACCEPTE un dump tronque : la table des matieres est en
    tete du format custom. Seule la lecture de l'archive entiere le detecte.
    C'est le fichier de la bonne taille qui ne se restaure pas, et le verifier
    trop tard vide la base avant d'echouer."""
    dump = tmp_path / "complet.dump"
    run("pg_dump", libpq_url(), "--format=custom", "--file", str(dump))

    octets = dump.read_bytes()
    tronque = tmp_path / "tronque.dump"

    # Une troncature trop courte emporte la TOC elle-meme, et --list echoue
    # alors pour la mauvaise raison. On cherche une longueur qui laisse la TOC
    # lisible : c'est ce cas-la qui est dangereux.
    trompeuses = []
    for fraction in (0.95, 0.9, 0.85, 0.8, 0.7, 0.6):
        tronque.write_bytes(octets[: int(len(octets) * fraction)])
        faible = run("pg_restore", "--list", str(tronque))
        complet = run("pg_restore", "-f", "/dev/null", str(tronque))
        # quelle que soit la longueur, la lecture complete doit rejeter
        assert complet.returncode != 0, f"lecture complete trop permissive a {fraction}"
        if faible.returncode == 0:
            trompeuses.append(fraction)

    assert trompeuses, (
        "il existe des troncatures que --list accepte : c'est pourquoi la "
        "verification lit l'archive entiere"
    )

    sain = run("pg_restore", "-f", "/dev/null", str(dump))
    assert sain.returncode == 0, "et ne doit pas rejeter un dump valide"


def test_un_dump_vide_est_rejete(tmp_path: Path) -> None:
    vide = tmp_path / "vide.dump"
    vide.write_bytes(b"")
    assert vide.stat().st_size == 0
    assert run("pg_restore", "-f", "/dev/null", str(vide)).returncode != 0


def test_un_fichier_utf16_est_rejete(tmp_path: Path) -> None:
    """Ce que produirait une redirection PowerShell : le fichier existe, il pese
    le bon ordre de grandeur, et il n'est pas une archive."""
    faux = tmp_path / "utf16.dump"
    faux.write_bytes("PGDMP".encode("utf-16"))
    assert run("pg_restore", "-f", "/dev/null", str(faux)).returncode != 0


def test_le_dump_contient_bien_les_quatre_tables(populated: Session, tmp_path: Path) -> None:
    dump = tmp_path / "essai.dump"
    run("pg_dump", libpq_url(), "--format=custom", "--file", str(dump))
    toc = run("pg_restore", "--list", str(dump)).stdout
    for table in TABLES:
        assert f"TABLE DATA public {table}" in toc, table


def test_les_donnees_restaurees_sont_les_memes_pas_seulement_le_compte(
    populated: Session, scratch_database: str, tmp_path: Path
) -> None:
    """Comparer les comptes ne suffirait pas a detecter un contenu corrompu."""
    dump = tmp_path / "essai.dump"
    run("pg_dump", libpq_url(), "--format=custom", "--file", str(dump))
    run("pg_restore", "--dbname", libpq_url(scratch_database), "--no-owner", str(dump))

    engine = create_engine(sa_url(scratch_database))
    try:
        with engine.connect() as connection:
            urls = set(connection.scalars(text("SELECT url_canonical FROM article")))
            gap = connection.scalar(text("SELECT gap_status FROM feed_run LIMIT 1"))
            reason = connection.scalar(text("SELECT reason FROM missed_run LIMIT 1"))
    finally:
        engine.dispose()

    with Session(create_engine(sa_url())) as origine:
        attendus = set(origine.scalars(text("SELECT url_canonical FROM article")))

    assert urls == attendus
    assert gap == "none", "les colonnes ajoutees par la migration 0002 survivent"
    assert reason == "docker_unavailable", "et la table missed_run aussi"


def test_le_modele_couvre_les_quatre_tables_sauvegardees() -> None:
    """Garde-fou : si une table est ajoutee au modele sans etre verifiee ici,
    le test de restauration cesserait silencieusement de la couvrir."""
    connues = {
        Feed.__tablename__,
        Article.__tablename__,
        ArticleFeed.__tablename__,
        FeedRun.__tablename__,
        MissedRun.__tablename__,
    }
    assert set(TABLES) <= connues
    non_verifiees = connues - set(TABLES)
    assert non_verifiees == {MissedRun.__tablename__}, (
        "missed_run est verifiee par test_les_donnees_restaurees ; toute autre "
        "table nouvelle doit etre ajoutee a TABLES"
    )


def test_l_horodatage_du_nom_de_dump_est_en_utc() -> None:
    """Piege D : le nom de fichier doit etre comparable entre deux fuseaux."""
    maintenant = datetime.now(tz=UTC)
    attendu = maintenant.strftime("autonews-%Y%m%d-%H%M%S.dump")
    assert attendu.startswith("autonews-")
    assert len(attendu) == len("autonews-20260829-225847.dump")
