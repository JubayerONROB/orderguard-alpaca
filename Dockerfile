# syntax=docker/dockerfile:1
#
# Multi-stage build for the OrderGuard Streamlit app.
#
# Stage 1 (builder) installs the package and its runtime dependencies only -- no
# pytest/ruff, those belong to CI (.github/workflows/test.yml), not the shipped image.
# Stage 2 (runtime) copies the installed environment plus exactly the source trees the
# app needs at runtime (src/, ui/, eval/ -- eval/ holds fixtures, cassettes, and the CLI
# harness that ui/app.py imports for fixture/replay mode) and nothing else: no tests/,
# no .git, no .venv, no eval/runs, no .env. Credentials are supplied at `docker run`
# time via -e / --env-file, never baked into the image.

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/
# `pip install -e .` needs README.md present (pyproject.toml's `readme = "README.md"`).
COPY README.md ./

RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime OS deps only (certs for outbound HTTPS to Alpaca/Agnes) -- no compiler toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY src/ ./src/
COPY ui/ ./ui/
COPY eval/ ./eval/
COPY CLAUDE.md ./CLAUDE.md
COPY pyproject.toml ./pyproject.toml
COPY README.md ./README.md

# `pip install -e .` (as the builder stage ran) leaves an editable install pointing at
# /build/src, which doesn't exist in this stage -- reinstall in-place, no deps (already
# satisfied from /install), so `import orderguard` resolves against the copied ./src/.
RUN pip install --no-cache-dir --no-deps -e .

# eval/runs/ is gitignored (holds per-run trajectories/results) but the app still writes
# to it at runtime -- create it so a read-only mount or missing-dir surprise doesn't
# crash a fresh container.
RUN mkdir -p eval/runs/trajectories

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PYTHONUNBUFFERED=1

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
