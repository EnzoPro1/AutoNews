#!/bin/sh
# Migrations puis synchronisation du YAML vers la table feed, puis la commande passee.
# Aucun appel reseau sortant ici : `ingest` reste manuel.
set -e

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] veille seed"
python -m veille seed

echo "[entrypoint] exec $*"
exec "$@"
