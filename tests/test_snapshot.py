import json
import sys
import yaml
from pathlib import Path
import pytest
from mcp_vcr.snapshot import (
    run_snapshot,
    run_verify,
    find_source_session,
    normalize_transcript_data
)
from mcp_vcr.validator import validate_file

DUMMY_SERVER_CODE = """
import sys
import json
from pathlib import Path

# Load tools from local tools.json
tools_path = Path("tools.json")
if tools_path.exists():
    with open(tools_path, "r", encoding="utf-8") as f:
        tools = json.load(f)
else:
    tools = ["toolA"]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        method = data.get("method")
        msg_id = data.get("id")
        
        if msg_id is not None:
            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "test-server", "version": "1.0.0"}
                    }
                }
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [{"name": name, "description": "A tool"} for name in tools]
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"echo": method}
                }
            sys.stdout.write(json.dumps(resp) + "\\n")
            sys.stdout.flush()
    except Exception as e:
        sys.stderr.write(f"Error in dummy server: {e}\\n")
        sys.stderr.flush()
"""

@pytest.fixture
def regression_server_setup(tmp_path, monkeypatch):
    """Sets up tools.json and the regression server in a temporary directory."""
    monkeypatch.chdir(tmp_path)
    
    server_path = tmp_path / "server.py"
    server_path.write_text(DUMMY_SERVER_CODE, encoding="utf-8")
    
    tools_path = tmp_path / "tools.json"
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolA"], f)
        
    return server_path, tools_path

def test_run_snapshot_default_and_custom_naming(tmp_path, monkeypatch):
    """Verify that default and named transcripts generate correctly named goldens."""
    monkeypatch.chdir(tmp_path)
    
    # Create sessions/ dir
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    
    transcript_data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "abcdef12",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": [
            {"t": 0, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
            {"t": 10, "dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}}
        ]
    }
    
    # 1. Default Generated Name
    default_file = sessions_dir / "session_20260518_120000_abcdef12.yaml"
    with open(default_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(transcript_data, f)
        
    golden_default = run_snapshot(default_file)
    assert golden_default.name == "abcdef12_golden.yaml"
    assert golden_default.exists()
    validate_file(golden_default)
    
    # 2. Custom Named Session
    custom_file = sessions_dir / "my_session.yaml"
    with open(custom_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(transcript_data, f)
        
    golden_custom = run_snapshot(custom_file)
    assert golden_custom.name == "my_session_golden.yaml"
    assert golden_custom.exists()
    validate_file(golden_custom)

def test_run_snapshot_idempotency(tmp_path, monkeypatch):
    """Verify that running snapshot twice produces identical goldens."""
    monkeypatch.chdir(tmp_path)
    
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    
    transcript_data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "my_session",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": [
            {"t": 0, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
            {"t": 10, "dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}}
        ]
    }
    
    custom_file = sessions_dir / "my_session.yaml"
    with open(custom_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(transcript_data, f)
        
    g1 = run_snapshot(custom_file)
    g1_content = g1.read_text(encoding="utf-8")
    
    g2 = run_snapshot(custom_file)
    g2_content = g2.read_text(encoding="utf-8")
    
    assert g1_content == g2_content

@pytest.mark.asyncio
async def test_regression_testing_pass_fail_update(regression_server_setup, tmp_path):
    """Verify verify/update lifecycle: passes, fails on regression, updates golden snapshot."""
    server_path, tools_path = regression_server_setup
    
    # Create original recorded transcript in sessions/
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    
    transcript_data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "abcdef12",
            "server_command": [sys.executable, str(server_path)],
            "schema_version": "1.0"
        },
        "messages": [
            {"t": 0, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
            {"t": 10, "dir": "s2c", "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test-server", "version": "1.0.0"}
                }
            }},
            {"t": 20, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
            {"t": 30, "dir": "s2c", "payload": {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [{"name": "toolA", "description": "A tool"}]
                }
            }}
        ]
    }
    
    source_file = sessions_dir / "my_session.yaml"
    with open(source_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(transcript_data, f)
        
    # Create the golden snapshot
    golden_path = run_snapshot(source_file)
    assert golden_path.exists()
    
    snapshots_dir = tmp_path / "snapshots"
    
    # 1. Verification Pass (server is unchanged)
    exit_pass = await run_verify(snapshots_dir, server_args=[sys.executable, str(server_path)])
    assert exit_pass == 0
    
    # 2. Verification Fail/Regression (server tools change from toolA -> toolB)
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolB"], f)
        
    exit_fail = await run_verify(snapshots_dir, server_args=[sys.executable, str(server_path)])
    assert exit_fail == 1
    
    # 3. Verification Update (updates the snapshot, exits 0)
    exit_update = await run_verify(snapshots_dir, server_args=[sys.executable, str(server_path)], update=True)
    assert exit_update == 0
    
    # Verify golden file is updated to toolB
    with open(golden_path, "r", encoding="utf-8") as f:
        updated_data = yaml.safe_load(f)
    
    messages = updated_data["messages"]
    tools_response = next(
        (
            m for m in messages
            if m.get("dir") == "s2c"
            and "tools" in m.get("payload", {}).get("result", {})
        ),
        None,
    )
    assert tools_response is not None, "Expected tools/list server response in updated snapshot"
    assert tools_response["payload"]["result"]["tools"][0]["name"] == "toolB"
    
    # 4. Verification Pass again (since snapshot now matches)
    exit_pass_2 = await run_verify(snapshots_dir, server_args=[sys.executable, str(server_path)])
    assert exit_pass_2 == 0

    # Ensure no temporary replay artifacts remain in snapshots_dir
    leftovers = list(snapshots_dir.glob("*-replay-*.yaml")) + list(snapshots_dir.glob("*_normalized.yaml"))
    assert leftovers == []

@pytest.mark.asyncio
async def test_run_verify_missing_source_fallback(regression_server_setup, tmp_path):
    """Verify that verify mode falls back to replaying the golden itself when original source is missing."""
    server_path, _ = regression_server_setup
    
    # We do NOT create anything in sessions/
    # Instead, we directly create the golden snapshot in snapshots/
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    
    transcript_data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "abcdef12",
            "server_command": [sys.executable, str(server_path)],
            "schema_version": "1.0"
        },
        "messages": [
            {"t": 0, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
            {"t": 10, "dir": "s2c", "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test-server", "version": "1.0.0"}
                }
            }},
            {"t": 20, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
            {"t": 30, "dir": "s2c", "payload": {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [{"name": "toolA", "description": "A tool"}]
                }
            }}
        ]
    }
    
    golden_path = snapshots_dir / "my_session_golden.yaml"
    normalized_data = normalize_transcript_data(transcript_data)
    with open(golden_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(normalized_data, f)
        
    # Verify that find_source_session indeed returns None because sessions/ is empty
    source = find_source_session(golden_path)
    assert source is None
    
    # Run verify and ensure it gracefully falls back, replays successfully, and passes (exit code 0)
    exit_code = await run_verify(snapshots_dir, server_args=[sys.executable, str(server_path)])
    assert exit_code == 0

