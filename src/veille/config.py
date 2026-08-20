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

    feeds_path: Path = Path("/app/feeds.yaml")
    log_level: str = "INFO"

    page_size: int = 50


settings = Settings()
