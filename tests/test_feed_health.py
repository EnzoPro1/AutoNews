"""Lisibilite de /feeds : distinguer une panne transitoire d'un flux casse."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import NOW, make_feed
from veille.models import FeedRun
from veille.web.filters import error_kind

pytestmark = pytest.mark.db


def test_feeds_page_separates_a_transient_outage_from_a_broken_feed(
    session: Session, client: TestClient
) -> None:
    """hnrss tombe regulierement. Si un 502 s'affiche comme un flux casse, /feeds
    est rouge en permanence et on cesse de le regarder."""
    reseau = make_feed(session, "hn-sec")
    casse = make_feed(session, "flux-mort")
    session.add_all(
        [
            FeedRun(
                feed_id=reseau.id,
                started_at=NOW,
                finished_at=NOW,
                status="error",
                http_status=502,
                error_message="FetchError: hn-sec : HTTP 502",
                n_new=0,
                n_seen=0,
            ),
            FeedRun(
                feed_id=casse.id,
                started_at=NOW,
                finished_at=NOW,
                status="error",
                error_message="FeedParseError: flux illisible : SAXParseException(...)",
                n_new=0,
                n_seen=0,
            ),
        ]
    )
    session.commit()

    body = client.get("/feeds").text
    assert "kind-reseau" in body
    assert "kind-flux" in body
    assert "réseau" in body


def test_error_kind_contract_matches_what_the_pipeline_writes() -> None:
    """Le pipeline ecrit "<NomDException>: <message>". Ce test verrouille le
    contrat des deux cotes : si le format change, il casse ici."""
    assert error_kind("FetchError: hn-sec : HTTP 502") == "reseau"
    assert error_kind("FetchError: hn-ai : timed out") == "reseau"
    assert error_kind("FeedParseError: flux illisible : SAXParseException(...)") == "flux"
    assert error_kind("InvalidUrlError: schema interdit : javascript") == "flux"
    assert error_kind("ProgrammingError: colonne inconnue") == "interne"
    # un run 'ok' peut porter un avertissement : ce n'est pas une panne
    assert error_kind("flux degrade : CharacterEncodingOverride()") == ""
    assert error_kind(None) == ""
    assert error_kind("") == ""
