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


def test_test_json_output_structure(tmp_path: Path, toy_pass_transcript, write_suite):
    # Run test command with --json on toy_server using custom toy suite
    write_suite(tmp_path / "toy_suite", "toy_suite", toy_pass_transcript, "init_only.yaml", "Toy pass suite")

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


def test_test_all_json_envelope_and_execution(tmp_path: Path, toy_pass_transcript, write_suite):
    write_suite(tmp_path / "suite1", "suite1", toy_pass_transcript, "t1.yaml", "Suite 1 pass")
    write_suite(tmp_path / "suite2", "suite2", toy_pass_transcript, "t2.yaml", "Suite 2 pass")

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


def test_test_all_text_output(tmp_path: Path, toy_pass_transcript, write_suite):
    write_suite(tmp_path / "suite1", "suite1", toy_pass_transcript, "t1.yaml", "Suite 1 pass")

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


def test_test_use_hint_outside_bundled_rejected(monkeypatch, tmp_path: Path):
    from mcp_vcr.suite import SuiteManifest, SuiteRunner

    manifest = SuiteManifest(
        name="external_suite",
        description="External suite",
        server_hint="python server.py",
        transcripts=["t.yaml"],
        suite_dir=tmp_path / "external_suite"
    )

    monkeypatch.setattr(SuiteRunner, "find_suite", lambda *args, **kwargs: manifest)

    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "external_suite", "--use-hint"])
    assert result.exit_code == 1
    assert "can only be used with bundled suites" in result.output


def test_test_use_hint_no_hint_defined_rejected(monkeypatch):
    from mcp_vcr.suite import SuiteManifest, SuiteRunner

    manifest = SuiteManifest(
        name="no_hint_suite",
        description="Bundled suite without hint",
        server_hint=None,
        transcripts=["t.yaml"],
        suite_dir=SuiteRunner.get_bundled_suites_dir() / "filesystem"
    )

    monkeypatch.setattr(SuiteRunner, "find_suite", lambda *args, **kwargs: manifest)

    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "no_hint_suite", "--use-hint"])
    assert result.exit_code == 1
    assert "No server hint defined" in result.output


def test_test_use_hint_clean_hint_success(monkeypatch):
    from mcp_vcr.suite import SuiteManifest, SuiteResult, SuiteRunner

    manifest = SuiteManifest(
        name="valid_hint_suite",
        description="Bundled suite with clean hint",
        server_hint="python -m my_server --port 8080",
        transcripts=["t.yaml"],
        suite_dir=SuiteRunner.get_bundled_suites_dir() / "filesystem"
    )

    captured = {}

    async def mock_run_suite(self, m, server_args, **kwargs):
        captured["server_args"] = server_args
        return SuiteResult(
            suite_name=m.name,
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            exit_code=0,
            results=[{"transcript": "t.yaml", "status": "pass", "message": "OK", "diff": None}],
        )

    monkeypatch.setattr(SuiteRunner, "find_suite", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(SuiteRunner, "run_suite", mock_run_suite)

    runner = CliRunner()
    result = runner.invoke(main, ["test", "--suite", "valid_hint_suite", "--use-hint"])
    assert result.exit_code == 0
    assert captured["server_args"] == ["python", "-m", "my_server", "--port", "8080"]


