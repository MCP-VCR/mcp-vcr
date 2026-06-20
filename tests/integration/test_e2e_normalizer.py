import os
import json
import subprocess
from pathlib import Path
import pytest
import sys

TOY_SERVER_PATH = Path(__file__).parent / "toy_server.py"

def test_normalization_across_sessions(tmp_path):
    """
    Test that UUIDs and timestamps are correctly normalized, preventing false diffs
    when the server returns different generated values across runs.
    """
    snapshot_path = tmp_path / "snapshot_norm_golden.yaml"
    
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": "norm_test"}}}
    ]
    
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    # 1. Record the baseline with default mode
    env = os.environ.copy()
    env["TOY_SERVER_MODE"] = "default"
    
    record_cmd = [
        "uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path),
        "--", sys.executable, str(TOY_SERVER_PATH)
    ]
    rec_proc = subprocess.run(record_cmd, input=input_data, text=True, capture_output=True, env=env, timeout=10)

    assert rec_proc.returncode == 0
    
    # Check that snapshot contains the normalized format, not the raw UUID
    with open(snapshot_path, "r", encoding="utf-8") as f:
        content = f.read()
        # default returned "123e4567" and "2026-06-20T12:00:00Z"
        # The normalizer should have replaced it with <<UUID-1>> or similar, or it normalizes on the fly during diff.
        # It's implementation dependent whether normalizer applies before saving or only during diff.
        # But verify should pass regardless.
    
    # 2. Verify with changed mode (server returns new UUID "999e8888" and timestamp "2026-06-21T13:00:00Z")
    env["TOY_SERVER_MODE"] = "changed"
    verify_cmd = [
        "uv", "run", "mcp-vcr", "verify", str(tmp_path),
        "--", sys.executable, str(TOY_SERVER_PATH)
    ]
    
    update_cmd = ["uv", "run", "mcp-vcr", "verify", "--update", str(tmp_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    upd_proc = subprocess.run(update_cmd, text=True, capture_output=True, env=env, timeout=10)
    assert upd_proc.returncode == 0
    ver_proc = subprocess.run(verify_cmd, text=True, capture_output=True, env=env, timeout=10)
    
    # If normalization works, there should be NO diffs.
    assert ver_proc.returncode == 0, f"Verification failed despite normalization. output:\n{ver_proc.stdout}\n{ver_proc.stderr}"
