FROM ghcr.io/meta-pytorch/openenv-base:latest

WORKDIR /app/env

COPY pyproject.toml .
COPY uv.lock .

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -f uv.lock ]; then \
      uv sync --frozen --no-install-project --no-editable; \
    else \
      uv sync --no-install-project --no-editable; \
    fi

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-editable

ENV PYTHONPATH="/app/env:$PYTHONPATH"
ENV PATH="/app/env/.venv/bin:$PATH"
ENV WORKERS=1
ENV MAX_CONCURRENT_ENVS=100

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

CMD ["/bin/sh", "-c", "uvicorn incident_ops_env.server.app:app --host 0.0.0.0 --port 7860 --workers ${WORKERS}"]
