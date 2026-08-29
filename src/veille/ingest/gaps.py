"""Detection de saturation de page — regle de decision pure.

Un flux RSS n'expose que sa page courante. Si tout ce qu'elle contient est plus
recent que tout ce qu'on connaissait, elle ne recouvre plus notre historique :
des items ont defile entre les deux runs et sont definitivement perdus. On ne
peut pas les rattraper, mais on peut refuser de faire comme s'ils n'avaient pas
existe.

Aucune base, aucun reseau, aucune horloge : tout arrive en parametre, la
fonction est testable isolement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from veille.normalize.dates import has_degenerate_span
from veille.schemas import GapStatus, ParsedEntry, RunStatus

#: En dessous, on ne peut pas distinguer un vrai saut d'une gigue de dates.
MIN_RELIABLE_ENTRIES = 3

#: Part minimale d'entrees a date fiable dans la page. En dessous, la
#: chronologie de la page est majoritairement inventee.
MIN_RELIABLE_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class GapAssessment:
    """Verdict et ses deux bornes, telles qu'elles seront ecrites dans feed_run."""

    status: GapStatus
    oldest_in_page: datetime | None
    prev_max_published: datetime | None
    #: quelle regle a tranche. Journalise, pas stocke : feed_run n'a pas de
    #: colonne pour ca et n'en a pas besoin, les deux bornes suffisent a rejouer.
    reason: str


def assess_gap(
    *,
    status: RunStatus,
    entries: list[ParsedEntry],
    prev_max_published: datetime | None,
    n_new: int,
) -> GapAssessment:
    """Applique la regle de saturation de page.

    `prev_max_published` doit avoir ete lu AVANT l'ecriture des entrees de ce
    run, sinon les articles qu'on vient de stocker l'inflatent et la detection
    ne declenche jamais.
    """
    # 1. Rien n'a ete fetche : ni un 304 ni une erreur ne permettent d'evaluer
    #    quoi que ce soit. 'unknown', jamais 'none' -- ne pas savoir n'est pas
    #    savoir que non.
    if status != "ok":
        return GapAssessment("unknown", None, prev_max_published, f"run en statut {status}")

    if not entries:
        return GapAssessment("unknown", None, prev_max_published, "page sans entree")

    # 2. Bornes calculees uniquement sur les dates venant reellement du flux.
    #    Une seule date 'fetched' vaut ~l'heure du run : la laisser entrer dans
    #    le calcul ferait de prev_max un "maintenant" perpetuel, donc un 'none'
    #    permanent -- un faux negatif silencieux, bien pire qu'un 'unknown'.
    reliable = [e.published_at for e in entries if e.date_source != "fetched"]

    if len(reliable) < MIN_RELIABLE_ENTRIES or len(reliable) < MIN_RELIABLE_SHARE * len(entries):
        return GapAssessment(
            "unknown",
            None,
            prev_max_published,
            f"{len(reliable)}/{len(entries)} entrees a date fiable",
        )

    # 3. Page sans chronologie : meme seuil que celui applique dans `parse`, et
    #    defini au meme endroit pour qu'ils ne divergent pas.
    if has_degenerate_span(reliable):
        return GapAssessment(
            "unknown", None, prev_max_published, "page sans chronologie exploitable"
        )

    oldest = min(reliable)

    # 4. Premier run de ce flux : aucune borne inferieure, donc rien a comparer.
    if prev_max_published is None:
        return GapAssessment("unknown", oldest, None, "premier run de ce flux")

    # 5. La page recouvre encore notre historique : continuite etablie. Un flux
    #    a faible debit (CERT-FR) restera durablement ici, et c'est correct :
    #    sa page ne tourne pas, donc rien ne defile.
    if oldest <= prev_max_published:
        return GapAssessment("none", oldest, prev_max_published, "la page recouvre l'historique")

    # 6. Plus aucun recouvrement, mais aucun item nouveau : c'est logiquement
    #    contradictoire. Des items qu'on connait tous ont forcement ete vus,
    #    donc prev_max les couvrait. Le declenchement ne peut venir que d'un
    #    deplacement de dates (flux qui reedite en re-horodatant), pas d'un trou.
    if n_new == 0:
        return GapAssessment(
            "unknown", oldest, prev_max_published, "dates deplacees, aucun item nouveau"
        )

    # 7. Recouvrement perdu et items nouveaux : intervalle non couvert entre
    #    prev_max_published et oldest_in_page.
    return GapAssessment(
        "suspected", oldest, prev_max_published, "plus aucun recouvrement avec l'historique"
    )
