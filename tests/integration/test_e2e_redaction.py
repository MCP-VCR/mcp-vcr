import os
import json
import subprocess
import yaml
from pathlib import Path
import pytest
import sys

TOY_SERVER_PATH = Path(__file__).parent / "toy_server.py"

def test_redaction(tmp_path):
    """
    Test that secrets in the payload (both top-level and nested) are
    redacted before being written to the transcript on disk.
    """
    snapshot_path = tmp_path / "snapshot_redacted.yaml"
    
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": "secret_test"}}}
    ]
    
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    record_cmd = [
        "uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path),
        "--", sys.executable, str(TOY_SERVER_PATH)
    ]
    rec_proc = subprocess.run(record_cmd, input=input_data, text=True, capture_output=True)

    assert rec_proc.returncode == 0
    
    with open(snapshot_path, "r", encoding="utf-8") as f:
        transcript = yaml.safe_load(f)
        
    messages = transcript.get("messages", [])
    assert len(messages) > 0
    
    server_msgs = [m for m in messages if m["dir"] == "s2c"]
    tool_call_resp = server_msgs[1]["payload"]
    
    assert tool_call_resp["result"]["api_key"] == "<REDACTED_api_key>"
    assert tool_call_resp["result"]["nested"]["token"] == "<REDACTED_token>"
