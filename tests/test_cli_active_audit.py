import json
from click.testing import CliRunner
from mcp_vcr.cli import main


def test_cli_audit_flags_mutual_exclusivity():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--active", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_cli_audit_requires_mode():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "Either --passive or --active flag is required" in result.output


def test_cli_audit_risks_without_active():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--passive", "--i-understand-the-risks", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "--i-understand-the-risks can only be used with --active" in result.output


def test_cli_audit_high_tier_requires_risks_flag():
    runner = CliRunner()
    # High tier without --i-understand-the-risks
    result = runner.invoke(main, ["audit", "--active", "--active-level", "high", "--", "python", "server.py"])
    assert result.exit_code == 2
    assert "High-tier canaries include command injection probes" in result.output
    assert "Pass --i-understand-the-risks to confirm" in result.output


def test_cli_audit_active_without_server():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--active"])
    assert result.exit_code == 1
    assert "No server command specified" in result.output


def test_cli_audit_json_error_envelope():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--active", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert data["command"] == "audit"
