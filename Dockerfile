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
