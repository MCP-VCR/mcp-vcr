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


def test_test_all_and_suite_mutually_exclusive():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "filesystem", "--all", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_test_use_hint_and_all_rejected():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--all", "--use-hint"])
    assert result.exit_code == 2
    assert "--use-hint cannot be used with --all" in result.output


def test_test_use_hint_with_suites_dir_rejected(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "custom", "--suites-dir", str(tmp_path), "--use-hint"])
    assert result.exit_code == 2
    assert "--use-hint cannot be used with --suites-dir" in result.output
    assert "informational only" in result.output


def test_test_all_no_server_args():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--all"])
    assert result.exit_code == 1
    assert "No server command specified" in result.output


def test_test_no_server_args_shows_hint_suggestion():
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "filesystem"])
    assert result.exit_code == 1
    assert "Hint from suite manifest" in result.output
    assert "@modelcontextprotocol/server-filesystem" in result.output
    assert "--use-hint" in result.output


def test_test_all_json_envelope_and_execution(tmp_path: Path):
    import yaml
    suite1_dir = tmp_path / "suite1"
    suite1_dir.mkdir()
    suite2_dir = tmp_path / "suite2"
    suite2_dir.mkdir()

    t_pass = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "11112222",
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

    (suite1_dir / "t1.yaml").write_text(yaml.dump(t_pass), encoding="utf-8")
    (suite1_dir / "suite.yaml").write_text(
        yaml.dump({
            "name": "suite1",
            "description": "Suite 1 pass",
            "transcripts": ["t1.yaml"]
        }),
        encoding="utf-8"
    )

    (suite2_dir / "t2.yaml").write_text(yaml.dump(t_pass), encoding="utf-8")
    (suite2_dir / "suite.yaml").write_text(
        yaml.dump({
            "name": "suite2",
            "description": "Suite 2 pass",
            "transcripts": ["t2.yaml"]
        }),
        encoding="utf-8"
    )

    toy_server_py = str(Path(__file__).parent / "integration" / "toy_server.py")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "test",
            "--all",
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
    assert data["mode"] == "all"
    assert "suite_results" in data
    assert len(data["suite_results"]) == 2
    suite_names = [sr["suite"] for sr in data["suite_results"]]
    assert "suite1" in suite_names
    assert "suite2" in suite_names

    assert data["summary"] == {
        "suites_total": 2,
        "suites_passed": 2,
        "suites_failed": 0,
        "transcripts_total": 2,
        "transcripts_passed": 2,
        "transcripts_failed": 0,
        "transcripts_skipped": 0,
    }


def test_test_all_text_output(tmp_path: Path):
    import yaml
    suite1_dir = tmp_path / "suite1"
    suite1_dir.mkdir()

    t_pass = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "11112222",
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

    (suite1_dir / "t1.yaml").write_text(yaml.dump(t_pass), encoding="utf-8")
    (suite1_dir / "suite.yaml").write_text(
        yaml.dump({
            "name": "suite1",
            "description": "Suite 1 pass",
            "transcripts": ["t1.yaml"]
        }),
        encoding="utf-8"
    )

    toy_server_py = str(Path(__file__).parent / "integration" / "toy_server.py")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "test",
            "--all",
            "--suites-dir",
            str(tmp_path),
            "--",
            sys.executable,
            toy_server_py,
        ],
    )

    assert result.exit_code == 0
    assert "━━ suite1" in result.output
    assert "RESULT: 1/1 suites passed | 1/1 transcripts passed, 0/1 failed" in result.output


def test_test_use_hint_shell_injection_rejected(monkeypatch):
    from mcp_vcr.suite import SuiteManifest, SuiteRunner

    manifest = SuiteManifest(
        name="evil",
        description="Evil suite",
        server_hint="cat /etc/passwd | sh; rm -rf /",
        transcripts=["t.yaml"],
        suite_dir=SuiteRunner.get_bundled_suites_dir() / "filesystem"
    )

    monkeypatch.setattr(SuiteRunner, "find_suite", lambda *args, **kwargs: manifest)

    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "evil", "--use-hint"])
    assert result.exit_code == 1
    assert "unsupported shell characters" in result.output


