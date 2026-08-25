import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from mcp_vcr.fuzzer import FuzzEngine, FuzzResult, FuzzCaseResult
from mcp_vcr.transports.base import Transport


class MockFuzzTransport(Transport):
    """
    Mock transport for testing FuzzEngine without launching real subprocesses.
    """

    def __init__(
        self,
        handshake_responses: Optional[List[Dict[str, Any]]] = None,
        fuzz_responses: Optional[List[Any]] = None,
        crash_on_fuzz_write: bool = False,
        timeout_on_fuzz_read: bool = False,
        captured_stderr: str = "",
        fail_bootstrap_attempts: int = 0,
    ):
        self.handshake_responses = (
            handshake_responses
            if handshake_responses is not None
            else [
                # initialize response (id=1)
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "mock-server", "version": "1.0"},
                    },
                },
                # tools/list response (id=2)
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "mock_tool",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"arg": {"type": "string"}},
                                    "required": ["arg"],
                                },
                            }
                        ]
                    },
                },
            ]
        )
        self.fuzz_responses = fuzz_responses or []
        self.crash_on_fuzz_write = crash_on_fuzz_write
        self.timeout_on_fuzz_read = timeout_on_fuzz_read
        self.captured_stderr = captured_stderr
        self.fail_bootstrap_attempts = fail_bootstrap_attempts

        self._started = False
        self._running = False
        self._handshake_idx = 0
        self._fuzz_idx = 0
        self._in_fuzz_phase = False
        self._read_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.recorded_requests: List[Dict[str, Any]] = []
        self.start_count = 0
        self.shutdown_count = 0

    async def start(self) -> None:
        self.start_count += 1
        self._started = True
        self._running = True
        self._handshake_idx = 0

    @property
    def server_running(self) -> bool:
        return self._running

    async def read_client_message(self) -> Optional[bytes]:
        return None

    async def write_to_client(self, data: bytes) -> None:
        pass

    async def write_to_server(self, data: bytes) -> None:
        if not self._running:
            raise ConnectionError("Server process dead")

        try:
            req = json.loads(data.decode("utf-8").strip())
            self.recorded_requests.append(req)
        except Exception:
            self.recorded_requests.append({"raw": data})

        # Check if failing bootstrap
        if self.start_count <= self.fail_bootstrap_attempts:
            self._running = False
            raise ConnectionError("Bootstrap server crash")

        # Process standard handshake messages
        try:
            req_obj = json.loads(data.decode("utf-8").strip())
            if isinstance(req_obj, dict):
                method = req_obj.get("method")
                req_id = req_obj.get("id")

                if method == "initialize":
                    if self._handshake_idx < len(self.handshake_responses):
                        resp = dict(self.handshake_responses[self._handshake_idx])
                        self._handshake_idx += 1
                        if req_id is not None and "id" in resp:
                            resp["id"] = req_id
                        await self._read_queue.put(json.dumps(resp).encode("utf-8") + b"\n")
                    return
                elif method == "notifications/initialized":
                    return
                elif method == "tools/list":
                    if self._handshake_idx < len(self.handshake_responses):
                        resp = dict(self.handshake_responses[self._handshake_idx])
                        self._handshake_idx += 1
                        if req_id is not None and "id" in resp:
                            resp["id"] = req_id
                        await self._read_queue.put(json.dumps(resp).encode("utf-8") + b"\n")
                    return
        except Exception:
            pass

        # We are in fuzz phase
        self._in_fuzz_phase = True

        if self.crash_on_fuzz_write:
            self._running = False
            raise ConnectionError("Simulated pipe write crash")

        # Handle fuzz response phase
        if self.timeout_on_fuzz_read:
            return

        if self._fuzz_idx < len(self.fuzz_responses):
            resp_item = self.fuzz_responses[self._fuzz_idx]
            self._fuzz_idx += 1
            if resp_item is None:
                # None represents server crash / EOF
                self._running = False
                await self._read_queue.put(b"")
            elif isinstance(resp_item, bytes):
                await self._read_queue.put(resp_item)
            elif isinstance(resp_item, str):
                await self._read_queue.put(resp_item.encode("utf-8") + b"\n")
            elif isinstance(resp_item, dict):
                await self._read_queue.put(json.dumps(resp_item).encode("utf-8") + b"\n")
            elif isinstance(resp_item, list):
                for item in resp_item:
                    if isinstance(item, dict):
                        await self._read_queue.put(json.dumps(item).encode("utf-8") + b"\n")

    async def read_server_message(self) -> Optional[bytes]:
        if not self._running:
            return None
        if self.timeout_on_fuzz_read and self._in_fuzz_phase:
            # Hang forever until cancelled or timeout
            await asyncio.sleep(100)
            return b""
        return await self._read_queue.get()

    def drain_captured_stderr(self) -> str:
        res = self.captured_stderr
        self.captured_stderr = ""
        return res

    async def shutdown(self, sig: Optional[int] = None) -> int:
        self.shutdown_count += 1
        self._running = False
        return 0


@pytest.fixture
def mock_snapshot_path(tmp_path) -> Path:
    p = tmp_path / "test_golden.yaml"
    content = """meta:
  version: 1
  recorded_at: '2026-08-25T12:00:00Z'
  session_id: 'test001'
  server_command: ['python', 'server.py']
  protocol_version: '2024-11-05'
messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: '2.0'
      id: 10
      method: 'tools/call'
      params:
        name: 'mock_tool'
        arguments:
          arg: 'val'
"""
    p.write_text(content, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_fuzz_pass_on_proper_error_response(mock_snapshot_path):
    fuzz_resp = {
        "jsonrpc": "2.0",
        "id": 10,
        "error": {"code": -32602, "message": "Invalid params"},
    }
    transport = MockFuzzTransport(fuzz_responses=[fuzz_resp])

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.exit_code == 0
    assert len(res.results) > 0
    assert res.results[0].verdict == "pass"
    assert res.results[0].error_code == -32602


@pytest.mark.asyncio
async def test_fuzz_fail_on_success_for_invalid_input(mock_snapshot_path):
    fuzz_resp = {
        "jsonrpc": "2.0",
        "id": 10,
        "result": {"content": [{"type": "text", "text": "ok"}]},
    }
    transport = MockFuzzTransport(fuzz_responses=[fuzz_resp])

    engine = FuzzEngine()
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.exit_code == 1
    assert res.results[0].verdict == "fail"


@pytest.mark.asyncio
async def test_fuzz_crash_triggers_restart(mock_snapshot_path):
    # First mutation crashes server (returns None)
    # Second mutation returns proper error
    fuzz_resp1 = None
    fuzz_resp2 = {
        "jsonrpc": "2.0",
        "id": 10,
        "error": {"code": -32602, "message": "Invalid params"},
    }

    t1 = MockFuzzTransport(fuzz_responses=[fuzz_resp1], captured_stderr="Traceback (most recent call last):\nKeyError: 'arg'")
    t2 = MockFuzzTransport(fuzz_responses=[fuzz_resp2])
    transports = [t1, t2]

    engine = FuzzEngine(max_mutations=2)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transports.pop(0))

    assert res.results[0].verdict == "crash"
    assert "KeyError" in (res.results[0].detail or "")
    assert res.results[1].verdict == "pass"


@pytest.mark.asyncio
async def test_fuzz_timeout_triggers_kill_and_restart(mock_snapshot_path):
    t1 = MockFuzzTransport(timeout_on_fuzz_read=True)
    t2 = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "Invalid params"}}
    ])
    transports = [t1, t2]

    engine = FuzzEngine(timeout_ms=50, max_mutations=2)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transports.pop(0))

    assert res.results[0].verdict == "timeout"
    assert t1.shutdown_count >= 1
    assert res.results[1].verdict == "pass"


@pytest.mark.asyncio
async def test_fuzz_timeout_does_not_cascade(mock_snapshot_path):
    t1 = MockFuzzTransport(timeout_on_fuzz_read=True)
    t2 = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "Invalid params"}}
    ])
    transports = [t1, t2]

    engine = FuzzEngine(timeout_ms=50, max_mutations=2)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transports.pop(0))

    # Second case after timeout should succeed independently
    assert res.results[0].verdict == "timeout"
    assert res.results[1].verdict == "pass"


@pytest.mark.asyncio
async def test_fuzz_protocol_error_on_wrong_id(mock_snapshot_path):
    fuzz_resp = {
        "jsonrpc": "2.0",
        "id": 999,  # Expected 10
        "error": {"code": -32602, "message": "Invalid params"},
    }
    transport = MockFuzzTransport(fuzz_responses=[fuzz_resp])

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.results[0].verdict == "protocol_error"
    assert "does not match request id" in (res.results[0].detail or "")


@pytest.mark.asyncio
async def test_fuzz_protocol_error_on_malformed_response(mock_snapshot_path):
    fuzz_resp = {"id": 10, "result": "ok"}  # Missing jsonrpc field
    transport = MockFuzzTransport(fuzz_responses=[fuzz_resp])

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.results[0].verdict == "protocol_error"


@pytest.mark.asyncio
async def test_fuzz_skipped_on_oversized_payload(mock_snapshot_path):
    transport = MockFuzzTransport()

    engine = FuzzEngine(max_payload_bytes=5)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.results[0].verdict == "skipped"
    assert res.results[0].elapsed_ms == 0


@pytest.mark.asyncio
async def test_wall_clock_limit_aborts(mock_snapshot_path):
    transport = MockFuzzTransport(timeout_on_fuzz_read=True)

    engine = FuzzEngine(timeout_ms=50, wall_clock_limit_s=0)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.aborted is True
    assert res.exit_code == 2


@pytest.mark.asyncio
async def test_max_restarts_aborts(mock_snapshot_path):
    # Transport that crashes on every fuzz read
    class CrashingTransport(MockFuzzTransport):
        async def read_server_message(self):
            if self._in_fuzz_phase:
                return None
            return await super().read_server_message()

    engine = FuzzEngine(timeout_ms=100, max_restarts=1, max_mutations=5)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: CrashingTransport())

    assert res.aborted is True
    assert res.exit_code == 2
    assert "restart budget" in (res.abort_reason or "")


@pytest.mark.asyncio
async def test_fuzz_pipe_write_crash_triggers_restart(mock_snapshot_path):
    t1 = MockFuzzTransport(crash_on_fuzz_write=True, captured_stderr="Pipe error")
    t2 = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "Invalid params"}}
    ])
    transports = [t1, t2]

    engine = FuzzEngine(max_mutations=2)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transports.pop(0))

    assert res.results[0].verdict == "crash"
    assert "Pipe error" in (res.results[0].detail or "")
    assert res.results[1].verdict == "pass"


@pytest.mark.asyncio
async def test_max_mutations_respected(mock_snapshot_path):
    transport = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "err"}}
    ] * 10)

    engine = FuzzEngine(max_mutations=3)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert len(res.results) == 3


@pytest.mark.asyncio
async def test_exit_code_zero_when_all_pass(mock_snapshot_path):
    transport = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "err"}}
    ] * 10)

    engine = FuzzEngine(max_mutations=2)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.exit_code == 0


@pytest.mark.asyncio
async def test_exit_code_one_on_any_failure(mock_snapshot_path):
    transport = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "result": "bad"}
    ])

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.exit_code == 1


@pytest.mark.asyncio
async def test_exit_code_two_on_abort(mock_snapshot_path):
    engine = FuzzEngine(wall_clock_limit_s=0)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: MockFuzzTransport())

    assert res.exit_code == 2


@pytest.mark.asyncio
async def test_stderr_captured_on_crash(mock_snapshot_path):
    t = MockFuzzTransport(fuzz_responses=[None], captured_stderr="Custom error message")

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: t)

    assert res.results[0].verdict == "crash"
    assert "Custom error message" in (res.results[0].detail or "")


@pytest.mark.asyncio
async def test_live_tools_list_called_on_startup(mock_snapshot_path):
    t = MockFuzzTransport()

    engine = FuzzEngine(max_mutations=1)
    await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: t)

    methods = [req.get("method") for req in t.recorded_requests if isinstance(req, dict)]
    assert "initialize" in methods
    assert "tools/list" in methods


@pytest.mark.asyncio
async def test_tools_schema_cached_across_restart(mock_snapshot_path):
    t1 = MockFuzzTransport(fuzz_responses=[None])  # Crashes
    t2 = MockFuzzTransport(fuzz_responses=[
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "err"}}
    ])
    transports = [t1, t2]

    engine = FuzzEngine(max_mutations=2)
    await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transports.pop(0))

    methods_t2 = [req.get("method") for req in t2.recorded_requests if isinstance(req, dict)]
    assert "initialize" in methods_t2
    # Verify tools/list was NOT called again on t2
    assert "tools/list" not in methods_t2


@pytest.mark.asyncio
async def test_response_id_matching_filters_notifications(mock_snapshot_path):
    notification = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
    response = {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "err"}}

    t = MockFuzzTransport(fuzz_responses=[[notification, response]])

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: t)

    assert res.results[0].verdict == "pass"


@pytest.mark.asyncio
async def test_bootstrap_failure_retries_under_restart_budget(mock_snapshot_path):
    # Fails 3 bootstrap attempts with max_restarts=2 -> total allowed attempts = 3
    t_factory = lambda: MockFuzzTransport(fail_bootstrap_attempts=10)

    engine = FuzzEngine(max_restarts=2)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=t_factory)

    assert res.total_mutations == 0
    assert res.aborted is True
    assert res.exit_code == 2
    assert "server failed to complete handshake" in (res.abort_reason or "")


@pytest.mark.asyncio
async def test_bootstrap_succeeds_after_transient_failure(mock_snapshot_path):
    # Fails 1 bootstrap attempt, succeeds on second
    attempts = 0

    def t_factory():
        nonlocal attempts
        attempts += 1
        return MockFuzzTransport(
            fail_bootstrap_attempts=1 if attempts == 1 else 0,
            fuzz_responses=[
                {"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "err"}}
            ],
        )

    engine = FuzzEngine(max_restarts=2, max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=t_factory)

    assert res.total_mutations == 1
    assert res.results[0].verdict == "pass"


@pytest.mark.asyncio
async def test_custom_handshake_responses_in_mock_transport(mock_snapshot_path):
    custom_init_resp = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "custom-server", "version": "9.9.9"},
        },
    }
    custom_tools_resp = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "custom_tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"param": {"type": "string"}},
                        "required": ["param"],
                    },
                }
            ]
        },
    }
    transport = MockFuzzTransport(
        handshake_responses=[custom_init_resp, custom_tools_resp],
        fuzz_responses=[{"jsonrpc": "2.0", "id": 10, "error": {"code": -32602, "message": "err"}}],
    )

    engine = FuzzEngine(max_mutations=1)
    res = await engine.run_fuzz(mock_snapshot_path, transport_factory=lambda: transport)

    assert res.server_info.get("name") == "custom-server"
    assert res.server_info.get("version") == "9.9.9"

