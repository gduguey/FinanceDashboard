# Multi-stage Dockerfile: build frontend, build backend, final runtime

# Stage 1: Build Node/React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./

# Vite bakes VITE_* vars into the built JS at build time, not read at
# runtime — so this has to arrive as a build arg (see docker-compose.yml's
# build.args), not an ordinary container env var. A silently-blank key
# would build fine and only fail once a browser loads the result, so this
# refuses to build at all instead — same "refuse rather than half-configure"
# call as AppRuntimeDatabaseSettings makes for DATABASE_URL_APP.
# (Docker's build linter flags any ARG/ENV named like a secret regardless of
# content — this one isn't: Clerk publishable keys are meant to ship inside
# browser JS, see trades.api.auth's own docstring on the same key.)
ARG VITE_CLERK_PUBLISHABLE_KEY
RUN test -n "$VITE_CLERK_PUBLISHABLE_KEY" || { echo "VITE_CLERK_PUBLISHABLE_KEY build arg is required" >&2; exit 1; }
ENV VITE_CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLISHABLE_KEY

RUN npm run build

# Stage 2: Build Python environment
FROM python:3.14-slim AS python-builder
WORKDIR /app

# Grab the uv binary straight from its official image — no pip/curl install needed
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Only what dependency resolution + the package build need
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

# --locked fails the build if uv.lock is out of sync with pyproject.toml,
# instead of silently re-resolving — same exact versions as local `uv sync`.
# --no-dev skips pytest/ruff/jupyter/etc; --extra pulls in the api+llm groups.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --locked --no-dev --extra api --extra llm

# Stage 3: Runtime image
FROM python:3.14-slim
WORKDIR /app

# Install runtime dependencies: curl for health checks, postgresql-client for
# `pg_dump` (see db.backup, run once daily at 3:00 UTC via a crontab entry
# on the deploy VM) — Debian's packaged version may trail the
# postgres:16-alpine server's; pg_dump
# is generally compatible with newer servers, but pin to the PGDG apt repo
# instead if that ever actually causes a dump to fail.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy Python virtual environment from builder
COPY --from=python-builder /opt/venv /opt/venv

# Copy source code
COPY src/ src/

# alembic.ini lives at the repo root, not under src/ — needed here so
# `alembic upgrade head` (run by CMD below on every container start) can
# find its own config and migration scripts.
COPY alembic.ini ./

# Copy built frontend from builder
COPY --from=frontend-builder /app/web/dist/ web/dist/

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VENV_PATH="/opt/venv"

# Create non-root user for security, and an empty data mount point
# (real data always comes from the volume mount / Postgres / R2 at runtime,
# never baked into the image)
RUN useradd -m -u 1000 appuser && \
    mkdir -p data && \
    chown -R appuser:appuser /app
USER appuser

# Health check: verify FastAPI is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose ports
EXPOSE 8000

# Run migrations, then the FastAPI server — every container start, not a
# separate manual deploy step (see migration 817ace9deb09's own docstring
# for why a failed migration refusing to start the app is deliberate).
# Shell form (not the usual JSON-array CMD) specifically so `&&`/`exec` work
# without a separate entrypoint script — `exec` replaces this shell with
# uvicorn as PID 1, so it still receives signals (e.g. `docker stop`) directly.
# - Bind to 0.0.0.0 so it's accessible from outside the container
# - Use uvicorn directly (already installed via --extra api)
CMD alembic upgrade head && exec uvicorn trades.api:app --host 0.0.0.0 --port 8000
