# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Invoice Intake Agent — multi-stage container.
#
# Stages:
#   1. frontend  — Bun builds the React/Vite dashboard into /frontend/dist
#   2. base      — uv-managed Python venv with project + deps installed
#   3. runtime   — slim image, runs `infotech-email-agent up` on port 8000
#   4. test      — separate target that runs `uv run pytest -q` (used by CI)
#
# Build:
#   docker build -t infotech-agent .                    # runtime image
#   docker build -t infotech-agent-tests --target test .# test image (runs suite)
#
# Run:
#   docker run --rm -p 8000:8000 \
#              -e OPENAI_API_KEY=$OPENAI_API_KEY \
#              -v "$PWD/out:/app/out" \
#              infotech-agent
# ---------------------------------------------------------------------------

# ---- 1. Frontend bundle ---------------------------------------------------
FROM oven/bun:1.3-alpine AS frontend
WORKDIR /frontend

# Install JS deps with cache mount (lockfile-driven).
COPY frontend/package.json frontend/bun.lock* ./
RUN --mount=type=cache,target=/root/.bun \
    bun install --frozen-lockfile || bun install

# Build the Vite bundle.
COPY frontend/ ./
RUN bun run build

# ---- 2. Python base (uv) --------------------------------------------------
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:/root/.local/bin:$PATH"

# System deps:
#   - tesseract-ocr + libs needed by pytesseract / OCR fallback
#   - libgl/libglib for some PyMuPDF builds
#   - curl for installing uv
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libtesseract-dev \
        libgl1 \
        libglib2.0-0 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Lock-first install (better layer caching).
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Copy the rest of the project AFTER the dep layer so source edits don't
# bust the wheel cache.
COPY main.py ./
COPY scripts/ ./scripts/
COPY examples/ ./examples/
COPY docs/ ./docs/

# Drop the prebuilt frontend bundle into the location the FastAPI adapter
# serves from (FRONTEND_DIST = repo_root / "frontend" / "dist").
COPY --from=frontend /frontend/dist/ ./frontend/dist/

# ---- 3. Runtime image -----------------------------------------------------
FROM base AS runtime

ENV INVOICE_WEB_HOST=0.0.0.0 \
    INVOICE_WEB_PORT=8000 \
    INVOICE_WEB_RUNS_DIR=/app/out/web

EXPOSE 8000

# `up --no-browser`: never try to open a browser inside a container.
CMD ["uv", "run", "infotech-email-agent", "up", "--no-browser", \
     "--host", "0.0.0.0", "--port", "8000"]

# ---- 4. Test image --------------------------------------------------------
# Built only on demand: `docker build --target test ...`.
# Includes dev deps (pytest, coverage) and runs the offline suite — no
# OPENAI_API_KEY is required because conftest.py fully stubs the SDK.
FROM base AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

COPY tests/ ./tests/

# Default command runs the entire suite with the project's coverage gate.
CMD ["uv", "run", "pytest", "-q"]
