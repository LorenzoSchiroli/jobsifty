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

COPY llmpool/ ./llmpool/

RUN uv sync --package ingestion --no-dev

RUN uv run playwright install chromium && \
    uv run playwright install-deps chromium

COPY ingestion/ ./ingestion/

WORKDIR /app/ingestion

# Compose runs this loop (k8s and ECS override the command). INGESTION_INTERVAL_HOURS
# sets the pause between runs and, through --interval-hours, the run's own timing;
# OFFER_SCOPE is what to search for (ingestion/offer/scraping/scopes.py).
ENV INGESTION_INTERVAL_HOURS=2 OFFER_SCOPE=ai
CMD ["sh", "-c", "while true; do timeout $((INGESTION_INTERVAL_HOURS * 3600 - 600)) uv run python -m ingestion.pipeline \"$OFFER_SCOPE\" --interval-hours $INGESTION_INTERVAL_HOURS; sleep $((INGESTION_INTERVAL_HOURS * 3600)); done"]
