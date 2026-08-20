# veille

Agregateur de news IA + cybersecurite (sources FR et internationales). V0 : ingestion
RSS, deduplication par URL canonique, affichage.

Le conteneur est le seul environnement d'execution. Rien ne tourne sur l'hote.

## Lancement

```bash
cp .env.example .env
docker compose up -d --build   # migrations + seed automatiques, app sur http://localhost:8000
docker compose exec app python -m veille ingest
```
