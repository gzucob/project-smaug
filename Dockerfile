FROM ghcr.io/astral-sh/uv:0.12.2 AS uv
FROM python:3.13-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-bookworm
ARG APPLICATION_COMMIT=unknown
LABEL org.opencontainers.image.source="https://github.com/gzucob/project-smaug"
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SMAUG_APPLICATION_COMMIT=$APPLICATION_COMMIT
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
RUN mkdir -p /data/sources /data/cache/cvm /data/cache/b3
CMD ["uvicorn", "smaug.entrypoints.api:app", "--host", "0.0.0.0", "--port", "8000"]
