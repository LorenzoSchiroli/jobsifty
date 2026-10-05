import pytest
from unittest.mock import patch, MagicMock
import backend.search.models_lifecycle as m
from backend.search.models_lifecycle import get_biencoder, get_reranker, init_models, encode_query


def test_get_biencoder_raises_before_init():
    m._biencoder = None
    with pytest.raises(AssertionError):
        get_biencoder()


def test_get_reranker_raises_before_init():
    m._reranker = None
    with pytest.raises(AssertionError):
        get_reranker()


def test_init_models_sets_both():
    m._biencoder = None
    m._reranker = None
    mock_st = MagicMock()
    mock_ce = MagicMock()
    with patch("backend.search.models_lifecycle.SentenceTransformer", return_value=mock_st), \
         patch("backend.search.models_lifecycle.CrossEncoder", return_value=mock_ce), \
         patch("backend.search.models_lifecycle.torch.set_num_threads"):
        init_models()
    assert get_biencoder() is mock_st
    assert get_reranker() is mock_ce


def test_init_models_uses_e5_reranker_env_and_threads(monkeypatch):
    monkeypatch.setenv("RERANKER_MODEL", "some/reranker")
    monkeypatch.setenv("TORCH_NUM_THREADS", "3")
    with patch("backend.search.models_lifecycle.SentenceTransformer") as st, \
         patch("backend.search.models_lifecycle.CrossEncoder") as ce, \
         patch("backend.search.models_lifecycle.torch.set_num_threads") as threads:
        init_models()
    st.assert_called_once_with("intfloat/multilingual-e5-small")
    ce.assert_called_once_with("some/reranker", max_length=256)
    threads.assert_called_once_with(3)


def test_encode_query_adds_e5_prefix_and_normalizes():
    biencoder = MagicMock()
    biencoder.encode.return_value.tolist.return_value = [0.1, 0.2]
    assert encode_query(biencoder, "ml engineer") == [0.1, 0.2]
    biencoder.encode.assert_called_once_with("query: ml engineer", normalize_embeddings=True)
