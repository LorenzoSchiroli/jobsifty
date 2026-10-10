FROM python:3.13

WORKDIR /app

# Worker nightly Postgres backup: pg_dump + rclone → Hetzner Storage Box.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client rclone \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv --quiet

# Models before dependencies: this layer only rebuilds when a model changes,
# not on every uv.lock update. Files land in the default Hugging Face cache,
# where sentence-transformers looks for them at runtime.
RUN uvx --from huggingface_hub hf download intfloat/multilingual-e5-small \
        --include "*.json" --include "*.safetensors" --include "*.model" --exclude "onnx/*" --exclude "openvino/*" \
    && uvx --from huggingface_hub hf download cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 \
        --include "*.json" --include "*.safetensors" --include "*.model" --exclude "onnx/*" --exclude "openvino/*" \
    && uv cache clean

COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/pyproject.toml

RUN uv sync --package backend --no-dev

COPY backend/ ./backend/
RUN chmod +x /app/backend/backend/scripts/postgres_backup.sh

WORKDIR /app/backend

EXPOSE 8000

CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
