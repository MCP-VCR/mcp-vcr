import os
import shutil
import yaml
from pathlib import Path
from mcp_vcr.interceptor import MessageInterceptor, Direction
from mcp_vcr.recorder import TranscriptRecorder

def test_session_id_generation():
    """Verify unique 8-character hex session IDs are generated."""
    r1 = TranscriptRecorder()
    r2 = TranscriptRecorder()
    
    assert len(r1.session_id) == 8
    assert len(r2.session_id) == 8
    assert r1.session_id != r2.session_id


def test_filename_and_directory_creation():
    """Verify directory auto-creation and custom output filenames."""
    custom_path = "test_sessions_dir/my_test_transcript.yaml"
    if os.path.exists("test_sessions_dir"):
        shutil.rmtree("test_sessions_dir")
        
    recorder = TranscriptRecorder(filename=custom_path, server_command=["python", "test.py"])
    assert recorder.filepath == Path(custom_path)
    
    # Starting session must create target parent directory
    recorder.start_session()
    assert os.path.exists("test_sessions_dir")
    
    recorder.close()
    shutil.rmtree("test_sessions_dir")


def test_streaming_incremental_writes():
    """Verify incremental streaming write flushes valid partial YAML to disk instantly."""
    test_file = "sessions/test_streaming.yaml"
    if os.path.exists(test_file):
        os.remove(test_file)
        
    recorder = TranscriptRecorder(filename=test_file, server_command=["mcp-server"])
    recorder.start_session()
    
    # Write 3 messages incrementally
    msg1 = {"t": 10, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "m1"}}
    msg2 = {"t": 20, "dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 1, "result": "r1"}}
    msg3 = {"t": 30, "dir": "c2s", "payload": {"jsonrpc": "2.0", "method": "n1"}}
    
    recorder.write(msg1)
    recorder.write(msg2)
    recorder.write(msg3)
    
    # Read the file directly while session is still running (handle is not closed)
    # We must see all 3 complete messages in the file, proving it flushes incrementally
    with open(test_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    assert data["version"] == 1
    assert len(data["messages"]) == 3
    assert data["messages"][0]["t"] == 10
    assert data["messages"][1]["payload"]["result"] == "r1"
    
    recorder.close()
    if os.path.exists(test_file):
        os.remove(test_file)


def test_stable_key_sorting():
    """Verify deterministic stable field key ordering is enforced."""
    test_file = "sessions/test_sorting.yaml"
    if os.path.exists(test_file):
        os.remove(test_file)
        
    recorder = TranscriptRecorder(filename=test_file, server_command=["test"])
    recorder.start_session()
    
    # Payload with deliberately randomized insertion key order
    msg = {
        "t": 100,
        "dir": "c2s",
        "payload": {
            "z_key": "last",
            "a_key": "first",
            "m_key": "middle",
            "nested": {
                "y": 2,
                "x": 1
            }
        }
    }
    recorder.write(msg)
    recorder.close()
    
    # Read raw content to verify key order in YAML string
    with open(test_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    # The keys must be sorted alphabetically: a_key, m_key, nested, z_key
    # And nested keys must be sorted: x, y
    assert content.index("a_key") < content.index("m_key")
    assert content.index("m_key") < content.index("nested")
    assert content.index("x: 1") < content.index("y: 2")
    
    if os.path.exists(test_file):
        os.remove(test_file)


def test_lazy_metadata_backfill():
    """Verify client_hint and protocol_version are backfilled on close from initialize exchange."""
    test_file = "sessions/test_lazy.yaml"
    if os.path.exists(test_file):
        os.remove(test_file)
        
    recorder = TranscriptRecorder(filename=test_file, server_command=["test"])
    interceptor = MessageInterceptor(recorder=recorder)
    
    recorder.start_session()
    
    # 1. Initialize Request from client 'AwesomeAgent'
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "AwesomeAgent"}
        }
    }
    interceptor.observe(req, Direction.C2S)
    
    # 2. Initialize Response from server supporting protocol '2024-11-05'
    res = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05"
        }
    }
    interceptor.observe(res, Direction.S2C)
    
    # Verify they aren't backfilled in the file immediately since the file handle is open
    assert recorder.client_hint == "AwesomeAgent"
    assert recorder.protocol_version == "2024-11-05"
    
    # Close session (performs clean post-processing backfill)
    recorder.close()
    
    # Read the final file to verify meta contains the backfilled values
    with open(test_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    assert data["meta"]["client_hint"] == "AwesomeAgent"
    assert data["meta"]["protocol_version"] == "2024-11-05"
    
    if os.path.exists(test_file):
        os.remove(test_file)
