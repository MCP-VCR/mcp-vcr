import pytest
from pathlib import Path
from mcp_vcr.validator import validate_file
from pydantic import ValidationError

def test_validate_sample_v1():
    fixture_path = Path("fixtures/sample_v1.yaml")
    transcript = validate_file(fixture_path)
    assert transcript.meta.version == 1
    assert len(transcript.messages) == 2
    assert transcript.messages[0].dir == "c2s"
    assert transcript.messages[1].dir == "s2c"

def test_validate_invalid_version(tmp_path):
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("""
meta:
  version: "wrong"
  recorded_at: "2024-01-15T14:30:22.471Z"
  session_id: "a3f2b1c9"
  server_command: ["python", "my_server.py"]
messages: []
""")
    with pytest.raises(ValidationError):
        validate_file(invalid_yaml)

def test_validate_missing_fields(tmp_path):
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("""
meta:
  version: 1
messages: []
""")
    with pytest.raises(ValidationError):
        validate_file(invalid_yaml)
