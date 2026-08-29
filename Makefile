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

# --- Sauvegarde -------------------------------------------------------------
# Piege B : pg_dump ecrit DANS le conteneur, sur le montage ./backups. Le dump
# ne transite jamais par un pipe PowerShell, qui alloue un pseudo-TTY et redirige
# en UTF-16 -- deux facons de corrompre un format custom silencieusement, avec
# un fichier de la bonne taille qui ne se restaure pas.
# Toute la logique est dans un `sh -c` cote conteneur : la recette make reste
# une seule commande, valide que make utilise sh ou cmd.exe.
# La verification lit l'archive ENTIERE (pg_restore -f /dev/null), pas seulement
# sa table des matieres. Mesure faite : `pg_restore --list` ACCEPTE un dump coupe
# a 40 Ko, la TOC etant ecrite en tete du format custom. C'est exactement le
# fichier de la bonne taille qui ne se restaure pas.
#
# `restore` fait cette verification AVANT de lancer pg_restore --clean. Sans cet
# ordre, un dump tronque passe la verification faible, --clean supprime les
# tables, puis la restauration echoue sur la fin de fichier : la base est videe
# par une sauvegarde inutilisable. Constate en conditions reelles pendant le
# developpement de ce lot, corpus perdu et repris depuis le dump precedent.
BACKUP_KEEP ?= 14

backup:        ## Dump verifie dans backups/, rotation des 14 plus recents
	$(COMPOSE) exec -T db sh -c 'set -e; f=/backups/autonews-$$(date -u +%Y%m%d-%H%M%S).dump; pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --format=custom --file="$$f"; test -s "$$f" || { echo "ECHEC : dump vide" >&2; rm -f "$$f"; exit 1; }; pg_restore -f /dev/null "$$f" >/dev/null 2>&1 || { echo "ECHEC : dump illisible ou tronque" >&2; rm -f "$$f"; exit 1; }; ls -1t /backups/*.dump 2>/dev/null | tail -n +$$(($(BACKUP_KEEP)+1)) | xargs -r rm -f; echo "OK $$f ($$(du -h "$$f" | cut -f1))"'

restore:       ## make restore FILE=backups/autonews-....dump  -- ECRASE la base
	@test -n "$(FILE)" || { echo "usage : make restore FILE=backups/autonews-....dump" >&2; exit 1; }
	$(COMPOSE) exec -T db sh -c 'set -e; f=/backups/$(notdir $(FILE)); test -f "$$f" || { echo "introuvable : $$f" >&2; exit 1; }; pg_restore -f /dev/null "$$f" >/dev/null 2>&1 || { echo "ECHEC : dump illisible ou tronque, restauration REFUSEE avant toute suppression" >&2; exit 1; }; pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error "$$f"; echo "restaure depuis $$f"'

backups:       ## Liste les sauvegardes presentes
	$(COMPOSE) exec -T db sh -c 'ls -lht /backups/*.dump 2>/dev/null || echo "aucune sauvegarde"'

shell:
	$(COMPOSE) exec app bash

psql:
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-veille} -d $${POSTGRES_DB:-veille}
