import logging
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"
# The backend encodes queries with the same model and a "query: " prefix.
PASSAGE_PREFIX = "passage: "

# Token windows over the description; with the title and prefix they stay
# under the model's 512-token limit.
WINDOW_TOKENS = 400
OVERLAP_TOKENS = 64
MAX_CHUNKS = 4

_model: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model (%s)...", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        _model.max_seq_length = 512
        logger.info("Embedding model ready.")
    return _model


def _windows(text: str, tokenizer) -> list[str]:
    """Split text into overlapping token windows, sliced from the original string."""
    if not text or not text.strip():
        return []
    # verbose=False: only offsets are needed, so the >512-token warning is noise here.
    enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, verbose=False)
    offsets = enc["offset_mapping"]
    if not offsets:
        return []
    step = WINDOW_TOKENS - OVERLAP_TOKENS
    windows: list[str] = []
    for start in range(0, len(offsets), step):
        span = offsets[start:start + WINDOW_TOKENS]
        windows.append(text[span[0][0]:span[-1][1]].strip())
        if start + WINDOW_TOKENS >= len(offsets) or len(windows) == MAX_CHUNKS:
            break
    return [w for w in windows if w]


def embed_chunks(title: str, text: str | None) -> list[dict]:
    """Embed `title + window` for each token window of `text`.

    Returns [{"text", "embedding"}]; a title-only chunk when there is no text,
    and [] only when both are empty.
    """
    model = get_embedder()
    windows = _windows(text or "", model.tokenizer) or [""]
    chunk_texts = [f"{title}\n{w}".strip() for w in windows]
    chunk_texts = [t for t in chunk_texts if t]
    if not chunk_texts:
        return []
    vectors = model.encode([PASSAGE_PREFIX + t for t in chunk_texts], normalize_embeddings=True)
    return [{"text": t, "embedding": [float(x) for x in v]} for t, v in zip(chunk_texts, vectors)]
