import json
import sys
import pytest
from click.testing import CliRunner
from mcp_vcr.cli import main


@pytest.fixture
def cli_snapshot(tmp_path):
    p = tmp_path / "cli_golden.yaml"
    content = """meta:
  version: 1
  recorded_at: '2026-08-25T12:00:00Z'
  session_id: 'clitest0'
  server_command: ['python', 'server.py']
  protocol_version: '2024-11-05'
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: '2.0'
      id: 1
      method: 'tools/call'
      params:
        name: 'read_file'
        arguments:
          path: '/tmp/foo'
"""
    p.write_text(content, encoding="utf-8")
    return p


def test_fuzz_requires_snapshot_argument():
    runner = CliRunner()
    result = runner.invoke(main, ["fuzz"])
    assert result.exit_code != 0
    assert "Missing argument 'SNAPSHOT'" in result.output or "Error:" in result.output


def test_fuzz_requires_server_command(cli_snapshot):
    runner = CliRunner()
    result = runner.invoke(main, ["fuzz", str(cli_snapshot)])
    assert result.exit_code != 0
    assert "No server command specified" in result.output or "ERROR" in result.output


def test_fuzz_json_output_envelope(cli_snapshot):
    runner = CliRunner()
    cmd = [
        "fuzz",
        str(cli_snapshot),
        "--json",
        "--strategy", "field_removal",
        "--strategy", "type_confusion",
        "--max-mutations", "2",
        "--", sys.executable, "tests/integration/toy_server_benign_jargon.py"
    ]
    result = runner.invoke(main, cmd)
    assert result.exit_code == 0

    envelope = json.loads(result.stdout)
    assert envelope["command"] == "fuzz"
    assert envelope["status"] in ("ok", "fail", "aborted")
    assert "total_mutations" in envelope
    assert "results" in envelope
    assert "summary" in envelope
    assert "exit_code" in envelope


def test_fuzz_with_strategy_filter(cli_snapshot):
    runner = CliRunner()
    cmd = [
        "fuzz",
        str(cli_snapshot),
        "--json",
        "--strategy", "field_removal",
        "--", sys.executable, "tests/integration/toy_server_benign_jargon.py"
    ]
    result = runner.invoke(main, cmd)
    assert result.exit_code == 0

    envelope = json.loads(result.stdout)
    for case in envelope["results"]:
        assert case["mutation"]["strategy"] == "field_removal"


def test_fuzz_human_output_format(cli_snapshot):
    runner = CliRunner()
    cmd = [
        "fuzz",
        str(cli_snapshot),
        "--strategy", "field_removal",
        "--max-mutations", "2",
        "--", sys.executable, "tests/integration/toy_server_benign_jargon.py"
    ]
    result = runner.invoke(main, cmd)
    stderr_out = result.stderr

    assert "Fuzz Testing — mcp-vcr fuzz" in stderr_out
    assert "Snapshot:" in stderr_out
    assert "━━ Results ━━" in stderr_out
    assert "━━ Summary ━━" in stderr_out


def test_fuzz_seed_flag_random(cli_snapshot):
    runner = CliRunner()
    cmd = [
        "fuzz",
        str(cli_snapshot),
        "--seed", "random",
        "--strategy", "type_confusion",
        "--max-mutations", "2",
        "--json",
        "--", sys.executable, "tests/integration/toy_server_benign_jargon.py"
    ]
    result = runner.invoke(main, cmd)
    assert result.exit_code == 0

    envelope = json.loads(result.stdout)
    assert envelope["resource_limits"]["seed"] is not None


def test_fuzz_seed_flag_integer(cli_snapshot):
    runner = CliRunner()
    cmd = [
        "fuzz",
        str(cli_snapshot),
        "--seed", "42",
        "--strategy", "type_confusion",
        "--max-mutations", "2",
        "--json",
        "--", sys.executable, "tests/integration/toy_server_benign_jargon.py"
    ]
    result = runner.invoke(main, cmd)
    assert result.exit_code == 0

    envelope = json.loads(result.stdout)
    assert envelope["resource_limits"]["seed"] == 42


def test_fuzz_fragile_server_detects_issues(cli_snapshot):
    runner = CliRunner()
    cmd = [
        "fuzz",
        str(cli_snapshot),
        "--timeout", "500",
        "--max-mutations", "5",
        "--json",
        "--", sys.executable, "tests/integration/toy_server_fragile.py"
    ]
    result = runner.invoke(main, cmd)
    assert result.exit_code != 0

    envelope = json.loads(result.stdout)
    assert envelope["status"] in ("fail", "aborted")




