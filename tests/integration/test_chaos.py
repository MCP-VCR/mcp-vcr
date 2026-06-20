import os
import json
import subprocess
import yaml
from pathlib import Path
import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys

TOY_SERVER_PATH = Path(__file__).parent / "toy_server.py"

def test_chaos_invalid_json(tmp_path):
    """Recorder should not crash when receiving invalid JSON."""
    snapshot_path = tmp_path / "snapshot_invalid.yaml"
    
    # Valid init, then invalid JSON
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    
    input_data = json.dumps(init_req) + "\n" + "{invalid\n" + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}) + "\n" + json.dumps({"jsonrpc": "2.0", "method": "exit"}) + "\n"
    
    cmd = ["uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    proc = subprocess.run(cmd, input=input_data, text=True, capture_output=True, timeout=10)

    
    # Should not crash proxy, proxy exits 0
    assert proc.returncode == 0
    
    with open(snapshot_path, "r", encoding="utf-8") as f:
        transcript = yaml.safe_load(f)
        
    assert "messages" in transcript

def test_chaos_server_crash(tmp_path):
    """Transcript should still be saved if the server crashes."""
    snapshot_path = tmp_path / "snapshot_crash.yaml"
    
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": "crash"}}}
    ]
    
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    cmd = ["uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    proc = subprocess.run(cmd, input=input_data, text=True, capture_output=True, timeout=10)

    
    # The proxy will probably exit with non-zero because the child process died
    # But the snapshot must be written!
    assert snapshot_path.exists()
    
    with open(snapshot_path, "r", encoding="utf-8") as f:
        transcript = yaml.safe_load(f)
    
    messages = transcript.get("messages", [])
    assert len(messages) > 0
    # We expect the initialize to be captured, but maybe not the response to crash
    assert len(messages) > 0

def test_chaos_huge_payload(tmp_path):
    """Recorder should survive huge payloads."""
    snapshot_path = tmp_path / "snapshot_huge.yaml"
    
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": "large_payload"}}}
    ]
    
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    cmd = ["uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    proc = subprocess.run(cmd, input=input_data, text=True, capture_output=True, timeout=10)

    
    assert proc.returncode == 0
    assert snapshot_path.exists()
    assert snapshot_path.stat().st_size > 1024 * 1024 # Should be over 1MB

def test_chaos_concurrent_requests(tmp_path):
    """Verify matching by JSON-RPC id remains correct under load."""
    snapshot_path = tmp_path / "snapshot_concurrent.yaml"
    
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}]
    
    # 100 concurrent requests
    for i in range(2, 102):
        requests.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "toy_tool", "arguments": {"arg": f"concurrent_{i}"}}})
        
    requests.append({"jsonrpc": "2.0", "method": "exit"})
    input_data = "\n".join(json.dumps(req) for req in requests) + "\n"
    
    cmd = ["uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path), "--", sys.executable, str(TOY_SERVER_PATH)]
    proc = subprocess.run(cmd, input=input_data, text=True, capture_output=True, timeout=10)

    
    assert proc.returncode == 0
    
    with open(snapshot_path, "r", encoding="utf-8") as f:
        transcript = yaml.safe_load(f)
        
    assert "messages" in transcript
    msgs = transcript["messages"]
    
    # Ensure 100 requests and 100 responses + init
    client_msgs = [m for m in msgs if m["dir"] == "c2s"]
    server_msgs = [m for m in msgs if m["dir"] == "s2c"]
    
    assert len(client_msgs) == 102
    assert len(server_msgs) == 101 # Initialize response + 100 tool call responses
