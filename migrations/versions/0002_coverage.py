"""couverture : tri-etat de trou sur feed_run, table missed_run

Revision ID: 0002_coverage
Revises: 0001_initial
Create Date: 2026-08-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_coverage"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tri-etat, pas un booleen : "pas de trou" et "indeterminable" sont deux
    # informations distinctes. Le defaut 'unknown' est le bon pour les lignes
    # existantes, qui precedent l'instrumentation : on ne sait pas, on le dit.
    op.add_column(
        "feed_run",
        sa.Column("gap_status", sa.Text(), nullable=False, server_default="unknown"),
    )
    op.create_check_constraint(
        "ck_feed_run_gap_status",
        "feed_run",
        "gap_status IN ('none', 'suspected', 'unknown')",
    )

    # Les deux bornes de la decision. Elles ne sont pas decoratives : un drapeau
    # qu'on ne peut pas auditer est un drapeau qu'on cesse de croire.
    op.add_column(
        "feed_run", sa.Column("oldest_in_page", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "feed_run", sa.Column("prev_max_published", sa.DateTime(timezone=True), nullable=True)
    )

    # Table dediee plutot que des lignes feed_run : une tentative avortee ne
    # concerne aucun flux (Docker etait mort, personne n'a ete contacte), et un
    # run en erreur ne ferme aucun intervalle de couverture. La ranger dans
    # feed_run donnerait deux sens a la meme table.
    op.create_table(
        "missed_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("drained_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_missed_run"),
        # Rend le drainage idempotent gratuitement : rejouer le fichier
        # d'attente ne peut pas creer de doublon, quelle que soit la logique de
        # vidage cote script.
        sa.UniqueConstraint("attempted_at", "reason", name="uq_missed_run_attempt"),
        sa.CheckConstraint(
            "reason IN ('docker_unavailable', 'lock_held', 'wrapper_error')",
            name="ck_missed_run_reason",
        ),
    )
    op.create_index("ix_missed_run_attempted", "missed_run", ["attempted_at"])


def downgrade() -> None:
    op.drop_index("ix_missed_run_attempted", table_name="missed_run")
    op.drop_table("missed_run")
    op.drop_column("feed_run", "prev_max_published")
    op.drop_column("feed_run", "oldest_in_page")
    op.drop_constraint("ck_feed_run_gap_status", "feed_run", type_="check")
    op.drop_column("feed_run", "gap_status")
