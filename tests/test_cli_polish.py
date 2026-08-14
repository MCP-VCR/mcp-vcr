import json
import yaml
from pathlib import Path
import pytest
from click.testing import CliRunner
from mcp_vcr.cli import main
from mcp_vcr.redactor import Redactor

@pytest.fixture
def temp_sessions_dir(tmp_path):
    """Fixture that prepares a temporary sessions directory with mock transcripts."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    
    # 1. Create session A (recorded at 2026-05-18T10:00:00Z, id=aaaa1111)
    session_a = {
        "meta": {
            "recorded_at": "2026-05-18T10:00:00Z",
            "session_id": "aaaa1111",
            "client_hint": "test-client-a",
            "protocol_version": "2024-11-05",
            "server_command": ["python", "server.py"],
            "version": 1,
            "schema_version": "1.0"
        },
        "messages": [
            {
                "t": 10,
                "dir": "c2s",
                "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
            },
            {
                "t": 20,
                "dir": "s2c",
                "payload": {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}
            }
        ]
    }
    
    # 2. Create session B (recorded at 2026-05-18T11:00:00Z, id=bbbb2222)
    session_b = {
        "meta": {
            "recorded_at": "2026-05-18T11:00:00Z",
            "session_id": "bbbb2222",
            "client_hint": "test-client-b",
            "protocol_version": "2024-11-05",
            "server_command": ["python", "server.py"],
            "version": 1,
            "schema_version": "1.0"
        },
        "messages": [
            {
                "t": 5,
                "dir": "c2s",
                "payload": {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"token": "secret-token"}}
            },
            {
                "t": 15,
                "dir": "s2c",
                "payload": {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
            }
        ]
    }
    
    # 3. Create an ambiguous session C sharing the "aa" prefix (id=aaaa3333)
    session_c = {
        "meta": {
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "aaaa3333",
            "client_hint": "test-client-c",
            "protocol_version": "2024-11-05",
            "server_command": ["python", "server.py"],
            "version": 1,
            "schema_version": "1.0"
        },
        "messages": []
    }
    
    with open(sessions_dir / "session_a.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(session_a, f)
        
    with open(sessions_dir / "session_b.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(session_b, f)
        
    with open(sessions_dir / "session_c.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(session_c, f)
        
    return sessions_dir

def test_version_option():
    """Verify that the program displays the correct version string."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "mcp-vcr, version 0.2.1" in result.output

def test_list_sessions_json(temp_sessions_dir):
    """Verify that mcp-vcr list with json format outputs a sorted JSON array of sessions."""
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--format", "json", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    
    sessions = json.loads(result.output)
    assert len(sessions) == 3
    # Check that they are sorted newest first (date descending)
    assert sessions[0]["session_id"] == "aaaa3333" # 12:00:00
    assert sessions[1]["session_id"] == "bbbb2222" # 11:00:00
    assert sessions[2]["session_id"] == "aaaa1111" # 10:00:00
    
    # Check fields
    assert sessions[1]["client_hint"] == "test-client-b"
    assert sessions[1]["message_count"] == 2

def test_list_sessions_text(temp_sessions_dir):
    """Verify that mcp-vcr list with text format prints a clean table structure."""
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--format", "text", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    assert "DATE" in result.output
    assert "SESSION ID" in result.output
    assert "bbbb2222" in result.output
    assert "test-client-b" in result.output

def test_list_sessions_empty(tmp_path):
    """Verify that list handles non-existent or empty directories gracefully."""
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--dir", str(tmp_path / "empty")])
    assert result.exit_code == 0
    assert "No sessions found" in result.output

def test_inspect_unique_session(temp_sessions_dir):
    """Verify that inspect uniquely matches a prefix and prints detailed metadata/stats."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "bbbb", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    assert "Session Inspection: bbbb2222" in result.output
    assert "Metadata:" in result.output
    assert "client_hint: test-client-b" in result.output
    assert "Total Messages: 2" in result.output
    assert "tools/list" in result.output

def test_inspect_with_messages(temp_sessions_dir):
    """Verify that inspect --messages shows chronological message lists with timestamps."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "bbbb", "--messages", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    assert "Messages List:" in result.output
    assert "[     5 ms] c2s   (Request: tools/list, id=2)" in result.output
    assert "[    15 ms] s2c   (Response Success, id=2)" in result.output

def test_inspect_not_found(temp_sessions_dir):
    """Verify that inspect exits 1 when a session prefix cannot be matched."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "not_exist", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 1
    assert "ERROR: No session found with ID prefix" in result.output

def test_inspect_ambiguous_prefix(temp_sessions_dir):
    """Verify that inspect prints ambiguous prefix list and exits 1 if prefix matches multiple sessions."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "aa", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 1
    assert "ERROR: Ambiguous prefix 'aa'. Multiple sessions matched:" in result.output
    assert "aaaa1111" in result.output
    assert "aaaa3333" in result.output

def test_diff_ignore_fields(temp_sessions_dir):
    """Verify that the diff subcommand ignores specified JSON paths and exits with 0 on no changes."""
    runner = CliRunner()
    
    path_a = temp_sessions_dir / "session_a.yaml"
    path_b = temp_sessions_dir / "session_a_modified.yaml"
    
    with open(path_a, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    # Introduce a structural modification in result.protocolVersion (type mismatch: string to integer)
    data["messages"][1]["payload"]["result"]["protocolVersion"] = 12345
    with open(path_b, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
        
    # Diff without ignore -> should fail with exit code 1
    res_fail = runner.invoke(main, ["diff", str(path_a), str(path_b)])
    assert res_fail.exit_code == 1
    
    # Diff with ignore -> should pass with exit code 0
    res_pass = runner.invoke(main, ["diff", str(path_a), str(path_b), "--ignore", "result.protocolVersion"])
    assert res_pass.exit_code == 0

def test_record_output_dir_validation(tmp_path):
    """Verify that record validates output directory creation at startup."""
    runner = CliRunner()
    bad_file = tmp_path / "bad_file.txt"
    bad_file.write_text("just a file")
    
    # Try recording to a subdirectory of a file (should fail directory validation)
    result = runner.invoke(main, ["record", "-o", str(bad_file / "invalid"), "--", "python", "server.py"])
    assert result.exit_code == 1
    assert "ERROR: Cannot create output directory" in result.output

def test_redactor_enabled_disabled():
    """Verify that the Redactor's enabled/disabled flag works programmatically."""
    payload = {"password": "mypassword", "normal_field": "hello"}
    
    # 1. Enabled (redacts password)
    r_enabled = Redactor(enabled=True)
    redacted = r_enabled.redact(payload)
    assert redacted["password"] == "<REDACTED_password>"
    assert redacted["normal_field"] == "hello"
    
    # 2. Disabled (returns deep copy, no redaction)
    r_disabled = Redactor(enabled=False)
    unredacted = r_disabled.redact(payload)
    assert unredacted["password"] == "mypassword"
    assert unredacted["normal_field"] == "hello"


def test_list_json_flag(temp_sessions_dir):
    """Verify that mcp-vcr list --json outputs a structured JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--json", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["command"] == "list"
    assert data["sessions_dir"] == str(temp_sessions_dir)
    assert isinstance(data["sessions"], list)
    assert len(data["sessions"]) == 3
    assert data["sessions"][0]["session_id"] == "aaaa3333"


def test_list_json_format_mutual_exclusion(temp_sessions_dir):
    """Verify that passing --json and --format together errors with exit code 2."""
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--json", "--format", "text", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 2
    assert "ERROR: --json and --format are mutually exclusive." in result.output


def test_diff_json_format_mutual_exclusion(temp_sessions_dir):
    """Verify that diff with both --json and --format errors with exit code 2."""
    runner = CliRunner()
    path_a = temp_sessions_dir / "session_a.yaml"
    path_b = temp_sessions_dir / "session_b.yaml"
    result = runner.invoke(main, ["diff", str(path_a), str(path_b), "--json", "--format", "text"])
    assert result.exit_code == 2
    assert "ERROR: --json and --format are mutually exclusive." in result.output


def test_inspect_json_flag(temp_sessions_dir):
    """Verify that inspect --json outputs metadata and stats, but omits messages by default."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "bbbb", "--json", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["command"] == "inspect"
    assert data["session_id"] == "bbbb2222"
    assert "session_file" in data
    assert data["metadata"]["client_hint"] == "test-client-b"
    assert data["stats"]["total_messages"] == 2
    assert "tools/list" in data["methods"]
    assert "messages" not in data


def test_inspect_json_with_messages(temp_sessions_dir):
    """Verify that inspect --json --messages includes the messages array."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "bbbb", "--json", "--messages", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert "messages" in data
    assert len(data["messages"]) == 2


def test_inspect_json_error_not_found(temp_sessions_dir):
    """Verify that inspect --json emits error envelope on failure."""
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "not_exist", "--json", "--dir", str(temp_sessions_dir)])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert data["command"] == "inspect"
    assert "error" in data

