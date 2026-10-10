FROM python:3.13

WORKDIR /app

RUN pip install uv --quiet

# Model before dependencies: this layer only rebuilds when the model changes,
# not on every uv.lock update. Files land in the default Hugging Face cache,
# where sentence-transformers looks for them at runtime.
RUN uvx --from huggingface_hub hf download intfloat/multilingual-e5-small \
        --include "*.json" --include "*.safetensors" --include "*.model" --exclude "onnx/*" --exclude "openvino/*" \
    && uv cache clean

COPY pyproject.toml uv.lock ./
COPY ingestion/pyproject.toml ./ingestion/pyproject.toml

RUN uv sync --package ingestion --no-dev

RUN uv run playwright install chromium && \
    uv run playwright install-deps chromium

COPY ingestion/ ./ingestion/

WORKDIR /app/ingestion

CMD ["sh", "-c", "if [ -z \"$OFFER_QUERY\" ]; then echo 'ERROR: OFFER_QUERY is not set' >&2; exit 1; fi; while true; do uv run python -m ingestion.pipeline \"$OFFER_QUERY\" --hours 2; sleep 7200; done"]
