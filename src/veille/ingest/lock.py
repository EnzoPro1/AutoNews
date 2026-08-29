"""Verrou consultatif Postgres.

Une tache planifiee peut chevaucher un `make ingest` lance a la main.
`-MultipleInstances IgnoreNew` ne couvre que la tache, pas ce croisement-la.

Deux ingestions simultanees ne corrompraient pas les donnees -- les upserts sont
idempotents -- mais elles fausseraient la detection de trou : la seconde lirait
un prev_max deja avance par la premiere. Le verrou evite ca.

Portee globale, une seule cle : le pipeline est sequentiel, et un verrou par
flux multiplierait les allers-retours sans benefice tant qu'on n'ingere pas en
parallele.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Cle arbitraire mais stable. Un entier signe 64 bits, choisi hors des plages
#: qu'un autre outil utiliserait par hasard.
INGEST_LOCK_KEY = 0x5645494C4C450001


@contextmanager
def advisory_lock(session: Session, key: int = INGEST_LOCK_KEY) -> Iterator[bool]:
    """Tente de prendre le verrou. Rend True s'il est obtenu, False sinon.

    Verrou de session : il est relache explicitement en sortie, et de toute
    facon a la fermeture de la connexion si le processus meurt brutalement.
    C'est ce qu'on veut sur un laptop qui s'eteint sans prevenir -- un verrou
    persistant bloquerait toutes les ingestions suivantes.
    """
    acquired = bool(session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
    if not acquired:
        logger.warning("verrou d'ingestion deja pris, ce n'est pas une panne")
    try:
        yield acquired
    finally:
        if acquired:
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            session.commit()
