import json
import sys
from pathlib import Path

from click.testing import CliRunner

from mcp_vcr.cli import main


def test_test_list_suites():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--list-suites"])
    assert result.exit_code == 0
    assert "filesystem" in result.output
    assert "memory" in result.output
    assert "time" in result.output


def test_test_list_suites_json():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--list-suites", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["command"] == "test"
    assert "suites" in data
    suite_names = [s["name"] for s in data["suites"]]
    assert "filesystem" in suite_names
    assert "memory" in suite_names
    assert "time" in suite_names


def test_test_suite_not_found():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["test", "--suite", "nonexistent_suite_name", "--", "python", "server.py"],
    )
    assert result.exit_code == 1
    assert "nonexistent_suite_name" in (result.output or "")


def test_test_no_server_args():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "filesystem"])
    assert result.exit_code == 1
    assert "No server command specified" in (result.output or "")


def test_test_no_suite_specified():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--", "python", "server.py"])
    assert result.exit_code == 1
    assert "No suite specified" in (result.output or "")


def test_test_json_output_structure(tmp_path: Path):
    # Run test command with --json on toy_server using custom toy suite
    import yaml

    suite_dir = tmp_path / "toy_suite"
    suite_dir.mkdir()

    t_pass = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "ee55ff66",
            "server_command": ["python", "tests/integration/toy_server.py"],
            "protocol_version": "2024-11-05",
            "client_hint": "pytest",
            "schema_version": "1.0",
        },
        "messages": [
            {
                "t": 0,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            },
            {
                "t": 25,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                        "serverInfo": {"name": "toy-server", "version": "1.0.0"},
                    },
                },
            },
            {
                "t": 30,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            },
        ],
    }

    (suite_dir / "init_only.yaml").write_text(yaml.dump(t_pass), encoding="utf-8")
    (suite_dir / "suite.yaml").write_text(
        yaml.dump(
            {
                "name": "toy_suite",
                "description": "Toy pass suite",
                "transcripts": ["init_only.yaml"],
            }
        ),
        encoding="utf-8",
    )

    toy_server_py = str(Path(__file__).parent / "integration" / "toy_server.py")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "test",
            "--suite",
            "toy_suite",
            "--suites-dir",
            str(tmp_path),
            "--json",
            "--",
            sys.executable,
            toy_server_py,
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["command"] == "test"
    assert data["suite"] == "toy_suite"
    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["transcript"] == "init_only.yaml"
    assert data["results"][0]["status"] == "pass"
    assert data["summary"] == {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
    }


def test_test_suites_dir_nonexistent(tmp_path: Path):
    nonexistent = tmp_path / "nonexistent_suites_dir"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["test", "--suites-dir", str(nonexistent), "--list-suites"],
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output.lower() or "invalid" in result.output.lower()


def test_test_timeout_invalid():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["test", "--suite", "filesystem", "--timeout", "0", "--", "python", "server.py"],
    )
    assert result.exit_code != 0
    assert "at least 1" in result.output.lower() or "invalid" in result.output.lower() or "range" in result.output.lower()


def test_test_timeout_omitted_passed_as_none(monkeypatch):
    captured = {}
    from mcp_vcr.suite import SuiteRunner
    orig_init = SuiteRunner.__init__

    def mock_init(self, *args, **kwargs):
        captured["timeout_ms"] = kwargs.get("timeout_ms")
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(SuiteRunner, "__init__", mock_init)

    runner = CliRunner()
    result = runner.invoke(main, ["test", "--list-suites"])
    assert result.exit_code == 0
    assert captured["timeout_ms"] is None


def test_test_timeout_explicit_passed(monkeypatch):
    captured = {}
    from mcp_vcr.suite import SuiteRunner
    orig_init = SuiteRunner.__init__

    def mock_init(self, *args, **kwargs):
        captured["timeout_ms"] = kwargs.get("timeout_ms")
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(SuiteRunner, "__init__", mock_init)

    runner = CliRunner()
    result = runner.invoke(main, ["test", "--timeout", "12345", "--list-suites"])
    assert result.exit_code == 0
    assert captured["timeout_ms"] == 12345

