import json
import pytest
from pathlib import Path
from mcp_vcr.reporter import ReportEngine, ReportData, REPORT_SCHEMA_VERSION


def test_reporter_xss_prevention(tmp_path: Path):
    # Envelope containing malicious user/server payload strings
    malicious_envelope = {
        "status": "fail",
        "command": "audit",
        "mode": "active",
        "title": "<script>alert('title_xss')</script>",
        "findings": [
            {
                "tool": "tool_<script>alert(1)</script>",
                "canary": "canary_<img onerror=alert(2) src=x>",
                "verdict": "vulnerable",
                "elapsed_ms": 15,
                "response_snippet": "<iframe src='javascript:alert(3)'></iframe>",
                "detail": "<a href='javascript:alert(4)'>clickme</a>",
            }
        ],
    }

    input_file = tmp_path / "audit_xss.json"
    input_file.write_text(json.dumps(malicious_envelope), encoding="utf-8")

    report_data = ReportEngine.from_json_files([input_file], title="XSS Test Report <script>")
    engine = ReportEngine()
    html_file = tmp_path / "report.html"
    engine.generate_html(report_data, html_file)

    html_content = html_file.read_text(encoding="utf-8")

    # Hard security assertions: no raw unescaped HTML injection strings allowed
    assert "<script>alert(" not in html_content
    assert "<img onerror=" not in html_content
    assert "<iframe src=" not in html_content
    assert "href='javascript:" not in html_content

    # Escaped representations must be present
    assert "&lt;script&gt;" in html_content
    assert "&lt;iframe" in html_content


def test_reporter_json_round_trip(tmp_path: Path):
    envelope = {
        "status": "ok",
        "command": "verify",
        "snapshot": "my_golden.yaml",
        "diff": "",
    }
    input_file = tmp_path / "verify.json"
    input_file.write_text(json.dumps(envelope), encoding="utf-8")

    report_data = ReportEngine.from_json_files([input_file], title="Integration Report")
    engine = ReportEngine()
    out_json = tmp_path / "report.json"
    engine.generate_json(report_data, out_json)

    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert data["title"] == "Integration Report"
    assert len(data["sections"]) == 1
    assert data["sections"][0]["command"] == "verify"


def test_reporter_version_tolerance(tmp_path: Path):
    # Envelope missing command field
    old_envelope = {
        "status": "ok",
        "custom_data": "legacy",
    }
    input_file = tmp_path / "legacy.json"
    input_file.write_text(json.dumps(old_envelope), encoding="utf-8")

    report_data = ReportEngine.from_json_files([input_file])
    engine = ReportEngine()
    out_html = tmp_path / "report.html"
    engine.generate_html(report_data, out_html)

    content = out_html.read_text(encoding="utf-8")
    assert "Unrecognized Section — unknown" in content


def test_reporter_performance_metrics_omitted_when_no_timing(tmp_path: Path):
    envelope = {
        "status": "ok",
        "command": "verify",
        "snapshot": "test_snap",
    }
    input_file = tmp_path / "verify.json"
    input_file.write_text(json.dumps(envelope), encoding="utf-8")

    report_data = ReportEngine.from_json_files([input_file])
    engine = ReportEngine()
    out_html = tmp_path / "report.html"
    engine.generate_html(report_data, out_html)

    content = out_html.read_text(encoding="utf-8")
    # Performance metrics section must be omitted
    assert "Performance Metrics" not in content
