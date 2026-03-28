FROM ghcr.io/meta-pytorch/openenv-base:latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/env

COPY pyproject.toml .
COPY uv.lock .

# Plain RUN (no BuildKit cache mounts): some HF Space builders hang or omit logs with --mount=type=cache.
RUN if [ -f uv.lock ]; then \
      uv sync --frozen --no-install-project --no-editable; \
    else \
      uv sync --no-install-project --no-editable; \
    fi

COPY . .

RUN uv sync --no-editable

ENV PYTHONPATH="/app/env:$PYTHONPATH"
ENV PATH="/app/env/.venv/bin:$PATH"
# Uvicorn worker count comes only from the CMD line below (--workers 1). The WORKERS env var is
# not passed to uvicorn, so platform-provided WORKERS cannot spawn extra processes.
ENV WORKERS=1
ENV MAX_CONCURRENT_ENVS=100

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

CMD ["/bin/sh", "-c", "exec uvicorn incident_ops_env.server.app:app --host 0.0.0.0 --port 7860 --workers 1"]
