# veille

Agrégateur de news IA + cybersécurité (sources FR et internationales). Ingestion
RSS, déduplication par URL canonique, affichage, et instrumentation de la
couverture temporelle.

Le conteneur est le seul environnement d'exécution. Rien ne tourne sur l'hôte.

## Lancement

```bash
cp .env.example .env
docker compose up -d --build   # migrations + seed automatiques, app sur http://localhost:8000
docker compose exec app python -m veille ingest
```

---

## ⚠️ Avant tout `docker compose down -v`

**`docker compose down -v` détruit le corpus.** Le `-v` supprime le volume
`pgdata`, et un flux RSS n'expose que sa page courante : ce qui a défilé est
définitivement perdu, aucune réingestion ne le ramènera.

Sauvegarder d'abord :

```bash
make backup
```

`docker compose down` **sans** `-v` est sans danger : le volume survit.

### Sauvegarde

| commande | effet |
|---|---|
| `make backup` | dump vérifié dans `backups/`, rotation des 14 plus récents |
| `make backups` | liste les sauvegardes présentes |
| `make restore FILE=backups/autonews-....dump` | **écrase** la base courante |

Le dump est écrit par `pg_dump` **dans le conteneur**, sur le montage
`./backups`. Il ne transite jamais par un tube PowerShell, qui le corromprait
silencieusement.

La vérification lit l'archive **entière**, pas seulement sa table des matières :
`pg_restore --list` accepte un dump tronqué, la table des matières étant écrite
en tête. `make restore` fait cette vérification **avant** de supprimer quoi que
ce soit — sans cet ordre, un dump tronqué vide la base puis échoue.

## Collecte planifiée (Windows)

```powershell
.\scripts\register-task.ps1              # toutes les 6 h, plus à l'ouverture de session
.\scripts\register-task.ps1 -IntervalHours 4
.\scripts\register-task.ps1 -Unregister  # désinstaller
```

Vérifier :

```powershell
Get-ScheduledTask -TaskName 'AutoNews - ingestion' | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName 'AutoNews - ingestion'   # lancer à la main
Get-Content .\logs\ingest-*.log -Tail 40
```

L'intervalle de 6 h vient de la mesure de rotation des pages : le flux le plus
rapide (BleepingComputer) ne couvre qu'environ 26 h, six heures laissent donc
absorber trois exécutions manquées consécutives. Si tu le changes, change aussi
`VEILLE_INGEST_INTERVAL_HOURS` dans `.env` — c'est de là que `/coverage` tire son
seuil de trou.

`make ingest` reste disponible et inchangé. Si les deux se croisent, la seconde
sort proprement avec le code 3 : ce n'est pas une panne.

**Le Planificateur ne rattrape qu'une occurrence manquée, pas toutes.** Après
trois jours hors ligne il y aura une exécution de rattrapage, pas douze. Sans
conséquence : RSS ne permet de toute façon aucun rattrapage d'historique.

### Quand Docker refuse de démarrer

Un arrêt non propre laisse des sockets résiduels que Windows ne sait pas
supprimer. Symptôme : `initializing Inference manager: ... The file cannot be
accessed by the system`.

```powershell
.\scripts\repair-docker-sockets.ps1 -WhatIfOnly   # voir sans rien changer
.\scripts\repair-docker-sockets.ps1
```

Ce script ne doit **jamais** être planifié : il tue des processus et renomme des
dossiers du profil utilisateur. Une réparation automatique qui se trompe pendant
un déplacement est pire qu'un trou de collecte signalé — l'enrobage, lui,
consigne l'incident et `/coverage` le montre.

## Pages

| route | question à laquelle elle répond |
|---|---|
| `/` | qu'est-ce qui est paru ? |
| `/feeds` | le pipeline va-t-il bien ? |
| `/coverage` | **sur quelle période puis-je faire confiance à ces données ?** |

`/coverage` n'affiche aucun pourcentage de couverture, délibérément : un
« 97 % » se lit comme une note et invite à arrondir à « bon ». Elle montre des
intervalles et des périodes datées. La fenêtre se termine à l'instant présent et
non au dernier run : sans cela, un corpus arrêté depuis dix jours afficherait un
sans-faute.

## Développement

```bash
make test    # suite complète, base de test dédiée
make lint    # exactement ce que fait la CI
make logs
```
