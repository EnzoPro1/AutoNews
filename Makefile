# Le conteneur est le seul environnement d'execution : Python 3.12 n'existe pas sur l'hote.
COMPOSE ?= docker compose

.PHONY: up down logs test lint fmt ingest seed shell psql

up:            ## Construit et demarre app + db (migrations + seed automatiques)
	$(COMPOSE) up -d --build

down:          ## Arrete les services (le volume pgdata survit)
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f app

test:          ## Suite complete, y compris les tests marques @pytest.mark.db
	$(COMPOSE) run --rm app pytest

# --entrypoint "" : le lint n'a pas besoin des migrations ni du seed.
lint:          ## Exactement ce que fait la CI
	$(COMPOSE) run --rm --no-deps --entrypoint "" app ruff check .
	$(COMPOSE) run --rm --no-deps --entrypoint "" app ruff format --check .

fmt:
	$(COMPOSE) run --rm --no-deps --entrypoint "" app ruff format .
	$(COMPOSE) run --rm --no-deps --entrypoint "" app ruff check --fix .

seed:          ## Resynchronise feeds.yaml vers la table feed
	$(COMPOSE) exec app python -m veille seed

ingest:        ## Ingere tous les flux (make ingest FEED=cert-fr pour un seul)
	$(COMPOSE) exec app python -m veille ingest $(if $(FEED),--feed $(FEED),)

shell:
	$(COMPOSE) exec app bash

psql:
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-veille} -d $${POSTGRES_DB:-veille}
