import json
import pytest
from pathlib import Path
from mcp_vcr.formats import detect_format, iter_messages, load_meta
from mcp_vcr.recorder import TranscriptRecorder

def test_detect_format(tmp_path):
    # Test by extension
    f_ndjson = tmp_path / "test.ndjson"
    f_ndjson.touch()
    assert detect_format(f_ndjson) == "ndjson"

    f_yaml = tmp_path / "test.yaml"
    f_yaml.touch()
    assert detect_format(f_yaml) == "yaml"

    f_yml = tmp_path / "test.yml"
    f_yml.touch()
    assert detect_format(f_yml) == "yaml"

    # Test by content sniffing (extensionless)
    f_none = tmp_path / "test_none"
    with open(f_none, "w", encoding="utf-8") as f:
        f.write('{"_type": "meta", "version": 1}\n')
    assert detect_format(f_none) == "ndjson"

    # YAML content sniff fallback
    f_none_yaml = tmp_path / "test_none_yaml"
    with open(f_none_yaml, "w", encoding="utf-8") as f:
        f.write('meta:\n  version: 1\n')
    assert detect_format(f_none_yaml) == "yaml"

    # Flow mapping YAML should not be misidentified as NDJSON
    f_flow_yaml = tmp_path / "test_flow_yaml"
    with open(f_flow_yaml, "w", encoding="utf-8") as f:
        f.write('{key: value}\n')
    assert detect_format(f_flow_yaml) == "yaml"

def test_iter_messages_and_load_meta(tmp_path):
    # 1. Test YAML
    yaml_content = """
meta:
  version: 1
  session_id: "yaml-session"
messages:
  - t: 10
    dir: "c2s"
    payload: {"method": "ping"}
  - t: 20
    dir: "s2c"
    payload: {"result": "pong"}
"""
    f_yaml = tmp_path / "session.yaml"
    with open(f_yaml, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    meta = load_meta(f_yaml)
    assert meta["session_id"] == "yaml-session"

    messages = list(iter_messages(f_yaml))
    assert len(messages) == 2
    assert messages[0]["t"] == 10
    assert messages[1]["payload"]["result"] == "pong"

    # 2. Test NDJSON
    ndjson_content = """{"_type": "meta", "version": 1, "session_id": "ndjson-session"}
{"t": 10, "dir": "c2s", "payload": {"method": "ping"}}
{"t": 20, "dir": "s2c", "payload": {"result": "pong"}}
"""
    f_ndjson = tmp_path / "session.ndjson"
    with open(f_ndjson, "w", encoding="utf-8") as f:
        f.write(ndjson_content)

    meta_nd = load_meta(f_ndjson)
    assert meta_nd["session_id"] == "ndjson-session"

    messages_nd = list(iter_messages(f_ndjson))
    assert len(messages_nd) == 2
    assert messages_nd[0]["t"] == 10
    assert messages_nd[1]["payload"]["result"] == "pong"

def test_recorder_ndjson_roundtrip(tmp_path):
    # Record to NDJSON
    recorder = TranscriptRecorder(filename=str(tmp_path / "record.ndjson"), server_command=["python"], format="ndjson")
    recorder.start_session()
    
    recorder.write({"t": 5, "dir": "c2s", "payload": {"method": "list"}})
    recorder.write({"t": 12, "dir": "s2c", "payload": {"result": []}})
    
    # Update lazy metadata
    recorder.update_lazy_metadata(client_hint="test-client", protocol_version="2024-11-05")
    recorder.close()
    
    # Verify file content
    filepath = tmp_path / "record.ndjson"
    assert filepath.exists()
    
    meta = load_meta(filepath)
    assert meta["client_hint"] == "test-client"
    assert meta["protocol_version"] == "2024-11-05"
    assert meta["server_command"] == ["python"]
    
    messages = list(iter_messages(filepath))
    assert len(messages) == 2
    assert messages[0]["t"] == 5
    assert messages[1]["dir"] == "s2c"
