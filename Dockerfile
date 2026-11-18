# syntax=docker/dockerfile:1
# Huayu Coach production image. NOT BUILT YET: Docker is not installed on the development Mac (see deploy/deploy.md).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

# uv, pinned (https://docs.astral.sh/uv/guides/integration/docker/)
COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /usr/local/bin/uv

WORKDIR /app

# 1) dependencies: this layer is rebuilt only when pyproject.toml or uv.lock change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) application, reference data and database migrations
COPY app ./app
COPY seeds ./seeds
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# Run as an unprivileged user. /data is the only writable place (SQLite database, outbox) and is a volume.
RUN groupadd --system --gid 10001 huayu \
 && useradd --system --uid 10001 --gid huayu --no-create-home --shell /usr/sbin/nologin huayu \
 && mkdir -p /data && chown huayu:huayu /data
USER huayu

# compose.yml sets these again so a stray value in .env cannot turn production into dev mode.
# FORWARDED_ALLOW_IPS=*: uvicorn trusts X-Forwarded-* from the Caddy container. This is safe only because port 8000 is never published.
ENV HUAYU_DATA_DIR=/data \
    HUAYU_ENV=prod \
    HUAYU_TRUSTED_PROXY=true \
    FORWARDED_ALLOW_IPS="*"
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# `prepare` applies database migrations and loads reference data (safe to repeat), then the server starts.
# Other commands override this CMD, for example: docker compose exec app python -m app.cli create-admin --email you@example.com
# --no-access-log: uvicorn's access log would write every URL, and the password-reset link /reset/<token> carries a live
# credential in its path. The log then holds only start-up lines and errors (see deploy/deploy.md, sections 8 and 10).
CMD ["sh", "-c", "python -m app.cli prepare && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --no-access-log"]
