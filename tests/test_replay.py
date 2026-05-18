import asyncio
import json
import logging
import sys
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mcp_vcr.replay import ReplayEngine
from mcp_vcr.schema import Direction
from mcp_vcr.validator import validate_transcript

# Helper to create a dummy python server script
DUMMY_SERVER_CODE = """
import sys
import json
import time

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        method = data.get("method")
        msg_id = data.get("id")
        
        if msg_id is not None:
            if method == "slow":
                time.sleep(2)
            elif method == "crash":
                sys.exit(42)
            elif method == "malformed":
                sys.stdout.write("not-a-json\\n")
                sys.stdout.flush()
                continue
            elif method == "non_utf8":
                sys.stdout.buffer.write(b"\\xff\\xfe\\xfd\\n")
                sys.stdout.buffer.flush()
                continue
            elif method == "async_notif":
                notif = {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progress": 50}
                }
                sys.stdout.write(json.dumps(notif) + "\\n")
                sys.stdout.flush()
                
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"method_echo": method, "status": "ok"}
            }
            sys.stdout.write(json.dumps(resp) + "\\n")
            sys.stdout.flush()
    except Exception as e:
        sys.stderr.write(f"Error in dummy server: {e}\\n")
        sys.stderr.flush()
"""

@pytest.fixture
def dummy_server_path(tmp_path):
    path = tmp_path / "dummy_server.py"
    path.write_text(DUMMY_SERVER_CODE, encoding="utf-8")
    return path

@pytest.fixture
def sample_transcript_path(tmp_path):
    transcript_yaml = """meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "dummy.py"]
  schema_version: "1.0"
  protocol_version: "2024-11-05"
  client_hint: "TestClient"
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 1
      method: "initialize"
  - t: 10
    dir: s2c
    payload:
      jsonrpc: "2.0"
      id: 1
      result:
        status: "ok"
  - t: 20
    dir: c2s
    payload:
      jsonrpc: "2.0"
      method: "notifications/initialized"
  - t: 30
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 2
      method: "tools/list"
  - t: 40
    dir: s2c
    payload:
      jsonrpc: "2.0"
      id: 2
      result:
        tools: []
"""
    path = tmp_path / "session_abcdef12.yaml"
    path.write_text(transcript_yaml, encoding="utf-8")
    return path

@pytest.mark.asyncio
async def test_replay_successful_integration(sample_transcript_path, dummy_server_path):
    """Verify that a successful replay correctly communicates with subprocess and generates output transcript."""
    engine = ReplayEngine(settle_delay_ms=10)
    server_args = [sys.executable, str(dummy_server_path)]
    
    output_path = await engine.run_replay(sample_transcript_path, server_args=server_args)
    
    # 1. Output file must exist
    assert output_path.exists()
    assert output_path.stem.startswith("session_abcdef12-replay-")
    
    # 2. Schema validation must succeed
    errors = validate_transcript(output_path)
    assert not errors, f"Output transcript failed validation: {errors}"
    
    # 3. Content assertions
    with open(output_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    meta = data["meta"]
    assert meta["version"] == 1
    assert meta["client_hint"] == "TestClient"
    assert meta["protocol_version"] == "2024-11-05"
    assert "session_id" in meta
    assert "-replay-" in meta["session_id"]
    assert "incomplete" not in meta
    
    messages = data["messages"]
    # Replay output should contain only captured s2c messages
    # In original: initialize response (id=1), tools/list response (id=2). (The notification has no response).
    assert len(messages) == 2
    for msg in messages:
        assert msg["dir"] == "s2c"
        assert msg["t"] >= 0
        assert "id" in msg["payload"]
        assert msg["payload"]["result"]["status"] == "ok"

@pytest.mark.asyncio
async def test_replay_notification_settle_delay(sample_transcript_path, dummy_server_path):
    """Verify that notifications trigger settle delay without waiting for a server response."""
    engine = ReplayEngine(settle_delay_ms=150)
    server_args = [sys.executable, str(dummy_server_path)]
    
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await engine.run_replay(sample_transcript_path, server_args=server_args)
        
        # Verify settle delay of 150ms (0.15s) was applied
        mock_sleep.assert_any_call(0.15)

@pytest.mark.asyncio
async def test_replay_request_timeout(tmp_path, dummy_server_path):
    """Verify that slow requests trigger a timeout, resulting in incomplete meta and partial transcript."""
    transcript_yaml = """meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef34"
  server_command: ["python", "dummy.py"]
  schema_version: "1.0"
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 1
      method: "slow"
  - t: 10
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 2
      method: "initialize"
"""
    t_path = tmp_path / "session_timeout1.yaml"
    t_path.write_text(transcript_yaml, encoding="utf-8")
    
    # Configure short timeout of 100ms (0.1s)
    engine = ReplayEngine(timeout_ms=100)
    server_args = [sys.executable, str(dummy_server_path)]
    
    output_path = await engine.run_replay(t_path, server_args=server_args)
    
    assert output_path.exists()
    
    # Validate the resulting incomplete transcript
    errors = validate_transcript(output_path)
    assert not errors, f"Incomplete output transcript failed validation: {errors}"
    
    with open(output_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    meta = data["meta"]
    assert meta["incomplete"] is True
    assert meta["incomplete_reason"] == "timeout"
    
    # Replay terminates on first timeout, so no s2c messages should be saved
    assert len(data["messages"]) == 0

@pytest.mark.asyncio
async def test_replay_server_crash(tmp_path, dummy_server_path):
    """Verify that a server crash/exit is detected as server_crash with incomplete status."""
    transcript_yaml = """meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef56"
  server_command: ["python", "dummy.py"]
  schema_version: "1.0"
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 1
      method: "crash"
"""
    t_path = tmp_path / "session_crash123.yaml"
    t_path.write_text(transcript_yaml, encoding="utf-8")
    
    engine = ReplayEngine()
    server_args = [sys.executable, str(dummy_server_path)]
    
    output_path = await engine.run_replay(t_path, server_args=server_args)
    
    assert output_path.exists()
    
    with open(output_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    meta = data["meta"]
    assert meta["incomplete"] is True
    assert meta["incomplete_reason"] == "server_crash"

def test_replay_engine_config_loading(tmp_path):
    """Verify ReplayEngine config loading from custom file path and default behavior."""
    # Write a custom .mcp-vcr.yaml config
    config_data = {
        "replay": {
            "timeout_ms": 2500,
            "settle_delay_ms": 120
        }
    }
    config_file = tmp_path / ".mcp-vcr.yaml"
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    
    # 1. Load from the custom config path
    engine1 = ReplayEngine(config_path=config_file)
    assert engine1.timeout_ms == 2500
    assert engine1.settle_delay_ms == 120
    
    # 2. Overrides take precedence
    engine2 = ReplayEngine(config_path=config_file, timeout_ms=900, settle_delay_ms=30)
    assert engine2.timeout_ms == 900
    assert engine2.settle_delay_ms == 30
    
    # 3. Fallback to defaults when config doesn't exist
    non_existent = tmp_path / "missing.yaml"
    engine3 = ReplayEngine(config_path=non_existent)
    assert engine3.timeout_ms == 5000
    assert engine3.settle_delay_ms == 50

@pytest.mark.asyncio
async def test_replay_async_notification_captured(tmp_path, dummy_server_path):
    """Verify that asynchronous notifications emitted by the server before the matching response are captured."""
    transcript_yaml = """meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef78"
  server_command: ["python", "dummy.py"]
  schema_version: "1.0"
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 10
      method: "async_notif"
"""
    t_path = tmp_path / "session_async_notif.yaml"
    t_path.write_text(transcript_yaml, encoding="utf-8")
    
    engine = ReplayEngine()
    server_args = [sys.executable, str(dummy_server_path)]
    
    output_path = await engine.run_replay(t_path, server_args=server_args)
    
    assert output_path.exists()
    errors = validate_transcript(output_path)
    assert not errors, f"Output transcript failed validation: {errors}"
    
    with open(output_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    messages = data["messages"]
    # Replay output should capture two s2c messages:
    # 1. The progress notification (no "id")
    # 2. The method response (id: 10)
    assert len(messages) == 2
    
    notif_msg = messages[0]
    assert notif_msg["dir"] == "s2c"
    assert "id" not in notif_msg["payload"]
    assert notif_msg["payload"]["method"] == "notifications/progress"
    
    resp_msg = messages[1]
    assert resp_msg["dir"] == "s2c"
    assert resp_msg["payload"]["id"] == 10
    assert resp_msg["payload"]["result"]["method_echo"] == "async_notif"

@pytest.mark.asyncio
async def test_replay_malformed_response(tmp_path, dummy_server_path):
    """Verify that a malformed/non-JSON response from the server is captured as 'malformed_response'."""
    transcript_yaml = """meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef99"
  server_command: ["python", "dummy.py"]
  schema_version: "1.0"
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 1
      method: "malformed"
"""
    t_path = tmp_path / "session_malformed.yaml"
    t_path.write_text(transcript_yaml, encoding="utf-8")
    
    engine = ReplayEngine()
    server_args = [sys.executable, str(dummy_server_path)]
    
    output_path = await engine.run_replay(t_path, server_args=server_args)
    
    assert output_path.exists()
    errors = validate_transcript(output_path)
    assert not errors, f"Output transcript failed validation: {errors}"
    
    with open(output_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    meta = data["meta"]
    assert meta["incomplete"] is True
    assert meta["incomplete_reason"] == "malformed_response"


