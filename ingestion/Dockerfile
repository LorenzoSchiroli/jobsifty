FROM python:3.13

WORKDIR /app

RUN pip install uv --quiet

COPY pyproject.toml uv.lock ./
COPY ingestion/pyproject.toml ./ingestion/pyproject.toml

RUN uv sync --package ingestion --no-dev

RUN uv run playwright install chromium && \
    uv run playwright install-deps chromium

RUN uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY ingestion/ ./ingestion/

WORKDIR /app/ingestion

CMD ["sh", "-c", "if [ -z \"$OFFER_QUERY\" ]; then echo 'ERROR: OFFER_QUERY is not set' >&2; exit 1; fi; while true; do uv run python -m ingestion.pipeline \"$OFFER_QUERY\" --hours 2; sleep 7200; done"]
