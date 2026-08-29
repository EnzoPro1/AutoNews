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

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Cle arbitraire mais stable. Un entier signe 64 bits, choisi hors des plages
#: qu'un autre outil utiliserait par hasard.
INGEST_LOCK_KEY = 0x5645494C4C450001


@contextmanager
def advisory_lock(bind: Engine | Session, key: int = INGEST_LOCK_KEY) -> Iterator[bool]:
    """Tente de prendre le verrou. Rend True s'il est obtenu, False sinon.

    Le verrou est pris sur une connexion DEDIEE, ouverte pour la duree du bloc,
    et surtout pas sur la Session du pipeline. Un verrou consultatif de session
    vit sur la connexion : or `session.commit()` rend la connexion au pool, et
    le pipeline commite apres chaque flux. Le verrou serait alors abandonne sur
    une connexion rendue au pool pendant que le deverrouillage s'executerait sur
    une autre -- il ne serait jamais relache, et l'ingestion suivante se
    croirait en concurrence avec elle-meme. Symptome constate avant correction :
    sept tests d'integration echouant en IngestLockedError.

    La connexion dediee garantit aussi la liberation a la mort du processus,
    ce qu'on veut sur un laptop qui s'eteint sans prevenir : un verrou
    persistant bloquerait toutes les ingestions suivantes.
    """
    engine = bind.get_bind() if isinstance(bind, Session) else bind
    connection = engine.connect()
    acquired = False
    try:
        acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
        if not acquired:
            logger.warning("verrou d'ingestion deja pris, ce n'est pas une panne")
        yield acquired
    finally:
        if acquired:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        connection.close()
