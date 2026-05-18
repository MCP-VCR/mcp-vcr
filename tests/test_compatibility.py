import logging
import pytest
import yaml
from pathlib import Path
from mcp_vcr.validator import validate_file, validate_transcript
from mcp_vcr.diff import run_diff
from mcp_vcr.schema import Transcript

def test_load_legacy_v0_missing_version(tmp_path, caplog):
    """Verify that a transcript missing version field logs a warning and proceeds as v0."""
    v0_data = {
        "meta": {
            # missing version
            "session_id": "abc123ef",
            "recorded_at": "2024-01-15T14:30:22.471Z",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": [
            {
                "t": 10,
                "dir": "c2s",
                "payload": {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            }
        ]
    }
    
    file_path = tmp_path / "v0_missing_version.yaml"
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(v0_data, f)
        
    with caplog.at_level(logging.WARNING):
        transcript = validate_file(file_path)
        
    # Check that we parsed it as Transcript model successfully
    assert isinstance(transcript, Transcript)
    assert transcript.meta.version == 0
    
    # Check warning was logged
    assert any("is missing 'version' field. Treating as legacy v0 transcript." in record.message for record in caplog.records)


def test_load_legacy_v0_explicit_version_0(tmp_path, caplog):
    """Verify that a transcript with version 0 logs a warning and proceeds as legacy v0."""
    v0_data = {
        "meta": {
            "version": 0,
            "session_id": "abc123ef",
            "recorded_at": "2024-01-15T14:30:22.471Z",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": []
    }
    
    file_path = tmp_path / "v0_explicit_version.yaml"
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(v0_data, f)
        
    with caplog.at_level(logging.WARNING):
        transcript = validate_file(file_path)
        
    assert isinstance(transcript, Transcript)
    assert transcript.meta.version == 0
    assert any("has legacy version 0. Treating as legacy v0 transcript." in record.message for record in caplog.records)


def test_load_v1_silently(tmp_path, caplog):
    """Verify that a valid v1 transcript loads silently with no warnings."""
    v1_data = {
        "meta": {
            "version": 1,
            "session_id": "abc123ef",
            "recorded_at": "2024-01-15T14:30:22.471Z",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": []
    }
    
    file_path = tmp_path / "v1_valid.yaml"
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(v1_data, f)
        
    with caplog.at_level(logging.WARNING):
        transcript = validate_file(file_path)
        
    assert isinstance(transcript, Transcript)
    assert transcript.meta.version == 1
    # No warnings logged
    assert len(caplog.records) == 0


def test_diff_version_mismatch_warning_and_error(tmp_path, caplog):
    """Verify that diffing a v0 transcript against a v1 transcript raises ValueError and logs warning."""
    v0_data = {
        "meta": {
            "version": 0,
            "session_id": "abc123ef",
            "recorded_at": "2024-01-15T14:30:22.471Z",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": []
    }
    
    v1_data = {
        "meta": {
            "version": 1,
            "session_id": "abc123ef",
            "recorded_at": "2024-01-15T14:30:22.471Z",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": []
    }
    
    path_a = tmp_path / "v0.yaml"
    path_b = tmp_path / "v1.yaml"
    
    with open(path_a, "w", encoding="utf-8") as f:
        yaml.safe_dump(v0_data, f)
    with open(path_b, "w", encoding="utf-8") as f:
        yaml.safe_dump(v1_data, f)
        
    with pytest.raises(ValueError) as excinfo, caplog.at_level(logging.WARNING):
        run_diff(path_a, path_b)
        
    assert "Cannot compare mismatched transcript versions" in str(excinfo.value)
    assert any("Version mismatch: transcript A has version 0, transcript B has version 1." in record.message for record in caplog.records)


def test_validate_schema_v0_missing_reports_error(tmp_path):
    """Verify that mcp-vcr validate on a v0 transcript reports version missing or invalid."""
    v0_data = {
        "meta": {
            "version": 0,
            "session_id": "abc123ef",
            "recorded_at": "2024-01-15T14:30:22.471Z",
            "server_command": ["python", "server.py"],
            "schema_version": "1.0"
        },
        "messages": []
    }
    
    file_path = tmp_path / "v0.yaml"
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(v0_data, f)
        
    errors = validate_transcript(file_path)
    assert len(errors) > 0
    # Must report enum violation (0 is not 1) or missing version
    assert any("version" in err["loc"] for err in errors)
