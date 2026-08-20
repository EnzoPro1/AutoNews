"""Exceptions du domaine. Toutes heritent de VeilleError pour que le pipeline
puisse isoler un flux sans avaler les erreurs de programmation."""

from __future__ import annotations


class VeilleError(Exception):
    """Racine des erreurs metier."""


class InvalidUrlError(VeilleError):
    """URL inexploitable : vide, trop longue, schema interdit."""


class FetchError(VeilleError):
    """Echec reseau ou reponse HTTP inutilisable."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class FeedParseError(VeilleError):
    """Flux illisible : XML tronque ou malforme sans entree exploitable."""
