# ── Stage 1: build wheel ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools
RUN pip install --no-cache-dir hatchling build

# Copy only what's needed to build the wheel
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Build wheel + sdist into dist/
RUN python -m build --wheel --outdir dist/

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="dbt-coverage-lib UI"
LABEL org.opencontainers.image.description="SonarQube-style static analysis + coverage for dbt projects"
LABEL org.opencontainers.image.source="https://github.com/your-org/dbt-coverage-lib"

# Non-root user for security
RUN groupadd --gid 1001 dbtcov && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /sbin/nologin dbtcov

WORKDIR /app

# Copy the built wheel from builder stage
COPY --from=builder /build/dist/*.whl ./dist/

# Install the wheel with UI extras (glob expansion then extras)
RUN WHEEL=$(ls ./dist/dbt_coverage_lib-*.whl | head -1) && \
    pip install --no-cache-dir "${WHEEL}[ui]" && \
    rm -rf ./dist/

# Data directory for the UI (scan results, SQLite DB)
ENV DBTCOV_UI_HOME=/data
RUN mkdir -p /data && chown dbtcov:dbtcov /data

USER dbtcov

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/meta')" || exit 1

CMD ["uvicorn", "dbt_coverage_ui.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
