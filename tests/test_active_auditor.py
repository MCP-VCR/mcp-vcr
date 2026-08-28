import asyncio
import json
import pytest
from mcp_vcr.active_auditor import (
    ActiveAuditEngine,
    ActiveAuditResult,
    _normalize_for_matching,
    check_canary_echo,
    generate_canary_payloads,
    redact_canaries,
)
from mcp_vcr.sandbox import SandboxConfig, SandboxedTransport


def test_generate_canary_payloads_tiers():
    tools = [
        {
            "name": "run_script",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
            },
        }
    ]

    # Low tier
    low_p = generate_canary_payloads(tools, severity_tier="low", allow_high=False)
    assert len(low_p) > 0
    assert all(p.severity_tier == "low" for p in low_p)
    assert any("path_traversal" in p.name for p in low_p)

    # Medium tier (includes low + medium)
    med_p = generate_canary_payloads(tools, severity_tier="medium", allow_high=False)
    assert len(med_p) > len(low_p)
    assert any(p.severity_tier == "low" for p in med_p)
    assert any(p.severity_tier == "medium" for p in med_p)
    assert not any(p.severity_tier == "high" for p in med_p)


def test_generate_canary_payloads_high_tier_gate():
    tools = [
        {
            "name": "exec_cmd",
            "inputSchema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
            },
        }
    ]

    # Must raise ValueError when high tier requested without allow_high=True
    with pytest.raises(ValueError, match="High-tier canary generation requires allow_high=True"):
        generate_canary_payloads(tools, severity_tier="high", allow_high=False)

    # When allow_high=True, must generate high-tier payloads
    high_p = generate_canary_payloads(tools, severity_tier="high", allow_high=True)
    assert any(p.severity_tier == "high" for p in high_p)
    assert any("command_injection" in p.name for p in high_p)


def test_normalize_for_matching():
    # HTML entity decoding
    assert _normalize_for_matching("root&#x3A;&#x2F;etc") == "root:/etc"
    # URL percent-decoding
    assert _normalize_for_matching("%2e%2e%2fpasswd") == "../passwd"
    # JSON string escape normalization
    assert _normalize_for_matching("line1\\nline2\\/test") == "line1\nline2/test"
    # Combination
    assert _normalize_for_matching("%2e%2e%2fpasswd&amp;test\\n") == "../passwd&test\n"


def test_check_canary_echo():
    markers = ["MCP_CANARY_12345", "root:"]

    # Literal match
    assert check_canary_echo("Response containing MCP_CANARY_12345 in output", markers) is True
    # Encoded match (URL encoded marker or text)
    assert check_canary_echo("Response %4D%43%50_CANARY_12345", markers) is True
    # Clean text
    assert check_canary_echo("Normal safe response", markers) is False


def test_redact_canaries_unconditional_parity():
    markers = ["CANARY_SECRET_XYZ"]
    raw_text = "Echoed CANARY_SECRET_XYZ in text"

    # Redaction on normalized text
    norm_text = _normalize_for_matching(raw_text)
    redacted = redact_canaries(norm_text, markers)

    assert "CANARY_SECRET_XYZ" not in redacted
    assert "<CANARY_DETECTED>" in redacted

    # Empty markers or text handles gracefully
    assert redact_canaries("", markers) == ""
    assert redact_canaries("no canary", []) == "no canary"


def test_sandbox_config_env(monkeypatch):
    monkeypatch.setenv("MY_SPECIAL_KEY", "SECRET_VALUE")
    sb = SandboxConfig(restrict_env=True, restrict_path=True, allow_env=["MY_SPECIAL_KEY"])
    st = SandboxedTransport(["python", "server.py"], config=sb)
    env = st._build_env()

    assert "PATH" in env
    assert env["PATH"].endswith("/usr/bin:/bin:/usr/local/bin")
    assert env.get("MY_SPECIAL_KEY") == "SECRET_VALUE"
    assert env.get("MCP_VCR_SANDBOX") == "1"



class MockTransport:
    def __init__(self, tools=None):
        self.tools = tools or [
            {
                "name": "read_file",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        ]
        self.running = False
        self._queue: Optional[asyncio.Queue] = None

    @property
    def queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    async def start(self):
        self.running = True

    async def read_client_message(self):
        return None

    async def write_to_server(self, data: bytes):
        try:
            req = json.loads(data.decode("utf-8").strip())
        except Exception:
            return

        req_id = req.get("id")
        method = req.get("method")
        if req_id is None:
            return

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "test-server"},
                },
            }
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.tools}}
        elif method == "tools/call":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": "root:x:0:0:root"}]},
            }
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}

        payload_bytes = json.dumps(resp).encode("utf-8") + b"\n"
        await self.queue.put(payload_bytes)

    async def read_server_message(self):
        return await self.queue.get()

    async def write_to_client(self, data: bytes):
        pass

    async def shutdown(self, sig=None):
        self.running = False
        return 0

    @property
    def server_running(self):
        return self.running




@pytest.mark.asyncio
async def test_active_audit_engine_run():
    def transport_factory():
        return MockTransport()

    engine = ActiveAuditEngine(timeout_ms=1000, severity_tier="low", delay_ms=0)
    result: ActiveAuditResult = await engine.run(transport_factory)

    assert result.tools_audited == 1
    assert result.canaries_executed > 0
    assert len(result.known_limitations) == 3
    assert result.summary["vulnerable"] > 0
    assert result.exit_code == 1


class MockEchoTransport(MockTransport):
    async def write_to_server(self, data: bytes):
        try:
            req = json.loads(data.decode("utf-8").strip())
        except Exception:
            return

        req_id = req.get("id")
        method = req.get("method")
        if req_id is None:
            return

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "echo-server"},
                },
            }
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.tools}}
        elif method == "tools/call":
            args = req.get("params", {}).get("arguments", {})
            val = args.get("path", "")
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Echo: {val}"}]},
            }
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}

        payload_bytes = json.dumps(resp).encode("utf-8") + b"\n"
        await self.queue.put(payload_bytes)


@pytest.mark.asyncio
async def test_active_audit_engine_verbatim_echo():
    def transport_factory():
        return MockEchoTransport()

    engine = ActiveAuditEngine(timeout_ms=1000, severity_tier="medium", delay_ms=0)
    result: ActiveAuditResult = await engine.run(transport_factory)

    assert result.tools_audited == 1
    assert result.canaries_executed > 0
    assert result.summary["vulnerable"] == 0
    assert result.summary["error"] > 0
    assert result.exit_code == 0


