from unittest.mock import patch, AsyncMock, MagicMock

from backend import bootstrap


async def test_bootstrap_main_runs_init_drop_setup_in_order():
    call_order = []

    async def record_init():
        call_order.append("init_opensearch")

    async def record_setup():
        call_order.append("checkpointer.setup")

    async def record_drop(_client):
        call_order.append("drop_legacy_index")
        return []

    mock_checkpointer = MagicMock()
    mock_checkpointer.setup = AsyncMock(side_effect=record_setup)
    mock_saver_cm = MagicMock()
    mock_saver_cm.__aenter__ = AsyncMock(return_value=mock_checkpointer)
    mock_saver_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("backend.bootstrap.init_opensearch", new=AsyncMock(side_effect=record_init)), \
         patch("backend.bootstrap.get_opensearch", return_value=MagicMock()), \
         patch("backend.bootstrap.drop_legacy_index", new=AsyncMock(side_effect=record_drop)), \
         patch("backend.bootstrap.AsyncPostgresSaver.from_conn_string", return_value=mock_saver_cm):
        await bootstrap.main()

    assert call_order == ["init_opensearch", "drop_legacy_index", "checkpointer.setup"]
