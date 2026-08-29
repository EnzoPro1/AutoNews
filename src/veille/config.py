"""Configuration. Seul module autorise a lire l'environnement."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VEILLE_", extra="ignore")

    database_url: str = "postgresql+psycopg://veille:veille@db:5432/veille"

    # User-Agent explicite et identifiable, comme demande par les editeurs de flux.
    user_agent: str = "veille/0.1 (+https://github.com/EnzoPro1/AutoNews)"
    http_timeout: float = 15.0
    http_max_attempts: int = 2

    #: Bundle CA a utiliser pour les requetes sortantes. None = certifi, le
    #: comportement par defaut. A renseigner uniquement quand un antivirus ou un
    #: proxy intercepte le TLS : httpx passe par certifi et ignore le magasin
    #: systeme, donc PIP_CERT ne suffit pas cote execution.
    ca_bundle: str | None = None

    feeds_path: Path = Path("/app/feeds.yaml")
    log_level: str = "INFO"

    page_size: int = 50

    #: Intervalle de la tache planifiee. /coverage en derive son seuil de trou
    #: (2x cet intervalle) : il ne doit pas etre code en dur, sinon la page
    #: devient fausse des que la frequence change.
    #: Valeur deduite de la mesure de rotation : la page de BleepingComputer ne
    #: couvre que ~26 h, 6 h laissent absorber trois runs manques consecutifs.
    ingest_interval_hours: float = 6.0

    #: Fichier d'attente des tentatives avortees, ecrit par l'enrobage PowerShell
    #: quand la base est injoignable, draine au run suivant.
    missed_runs_path: Path = Path("/app/state/missed-runs.jsonl")


settings = Settings()
