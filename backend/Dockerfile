FROM python:3.13

WORKDIR /app

# Worker nightly Postgres backup: pg_dump + rclone → Hetzner Storage Box.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client rclone \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv --quiet

COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/pyproject.toml

RUN uv sync --package backend --no-dev

RUN uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"
RUN uv run python -c "from sentence_transformers.cross_encoder import CrossEncoder; CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"

COPY backend/ ./backend/
RUN chmod +x /app/backend/backend/scripts/postgres_backup.sh

WORKDIR /app/backend

EXPOSE 8000

CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
