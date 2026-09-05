import importlib.resources
import json
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as _html_escape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPORT_SCHEMA_VERSION = 1


def _safe(text: Any) -> str:
    """HTML-escape any value before embedding in the report template.

    This is the ONLY path through which untrusted data reaches HTML output.
    Every renderer calls this for every data field. No raw string interpolation.
    """
    if text is None:
        return ""
    return _html_escape(str(text), quote=True)


@dataclass
class ReportSection:
    command: str
    status: str
    data: Dict[str, Any]
    timestamp: Optional[str] = None


@dataclass
class ReportData:
    title: str
    generated_at: str
    mcp_vcr_version: str
    report_schema_version: int
    sections: List[ReportSection]


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("mcp-vcr")
    except Exception:
        return "0.2.2"


def _load_template() -> str:
    try:
        return (
            importlib.resources.files("mcp_vcr")
            .joinpath("report_template.html")
            .read_text(encoding="utf-8")
        )
    except Exception:
        # Fallback if package reading fails
        tmpl_path = Path(__file__).parent / "report_template.html"
        if tmpl_path.exists():
            return tmpl_path.read_text(encoding="utf-8")
        raise FileNotFoundError("Could not find report_template.html")


class ReportEngine:
    """ReportEngine consumes structured JSON outputs from verify, test, audit, and fuzz commands

    and produces standalone HTML or JSON test reports.
    """

    @staticmethod
    def from_json_files(paths: List[Path], title: Optional[str] = None) -> ReportData:
        sections: List[ReportSection] = []
        for path in paths:
            if not path.exists():
                raise FileNotFoundError(f"Report input file not found: '{path}'")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                raise ValueError(f"Failed to parse JSON from '{path}': {e}")

            if not isinstance(data, dict):
                raise ValueError(f"JSON content in '{path}' must be a dictionary envelope")

            command = str(data.get("command", "unknown"))
            status = str(data.get("status", "unknown"))
            sections.append(
                ReportSection(
                    command=command,
                    status=status,
                    data=data,
                )
            )

        return ReportData(
            title=title or "mcp-vcr Test Report",
            generated_at=datetime.now(timezone.utc).isoformat(),
            mcp_vcr_version=_get_version(),
            report_schema_version=REPORT_SCHEMA_VERSION,
            sections=sections,
        )

    def generate_html(self, data: ReportData, output_path: Path) -> Path:
        """Generate self-contained HTML report from template."""
        template_str = _load_template()

        sections_html = []
        for section in data.sections:
            renderer = self._get_renderer(section.command)
            sections_html.append(renderer(section))

        summary_html = self._render_summary(data.sections)
        metrics_html = self._render_performance_metrics(data.sections)
        if metrics_html:
            sections_html.append(metrics_html)

        html = string.Template(template_str).safe_substitute(
            title=_safe(data.title),
            generated_at=_safe(data.generated_at),
            version=_safe(data.mcp_vcr_version),
            report_schema_version=data.report_schema_version,
            summary_html=summary_html,
            sections_html="\n".join(sections_html),
        )

        output_path.write_text(html, encoding="utf-8")
        return output_path

    def generate_json(self, data: ReportData, output_path: Path) -> Path:
        """Generate JSON report file."""
        report = {
            "report_schema_version": data.report_schema_version,
            "title": data.title,
            "generated_at": data.generated_at,
            "mcp_vcr_version": data.mcp_vcr_version,
            "sections": [
                {
                    "command": s.command,
                    "status": s.status,
                    "data": s.data,
                }
                for s in data.sections
            ],
        }
        output_path.write_text(
            json.dumps(report, indent=2, default=str),
            encoding="utf-8",
        )
        return output_path

    def _get_renderer(self, command: str) -> Callable[[ReportSection], str]:
        renderers: Dict[str, Callable[[ReportSection], str]] = {
            "verify": self._render_verify_section,
            "test": self._render_test_section,
            "audit": self._render_audit_section,
            "fuzz": self._render_fuzz_section,
        }
        return renderers.get(command, self._render_unknown_section)

    def _render_summary(self, sections: List[ReportSection]) -> str:
        total = len(sections)
        passed = sum(1 for s in sections if s.status in ("ok", "pass"))
        failed = total - passed

        status_class = "badge-pass" if failed == 0 else "badge-fail"
        status_text = "PASS" if failed == 0 else "FAIL"

        return f"""
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <span class="badge {status_class}">{status_text}</span>
            <strong style="margin-left: 12px;">{passed}/{total} Sections Passed</strong>
          </div>
          <div style="font-size: 14px; color: var(--text-muted);">
            Total Section Runs: {_safe(total)}
          </div>
        </div>
        """

    def _render_verify_section(self, section: ReportSection) -> str:
        d = section.data
        status_badge = (
            '<span class="badge badge-pass">PASS</span>'
            if section.status == "ok"
            else '<span class="badge badge-fail">FAIL</span>'
        )

        snapshot_name = _safe(d.get("snapshot", "snapshot"))
        diff_text = d.get("diff", "")

        diff_html = ""
        if diff_text:
            lines = str(diff_text).splitlines()
            formatted_lines = []
            for line in lines:
                s_line = _safe(line)
                if line.startswith("+"):
                    formatted_lines.append(f'<span class="line-add">{s_line}</span>')
                elif line.startswith("-"):
                    formatted_lines.append(f'<span class="line-del">{s_line}</span>')
                else:
                    formatted_lines.append(s_line)
            diff_html = f"<pre>{'<br>'.join(formatted_lines)}</pre>"
        else:
            diff_html = "<p style='color: var(--accent-green);'>No differences detected.</p>"

        return f"""
        <details open>
          <summary>
            <span>Verify — {snapshot_name}</span>
            {status_badge}
          </summary>
          <div class="section-content">
            <p><strong>Snapshot:</strong> {snapshot_name}</p>
            {diff_html}
          </div>
        </details>
        """

    def _render_test_section(self, section: ReportSection) -> str:
        d = section.data
        status_badge = (
            '<span class="badge badge-pass">PASS</span>'
            if section.status == "ok"
            else '<span class="badge badge-fail">FAIL</span>'
        )

        suite_name = _safe(d.get("suite", "all"))
        results = d.get("results", [])
        if not isinstance(results, list):
            results = []

        rows = []
        for r in results:
            if not isinstance(r, dict):
                continue
            t_name = _safe(r.get("transcript", ""))
            t_status = _safe(r.get("status", ""))
            t_msg = _safe(r.get("message", ""))
            badge = (
                '<span class="badge badge-pass">PASS</span>'
                if t_status == "pass"
                else '<span class="badge badge-fail">FAIL</span>'
            )
            rows.append(f"<tr><td>{t_name}</td><td>{badge}</td><td>{t_msg}</td></tr>")

        table_html = f"""
        <table>
          <thead>
            <tr><th>Transcript</th><th>Status</th><th>Message</th></tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
        """

        return f"""
        <details open>
          <summary>
            <span>Test Suite — {suite_name}</span>
            {status_badge}
          </summary>
          <div class="section-content">
            {table_html}
          </div>
        </details>
        """

    def _render_audit_section(self, section: ReportSection) -> str:
        d = section.data
        status_badge = (
            '<span class="badge badge-pass">PASS</span>'
            if section.status == "ok"
            else '<span class="badge badge-fail">FAIL</span>'
        )

        mode = _safe(d.get("mode", "passive"))
        findings = d.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        rows = []
        if mode == "active":
            for f in findings:
                if not isinstance(f, dict):
                    continue
                tool = _safe(f.get("tool_name", f.get("tool", "")))
                canary_name = _safe(
                    f.get("canary", {}).get("name", f.get("canary", ""))
                    if isinstance(f.get("canary"), dict)
                    else f.get("canary", "")
                )
                verdict = _safe(f.get("verdict", ""))
                snippet = _safe(f.get("response_snippet", ""))
                v_badge = (
                    '<span class="badge badge-fail">VULNERABLE</span>'
                    if verdict == "vulnerable"
                    else '<span class="badge badge-pass">SAFE</span>'
                )
                rows.append(
                    f"<tr><td>{tool}</td><td>{canary_name}</td><td>{v_badge}</td><td><code>{snippet}</code></td></tr>"
                )

            table_html = f"""
            <table>
              <thead>
                <tr><th>Tool</th><th>Canary Payload</th><th>Verdict</th><th>Response Snippet</th></tr>
              </thead>
              <tbody>
                {''.join(rows) if rows else '<tr><td colspan="4">No active audit findings.</td></tr>'}
              </tbody>
            </table>
            """
        else:
            for f in findings:
                if not isinstance(f, dict):
                    continue
                check = _safe(f.get("check", ""))
                severity = _safe(f.get("severity", ""))
                tool = _safe(f.get("tool", "server"))
                msg = _safe(f.get("message", ""))
                sev_badge = (
                    '<span class="badge badge-fail">HIGH</span>'
                    if severity == "high"
                    else (
                        '<span class="badge badge-warn">MEDIUM</span>'
                        if severity == "medium"
                        else '<span class="badge badge-pass">INFO</span>'
                    )
                )
                rows.append(
                    f"<tr><td>{check}</td><td>{tool}</td><td>{sev_badge}</td><td>{msg}</td></tr>"
                )

            table_html = f"""
            <table>
              <thead>
                <tr><th>Check</th><th>Target</th><th>Severity</th><th>Message</th></tr>
              </thead>
              <tbody>
                {''.join(rows) if rows else '<tr><td colspan="4">No passive findings detected.</td></tr>'}
              </tbody>
            </table>
            """

        return f"""
        <details open>
          <summary>
            <span>Security Audit — {mode.capitalize()} Mode</span>
            {status_badge}
          </summary>
          <div class="section-content">
            {table_html}
          </div>
        </details>
        """

    def _render_fuzz_section(self, section: ReportSection) -> str:
        d = section.data
        status_badge = (
            '<span class="badge badge-pass">PASS</span>'
            if section.status == "ok"
            else '<span class="badge badge-fail">FAIL</span>'
        )

        snapshot = _safe(d.get("source_snapshot", "snapshot"))
        summary = d.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}

        results = d.get("results", [])
        if not isinstance(results, list):
            results = []

        rows = []
        for r in results:
            if not isinstance(r, dict):
                continue
            mut_name = _safe(
                r.get("mutation", {}).get("name", "")
                if isinstance(r.get("mutation"), dict)
                else r.get("mutation", "")
            )
            verdict = _safe(r.get("verdict", ""))
            detail = _safe(r.get("detail", ""))
            elapsed = _safe(r.get("elapsed_ms", 0))

            v_class = "badge-pass" if verdict == "pass" else "badge-fail"
            rows.append(
                f"<tr><td>{mut_name}</td><td><span class=\"badge {v_class}\">{verdict}</span></td><td>{detail}</td><td>{elapsed}ms</td></tr>"
            )

        table_html = f"""
        <table>
          <thead>
            <tr><th>Mutation</th><th>Verdict</th><th>Detail</th><th>Elapsed</th></tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
        """

        return f"""
        <details open>
          <summary>
            <span>Fuzz Testing — {snapshot}</span>
            {status_badge}
          </summary>
          <div class="section-content">
            <p><strong>Summary:</strong> {_safe(summary)}</p>
            {table_html}
          </div>
        </details>
        """

    def _render_unknown_section(self, section: ReportSection) -> str:
        cmd = _safe(section.command)
        raw_data = _safe(json.dumps(section.data, indent=2))
        return f"""
        <details open>
          <summary>
            <span>Unrecognized Section — {cmd}</span>
            <span class="badge badge-warn">UNKNOWN</span>
          </summary>
          <div class="section-content">
            <pre>{raw_data}</pre>
          </div>
        </details>
        """

    def _render_performance_metrics(self, sections: List[ReportSection]) -> Optional[str]:
        elapsed_times: List[int] = []

        for s in sections:
            results = s.data.get("results", [])
            if isinstance(results, list):
                for r in results:
                    if isinstance(r, dict) and "elapsed_ms" in r:
                        try:
                            elapsed_times.append(int(r["elapsed_ms"]))
                        except (ValueError, TypeError):
                            pass

            findings = s.data.get("findings", [])
            if isinstance(findings, list):
                for f in findings:
                    if isinstance(f, dict) and "elapsed_ms" in f:
                        try:
                            elapsed_times.append(int(f["elapsed_ms"]))
                        except (ValueError, TypeError):
                            pass

        if not elapsed_times:
            return None

        elapsed_times.sort()
        count = len(elapsed_times)
        min_v = elapsed_times[0]
        max_v = elapsed_times[-1]

        def _percentile(p: float) -> int:
            idx = int(round((p / 100.0) * (count - 1)))
            return elapsed_times[max(0, min(count - 1, idx))]

        p50 = _percentile(50)
        p95 = _percentile(95)
        p99 = _percentile(99)

        return f"""
        <details open>
          <summary>
            <span>Performance Metrics</span>
            <span class="badge badge-pass">LATENCY</span>
          </summary>
          <div class="section-content">
            <table>
              <thead>
                <tr><th>Invocations</th><th>Min</th><th>Max</th><th>p50</th><th>p95</th><th>p99</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>{_safe(count)}</td>
                  <td>{_safe(min_v)}ms</td>
                  <td>{_safe(max_v)}ms</td>
                  <td>{_safe(p50)}ms</td>
                  <td>{_safe(p95)}ms</td>
                  <td>{_safe(p99)}ms</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
        """
