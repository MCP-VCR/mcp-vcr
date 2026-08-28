import json
import sys
from pathlib import Path

from click.testing import CliRunner

from mcp_vcr.cli import main

INSECURE_SERVER_PY = str(Path(__file__).parent / "integration" / "toy_server_insecure.py")
BENIGN_SERVER_PY = str(Path(__file__).parent / "integration" / "toy_server_benign_jargon.py")
TOY_SERVER_PY = str(Path(__file__).parent / "integration" / "toy_server.py")


def test_audit_without_passive_flag_fails():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--", sys.executable, TOY_SERVER_PY])
    assert result.exit_code == 2
    assert "Either --passive or --active flag is required" in result.output



def test_audit_no_server_args_fails():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive"])
    assert result.exit_code == 1
    assert "No server command specified" in result.output


def test_audit_clean_toy_server_text_output():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--", sys.executable, TOY_SERVER_PY])
    assert result.exit_code == 0
    assert "Security Audit — Passive Mode" in result.output
    assert "toy-server" in result.output
    assert "Clean run" in result.output or "0 high, 0 medium" in result.output


def test_audit_clean_toy_server_json_envelope():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--json", "--", sys.executable, TOY_SERVER_PY])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["command"] == "audit"
    assert data["mode"] == "passive"
    assert "summary" in data
    assert "raw_summary" in data
    assert data["summary"]["high"] == 0
    assert data["summary"]["medium"] == 0


def test_audit_insecure_toy_server():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--", sys.executable, INSECURE_SERVER_PY])
    assert result.exit_code == 1
    assert "HIGH" in result.output
    assert "MEDIUM" in result.output
    assert "description-injection" in result.output
    assert "sensitive-field-exposure" in result.output


def test_audit_insecure_toy_server_json():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--json", "--", sys.executable, INSECURE_SERVER_PY])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "fail"
    assert data["summary"]["high"] > 0
    assert data["summary"]["medium"] > 0
    assert data["summary"]["total"] == len(data["findings"])
    assert "raw_summary" in data


def test_audit_severity_filter_high_ignores_medium():
    runner = CliRunner()
    # Insecure server has high and medium findings
    result = runner.invoke(
        main,
        ["audit", "--passive", "--severity", "high", "--json", "--", sys.executable, INSECURE_SERVER_PY],
    )
    # Since high findings exist, exit code is still 1, but medium findings are excluded from summary & findings
    data = json.loads(result.output)
    assert data["severity_filter"] == "high"
    for f in data["findings"]:
        assert f["severity"] == "high"
    assert data["summary"]["medium"] == 0
    assert data["summary"]["total"] == len(data["findings"])
    assert data["raw_summary"]["medium"] > 0  # Preserved in raw_summary


def test_audit_severity_filter_exit_code_zero_when_only_lower_severities():
    # Construct a server or filter where no findings match the filter
    runner = CliRunner()
    # On clean server with severity=high, zero high findings -> exit 0
    result = runner.invoke(
        main,
        ["audit", "--passive", "--severity", "high", "--json", "--", sys.executable, TOY_SERVER_PY],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["summary"]["total"] == 0


def test_audit_benign_jargon_server_zero_false_positives():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--json", "--", sys.executable, BENIGN_SERVER_PY])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    # Should have zero high and zero medium findings
    assert data["summary"]["high"] == 0
    assert data["summary"]["medium"] == 0
