import os

import torch
from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder

# Must match the model ingestion embeds job chunks with (ingestion/embedder.py).
EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANKER_MAX_LENGTH = 256

_biencoder: SentenceTransformer | None = None
_reranker: CrossEncoder | None = None


def init_models() -> None:
    global _biencoder, _reranker
    # Torch defaults to one thread per node core; under a pod CPU limit that
    # gets throttled, so pin it to the limit.
    torch.set_num_threads(int(os.environ.get("TORCH_NUM_THREADS", "2")))
    _biencoder = SentenceTransformer(EMBEDDING_MODEL)
    _reranker = CrossEncoder(
        os.environ.get("RERANKER_MODEL", DEFAULT_RERANKER_MODEL),
        max_length=RERANKER_MAX_LENGTH,
    )


def get_biencoder() -> SentenceTransformer:
    assert _biencoder is not None, "models not initialized"
    return _biencoder


def get_reranker() -> CrossEncoder:
    assert _reranker is not None, "models not initialized"
    return _reranker


def encode_query(biencoder: SentenceTransformer, text: str) -> list[float]:
    # e5 models are trained with "query: " / "passage: " prefixes.
    return biencoder.encode(f"query: {text}", normalize_embeddings=True).tolist()
