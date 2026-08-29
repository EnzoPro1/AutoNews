# ---------- builder : resout et installe les dependances dans un venv isole ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# CA racine supplementaires. Le dossier est vide dans le depot : sans .crt,
# update-ca-certificates ne fait rien. Utile uniquement quand un antivirus ou un
# proxy intercepte le TLS sur la machine de build (cf docker/extra-ca/README.md).
COPY docker/extra-ca/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates > /dev/null 2>&1 || true
# pip utilise certifi par defaut : on le pointe explicitement vers le magasin
# systeme, sinon la CA ajoutee ci-dessus resterait ignoree.
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

# Installation editable : le venv pointe vers /app/src, que le runtime reprend au
# meme chemin. Permet de bind-monter ./src en dev sans reconstruire l'image.
RUN pip install -e ".[dev]"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:$PATH"

RUN useradd --create-home --uid 1000 veille

WORKDIR /app

COPY --from=builder /venv /venv
COPY --from=builder /app /app

# Meme point d'injection cote execution : httpx passe par certifi et ignore le
# magasin systeme, mais VEILLE_CA_BUNDLE peut le pointer vers ce fichier.
COPY docker/extra-ca/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates > /dev/null 2>&1 || true

# pg_dump / pg_restore en version 16 EXACTEMENT, pour que le test de
# restauration s'execute la ou tourne pytest.
#
# Le client par defaut de Debian trixie est en 17, et ne convient pas : pg_dump
# 17 sait bien lire un serveur 16, mais l'archive produite contient
# `SET transaction_timeout`, parametre inexistant en 16, et la restauration vers
# le serveur echoue. Mesure faite, l'erreur est exactement :
#   ERROR: unrecognized configuration parameter "transaction_timeout"
# Le depot PGDG donne la version alignee sur le serveur.
#
# Installe APRES update-ca-certificates : sinon curl echoue quand un antivirus
# intercepte le TLS sur la machine de build.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl gnupg; \
    . /etc/os-release; \
    key=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    install -d "$(dirname "$key")"; \
    curl --fail -o "$key" https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    echo "deb [signed-by=$key] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    apt-get purge -y curl gnupg; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY feeds.yaml ./feeds.yaml
COPY tests ./tests
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && chown -R veille:veille /app

USER veille

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "veille.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
