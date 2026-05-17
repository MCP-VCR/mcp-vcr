import os
import shutil
from pathlib import Path
from click.testing import CliRunner
from mcp_vcr.cli import main

def test_cli_validate_single_file_valid():
    """Verify cli validate exits 0 and prints OK for a valid transcript."""
    test_file = Path("sessions/test_cli_valid.yaml")
    valid_data = """meta:
  version: 1
  recorded_at: "2026-05-17T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "server.py"]
  schema_version: "1.0"
messages: []
"""
    test_file.parent.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(valid_data)
        
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(test_file)])
    
    assert result.exit_code == 0
    assert "OK" in result.output
    assert "valid" in result.output
    
    if test_file.exists():
        test_file.unlink()


def test_cli_validate_single_file_invalid():
    """Verify cli validate exits 1 and lists errors for an invalid transcript."""
    test_file = Path("sessions/test_cli_invalid.yaml")
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
        
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(test_file)])
    
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "version" in result.output
    
    if test_file.exists():
        test_file.unlink()


def test_cli_validate_directory():
    """Verify cli validate scans directories, listing status for each file separately."""
    test_dir = Path("test_cli_sessions")
    if test_dir.exists():
        shutil.rmtree(test_dir)
        
    test_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Valid file
    valid_file = test_dir / "valid_session.yaml"
    valid_data = """meta:
  version: 1
  recorded_at: "2026-05-17T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "server.py"]
  schema_version: "1.0"
messages: []
"""
    with open(valid_file, "w", encoding="utf-8") as f:
        f.write(valid_data)
        
    # 2. Invalid file
    invalid_file = test_dir / "invalid_session.yaml"
    invalid_data = """meta:
  recorded_at: "2026-05-17T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python", "server.py"]
  schema_version: "1.0"
messages: []
"""
    with open(invalid_file, "w", encoding="utf-8") as f:
        f.write(invalid_data)
        
    runner = CliRunner()
    result = runner.invoke(main, ["validate", str(test_dir)])
    
    # Should exit with code 1 because one file is invalid
    assert result.exit_code == 1
    # Both files must be reported on
    assert "valid_session.yaml" in result.output
    assert "OK" in result.output
    assert "invalid_session.yaml" in result.output
    assert "ERROR" in result.output
    
    shutil.rmtree(test_dir)
