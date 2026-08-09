import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from mcp_vcr.replay import ReplayEngine

@pytest.mark.asyncio
async def test_timing_faithful_delays(tmp_path):
    # msg 1: t = 100
    # msg 2: t = 250
    # msg 3: t = 300
    transcript_content = """
meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef77"
  server_command: ["python"]
messages:
  - t: 100
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 1, "method": "ping"}
  - t: 250
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 2, "method": "ping"}
  - t: 300
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 3, "method": "ping"}
"""
    session_file = tmp_path / "session_timing.yaml"
    with open(session_file, "w", encoding="utf-8") as f:
        f.write(transcript_content)

    engine = ReplayEngine(timing_faithful=True, timeout_ms=50)

    from mcp_vcr.transports import Transport
    mock_transport = MagicMock(spec=Transport)
    mock_transport.start = AsyncMock()
    mock_transport.shutdown = AsyncMock()
    mock_transport.write_to_server = AsyncMock()
    mock_transport.read_server_message = AsyncMock(side_effect=[
        b'{"jsonrpc": "2.0", "id": 1, "result": "pong"}\n',
        b'{"jsonrpc": "2.0", "id": 2, "result": "pong"}\n',
        b'{"jsonrpc": "2.0", "id": 3, "result": "pong"}\n'
    ])

    current_time = 0.0
    def mock_time():
        return current_time

    async def mock_sleep_impl(delay):
        nonlocal current_time
        current_time += delay

    with patch("time.monotonic", side_effect=mock_time), \
         patch("asyncio.sleep", side_effect=mock_sleep_impl) as mock_sleep:
        output_path = await engine.run_replay(session_file, transport=mock_transport)
        
        # Delays expected:
        # First message: 100ms delta -> sleep(0.1)
        # Second message: 150ms delta -> sleep(0.15)
        # Third message: 50ms delta -> sleep(0.05)
        assert mock_sleep.call_count == 3
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        assert pytest.approx(calls[0], 0.001) == 0.1
        assert pytest.approx(calls[1], 0.001) == 0.15
        assert pytest.approx(calls[2], 0.001) == 0.05
