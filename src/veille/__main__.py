"""CLI : `python -m veille ingest [--feed ID]` et `python -m veille seed`.

Point d'entree unique. Aucune logique metier ici : elle appelle le pipeline et
met en forme le resultat.
"""

from __future__ import annotations

import argparse
import logging
import sys

from veille.config import settings
from veille.db import session_scope
from veille.errors import VeilleError
from veille.feeds_config import load_feeds
from veille.ingest.pipeline import run_ingestion
from veille.schemas import FeedSpec
from veille.seed import seed_feeds

logger = logging.getLogger("veille")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="veille", description="Veille IA + cybersecurite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("seed", help="synchronise feeds.yaml vers la table feed")

    ingest = subparsers.add_parser("ingest", help="ingere les flux")
    ingest.add_argument("--feed", metavar="ID", help="n'ingerer que ce flux (id de feeds.yaml)")

    args = parser.parse_args(argv)
    _configure_logging()

    try:
        specs = load_feeds()
    except VeilleError as exc:
        logger.error("%s", exc)
        return 2

    if args.command == "seed":
        return _run_seed(specs)
    return _run_ingest(specs, feed_id=args.feed)


def _run_seed(specs: list[FeedSpec]) -> int:
    with session_scope() as session:
        report = seed_feeds(session, specs)

    for label, slugs in (
        ("cree", report.created),
        ("mis a jour", report.updated),
        ("desactive", report.deactivated),
    ):
        for slug in slugs:
            print(f"  {label:12} {slug}")
    print(f"seed : {len(specs)} flux declares, {len(report.unchanged)} inchanges")
    return 0


def _run_ingest(specs: list[FeedSpec], *, feed_id: str | None) -> int:
    if feed_id:
        specs = [spec for spec in specs if spec.id == feed_id]
        if not specs:
            logger.error("flux inconnu : %s", feed_id)
            return 2

    with session_scope() as session:
        outcomes = run_ingestion(session, specs)

    if not outcomes:
        logger.error("aucun flux ingere : la table feed est-elle vide ? lancer `veille seed`")
        return 2

    width = max(len(outcome.feed_slug) for outcome in outcomes)

    for outcome in outcomes:
        detail = f"{outcome.n_new:>4} nouveaux / {outcome.n_seen:>4} vus"
        if outcome.status == "error":
            detail = outcome.error_message or "echec"
        print(f"  {outcome.feed_slug:<{width}}  {outcome.status:<13} {detail}")

    n_ok = sum(1 for outcome in outcomes if outcome.status != "error")
    n_new = sum(outcome.n_new for outcome in outcomes)
    print(f"{n_ok}/{len(outcomes)} flux traites, {n_new} nouveaux articles")

    # Code retour non nul seulement si TOUS les flux ont echoue : un flux mort
    # est un incident normal, pas un echec du run.
    return 0 if n_ok else 1


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
