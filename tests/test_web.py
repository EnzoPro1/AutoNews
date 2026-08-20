"""Routes de lecture : liste paginee, filtres, echappement, sante des flux."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import veille.web.app
from conftest import NOW, make_feed, read_fixture
from veille.ingest.parse import parse_feed
from veille.ingest.store import store_entries
from veille.models import Article, Feed, FeedRun
from veille.web.filters import excerpt, relative_date, to_paris

pytestmark = pytest.mark.db


def ingest(session: Session, feed: Feed, fixture: str, when=NOW) -> None:
    parsed = parse_feed(read_fixture(fixture), fetched_at=when)
    store_entries(session, feed_id=feed.id, entries=parsed.entries, now=when)
    session.commit()


# ------------------------------------------------------------------ GET /


def test_index_is_reachable_when_empty(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Aucun article" in response.text


def test_index_lists_titles(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "sain"), "rss20_ok.xml")
    body = client.get("/").text
    assert "Une faille critique dans un routeur repandu" in body
    assert "Bulletin hebdomadaire" in body


def test_external_links_carry_rel_noopener_noreferrer(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "sain"), "rss20_ok.xml")
    body = client.get("/").text
    assert 'href="https://exemple-sain.test/2026/08/faille-routeur"' in body
    assert 'rel="noopener noreferrer"' in body


def test_href_is_the_canonical_url_not_the_tracked_one(
    session: Session, client: TestClient
) -> None:
    ingest(session, make_feed(session, "sain"), "rss20_ok.xml")
    body = client.get("/").text
    assert "utm_source" not in body, "aucun parametre de tracking transmis au navigateur"


def test_sorted_by_published_at_desc(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "sain"), "rss20_ok.xml")
    body = client.get("/").text
    positions = [
        body.index("Une faille critique"),
        body.index("Le modèle ouvert"),
        body.index("Bulletin hebdomadaire"),
    ]
    assert positions == sorted(positions)


def test_badges_show_lang_and_topic(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "sain", lang="fr", topic="sec"), "rss20_ok.xml")
    body = client.get("/").text
    assert "badge-lang" in body and ">FR<" in body
    assert "badge-topic" in body and "Cyber" in body


# ------------------------------------------------------------- filtres


def test_lang_filter(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "fr-feed", lang="fr"), "rss20_ok.xml")
    ingest(session, make_feed(session, "en-feed", lang="en"), "atom_ok.xml")

    body = client.get("/", params={"lang": "fr"}).text
    assert "Une faille critique" in body
    assert "Notes on running local models" not in body


def test_topic_filter_includes_both(session: Session, client: TestClient) -> None:
    """?topic=ai doit ramener aussi les flux classes 'both'."""
    ingest(session, make_feed(session, "ia", topic="ai", lang="en"), "atom_ok.xml")
    ingest(session, make_feed(session, "mixte", topic="both"), "rss20_ok.xml")
    ingest(session, make_feed(session, "cyber", topic="sec"), "hostile.xml")

    body = client.get("/", params={"topic": "ai"}).text
    assert "Notes on running local models" in body
    assert "Une faille critique" in body, "un flux 'both' doit apparaitre dans ?topic=ai"
    assert "Article piégé" not in body


def test_unknown_filter_value_is_rejected(client: TestClient) -> None:
    assert client.get("/", params={"lang": "de"}).status_code == 422
    assert client.get("/", params={"topic": "crypto"}).status_code == 422
    assert client.get("/", params={"page": 0}).status_code == 422


# ------------------------------------------------------------- pagination


def test_pagination_is_50_per_page(session: Session, client: TestClient) -> None:
    feed = make_feed(session, "gros")
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Article(
                first_feed_id=feed.id,
                url_canonical=f"https://exemple.test/a/{index}",
                title=f"Article numero {index}",
                content_hash=f"{index:064d}",
                published_at=base + timedelta(minutes=index),
                date_source="published",
                first_seen_at=base,
                last_seen_at=base,
                updated_at=base,
            )
            for index in range(120)
        ]
    )
    session.commit()

    first = client.get("/").text
    assert first.count('class="article"') == 50
    assert "page 1 / 3" in first
    assert "Article numero 119" in first

    second = client.get("/", params={"page": 2}).text
    assert second.count('class="article"') == 50
    assert "Article numero 119" not in second

    third = client.get("/", params={"page": 3}).text
    assert third.count('class="article"') == 20
    assert "Article numero 0" in third


def test_pagination_links_keep_the_filters(session: Session, client: TestClient) -> None:
    feed = make_feed(session, "gros", lang="fr", topic="sec")
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Article(
                first_feed_id=feed.id,
                url_canonical=f"https://exemple.test/b/{index}",
                title=f"Item {index}",
                content_hash=f"{index:064d}",
                published_at=base + timedelta(minutes=index),
                date_source="published",
                first_seen_at=base,
                last_seen_at=base,
                updated_at=base,
            )
            for index in range(60)
        ]
    )
    session.commit()

    body = client.get("/", params={"lang": "fr", "topic": "sec"}).text
    assert "?lang=fr&amp;topic=sec&amp;page=2" in body


# ------------------------------------------------------- contenu non fiable


def test_feed_html_never_reaches_the_page_as_markup(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "hostile"), "hostile.xml")
    body = client.get("/").text
    assert "<script>alert" not in body
    assert "onerror=" not in body
    assert "javascript:alert" not in body


PIEGE_URL = "https://exemple-hostile.test/piege"


def test_xss_payload_is_removed_by_nh3_not_merely_escaped(
    session: Session, client: TestClient
) -> None:
    """La chaine complete : brut stocke -> nh3 -> summary_clean -> page.

    Un simple echappement Jinja rendrait la page inoffensive tout en laissant
    `&lt;script&gt;alert(...)&lt;/script&gt;` visible en toutes lettres dans
    l'extrait. Ce test exige que la charge ait DISPARU, pas qu'elle soit inerte.
    """
    ingest(session, make_feed(session, "hostile"), "hostile.xml")
    article = session.scalar(select(Article).where(Article.url_canonical == PIEGE_URL))
    assert article is not None

    # 1. le brut conserve porte bien la charge : c'est donc nh3 qui la retire,
    #    et pas un assainisseur en amont dont personne n'aurait decide.
    raw = article.raw_summary or ""
    assert "<script>alert('xss')</script>" in raw
    assert 'onerror="alert(1)"' in raw
    assert 'href="javascript:alert(2)"' in raw
    assert "<!-- commentaire cache -->" in raw

    # 2. la colonne rendue est passee par l'allowlist nh3
    clean = (article.summary_clean or "").lower()
    assert "script" not in clean
    assert "onerror" not in clean
    assert "javascript:" not in clean
    assert "<img" not in clean
    assert "<!--" not in clean

    # 3. la page ne contient la charge sous AUCUNE forme, pas meme echappee
    body = client.get("/").text
    assert "&lt;script&gt;" not in body
    assert "alert(" not in body
    assert "onerror" not in body.lower()
    assert "commentaire cache" not in body
    assert "Texte légitime" in body


def test_the_template_reads_summary_clean_and_never_raw_summary() -> None:
    """Invariant 5, verifie sur la source des templates.

    Volontairement structurel : `excerpt` reexecute nh3 via strip_tags, donc
    afficher raw_summary donnerait AUJOURD'HUI exactement le meme texte a
    l'ecran. Aucune assertion de sortie ne peut donc distinguer les deux
    colonnes, et c'est precisement le piege : nh3 ecrirait une colonne que
    personne ne lit, et le jour ou une vue detail ajoutera |safe sur
    summary_clean, elle basculerait sur un chemin jamais exerce.
    """
    templates = (Path(veille.web.app.__file__).parent / "templates").glob("*.html")
    sources = {path.name: path.read_text(encoding="utf-8") for path in templates}
    assert sources, "aucun template trouve"

    index = sources["index.html"]
    assert "summary_clean | excerpt" in index or "summary_clean|excerpt" in index

    for name, source in sources.items():
        assert "raw_summary" not in source, f"{name} lit raw_summary"
        # `|safe` sur du contenu de flux : interdit tant qu'aucune vue detail ne
        # le justifie explicitement.
        assert "|safe" not in source.replace("Aucun |safe", "")
        assert "| safe" not in source


def test_summary_clean_is_what_gets_displayed(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "hostile"), "hostile.xml")
    article = session.scalar(select(Article).where(Article.url_canonical == PIEGE_URL))
    assert article is not None
    assert article.summary_clean != article.raw_summary

    body = client.get("/").text
    assert excerpt(article.summary_clean) in body


def test_article_without_summary_renders_no_empty_block(
    session: Session, client: TestClient
) -> None:
    """Les items Hacker News n'ont pas de resume exploitable : pas de bloc vide,
    pas de points de suspension."""
    ingest(session, make_feed(session, "hostile"), "hostile.xml")
    body = client.get("/").text

    assert "Sans resume exploitable" in body
    assert '<p class="summary"></p>' not in body
    assert '<p class="summary">…</p>' not in body
    # un seul des deux articles a un resume affichable
    assert body.count('class="summary"') == 1


def test_fallback_date_is_flagged_in_the_page(session: Session, client: TestClient) -> None:
    ingest(session, make_feed(session, "dates"), "dates_broken.xml")
    body = client.get("/").text
    assert "date approchée" in body


# ------------------------------------------------------------ GET /feeds


def test_feeds_page_lists_every_feed_even_without_a_run(
    session: Session, client: TestClient
) -> None:
    make_feed(session, "jamais-lance")
    body = client.get("/feeds").text
    assert "jamais-lance" in body
    assert "jamais" in body


def test_feeds_page_shows_the_last_run_only(session: Session, client: TestClient) -> None:
    feed = make_feed(session, "sain")
    session.add_all(
        [
            FeedRun(
                feed_id=feed.id,
                started_at=NOW - timedelta(days=1),
                finished_at=NOW - timedelta(days=1),
                status="error",
                error_message="ancien echec oublie",
                n_new=0,
                n_seen=0,
            ),
            FeedRun(
                feed_id=feed.id,
                started_at=NOW,
                finished_at=NOW,
                status="ok",
                http_status=200,
                n_new=3,
                n_seen=12,
            ),
        ]
    )
    session.commit()

    body = client.get("/feeds").text
    assert "ancien echec oublie" not in body
    assert "status-ok" in body
    assert ">200<" in body


def test_feeds_page_shows_errors(session: Session, client: TestClient) -> None:
    feed = make_feed(session, "casse")
    session.add(
        FeedRun(
            feed_id=feed.id,
            started_at=NOW,
            finished_at=NOW,
            status="error",
            http_status=503,
            error_message="FetchError: casse : HTTP 503",
            n_new=0,
            n_seen=0,
        )
    )
    session.commit()

    body = client.get("/feeds").text
    assert "status-error" in body
    assert "HTTP 503" in body


# ------------------------------------------------------------------ filtres Jinja


def test_relative_date_is_french() -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    assert relative_date(now - timedelta(seconds=20), now) == "à l'instant"
    assert relative_date(now - timedelta(minutes=42), now) == "il y a 42 min"
    assert relative_date(now - timedelta(hours=5), now) == "il y a 5 h"
    assert relative_date(now - timedelta(days=1), now) == "hier"
    assert relative_date(now - timedelta(days=4), now) == "il y a 4 jours"
    assert relative_date(now - timedelta(days=20), now) == "il y a 2 sem."


def test_conversion_to_paris_happens_only_at_render_time() -> None:
    utc_value = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    assert to_paris(utc_value).hour == 14  # CEST en aout
    assert utc_value.hour == 12, "la valeur d'origine reste en UTC"


def test_excerpt_returns_plain_text() -> None:
    assert excerpt("<p>Un <strong>resume</strong> court.</p>") == "Un resume court."
    assert excerpt(None) == ""
    assert excerpt("") == ""
    long_text = "<p>" + "mot " * 200 + "</p>"
    assert len(excerpt(long_text)) <= 241
    assert excerpt(long_text).endswith("…")
