import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from mcp_vcr.transports.base import Transport
from pytest_mcp_vcr.plugin import vcr_cassette, RecordingTransport, ReplayingTransport

@pytest.mark.asyncio
async def test_vcr_cassette_record(tmp_path):
    cassette_path = tmp_path / "cassette.yaml"
    
    mock_inner = MagicMock(spec=Transport)
    mock_inner.start = AsyncMock()
    mock_inner.shutdown = AsyncMock()
    mock_inner.write_to_server = AsyncMock()
    mock_inner.read_server_message = AsyncMock(return_value=b'{"jsonrpc": "2.0", "id": 42, "result": "hello"}')
    
    async with vcr_cassette(cassette_path, mode="record", inner_transport=mock_inner) as transport:
        assert isinstance(transport, RecordingTransport)
        await transport.write_to_server(b'{"jsonrpc": "2.0", "id": 42, "method": "ping"}')
        resp = await transport.read_server_message()
        assert resp == b'{"jsonrpc": "2.0", "id": 42, "result": "hello"}'
        
    assert cassette_path.exists()
    import yaml
    with open(cassette_path, "r") as f:
        data = yaml.safe_load(f)
    assert data["meta"]["version"] == 1
    messages = data["messages"]
    assert len(messages) == 2
    assert messages[0]["dir"] == "c2s"
    assert messages[0]["payload"]["method"] == "ping"
    assert messages[1]["dir"] == "s2c"
    assert messages[1]["payload"]["result"] == "hello"

@pytest.mark.asyncio
async def test_vcr_cassette_replay(tmp_path):
    cassette_path = tmp_path / "cassette_replay.yaml"
    cassette_yaml = """
meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef33"
  server_command: ["python"]
messages:
  - t: 0
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 100, "method": "test"}
  - t: 5
    dir: "s2c"
    payload: {"jsonrpc": "2.0", "id": 100, "result": "success_result"}
  - t: 10
    dir: "s2c"
    payload: {"jsonrpc": "2.0", "method": "my_notification"}
"""
    with open(cassette_path, "w", encoding="utf-8") as f:
        f.write(cassette_yaml)
        
    async with vcr_cassette(cassette_path, mode="replay") as transport:
        assert isinstance(transport, ReplayingTransport)
        await transport.write_to_server(b'{"jsonrpc": "2.0", "id": 100, "method": "test"}')
        
        resp = await transport.read_server_message()
        assert resp is not None
        payload = json.loads(resp.decode("utf-8"))
        assert payload["id"] == 100
        assert payload["result"] == "success_result"
        
        notif = await transport.read_server_message()
        assert notif is not None
        payload_notif = json.loads(notif.decode("utf-8"))
        assert "id" not in payload_notif
        assert payload_notif["method"] == "my_notification"

@pytest.mark.asyncio
async def test_vcr_cassette_replay_pipeline_and_id_reuse(tmp_path):
    cassette_path = tmp_path / "cassette_pipeline.yaml"
    cassette_yaml = """
meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef33"
  server_command: ["python"]
messages:
  - t: 0
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 1, "method": "foo"}
  - t: 5
    dir: "s2c"
    payload: {"jsonrpc": "2.0", "id": 1, "result": "foo_res"}
  - t: 10
    dir: "s2c"
    payload: {"jsonrpc": "2.0", "method": "notif"}
  - t: 15
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 1, "method": "bar"}
  - t: 20
    dir: "s2c"
    payload: {"jsonrpc": "2.0", "id": 1, "result": "bar_res"}
"""
    with open(cassette_path, "w", encoding="utf-8") as f:
        f.write(cassette_yaml)

    async with vcr_cassette(cassette_path, mode="replay") as transport:
        # Pipelined requests (write both before reading)
        await transport.write_to_server(b'{"jsonrpc": "2.0", "id": 42, "method": "foo"}')
        await transport.write_to_server(b'{"jsonrpc": "2.0", "id": 43, "method": "bar"}')

        # Read first response (ID 42 mapped from 1)
        resp1 = await transport.read_server_message()
        assert resp1 is not None
        payload1 = json.loads(resp1.decode("utf-8"))
        assert payload1["id"] == 42
        assert payload1["result"] == "foo_res"

        # Read notification (no ID)
        notif = await transport.read_server_message()
        assert notif is not None
        payload_notif = json.loads(notif.decode("utf-8"))
        assert payload_notif["method"] == "notif"

        # Read second response (ID 43 mapped from 1)
        resp2 = await transport.read_server_message()
        assert resp2 is not None
        payload2 = json.loads(resp2.decode("utf-8"))
        assert payload2["id"] == 43
        assert payload2["result"] == "bar_res"
