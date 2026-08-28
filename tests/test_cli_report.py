import json
from pathlib import Path
from click.testing import CliRunner
from mcp_vcr.cli import main


def test_cli_report_html_default(tmp_path: Path):
    json_file = tmp_path / "verify.json"
    json_file.write_text(json.dumps({"status": "ok", "command": "verify", "snapshot": "snap1"}), encoding="utf-8")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # copy json_file into isolated dir
        p = Path("verify.json")
        p.write_text(json_file.read_text())

        result = runner.invoke(main, ["report", "--input", "verify.json"])
        assert result.exit_code == 0
        assert "Report generated successfully" in result.output

        out_html = Path("report.html")
        assert out_html.exists()
        content = out_html.read_text(encoding="utf-8")
        assert "mcp-vcr Test Report" in content


def test_cli_report_json_format(tmp_path: Path):
    json_file = tmp_path / "audit.json"
    json_file.write_text(json.dumps({"status": "ok", "command": "audit", "mode": "passive"}), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["report", "--format", "json", "--input", str(json_file), "-o", str(tmp_path / "custom_report.json")])
    assert result.exit_code == 0

    out_json = tmp_path / "custom_report.json"
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["report_schema_version"] == 1


def test_cli_report_missing_input():
    runner = CliRunner()
    result = runner.invoke(main, ["report", "--input", "non_existent_file.json"])
    assert result.exit_code == 2
    assert "does not exist" in result.output

