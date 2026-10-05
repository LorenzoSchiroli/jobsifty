import re
from unittest.mock import MagicMock, patch

import numpy as np

from ingestion import embedder
from ingestion.embedder import embed_chunks, _windows


def _fake_tokenizer(text, **kwargs):
    return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}


def _fake_model():
    model = MagicMock()
    model.tokenizer = _fake_tokenizer
    model.encode.side_effect = lambda texts, normalize_embeddings: np.full((len(texts), 384), 0.1)
    return model


def test_windows_short_text_is_one_window():
    assert _windows("a b c", _fake_tokenizer) == ["a b c"]


def test_windows_overlap_and_cap():
    words = [f"w{i}" for i in range(2000)]
    windows = _windows(" ".join(words), _fake_tokenizer)
    assert len(windows) == embedder.MAX_CHUNKS
    first, second = windows[0].split(), windows[1].split()
    assert len(first) == embedder.WINDOW_TOKENS
    assert second[0] == words[embedder.WINDOW_TOKENS - embedder.OVERLAP_TOKENS]


def test_windows_empty():
    assert _windows("   ", _fake_tokenizer) == []


def test_embed_chunks_prefixes_title_and_passage():
    model = _fake_model()
    with patch("ingestion.embedder.get_embedder", return_value=model):
        chunks = embed_chunks("ML Engineer", "Train models in Python")
    assert chunks[0]["text"] == "ML Engineer\nTrain models in Python"
    assert len(chunks[0]["embedding"]) == 384
    assert model.encode.call_args.args[0] == ["passage: ML Engineer\nTrain models in Python"]
    assert model.encode.call_args.kwargs["normalize_embeddings"] is True


def test_embed_chunks_title_only_when_no_text():
    with patch("ingestion.embedder.get_embedder", return_value=_fake_model()):
        chunks = embed_chunks("ML Engineer", None)
    assert [c["text"] for c in chunks] == ["ML Engineer"]


def test_embed_chunks_empty_when_nothing():
    with patch("ingestion.embedder.get_embedder", return_value=_fake_model()):
        assert embed_chunks("", "") == []


def test_embed_chunks_multiple_windows():
    text = " ".join(f"w{i}" for i in range(900))
    with patch("ingestion.embedder.get_embedder", return_value=_fake_model()):
        chunks = embed_chunks("T", text)
    assert len(chunks) == 3
    assert all(c["text"].startswith("T\n") for c in chunks)
