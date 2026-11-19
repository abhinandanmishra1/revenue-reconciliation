FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.8.17
WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 10001 app
USER app

ENV PATH="/app/.venv/bin:$PATH" PORT=8000
EXPOSE 8000

# One API instance: apply migrations, then serve. The app reads X-Forwarded-For
# itself (TRUSTED_PROXY_HOPS), so uvicorn's own proxy-header rewriting is off.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-proxy-headers"]
