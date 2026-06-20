import os
import json
import subprocess
from pathlib import Path
import pytest
import sys
from mcp_vcr.cli import main
from click.testing import CliRunner

TOY_SERVER_PATH = Path(__file__).parent / "toy_server.py"

def test_recorder_exact_match(tmp_path):
    """
    Test that the recorder captures exact byte-for-byte matching of stdin/stdout.
    """
    snapshot_path = tmp_path / "snapshot.yaml"
    
    # We will simulate the client by sending a set of requests to `mcp-vcr record`
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": "hello unicode 🦊\nmultiline"}}}
    ]
    
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    # Run the recorder
    cmd = [
        "uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path),
        "--", sys.executable, str(TOY_SERVER_PATH)
    ]
    
    proc = subprocess.run(cmd, input=input_data, text=True, capture_output=True)

    assert proc.returncode == 0
    
    # Verify snapshot exists
    assert snapshot_path.exists()
    
    import yaml
    with open(snapshot_path, "r", encoding="utf-8") as f:
        transcript = yaml.safe_load(f)
        
    assert "messages" in transcript
    assert "messages" in transcript
    session = transcript
    
    # Check that messages match the input
    client_msgs = [m for m in session["messages"] if m["dir"] == "c2s"]
    server_msgs = [m for m in session["messages"] if m["dir"] == "s2c"]
    
    assert len(client_msgs) == 5
    assert len(server_msgs) == 3 # initialized has no response
    
    assert client_msgs[3]["payload"]["params"]["arguments"]["arg"] == "hello unicode 🦊\nmultiline"
