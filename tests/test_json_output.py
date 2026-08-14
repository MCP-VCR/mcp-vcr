import json
import sys
from pathlib import Path
import pytest
from click.testing import CliRunner
import yaml

from mcp_vcr.cli import main
from mcp_vcr.snapshot import run_snapshot


DUMMY_SERVER_CODE = """
import sys
import json
from pathlib import Path

tools_path = Path("tools.json")
if tools_path.exists():
    with open(tools_path, "r", encoding="utf-8") as f:
        tools = json.load(f)
else:
    tools = ["toolA"]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        method = data.get("method")
        msg_id = data.get("id")
        
        if msg_id is not None:
            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "test-server", "version": "1.0.0"}
                    }
                }
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [{"name": name, "description": "A tool"} for name in tools]
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"echo": method}
                }
            sys.stdout.write(json.dumps(resp) + "\\n")
            sys.stdout.flush()
    except Exception as e:
        sys.stderr.write(f"Error in dummy server: {e}\\n")
        sys.stderr.flush()
"""


@pytest.fixture
def regression_server_setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server_path = tmp_path / "dummy_server.py"
    server_path.write_text(DUMMY_SERVER_CODE, encoding="utf-8")
    tools_path = tmp_path / "tools.json"
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolA"], f)
    return server_path, tools_path


@pytest.fixture
def sample_session_and_snapshot(regression_server_setup, tmp_path):
    server_path, tools_path = regression_server_setup
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    
    transcript_data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "abcdef12",
            "server_command": [sys.executable, str(server_path)],
            "schema_version": "1.0"
        },
        "messages": [
            {"t": 0, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
            {"t": 10, "dir": "s2c", "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test-server", "version": "1.0.0"}
                }
            }},
            {"t": 20, "dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
            {"t": 30, "dir": "s2c", "payload": {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [{"name": "toolA", "description": "A tool"}]
                }
            }}
        ]
    }
    
    source_file = sessions_dir / "my_session.yaml"
    with open(source_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(transcript_data, f)
        
    golden_path = run_snapshot(source_file)
    snapshots_dir = tmp_path / "snapshots"
    return server_path, tools_path, source_file, golden_path, snapshots_dir


def test_verify_json_pass(sample_session_and_snapshot):
    """Verify verify --json outputs status 'ok', exit code 0 when passing."""
    server_path, _, _, _, snapshots_dir = sample_session_and_snapshot
    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--json", str(snapshots_dir), "--", sys.executable, str(server_path)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["command"] == "verify"
    assert data["summary"]["total"] == 1
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failed"] == 0
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "pass"
    assert "Verifying snapshot:" in result.stderr


def test_verify_json_fail(sample_session_and_snapshot):
    """Verify verify --json outputs status 'fail', exit code 1, and diff on regression."""
    server_path, tools_path, _, _, snapshots_dir = sample_session_and_snapshot
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolB"], f)
        
    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--json", str(snapshots_dir), "--", sys.executable, str(server_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "fail"
    assert data["command"] == "verify"
    assert data["summary"]["failed"] == 1
    assert data["results"][0]["status"] == "fail"
    assert data["results"][0]["diff"] is not None
    assert "changes" in data["results"][0]["diff"]


def test_verify_json_update(sample_session_and_snapshot):
    """Verify verify --update --json outputs status 'updated' in results."""
    server_path, tools_path, _, _, snapshots_dir = sample_session_and_snapshot
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolB"], f)
        
    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--update", "--json", str(snapshots_dir), "--", sys.executable, str(server_path)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["update"] is True
    assert data["results"][0]["status"] == "updated"


def test_verify_json_error(tmp_path):
    """Verify verify --json with nonexistent path emits error envelope and exits 1."""
    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--json", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["command"] == "verify"
    assert "error" in data


def test_check_json_pass(sample_session_and_snapshot, tmp_path):
    """Verify check --json outputs status 'ok' on passing sessions."""
    server_path, _, source_file, _, _ = sample_session_and_snapshot
    runner = CliRunner()
    result = runner.invoke(main, ["check", "--json", str(source_file), "--", sys.executable, str(server_path)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["command"] == "check"
    assert data["summary"]["passed"] == 1
    assert data["results"][0]["status"] == "ok"
    assert "session_file" in data["results"][0]
    assert "Checking session:" in result.stderr


def test_check_json_fail(sample_session_and_snapshot, tmp_path):
    """Verify check --json outputs status 'fail' and diff on regressions."""
    server_path, tools_path, source_file, _, _ = sample_session_and_snapshot
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolDifferent"], f)
        
    runner = CliRunner()
    result = runner.invoke(main, ["check", "--json", str(source_file), "--", sys.executable, str(server_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "fail"
    assert data["command"] == "check"
    assert data["summary"]["failed"] == 1
    assert data["results"][0]["status"] == "fail"
    assert data["results"][0]["diff"] is not None


def test_replay_json_output(sample_session_and_snapshot):
    """Verify replay --json emits structured JSON on successful replay."""
    server_path, _, source_file, _, _ = sample_session_and_snapshot
    runner = CliRunner()
    result = runner.invoke(main, ["replay", str(source_file), "--json", "--", sys.executable, str(server_path)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["command"] == "replay"
    assert data["session_file"] == str(source_file)
    assert "replay_file" in data
    assert data["incomplete"] is False
    assert "Starting replay of" in result.stderr


def test_replay_json_strict_fail(sample_session_and_snapshot):
    """Verify replay --strict --json emits status 'fail' on difference."""
    server_path, tools_path, source_file, _, _ = sample_session_and_snapshot
    with open(tools_path, "w", encoding="utf-8") as f:
        json.dump(["toolChanged"], f)
        
    runner = CliRunner()
    result = runner.invoke(main, ["replay", str(source_file), "--strict", "--json", "--", sys.executable, str(server_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "fail"
    assert data["command"] == "replay"
    assert data["strict"] is True
    assert data["diff"] is not None


def test_diff_json_flag_matching(sample_session_and_snapshot):
    """Verify diff --json outputs status 'ok' and mode field when files match."""
    _, _, _, golden_path, _ = sample_session_and_snapshot
    runner = CliRunner()
    result = runner.invoke(main, ["diff", str(golden_path), str(golden_path), "--json", "--mode", "semantic"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["command"] == "diff"
    assert data["mode"] == "semantic"
    assert isinstance(data["changes"], list)
    assert len(data["changes"]) == 0
    assert data["summary"]["changed"] == 0


def test_diff_json_flag_difference(sample_session_and_snapshot):
    """Verify diff --json outputs status 'fail' and changes list when files differ."""
    _, _, source_file, golden_path, _ = sample_session_and_snapshot
    runner = CliRunner()
    result = runner.invoke(main, ["diff", str(source_file), str(golden_path), "--json", "--mode", "structural"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "fail"
    assert data["command"] == "diff"
    assert data["mode"] == "structural"
    assert isinstance(data["changes"], list)
    assert len(data["changes"]) > 0
    assert "summary" in data



def test_record_json_missing_server_command(tmp_path):
    """Verify record --json without server command emits error envelope to stderr."""
    runner = CliRunner()
    result = runner.invoke(main, ["record", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stderr)
    assert data["status"] == "error"
    assert data["command"] == "record"
    assert "error" in data


import subprocess

def test_record_json_success(regression_server_setup, tmp_path):
    """Verify record --json captures interaction and emits envelope to stderr on exit."""
    server_path, _ = regression_server_setup
    session_file = tmp_path / "sessions" / "rec_test.yaml"
    init_msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    cmd = [
        sys.executable, "-m", "mcp_vcr.cli", "record", "--json", "-o", str(session_file),
        "--", sys.executable, str(server_path)
    ]
    proc = subprocess.run(cmd, input=f"{init_msg}\n", text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0
    # Extract the summary envelope emitted at the end of the proxy stderr stream
    json_start = proc.stderr.find("{")
    assert json_start != -1
    envelope_str = proc.stderr[json_start:]
    data = json.loads(envelope_str)
    assert data["status"] == "ok"
    assert data["command"] == "record"
    assert data["session_file"] == str(session_file)
    assert data["message_count"] >= 2
    assert "exit_code" not in data


def test_replay_json_error_missing_command(sample_session_and_snapshot):
    """Verify replay --json with transport=stdio and no command emits error envelope."""
    _, _, source_file, _, _ = sample_session_and_snapshot
    runner = CliRunner()
    result = runner.invoke(main, ["replay", str(source_file), "--transport", "stdio", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["command"] == "replay"
    assert "error" in data


def test_check_json_error_glob_not_found(regression_server_setup, tmp_path):
    """Verify check --json when glob matches no files emits error envelope."""
    server_path, _ = regression_server_setup
    runner = CliRunner()
    result = runner.invoke(main, ["check", "--json", str(tmp_path / "nonexistent_*.yaml"), "--", sys.executable, str(server_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["command"] == "check"
    assert "error" in data


def test_verify_per_item_status_vocabulary(sample_session_and_snapshot):
    """Verify results[].status uses 4-value vocabulary (pass/fail/updated/unchanged)."""
    server_path, _, _, _, snapshots_dir = sample_session_and_snapshot
    runner = CliRunner()
    
    # 1. pass
    res_pass = runner.invoke(main, ["verify", "--json", str(snapshots_dir), "--", sys.executable, str(server_path)])
    assert json.loads(res_pass.stdout)["results"][0]["status"] == "pass"
    
    # 2. unchanged (update mode with no diff)
    res_unchanged = runner.invoke(main, ["verify", "--update", "--json", str(snapshots_dir), "--", sys.executable, str(server_path)])
    assert json.loads(res_unchanged.stdout)["results"][0]["status"] == "unchanged"


def test_error_envelope_empty_fallback():
    """Verify error_envelope fallback when str(error) is empty."""
    from mcp_vcr.json_output import error_envelope
    
    class CustomEmptyError(Exception):
        pass
        
    env = error_envelope("diff", CustomEmptyError())
    assert env["status"] == "error"
    assert env["command"] == "diff"
    assert env["error"] == "CustomEmptyError"
    
    env_empty = error_envelope("diff", "")
    assert env_empty["status"] == "error"
    assert env_empty["command"] == "diff"
    assert env_empty["error"] == "Unknown error"



