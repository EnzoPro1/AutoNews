"""Drainage des tentatives d'ingestion qui n'ont jamais atteint la base.

Piege A : quand Docker ne repond pas, Postgres non plus. On ne peut donc pas
enregistrer l'incident au moment ou il se produit -- c'est precisement le cas
qu'il faut documenter. L'enrobage PowerShell ecrit alors une ligne dans un
fichier d'attente local, et le premier run qui reussit la remonte en base.

Ces lignes sont documentaires : elles expliquent POURQUOI il y a un trou. Elles
ne le referment pas, seul un run reussi le fait (voir web/coverage.py, R2).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from veille.config import settings
from veille.models import MissedRun
from veille.schemas import MISSED_REASONS

logger = logging.getLogger(__name__)


def drain_missed_runs(session: Session, path: Path | None = None) -> int:
    """Remonte le fichier d'attente en base et le vide. Rend le nombre insere.

    Ne leve jamais : un drainage qui echoue ne doit pas empecher d'ingerer. Le
    fichier n'est vide que si l'ecriture en base a reussi -- sinon on prefere
    redrainer les memes lignes au run suivant, la contrainte d'unicite rend
    l'operation sans consequence.
    """
    path = path or settings.missed_runs_path
    try:
        return _drain(session, path)
    except Exception:
        logger.exception("drainage impossible, l'ingestion continue")
        session.rollback()
        return 0


def _drain(session: Session, path: Path) -> int:
    if not path.exists():
        return 0

    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return 0

    now = datetime.now(tz=UTC)
    rows = [parsed for line in raw.splitlines() if (parsed := _parse_line(line, now))]

    if rows:
        # ON CONFLICT DO NOTHING sur (attempted_at, reason) : l'idempotence est
        # portee par le schema, pas par la logique de vidage du fichier. Un
        # drainage rejoue ne peut pas creer de doublon.
        session.execute(
            pg_insert(MissedRun)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_missed_run_attempt")
        )
        session.commit()

    # Vidage et non suppression : le fichier reste en place, l'enrobage n'a pas
    # a le recreer et ses permissions ne changent pas.
    path.write_text("", encoding="utf-8")
    logger.info("drainage : %s tentative(s) avortee(s) remontee(s)", len(rows))
    return len(rows)


def _parse_line(line: str, drained_at: datetime) -> dict[str, object] | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("ligne d'attente illisible, ignoree : %r", line[:120])
        return None

    attempted = _parse_utc(payload.get("attempted_at"))
    if attempted is None:
        logger.warning("ligne d'attente sans horodatage exploitable, ignoree : %r", line[:120])
        return None

    reason = str(payload.get("reason") or "wrapper_error")
    if reason not in MISSED_REASONS:
        logger.warning("motif inconnu %r, ramene a wrapper_error", reason)
        reason = "wrapper_error"

    detail = payload.get("detail")
    return {
        "attempted_at": attempted,
        "reason": reason,
        "detail": str(detail)[:2000] if detail else None,
        "drained_at": drained_at,
    }


def _parse_utc(value: object) -> datetime | None:
    """Piege D : un horodatage sans fuseau est refuse, pas suppose local.

    L'enrobage ecrit en UTC explicite. Accepter un naif reviendrait a
    l'interpreter differemment selon qu'on collecte depuis l'Europe ou le
    Quebec, et a fabriquer des trous fantomes ou des dates dans le futur.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        logger.warning("horodatage sans fuseau refuse : %r", value)
        return None
    return parsed.astimezone(UTC)
