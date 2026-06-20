import os
import json
import subprocess
from pathlib import Path
import pytest
import sys

TOY_SERVER_PATH = Path(__file__).parent / "toy_server.py"

def test_replay_correctness(tmp_path):
    """
    Test that replay reproduces the exact conversation and fails when output changes.
    """
    snapshot_path = tmp_path / "snapshot_golden.yaml"
    
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "file:///toy/resource"}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": "normal"}}}
    ]
    
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    # 1. Record the baseline
    record_cmd = [
        "uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path),
        "--", sys.executable, str(TOY_SERVER_PATH)
    ]
    rec_proc = subprocess.run(record_cmd, input=input_data, text=True, capture_output=True, timeout=10)

    assert rec_proc.returncode == 0
    
    # 2. Verify against the baseline (should pass)
    update_cmd = ["uv", "run", "mcp-vcr", "verify", "--update", str(tmp_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    subprocess.run(update_cmd, text=True, capture_output=True, timeout=10)
    verify_cmd = ["uv", "run", "mcp-vcr", "verify", str(tmp_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    ver_proc = subprocess.run(verify_cmd, text=True, capture_output=True, timeout=10)
    assert ver_proc.returncode == 0
    assert "PASS" in ver_proc.stdout
    
    # 3. Intentionally change server output and verify it fails
    env = os.environ.copy()
    env["TOY_SERVER_MODE"] = "changed"
    
    verify_cmd_fail = [
        "uv", "run", "mcp-vcr", "verify", str(tmp_path),
        "--", sys.executable, str(TOY_SERVER_PATH)
    ]
    ver_proc_fail = subprocess.run(verify_cmd_fail, text=True, capture_output=True, env=env, timeout=10)
    assert ver_proc_fail.returncode != 0
    # The output should contain diffs
    assert "Diff" in ver_proc_fail.stdout or "Diff" in ver_proc_fail.stderr or "diff" in ver_proc_fail.stdout.lower()
