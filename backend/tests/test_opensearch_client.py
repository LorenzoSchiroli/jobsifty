import pytest
from unittest.mock import AsyncMock, patch
import backend.opensearch_client as m
from backend.opensearch_client import get_existing_job_ids, drop_legacy_index, INDEX_NAME, LEGACY_INDEX_NAME


async def test_init_creates_index_when_missing(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_URL", "http://localhost:9200")
    mock_client = AsyncMock()
    mock_client.indices.exists.return_value = False
    with patch("backend.opensearch_client.AsyncOpenSearch", return_value=mock_client):
        m._client = None
        await m.init_opensearch()
    mock_client.indices.create.assert_called_once()
    mock_client.transport.perform_request.assert_called_once()


async def test_init_skips_index_creation_when_exists(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_URL", "http://localhost:9200")
    mock_client = AsyncMock()
    mock_client.indices.exists.return_value = True
    with patch("backend.opensearch_client.AsyncOpenSearch", return_value=mock_client):
        m._client = None
        await m.init_opensearch()
    mock_client.indices.create.assert_not_called()


def test_get_opensearch_raises_before_init():
    m._client = None
    with pytest.raises(AssertionError):
        m.get_opensearch()


async def test_get_existing_job_ids_returns_only_found():
    os_client = AsyncMock()
    os_client.mget.return_value = {"docs": [
        {"_id": "a", "found": True},
        {"_id": "b", "found": False},
        {"_id": "c", "found": True},
    ]}
    result = await get_existing_job_ids(os_client, ["a", "b", "c"])
    assert result == {"a", "c"}


async def test_get_existing_job_ids_empty_skips_call():
    os_client = AsyncMock()
    result = await get_existing_job_ids(os_client, [])
    assert result == set()
    os_client.mget.assert_not_called()


async def test_get_existing_job_ids_chunks_large_input():
    os_client = AsyncMock()
    os_client.mget.return_value = {"docs": []}
    ids = [str(i) for i in range(12000)]
    await get_existing_job_ids(os_client, ids, chunk_size=5000)
    assert os_client.mget.call_count == 3


async def test_init_passes_basic_auth_when_user_password_set(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_URL", "https://search.example.com")
    monkeypatch.setenv("OPENSEARCH_USER", "admin")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "secret")
    mock_client = AsyncMock()
    mock_client.indices.exists.return_value = True
    with patch("backend.opensearch_client.AsyncOpenSearch", return_value=mock_client) as ctor:
        m._client = None
        await m.init_opensearch()
    kwargs = ctor.call_args.kwargs
    assert kwargs.get("http_auth") == ("admin", "secret")
    assert kwargs.get("use_ssl") is True
    assert kwargs.get("verify_certs") is True


async def test_init_plain_http_without_auth_env(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_URL", "http://localhost:9200")
    monkeypatch.delenv("OPENSEARCH_USER", raising=False)
    monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)
    mock_client = AsyncMock()
    mock_client.indices.exists.return_value = True
    with patch("backend.opensearch_client.AsyncOpenSearch", return_value=mock_client) as ctor:
        m._client = None
        await m.init_opensearch()
    kwargs = ctor.call_args.kwargs
    assert "http_auth" not in kwargs or kwargs.get("http_auth") is None


def test_index_maps_chunks_as_nested_knn():
    props = m._INDEX_BODY["mappings"]["properties"]
    assert "embedding" not in props
    chunks = props["chunks"]
    assert chunks["type"] == "nested"
    assert chunks["properties"]["embedding"]["type"] == "knn_vector"
    assert chunks["properties"]["embedding"]["dimension"] == 384
    assert INDEX_NAME != LEGACY_INDEX_NAME


async def test_drop_legacy_index_deletes_when_present():
    os_client = AsyncMock()
    os_client.indices.exists.return_value = True
    assert await drop_legacy_index(os_client) is True
    os_client.indices.delete.assert_called_once_with(index=LEGACY_INDEX_NAME)


async def test_drop_legacy_index_noop_when_absent():
    os_client = AsyncMock()
    os_client.indices.exists.return_value = False
    assert await drop_legacy_index(os_client) is False
    os_client.indices.delete.assert_not_called()
