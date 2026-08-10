import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
from click.testing import CliRunner
import pytest
import yaml

from mcp_vcr.generator import GeneratorEngine, DiscoveryResult, ToolCallResult
from mcp_vcr.transports.base import Transport
from mcp_vcr.validator import validate_file
from mcp_vcr.cli import main


class MockTransport(Transport):
    """Mock transport implementation simulating server responses."""

    def __init__(self, responses=None, on_write=None, die_after_reads=None, sleep_delay=None):
        self.responses = list(responses or [])
        self.written_messages = []
        self.on_write = on_write
        self._running = False
        self._read_count = 0
        self.die_after_reads = die_after_reads
        self.sleep_delay = sleep_delay

    async def start(self) -> None:
        self._running = True

    async def read_client_message(self) -> Optional[bytes]:
        return None

    async def write_to_server(self, data: bytes) -> None:
        payload = json.loads(data.decode("utf-8"))
        self.written_messages.append(payload)
        if self.on_write:
            self.on_write(payload, self)

    async def read_server_message(self) -> Optional[bytes]:
        if not self._running:
            return None
        if self.sleep_delay:
            await asyncio.sleep(self.sleep_delay)
        if self.die_after_reads is not None and self._read_count >= self.die_after_reads:
            self._running = False
            return None
        if not self.responses:
            return None
        self._read_count += 1
        resp = self.responses.pop(0)
        if isinstance(resp, (dict, list)):
            return (json.dumps(resp) + "\n").encode("utf-8")
        elif isinstance(resp, str):
            return resp.encode("utf-8")
        return resp

    async def write_to_client(self, data: bytes) -> None:
        pass

    async def shutdown(self, sig: Optional[int] = None) -> int:
        self._running = False
        return 0

    @property
    def server_running(self) -> bool:
        return self._running


def test_generate_placeholder_args_basic_types():
    schema = {
        "type": "object",
        "properties": {
            "str_field": {"type": "string"},
            "num_field": {"type": "number"},
            "int_field": {"type": "integer"},
            "bool_field": {"type": "boolean"},
            "arr_field": {"type": "array"},
            "enum_field": {"type": "string", "enum": ["optionA", "optionB"]},
            "obj_field": {"type": "object", "properties": {"sub": {"type": "string"}}, "required": ["sub"]}
        },
        "required": ["str_field", "num_field", "int_field", "bool_field", "arr_field", "enum_field", "obj_field"]
    }
    args = GeneratorEngine.generate_placeholder_args(schema)
    assert args["str_field"] == "example_str_field"
    assert args["num_field"] == 0
    assert args["int_field"] == 0
    assert args["bool_field"] is False
    assert args["arr_field"] == []
    assert args["enum_field"] == "optionA"
    assert args["obj_field"] == {"sub": "example_sub"}


def test_generate_placeholder_args_required_only():
    schema = {
        "type": "object",
        "properties": {
            "req_field": {"type": "string"},
            "opt_field": {"type": "string"}
        },
        "required": ["req_field"]
    }
    args = GeneratorEngine.generate_placeholder_args(schema)
    assert "req_field" in args
    assert "opt_field" not in args


def test_generate_placeholder_args_nested_depth_2():
    # Depth 1 -> Depth 2 -> Depth 3 (object)
    schema = {
        "type": "object",
        "properties": {
            "level1": {
                "type": "object",
                "properties": {
                    "level2": {
                        "type": "object",
                        "properties": {
                            "level3": {
                                "type": "object",
                                "properties": {"val": {"type": "string"}},
                                "required": ["val"]
                            }
                        },
                        "required": ["level3"]
                    }
                },
                "required": ["level2"]
            }
        },
        "required": ["level1"]
    }
    args = GeneratorEngine.generate_placeholder_args(schema, max_depth=2)
    # level1 (depth 1) is recursed -> {"level2": ...}
    # level2 (depth 2) is recursed -> {"level3": ...}
    # level3 (depth 3 object) collapses to {}
    assert "level1" in args
    assert "level2" in args["level1"]
    assert "level3" in args["level1"]["level2"]
    assert args["level1"]["level2"]["level3"] == {}


def test_generate_placeholder_args_empty_schema():
    assert GeneratorEngine.generate_placeholder_args(None) == {}
    assert GeneratorEngine.generate_placeholder_args({}) == {}
    assert GeneratorEngine.generate_placeholder_args({"properties": {}}) == {}
    assert GeneratorEngine.generate_placeholder_args({"required": []}) == {}


@pytest.mark.asyncio
async def test_discover_stdio_basic():
    init_resp = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test-mock-server", "version": "1.2.3"}
        }
    }
    tools_resp = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "calc",
                    "description": "Calculator",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"expr": {"type": "string"}},
                        "required": ["expr"]
                    }
                }
            ]
        }
    }

    mock_transport = MockTransport(responses=[init_resp, tools_resp])
    engine = GeneratorEngine()
    discovery = await engine.discover(mock_transport)

    assert discovery.protocol_version == "2024-11-05"
    assert discovery.server_info["name"] == "test-mock-server"
    assert discovery.server_info["version"] == "1.2.3"
    assert len(discovery.tools) == 1
    assert discovery.tools[0]["name"] == "calc"
    assert len(mock_transport.written_messages) == 3
    assert mock_transport.written_messages[0]["method"] == "initialize"
    assert mock_transport.written_messages[1]["method"] == "notifications/initialized"
    assert mock_transport.written_messages[2]["method"] == "tools/list"


@pytest.mark.asyncio
async def test_discover_timeout():
    mock_transport = MockTransport(responses=[], sleep_delay=1.0)
    engine = GeneratorEngine()
    with pytest.raises(asyncio.TimeoutError):
        await engine.discover(mock_transport, timeout_ms=50)


@pytest.mark.asyncio
async def test_discover_server_crash():
    mock_transport = MockTransport(responses=[None])  # EOF
    engine = GeneratorEngine()
    with pytest.raises(ConnectionError):
        await engine.discover(mock_transport, timeout_ms=500)


@pytest.mark.asyncio
async def test_call_tools_partial_failure():
    discovery = DiscoveryResult(
        protocol_version="2024-11-05",
        server_info={"name": "test-server", "version": "1.0.0"},
        capabilities={},
        tools=[
            {"name": "tool1", "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}},
            {"name": "tool2", "inputSchema": {"type": "object", "properties": {"b": {"type": "number"}}, "required": ["b"]}},
            {"name": "tool3", "inputSchema": {"type": "object", "properties": {"c": {"type": "boolean"}}, "required": ["c"]}}
        ],
        initialize_response={"jsonrpc": "2.0", "id": 1, "result": {}},
        tools_list_response={"jsonrpc": "2.0", "id": 2, "result": {}}
    )

    resp1 = {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "tool1_ok"}]}}
    resp2 = {"jsonrpc": "2.0", "id": 4, "error": {"code": -32602, "message": "Invalid param b"}}
    resp3 = {"jsonrpc": "2.0", "id": 5, "result": {"content": [{"type": "text", "text": "tool3_ok"}]}}

    mock_transport = MockTransport(responses=[resp1, resp2, resp3])
    await mock_transport.start()
    engine = GeneratorEngine()

    results = await engine.call_tools(mock_transport, discovery)

    assert len(results) == 3
    assert results[0].status == "success"
    assert results[0].tool_name == "tool1"

    assert results[1].status == "error"
    assert results[1].tool_name == "tool2"
    assert "Invalid param b" in (results[1].error_message or "")

    assert results[2].status == "success"
    assert results[2].tool_name == "tool3"


@pytest.mark.asyncio
async def test_call_tools_transport_death():
    discovery = DiscoveryResult(
        protocol_version="2024-11-05",
        server_info={"name": "test-server", "version": "1.0.0"},
        capabilities={},
        tools=[
            {"name": "tool1", "inputSchema": {}},
            {"name": "tool2", "inputSchema": {}},
            {"name": "tool3", "inputSchema": {}}
        ],
        initialize_response={"jsonrpc": "2.0", "id": 1, "result": {}},
        tools_list_response={"jsonrpc": "2.0", "id": 2, "result": {}}
    )

    resp1 = {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    # Die after 1 response
    mock_transport = MockTransport(responses=[resp1], die_after_reads=1)
    await mock_transport.start()
    engine = GeneratorEngine()

    results = await engine.call_tools(mock_transport, discovery, timeout_ms=100)

    assert len(results) == 3
    assert results[0].status == "success"
    assert results[1].status == "error"  # failed on connection error
    assert results[2].status == "skipped"  # skipped because transport died


def test_build_transcript_structure_and_validation(tmp_path):
    init_resp = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "sample-server", "version": "1.0.0"}
        }
    }
    tools_resp = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}}]
        }
    }
    tool_call_res = ToolCallResult(
        tool_name="echo",
        request_payload={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"msg": "example_msg"}}},
        response_payload={"jsonrpc": "2.0", "id": 3, "result": {"echo": "example_msg"}},
        status="success"
    )

    discovery = DiscoveryResult(
        protocol_version="2024-11-05",
        server_info={"name": "sample-server", "version": "1.0.0"},
        capabilities={},
        tools=[{"name": "echo"}],
        initialize_response=init_resp,
        tools_list_response=tools_resp,
        tool_call_results=[tool_call_res]
    )

    engine = GeneratorEngine()
    transcript_data = engine.build_transcript(discovery, server_command=["python", "my_server.py"])

    # Check meta
    assert transcript_data["meta"]["version"] == 1
    assert transcript_data["meta"]["client_hint"] == "mcp-vcr-generate"
    assert transcript_data["meta"]["server_command"] == ["python", "my_server.py"]

    # Check messages order: init req, init resp, initialized notif, tools/list req, tools/list resp, tool req, tool resp
    messages = transcript_data["messages"]
    assert len(messages) == 7
    assert messages[0]["payload"]["method"] == "initialize"
    assert messages[0]["dir"] == "c2s"
    assert messages[1]["dir"] == "s2c"
    assert messages[2]["payload"]["method"] == "notifications/initialized"
    assert messages[2]["dir"] == "c2s"
    assert messages[3]["payload"]["method"] == "tools/list"
    assert messages[3]["dir"] == "c2s"
    assert messages[4]["dir"] == "s2c"
    assert messages[5]["payload"]["method"] == "tools/call"
    assert messages[5]["dir"] == "c2s"
    assert messages[6]["dir"] == "s2c"

    # Write and validate snapshot
    out_file = tmp_path / "sample_golden.yaml"
    engine.write_snapshot(transcript_data, out_file)
    assert out_file.exists()

    validated = validate_file(out_file, allow_v0=False)
    assert validated.meta.version == 1
    assert len(validated.messages) == 7


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workspace_server() -> Path:
    server = REPO_ROOT / "server.py"
    if not server.exists():
        pytest.skip("server.py fixture not present in repository root")
    return server


def test_write_snapshot_atomic(tmp_path, monkeypatch):
    engine = GeneratorEngine()
    transcript_data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-10T12:00:00Z",
            "session_id": "abcd1234",
            "server_command": ["python", "server.py"],
            "protocol_version": "2024-11-05",
            "client_hint": "mcp-vcr-generate",
            "schema_version": "1.0"
        },
        "messages": [
            {
                "t": 0,
                "dir": "c2s",
                "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            }
        ]
    }

    out_file = tmp_path / "atomic_golden.yaml"
    engine.write_snapshot(transcript_data, out_file)
    assert out_file.exists()


def test_write_snapshot_rollback_on_invalid(tmp_path):
    engine = GeneratorEngine()
    out_file = tmp_path / "rollback_golden.yaml"
    out_file.write_text("pre-existing")

    with pytest.raises(Exception):
        engine.write_snapshot({"meta": {"version": 1}, "messages": "not-a-list"}, out_file)

    assert out_file.read_text() == "pre-existing"
    assert list(tmp_path.glob(".*tmp")) == []


def test_cli_dry_run_no_file(tmp_path, monkeypatch, workspace_server):
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(main, ["generate", "--server", f"{sys.executable} {workspace_server}", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run complete. No snapshot written." in result.output
    assert not (tmp_path / "snapshots").exists()


def test_cli_flags_precedence(tmp_path, monkeypatch, workspace_server):
    """Verify --no-call takes precedence over --yes (no tools/call executed)."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {workspace_server}",
        "--yes",
        "--no-call",
        "--output", str(tmp_path / "precedence_golden.yaml")
    ])
    assert result.exit_code == 0
    assert "Golden snapshot written to:" in result.output

    # Inspect generated file
    with open(tmp_path / "precedence_golden.yaml", "r") as f:
        data = yaml.safe_load(f)
    methods = [m["payload"].get("method") for m in data["messages"] if "method" in m["payload"]]
    assert "tools/call" not in methods


def test_cli_non_interactive_skip(tmp_path, monkeypatch, workspace_server):
    """Verify non-interactive mode without --yes auto-skips tools/call with warning."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # In CliRunner, stdin is not a tty by default
    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {workspace_server}",
        "--output", str(tmp_path / "non_interactive_golden.yaml")
    ])
    assert result.exit_code == 0
    assert "Non-interactive mode detected" in result.output

    with open(tmp_path / "non_interactive_golden.yaml", "r") as f:
        data = yaml.safe_load(f)
    methods = [m["payload"].get("method") for m in data["messages"] if "method" in m["payload"]]
    assert "tools/call" not in methods


def test_generate_end_to_end(tmp_path, monkeypatch, workspace_server):
    """Run mcp-vcr generate --server 'python server.py' --yes against real test server."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {workspace_server}",
        "--yes",
        "--name", "test_server"
    ])
    assert result.exit_code == 0
    assert "tools/call: 1/1 stubs generated" in result.output

    out_file = tmp_path / "snapshots" / "test_server_golden.yaml"
    assert out_file.exists()

    validated = validate_file(out_file, allow_v0=False)
    assert validated.meta.version == 1
    assert len(validated.messages) >= 7

    # Verify toolA was called
    tool_call_msgs = [m for m in validated.messages if m.payload.get("method") == "tools/call"]
    assert len(tool_call_msgs) == 1
    assert tool_call_msgs[0].payload["params"]["name"] == "toolA"


def test_cli_interactive_prompt_yes(tmp_path, monkeypatch, workspace_server):
    """When TTY is present and user responds 'y', tools/call is executed."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mcp_vcr.cli._is_stdin_tty", lambda: True)

    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {workspace_server}",
        "--output", str(tmp_path / "interactive_yes.yaml")
    ], input="y\n")

    assert result.exit_code == 0
    assert "Proceed with tools/call?" in result.output
    assert "tools/call: 1/1 stubs generated" in result.output

    with open(tmp_path / "interactive_yes.yaml", "r") as f:
        data = yaml.safe_load(f)
    methods = [m["payload"].get("method") for m in data["messages"] if "method" in m["payload"]]
    assert "tools/call" in methods


def test_cli_interactive_prompt_no(tmp_path, monkeypatch, workspace_server):
    """When TTY is present and user responds 'n', tools/call is skipped."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mcp_vcr.cli._is_stdin_tty", lambda: True)

    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {workspace_server}",
        "--output", str(tmp_path / "interactive_no.yaml")
    ], input="n\n")

    assert result.exit_code == 0
    assert "Proceed with tools/call?" in result.output

    with open(tmp_path / "interactive_no.yaml", "r") as f:
        data = yaml.safe_load(f)
    methods = [m["payload"].get("method") for m in data["messages"] if "method" in m["payload"]]
    assert "tools/call" not in methods


def test_cli_schema_violation_warning(tmp_path, monkeypatch):
    """When server returns error on tools/call, verify CLI flags rejection and writes snapshot."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # Create a test server that rejects tools/call
    reject_server = tmp_path / "reject_server.py"
    reject_server.write_text("""
import sys, json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    data = json.loads(line)
    mid = data.get("id")
    method = data.get("method")
    if mid is not None:
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "reject-server", "version": "1.0"}}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": "bad_tool", "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}}]}}
        elif method == "tools/call":
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Invalid param x: must be > 0"}}
        else:
            resp = {"jsonrpc": "2.0", "id": mid, "result": {}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
""")

    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {reject_server}",
        "--yes",
        "--output", str(tmp_path / "rejected_golden.yaml")
    ])

    assert result.exit_code == 0
    assert "server rejected placeholder args" in result.output
    assert "1 returned error responses" in result.output

    out_file = tmp_path / "rejected_golden.yaml"
    assert out_file.exists()
    validated = validate_file(out_file, allow_v0=False)
    assert len(validated.messages) >= 7


def test_cli_server_name_sanitized(tmp_path, monkeypatch):
    """Verify serverInfo.name with path traversal characters is safely slugified."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    malicious_server = tmp_path / "malicious_server.py"
    malicious_server.write_text("""
import sys, json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    data = json.loads(line)
    mid = data.get("id")
    method = data.get("method")
    if mid is not None:
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "../../malicious/name", "version": "1.0"}}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"tools": []}}
        else:
            resp = {"jsonrpc": "2.0", "id": mid, "result": {}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
""")

    result = runner.invoke(main, [
        "generate",
        "--server", f"{sys.executable} {malicious_server}",
        "--no-call"
    ])

    assert result.exit_code == 0
    # Output must stay inside snapshots directory without path traversal
    expected_file = tmp_path / "snapshots" / "malicious_name_golden.yaml"
    assert expected_file.exists()

