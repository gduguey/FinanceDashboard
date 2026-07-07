# Multi-stage Dockerfile: build frontend, build backend, final runtime

# Stage 1: Build Node/React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Build Python environment
FROM python:3.14-slim AS python-builder
WORKDIR /app

# Copy all source files for the build
COPY . .

# Install from pyproject.toml - includes api extras (FastAPI, uvicorn) and llm extras
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -e '.[api,llm]'

# Stage 3: Runtime image
FROM python:3.14-slim
WORKDIR /app

# Install runtime dependencies (postgresql client if needed, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python virtual environment from builder
COPY --from=python-builder /opt/venv /opt/venv

# Copy source code
COPY src/ src/

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
    CMD curl -f http://localhost:8000/docs || exit 1

# Expose ports
EXPOSE 8000

# Run FastAPI server
# - Bind to 0.0.0.0 so it's accessible from outside the container
# - Use uvicorn directly (already installed via --extra api)
CMD ["uvicorn", "trades.api:app", "--host", "0.0.0.0", "--port", "8000"]
