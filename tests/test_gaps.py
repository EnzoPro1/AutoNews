"""Regle de saturation de page. Fonction pure : aucun test ici ne touche la base."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from veille.ingest.gaps import assess_gap
from veille.schemas import ParsedEntry

BASE = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def entry(offset_hours: float, *, date_source: str = "published") -> ParsedEntry:
    published = BASE + timedelta(hours=offset_hours)
    return ParsedEntry(
        url_canonical=f"https://exemple.test/{offset_hours}",
        url_original=f"https://exemple.test/{offset_hours}",
        title=f"Article {offset_hours}",
        content_hash="0" * 64,
        published_at=published,
        date_source=date_source,  # type: ignore[arg-type]
    )


def page(*offsets: float, date_source: str = "published") -> list[ParsedEntry]:
    return [entry(o, date_source=date_source) for o in offsets]


# --------------------------------------------------------- cas limites imposes


def test_premier_run_est_unknown_jamais_none() -> None:
    """Aucune borne inferieure : on ne sait pas, et ne pas savoir n'est pas
    savoir qu'il n'y a pas de trou."""
    result = assess_gap(status="ok", entries=page(0, 1, 2, 3, 4), prev_max_published=None, n_new=5)
    assert result.status == "unknown"
    assert result.prev_max_published is None
    assert result.oldest_in_page == BASE


@pytest.mark.parametrize("status", ["not_modified", "error"])
def test_304_et_error_sont_unknown_jamais_none(status: str) -> None:
    """Rien n'a ete fetche : aucune evaluation possible."""
    result = assess_gap(
        status=status,  # type: ignore[arg-type]
        entries=[],
        prev_max_published=BASE,
        n_new=0,
    )
    assert result.status == "unknown"
    assert result.oldest_in_page is None
    assert result.prev_max_published == BASE


def test_304_reste_unknown_meme_avec_des_entrees_residuelles() -> None:
    result = assess_gap(
        status="not_modified", entries=page(10, 11, 12), prev_max_published=BASE, n_new=0
    )
    assert result.status == "unknown"


def test_dates_peu_fiables_donnent_unknown() -> None:
    """Une seule date 'fetched' vaut ~l'heure du run : la laisser entrer dans le
    calcul produirait un 'none' permanent, faux negatif silencieux."""
    entries = page(5, 6, date_source="published") + page(7, 8, 9, date_source="fetched")
    result = assess_gap(status="ok", entries=entries, prev_max_published=BASE, n_new=5)
    assert result.status == "unknown"
    assert "date fiable" in result.reason


def test_page_entierement_fetched_donne_unknown() -> None:
    result = assess_gap(
        status="ok",
        entries=page(1, 2, 3, 4, 5, date_source="fetched"),
        prev_max_published=BASE,
        n_new=5,
    )
    assert result.status == "unknown"


def test_page_sans_chronologie_donne_unknown() -> None:
    """Cas Actu IA : six items dans la meme minute, aucune chronologie."""
    entries = [entry(10 + i / 3600.0) for i in range(6)]
    result = assess_gap(status="ok", entries=entries, prev_max_published=BASE, n_new=6)
    assert result.status == "unknown"
    assert "chronologie" in result.reason


def test_flux_a_faible_debit_reste_none_et_c_est_correct() -> None:
    """CERT-FR : la page ne tourne pas, donc rien ne defile. 'none' permanent
    est le comportement juste, pas un faux negatif."""
    entries = page(-500, -300, -100, 0, 2)  # page tres ancienne, inchangee
    result = assess_gap(status="ok", entries=entries, prev_max_published=BASE, n_new=0)
    assert result.status == "none"
    assert result.oldest_in_page == BASE + timedelta(hours=-500)


def test_article_reedite_dont_la_date_bouge_ne_produit_pas_de_suspected() -> None:
    """Aucun item nouveau : des items qu'on connait tous ont forcement ete vus,
    donc prev_max les couvrait. Le declenchement vient des dates, pas d'un trou."""
    result = assess_gap(
        status="ok", entries=page(10, 11, 12, 13, 14), prev_max_published=BASE, n_new=0
    )
    assert result.status == "unknown"
    assert "dates deplacees" in result.reason


# ------------------------------------------------------------ cas nominaux


def test_recouvrement_conserve_donne_none() -> None:
    result = assess_gap(status="ok", entries=page(-2, 0, 1, 3, 5), prev_max_published=BASE, n_new=3)
    assert result.status == "none"


def test_recouvrement_perdu_avec_items_nouveaux_donne_suspected() -> None:
    result = assess_gap(
        status="ok", entries=page(10, 12, 14, 16, 18), prev_max_published=BASE, n_new=5
    )
    assert result.status == "suspected"
    assert result.oldest_in_page == BASE + timedelta(hours=10)
    assert result.prev_max_published == BASE


def test_limite_exacte_egalite_donne_none() -> None:
    """oldest == prev_max : le recouvrement tient a un item, mais il tient."""
    result = assess_gap(status="ok", entries=page(0, 2, 4, 6, 8), prev_max_published=BASE, n_new=4)
    assert result.status == "none"


def test_les_deux_bornes_permettent_de_rejouer_la_decision() -> None:
    result = assess_gap(
        status="ok", entries=page(10, 12, 14, 16, 18), prev_max_published=BASE, n_new=5
    )
    assert result.oldest_in_page is not None and result.prev_max_published is not None
    assert result.oldest_in_page > result.prev_max_published
    intervalle = result.oldest_in_page - result.prev_max_published
    assert intervalle == timedelta(hours=10)


def test_page_vide_donne_unknown() -> None:
    result = assess_gap(status="ok", entries=[], prev_max_published=BASE, n_new=0)
    assert result.status == "unknown"


def test_deux_entrees_fiables_c_est_trop_peu() -> None:
    result = assess_gap(status="ok", entries=page(10, 12), prev_max_published=BASE, n_new=2)
    assert result.status == "unknown"


def test_le_verdict_est_toujours_un_des_trois_etats() -> None:
    cas = [
        ("ok", page(0, 1, 2, 3, 4), BASE, 2),
        ("ok", [], None, 0),
        ("error", [], BASE, 0),
        ("not_modified", [], BASE, 0),
        ("ok", page(10, 11, 12, 13, 14), BASE, 5),
        ("ok", page(1, 2, 3, date_source="fetched"), BASE, 3),
    ]
    for status, entries, prev, n_new in cas:
        result = assess_gap(
            status=status,  # type: ignore[arg-type]
            entries=entries,
            prev_max_published=prev,
            n_new=n_new,
        )
        assert result.status in ("none", "suspected", "unknown")
        assert result.reason
