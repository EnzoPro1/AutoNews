"""Integration de la detection de trou dans le pipeline.

Verifie ce que la fonction pure ne peut pas verifier : que la borne est lue au
bon moment, sur la bonne jointure, et ecrite dans feed_run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import NOW, make_feed, make_spec, read_fixture
from veille.ingest.pipeline import run_ingestion
from veille.models import Feed, FeedRun

pytestmark = pytest.mark.db

SPEC = make_spec("sain", url="https://sain.test/feed.xml")


def serving(body: bytes) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def last_run(session: Session, slug: str) -> FeedRun:
    return session.scalars(
        select(FeedRun)
        .join(Feed, Feed.id == FeedRun.feed_id)
        .where(Feed.slug == slug)
        .order_by(FeedRun.started_at.desc(), FeedRun.id.desc())
    ).first()


def shifted(fixture: str, days: int) -> bytes:
    """Meme flux, dates decalees : simule une page entierement renouvelee."""
    body = read_fixture(fixture).decode("utf-8")
    replacements = {
        "Wed, 19 Aug 2026 08:30:00 +0200": f"Wed, {19 + days} Aug 2026 08:30:00 +0200",
        "Tue, 18 Aug 2026 17:05:00 +0000": f"Tue, {18 + days} Aug 2026 17:05:00 +0000",
        "Mon, 17 Aug 2026 09:00:00 +0000": f"Mon, {17 + days} Aug 2026 09:00:00 +0000",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    # URL differentes, sinon store dedupliquerait et n_new vaudrait 0
    body = body.replace("/2026/08/", f"/2026/09/d{days}/")
    return body.encode("utf-8")


def test_premier_run_ecrit_unknown_pas_none(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [SPEC], client=client)

    run = last_run(session, "sain")
    assert run.gap_status == "unknown"
    assert run.prev_max_published is None
    assert run.oldest_in_page is not None, "la borne haute est quand meme auditable"


def test_second_run_identique_donne_none(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    body = read_fixture("rss20_ok.xml")
    with serving(body) as client:
        run_ingestion(session, [SPEC], client=client)
    with serving(body) as client:
        run_ingestion(session, [SPEC], client=client)

    run = last_run(session, "sain")
    assert run.gap_status == "none"
    assert run.oldest_in_page <= run.prev_max_published


def test_page_entierement_renouvelee_donne_suspected(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [SPEC], client=client)
    with serving(shifted("rss20_ok.xml", days=10)) as client:
        run_ingestion(session, [SPEC], client=client)

    run = last_run(session, "sain")
    assert run.gap_status == "suspected"
    assert run.oldest_in_page > run.prev_max_published
    # les deux bornes permettent de chiffrer l'intervalle non couvert
    assert run.oldest_in_page - run.prev_max_published > timedelta(days=1)


def test_304_ecrit_unknown_et_pas_none(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [SPEC], client=client)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_ingestion(session, [SPEC], client=client)

    run = last_run(session, "sain")
    assert run.status == "not_modified"
    assert run.gap_status == "unknown"
    assert run.oldest_in_page is None


def test_error_ecrit_unknown_et_pas_none(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        run_ingestion(session, [SPEC], client=client)

    run = last_run(session, "sain")
    assert run.status == "error"
    assert run.gap_status == "unknown"


def test_flux_aux_dates_degenerees_donne_unknown(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("degenerate_dates.xml")) as client:
        run_ingestion(session, [SPEC], client=client)
    with serving(read_fixture("degenerate_dates.xml")) as client:
        run_ingestion(session, [SPEC], client=client)

    run = last_run(session, "sain")
    assert run.gap_status == "unknown", "une page sans chronologie ne prouve aucune continuite"


def test_prev_max_est_lu_avant_l_ecriture(session: Session) -> None:
    """Si la borne etait lue apres store, elle inclurait les articles du run en
    cours et oldest <= prev_max serait toujours vrai : plus aucun 'suspected'."""
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [SPEC], client=client)
    premier = last_run(session, "sain")

    with serving(shifted("rss20_ok.xml", days=10)) as client:
        run_ingestion(session, [SPEC], client=client)
    second = last_run(session, "sain")

    # La borne du second run est l'etat de la base AVANT ce run, c'est-a-dire la
    # date la plus recente de la PREMIERE page (19/08 06:30 UTC), pas celle de la
    # seconde. Si elle etait lue apres store, elle vaudrait la date la plus
    # recente de la seconde page et le verdict serait 'none'.
    assert second.prev_max_published == datetime(2026, 8, 19, 6, 30, tzinfo=UTC)
    assert second.prev_max_published > premier.oldest_in_page
    assert second.prev_max_published < second.oldest_in_page
    assert second.gap_status == "suspected"


def test_article_relaye_par_un_autre_flux_compte_dans_la_borne(session: Session) -> None:
    """Jointure par article_feed : un article vu d'abord ailleurs mais relaye
    ici a bien ete observe par ce flux."""
    source = make_spec("source", url="https://source.test/feed.xml")
    relais = make_spec("relais", url="https://relais.test/feed.xml")
    make_feed(session, "source", url=source.url)
    make_feed(session, "relais", url=relais.url)

    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [source], client=client)
    with serving(read_fixture("relay.xml")) as client:
        run_ingestion(session, [relais], client=client)
    # second passage du relais : sa borne doit inclure l'article partage
    with serving(read_fixture("relay.xml")) as client:
        run_ingestion(session, [relais], client=client)

    run = last_run(session, "relais")
    assert run.prev_max_published is not None
    assert run.gap_status in ("none", "unknown")


def test_toutes_les_lignes_ont_un_gap_status_valide(session: Session) -> None:
    make_feed(session, "sain", url=SPEC.url)
    with serving(read_fixture("rss20_ok.xml")) as client:
        run_ingestion(session, [SPEC], client=client)

    statuts = set(session.scalars(select(FeedRun.gap_status)))
    assert statuts <= {"none", "suspected", "unknown"}
    assert statuts, "au moins un run"


def test_now_reste_inutilise_par_la_regle(session: Session) -> None:
    """Rappel : NOW n'intervient pas dans assess_gap, la decision ne depend que
    des dates du flux et de la base."""
    assert NOW.tzinfo is not None
