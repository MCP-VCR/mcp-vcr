import os
from pathlib import Path
import pytest
from mcp_vcr.validator import validate_transcript, validate_file, ValidationError

def test_validation_known_good_fixture():
    """Verify that a valid, well-structured transcript passes validation with no errors."""
    test_file = Path("sessions/test_valid_fixture.yaml")
    valid_data = """meta:
  version: 1
  recorded_at: "2026-05-17T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "server.py"]
  schema_version: "1.0"
  protocol_version: "2024-11-05"
  client_hint: "AwesomeClient"
messages:
- t: 0
  dir: "c2s"
  payload:
    jsonrpc: "2.0"
    method: "ping"
- t: 10
  dir: "s2c"
  payload:
    jsonrpc: "2.0"
    result: "pong"
"""
    test_file.parent.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(valid_data)
        
    errors = validate_transcript(test_file)
    assert len(errors) == 0
    
    # Should run and exit cleanly without raising ValidationError
    validate_file(test_file)
    
    if test_file.exists():
        test_file.unlink()


def test_validation_missing_version():
    """Verify that missing required 'version' field triggers clear validation error."""
    test_file = Path("sessions/test_missing_version.yaml")
    invalid_data = """meta:
  recorded_at: "2026-05-17T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "server.py"]
  schema_version: "1.0"
messages: []
"""
    test_file.parent.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(invalid_data)
        
    errors = validate_transcript(test_file)
    assert len(errors) == 1
    # ValidationError checks list and missing keys
    assert "version" in errors[0]["msg"] or "version" in errors[0]["loc"]
    
    with pytest.raises(ValidationError) as exc_info:
        validate_file(test_file)
    assert any("version" in err["loc"] for err in exc_info.value.errors())
    
    if test_file.exists():
        test_file.unlink()


def test_validation_invalid_direction():
    """Verify that invalid 'dir' value (not c2s/s2c) is caught and flagged."""
    test_file = Path("sessions/test_invalid_dir.yaml")
    invalid_data = """meta:
  version: 1
  recorded_at: "2026-05-17T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "server.py"]
  schema_version: "1.0"
messages:
- t: 0
  dir: "invalid_dir"
  payload:
    jsonrpc: "2.0"
    method: "ping"
"""
    test_file.parent.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(invalid_data)
        
    errors = validate_transcript(test_file)
    assert len(errors) == 1
    assert any("invalid_dir" in err["msg"] or "dir" in err["loc"] for err in errors)
    
    if test_file.exists():
        test_file.unlink()
