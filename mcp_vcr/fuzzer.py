import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Set

from .generator import CLIENT_NAME, CLIENT_PROTOCOL_VERSION, CLIENT_VERSION, GeneratorEngine
from .mutators import Mutation, generate_mutations
from .transports.base import Transport
from .validator import validate_file

logger = logging.getLogger("mcp-vcr.fuzzer")


@dataclass
class FuzzCaseResult:
    mutation: Mutation
    verdict: Literal["pass", "fail", "crash", "timeout", "skipped", "protocol_error"]
    response_payload: Optional[Dict[str, Any]] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    elapsed_ms: int = 0
    detail: Optional[str] = None


@dataclass
class FuzzResult:
    source_snapshot: str
    server_info: Dict[str, Any]
    protocol_version: str
    total_mutations: int
    results: List[FuzzCaseResult]
    resource_limits: Dict[str, Any]
    summary: Dict[str, int]
    exit_code: int
    aborted: bool
    abort_reason: Optional[str] = None


class FuzzEngine:
    """
    FuzzEngine orchestrates proactive fuzz testing against an MCP server.
    Applies structural and payload mutations to recorded client messages,
    streams them over the transport, and classifies server resilience.
    """

    def __init__(
        self,
        timeout_ms: int = 10000,
        max_mutations: Optional[int] = None,
        max_payload_bytes: Optional[int] = None,
        wall_clock_limit_s: Optional[int] = None,
        max_restarts: int = 10,
        strategies: Optional[Set[str]] = None,
        seed: Optional[int] = None,
    ):
        self.timeout_ms = timeout_ms
        self.max_mutations = max_mutations
        self.max_payload_bytes = max_payload_bytes
        self.wall_clock_limit_s = wall_clock_limit_s
        self.max_restarts = max_restarts
        self.strategies = strategies
        self.seed = seed

    async def run_fuzz(
        self,
        snapshot_path: Path,
        transport_factory: Callable[[], Transport],
        on_case_result: Optional[Callable[[FuzzCaseResult], None]] = None,
    ) -> FuzzResult:
        # Load transcript
        transcript = validate_file(snapshot_path)
        c2s_messages = [
            {"t": m.t, "dir": m.dir.value, "payload": m.payload}
            for m in transcript.messages
            if m.dir.value == "c2s"
        ]

        start_time = time.monotonic()
        _current_transport: Optional[Transport] = None
        _tools_schema: Optional[List[Dict[str, Any]]] = None
        _needs_restart = False
        _restart_count = 0

        server_info: Dict[str, Any] = {}
        protocol_version: str = CLIENT_PROTOCOL_VERSION

        async def _ensure_server_ready() -> bool:
            nonlocal _current_transport, _tools_schema, _needs_restart, _restart_count, server_info, protocol_version

            if (
                _current_transport is not None
                and not _needs_restart
                and _current_transport.server_running
            ):
                return True

            if _current_transport is not None:
                try:
                    await _current_transport.shutdown()
                except Exception as e:
                    logger.debug(f"Error shutting down transport: {e}")
                _current_transport = None

            if _needs_restart:
                _restart_count += 1
                _needs_restart = False

            while True:
                if _restart_count > self.max_restarts:
                    return False

                try:
                    t = transport_factory()
                    await t.start()
                    _current_transport = t

                    generator = GeneratorEngine()
                    # 1. Initialize
                    init_req = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": CLIENT_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                        },
                    }
                    init_resp = await generator._send_and_receive(
                        t, init_req, expected_id=1, timeout_ms=self.timeout_ms
                    )
                    if "error" in init_resp:
                        raise RuntimeError(f"Server rejected initialize: {init_resp['error']}")

                    res_obj = init_resp.get("result", {})
                    if isinstance(res_obj, dict):
                        server_info = res_obj.get("serverInfo", {})
                        protocol_version = res_obj.get("protocolVersion", protocol_version)

                    # 2. Initialized notification
                    init_notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
                    await generator._send_notification(t, init_notif)

                    # 3. Discover tools_schema on initial bootstrap only
                    if _tools_schema is None:
                        tools_req = {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                            "params": {},
                        }
                        tools_resp = await generator._send_and_receive(
                            t, tools_req, expected_id=2, timeout_ms=self.timeout_ms
                        )
                        if "error" in tools_resp:
                            raise RuntimeError(f"Server rejected tools/list: {tools_resp['error']}")
                        tools_res = tools_resp.get("result", {})
                        _tools_schema = (
                            tools_res.get("tools", []) if isinstance(tools_res, dict) else []
                        )

                    return True
                except Exception as e:
                    logger.warning(f"Bootstrap/restart attempt failed: {e}")
                    if _current_transport:
                        try:
                            await _current_transport.shutdown()
                        except Exception:
                            pass
                        _current_transport = None
                    _restart_count += 1

        try:
            # Perform initial bootstrap
            bootstrapped = await _ensure_server_ready()
            if not bootstrapped or _tools_schema is None:
                return FuzzResult(
                    source_snapshot=snapshot_path.name,
                    server_info=server_info,
                    protocol_version=protocol_version,
                    total_mutations=0,
                    results=[],
                    resource_limits={
                        "timeout_ms": self.timeout_ms,
                        "max_mutations": self.max_mutations,
                        "max_payload_bytes": self.max_payload_bytes,
                        "wall_clock_limit_s": self.wall_clock_limit_s,
                        "max_restarts": self.max_restarts,
                        "seed": self.seed,
                    },
                    summary={
                        "pass": 0,
                        "fail": 0,
                        "crash": 0,
                        "timeout": 0,
                        "skipped": 0,
                        "protocol_error": 0,
                    },
                    exit_code=2,
                    aborted=True,
                    abort_reason=f"server failed to complete handshake after {_restart_count} attempts",
                )

            # Generate mutations using discovered tools_schema
            mutations = generate_mutations(
                c2s_messages=c2s_messages,
                tools_schema=_tools_schema,
                strategies=self.strategies,
                max_mutations=self.max_mutations,
                seed=self.seed,
            )

            results: List[FuzzCaseResult] = []
            aborted = False
            abort_reason: Optional[str] = None

            for case_idx, mut in enumerate(mutations):
                # Check wall clock limit
                if (
                    self.wall_clock_limit_s is not None
                    and (time.monotonic() - start_time) >= self.wall_clock_limit_s
                ):
                    aborted = True
                    abort_reason = (
                        f"wall clock limit exceeded ({self.wall_clock_limit_s}s)"
                    )
                    break

                # Ensure server process is ready
                ready = await _ensure_server_ready()
                if not ready:
                    aborted = True
                    abort_reason = (
                        f"restart budget exhausted ({_restart_count} restarts)"
                    )
                    break

                # Prepare bytes
                if mut.payload is not None:
                    try:
                        raw_payload_bytes = json.dumps(mut.payload, sort_keys=True).encode("utf-8")
                    except Exception as e:
                        raw_payload_bytes = str(mut.payload).encode("utf-8")
                else:
                    raw_payload_bytes = mut.raw_bytes or b""

                # Check max payload bytes
                if (
                    self.max_payload_bytes is not None
                    and len(raw_payload_bytes) > self.max_payload_bytes
                ):
                    case_res = FuzzCaseResult(
                        mutation=mut,
                        verdict="skipped",
                        elapsed_ms=0,
                        detail=f"payload size {len(raw_payload_bytes)} exceeds limit {self.max_payload_bytes}",
                    )
                    results.append(case_res)
                    if on_case_result:
                        on_case_result(case_res)
                    continue

                # Execute fuzz case
                expected_id = None
                if mut.payload and isinstance(mut.payload, dict):
                    expected_id = mut.payload.get("id", case_idx + 100)

                line_bytes = (
                    raw_payload_bytes + b"\n"
                    if not raw_payload_bytes.endswith(b"\n")
                    else raw_payload_bytes
                )

                t_case_start = time.monotonic()
                try:
                    await _current_transport.write_to_server(line_bytes)
                except Exception as e:
                    # Pipe write failed -> crash
                    elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                    detail = None
                    if hasattr(_current_transport, "drain_captured_stderr"):
                        detail = getattr(_current_transport, "drain_captured_stderr")()
                    if not detail:
                        detail = f"Write to server failed: {e}"

                    _needs_restart = True
                    case_res = FuzzCaseResult(
                        mutation=mut,
                        verdict="crash",
                        elapsed_ms=elapsed_ms,
                        detail=detail,
                    )
                    results.append(case_res)
                    if on_case_result:
                        on_case_result(case_res)
                    continue

                # Wait for response with timeout
                deadline = time.monotonic() + (self.timeout_ms / 1000.0)
                case_res = None

                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # Timeout
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        _needs_restart = True
                        if _current_transport:
                            try:
                                await _current_transport.shutdown()
                            except Exception:
                                pass
                            _current_transport = None

                        case_res = FuzzCaseResult(
                            mutation=mut,
                            verdict="timeout",
                            elapsed_ms=elapsed_ms,
                            detail=f"No response within {self.timeout_ms}ms",
                        )
                        break

                    try:
                        resp_bytes = await asyncio.wait_for(
                            _current_transport.read_server_message(), timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        _needs_restart = True
                        if _current_transport:
                            try:
                                await _current_transport.shutdown()
                            except Exception:
                                pass
                            _current_transport = None

                        case_res = FuzzCaseResult(
                            mutation=mut,
                            verdict="timeout",
                            elapsed_ms=elapsed_ms,
                            detail=f"No response within {self.timeout_ms}ms",
                        )
                        break
                    except Exception as e:
                        # Read error -> crash
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        detail = None
                        if hasattr(_current_transport, "drain_captured_stderr"):
                            detail = getattr(_current_transport, "drain_captured_stderr")()
                        if not detail:
                            detail = f"Read error from server: {e}"
                        _needs_restart = True

                        case_res = FuzzCaseResult(
                            mutation=mut,
                            verdict="crash",
                            elapsed_ms=elapsed_ms,
                            detail=detail,
                        )
                        break

                    if not resp_bytes:
                        # EOF -> server exited / crashed
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        detail = None
                        if hasattr(_current_transport, "drain_captured_stderr"):
                            detail = getattr(_current_transport, "drain_captured_stderr")()
                        if not detail:
                            detail = "Server stdout reached EOF (subprocess exited)"
                        _needs_restart = True

                        case_res = FuzzCaseResult(
                            mutation=mut,
                            verdict="crash",
                            elapsed_ms=elapsed_ms,
                            detail=detail,
                        )
                        break

                    line_str = resp_bytes.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue

                    try:
                        resp_payload = json.loads(line_str)
                    except json.JSONDecodeError:
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        case_res = FuzzCaseResult(
                            mutation=mut,
                            verdict="protocol_error",
                            elapsed_ms=elapsed_ms,
                            detail=f"Server returned non-JSON response: {line_str!r}",
                        )
                        break

                    if not isinstance(resp_payload, dict):
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        case_res = FuzzCaseResult(
                            mutation=mut,
                            verdict="protocol_error",
                            elapsed_ms=elapsed_ms,
                            detail=f"Server response is not a JSON object: {line_str!r}",
                        )
                        break

                    resp_id = resp_payload.get("id")
                    if resp_id is None and "id" not in resp_payload:
                        # Notification from server -> continue waiting for response
                        continue

                    # Response ID check
                    if expected_id is not None and resp_id != expected_id:
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        case_res = FuzzCaseResult(
                            mutation=mut,
                            response_payload=resp_payload,
                            verdict="protocol_error",
                            elapsed_ms=elapsed_ms,
                            detail=f"response id={resp_id!r} does not match request id={expected_id!r}",
                        )
                        break

                    # Protocol format check
                    if resp_payload.get("jsonrpc") != "2.0":
                        elapsed_ms = int((time.monotonic() - t_case_start) * 1000)
                        case_res = FuzzCaseResult(
                            mutation=mut,
                            response_payload=resp_payload,
                            verdict="protocol_error",
                            elapsed_ms=elapsed_ms,
                            detail="Server response missing or invalid 'jsonrpc': '2.0'",
                        )
                        break

                    elapsed_ms = int((time.monotonic() - t_case_start) * 1000)

                    if "error" in resp_payload:
                        err_obj = resp_payload["error"]
                        if (
                            isinstance(err_obj, dict)
                            and "code" in err_obj
                            and "message" in err_obj
                            and isinstance(err_obj["code"], int)
                            and isinstance(err_obj["message"], str)
                        ):
                            case_res = FuzzCaseResult(
                                mutation=mut,
                                response_payload=resp_payload,
                                verdict="pass",
                                error_code=err_obj["code"],
                                error_message=err_obj["message"],
                                elapsed_ms=elapsed_ms,
                            )
                        else:
                            case_res = FuzzCaseResult(
                                mutation=mut,
                                response_payload=resp_payload,
                                verdict="fail",
                                elapsed_ms=elapsed_ms,
                                detail="Server returned malformed error object (missing code/message or invalid types)",
                            )
                    elif "result" in resp_payload:
                        case_res = FuzzCaseResult(
                            mutation=mut,
                            response_payload=resp_payload,
                            verdict="fail",
                            elapsed_ms=elapsed_ms,
                            detail="Server returned success result for invalid/mutated payload",
                        )
                    else:
                        case_res = FuzzCaseResult(
                            mutation=mut,
                            response_payload=resp_payload,
                            verdict="protocol_error",
                            elapsed_ms=elapsed_ms,
                            detail="Server response contains neither 'result' nor 'error'",
                        )
                    break

                if case_res is not None:
                    results.append(case_res)
                    if on_case_result:
                        on_case_result(case_res)

            summary = {
                "pass": 0,
                "fail": 0,
                "crash": 0,
                "timeout": 0,
                "skipped": 0,
                "protocol_error": 0,
            }
            for r in results:
                if r.verdict in summary:
                    summary[r.verdict] += 1

            if aborted:
                exit_code = 2
            elif (
                summary["fail"] > 0
                or summary["crash"] > 0
                or summary["timeout"] > 0
                or summary["protocol_error"] > 0
            ):
                exit_code = 1
            else:
                exit_code = 0

            return FuzzResult(
                source_snapshot=snapshot_path.name,
                server_info=server_info,
                protocol_version=protocol_version,
                total_mutations=len(results),
                results=results,
                resource_limits={
                    "timeout_ms": self.timeout_ms,
                    "max_mutations": self.max_mutations,
                    "max_payload_bytes": self.max_payload_bytes,
                    "wall_clock_limit_s": self.wall_clock_limit_s,
                    "max_restarts": self.max_restarts,
                    "seed": self.seed,
                },
                summary=summary,
                exit_code=exit_code,
                aborted=aborted,
                abort_reason=abort_reason,
            )

        finally:
            if _current_transport is not None:
                try:
                    await _current_transport.shutdown()
                except Exception:
                    pass
