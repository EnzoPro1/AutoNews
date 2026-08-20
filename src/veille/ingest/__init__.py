"""Pipeline d'ingestion en trois etapes separees : fetch -> parse -> store.

Aucune fonction n'en fait deux. Le reseau n'existe que dans `fetch`, la base
n'existe que dans `store`, et `parse` ne fait ni l'un ni l'autre.
"""
