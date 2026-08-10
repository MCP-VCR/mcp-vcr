import asyncio
import copy
import json
import logging
import os
import secrets
import time
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from .transports.base import Transport

logger = logging.getLogger("mcp-vcr.generator")

CLIENT_NAME = "mcp-vcr-generate"
CLIENT_VERSION = "0.2.0"


@dataclass
class ToolCallResult:
    tool_name: str
    request_payload: Dict[str, Any]
    response_payload: Optional[Dict[str, Any]]
    status: Literal["success", "error", "skipped"]
    error_message: Optional[str] = None


@dataclass
class DiscoveryResult:
    protocol_version: str
    server_info: Dict[str, Any]
    capabilities: Dict[str, Any]
    tools: List[Dict[str, Any]]
    initialize_response: Dict[str, Any]
    tools_list_response: Dict[str, Any]
    tool_call_results: List[ToolCallResult] = field(default_factory=list)


class GeneratorEngine:
    """
    GeneratorEngine orchestrates auto-discovery of tools from an MCP server,
    synthesizes type-appropriate placeholder arguments from inputSchemas,
    optionally executes live tools/call stubs with per-tool error isolation,
    and formats the result into a normalized golden snapshot transcript.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path

    @staticmethod
    def generate_placeholder_args(
        input_schema: Optional[Dict[str, Any]],
        max_depth: int = 2,
        _current_depth: int = 1
    ) -> Dict[str, Any]:
        """
        Generate placeholder arguments for required properties based on inputSchema.
        Recurses on nested objects up to max_depth (default 2), collapsing deeper objects to {}.
        """
        if not isinstance(input_schema, dict):
            return {}

        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            return {}

        required = input_schema.get("required")
        if not isinstance(required, list):
            required = []

        result: Dict[str, Any] = {}
        for field_name in required:
            if not isinstance(field_name, str):
                continue
            prop_schema = properties.get(field_name)
            if not isinstance(prop_schema, dict):
                result[field_name] = f"example_{field_name}"
                continue

            if "enum" in prop_schema and isinstance(prop_schema["enum"], list) and prop_schema["enum"]:
                result[field_name] = prop_schema["enum"][0]
                continue

            prop_type = prop_schema.get("type")
            if prop_type == "string":
                result[field_name] = f"example_{field_name}"
            elif prop_type in ("number", "integer"):
                result[field_name] = 0
            elif prop_type == "boolean":
                result[field_name] = False
            elif prop_type == "array":
                result[field_name] = []
            elif prop_type == "object":
                if _current_depth <= max_depth:
                    result[field_name] = GeneratorEngine.generate_placeholder_args(
                        prop_schema,
                        max_depth=max_depth,
                        _current_depth=_current_depth + 1
                    )
                else:
                    result[field_name] = {}
            else:
                result[field_name] = None

        return result

    async def _send_and_receive(
        self,
        transport: Transport,
        request_payload: Dict[str, Any],
        expected_id: Any,
        timeout_ms: int = 10000
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for matching response ID."""
        line_bytes = (json.dumps(request_payload, sort_keys=True) + "\n").encode("utf-8")
        await transport.write_to_server(line_bytes)

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Timed out waiting for response to request id={expected_id}")

            response_bytes = await asyncio.wait_for(
                transport.read_server_message(),
                timeout=remaining
            )
            if not response_bytes:
                raise ConnectionError("Server closed connection / EOF reached")

            line_str = response_bytes.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                payload = json.loads(line_str)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON from server during discovery: {e}")
                continue

            if isinstance(payload, dict) and payload.get("id") == expected_id:
                return payload

    async def _send_notification(
        self,
        transport: Transport,
        notification_payload: Dict[str, Any],
        settle_delay_ms: int = 50
    ) -> None:
        """Send a JSON-RPC notification and pause for settle delay."""
        line_bytes = (json.dumps(notification_payload, sort_keys=True) + "\n").encode("utf-8")
        await transport.write_to_server(line_bytes)
        if settle_delay_ms > 0:
            await asyncio.sleep(settle_delay_ms / 1000.0)

    async def discover(
        self,
        transport: Transport,
        timeout_ms: int = 10000
    ) -> DiscoveryResult:
        """
        Connect to the server and perform initialize + tools/list discovery.
        Does NOT shut down the transport to allow subsequent tool calls.
        """
        if not transport.server_running:
            await transport.start()

        # 1. Send initialize request (id=1)
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": CLIENT_NAME,
                    "version": CLIENT_VERSION
                }
            }
        }
        init_resp = await self._send_and_receive(transport, init_req, expected_id=1, timeout_ms=timeout_ms)
        if "error" in init_resp:
            err = init_resp["error"]
            raise RuntimeError(f"Server rejected initialize: {err}")

        result_obj = init_resp.get("result", {})
        protocol_version = result_obj.get("protocolVersion", "2024-11-05")
        server_info = result_obj.get("serverInfo", {})
        capabilities = result_obj.get("capabilities", {})

        # 2. Send notifications/initialized
        init_notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        await self._send_notification(transport, init_notif)

        # 3. Send tools/list request (id=2)
        tools_list_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        tools_list_resp = await self._send_and_receive(transport, tools_list_req, expected_id=2, timeout_ms=timeout_ms)
        if "error" in tools_list_resp:
            err = tools_list_resp["error"]
            raise RuntimeError(f"Server rejected tools/list: {err}")

        tools_result = tools_list_resp.get("result", {})
        tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []

        return DiscoveryResult(
            protocol_version=protocol_version,
            server_info=server_info,
            capabilities=capabilities,
            tools=tools,
            initialize_response=init_resp,
            tools_list_response=tools_list_resp,
            tool_call_results=[]
        )

    async def call_tools(
        self,
        transport: Transport,
        discovery: DiscoveryResult,
        timeout_ms: int = 10000,
        on_tool_result: Optional[Any] = None
    ) -> List[ToolCallResult]:
        """
        Execute placeholder tools/call requests against the live server.
        Uses per-tool error isolation: failures on individual tools are captured
        in ToolCallResult and do not abort the execution of remaining tools.
        """
        results: List[ToolCallResult] = []
        transport_broken = False

        for idx, tool in enumerate(discovery.tools, start=3):
            tool_name = tool.get("name", f"tool_{idx}")
            schema = tool.get("inputSchema", {})
            args = self.generate_placeholder_args(schema)

            req_payload = {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": args
                }
            }

            if transport_broken or not transport.server_running:
                res = ToolCallResult(
                    tool_name=tool_name,
                    request_payload=req_payload,
                    response_payload=None,
                    status="skipped",
                    error_message="transport closed"
                )
                results.append(res)
                if on_tool_result:
                    on_tool_result(res)
                continue

            try:
                resp_payload = await self._send_and_receive(
                    transport,
                    req_payload,
                    expected_id=idx,
                    timeout_ms=timeout_ms
                )

                if "error" in resp_payload:
                    err = resp_payload["error"]
                    if isinstance(err, dict):
                        err_code = err.get("code", "")
                        err_msg = err.get("message", str(err))
                        formatted_err = f"error {err_code}: {err_msg}" if err_code != "" else f"error: {err_msg}"
                    else:
                        formatted_err = f"error: {err}"
                    res = ToolCallResult(
                        tool_name=tool_name,
                        request_payload=req_payload,
                        response_payload=resp_payload,
                        status="error",
                        error_message=formatted_err
                    )
                else:
                    res = ToolCallResult(
                        tool_name=tool_name,
                        request_payload=req_payload,
                        response_payload=resp_payload,
                        status="success"
                    )
            except asyncio.TimeoutError:
                res = ToolCallResult(
                    tool_name=tool_name,
                    request_payload=req_payload,
                    response_payload=None,
                    status="error",
                    error_message=f"timeout waiting for response ({timeout_ms}ms)"
                )
            except Exception as e:
                transport_broken = True
                res = ToolCallResult(
                    tool_name=tool_name,
                    request_payload=req_payload,
                    response_payload=None,
                    status="error",
                    error_message=f"transport error: {e}"
                )

            results.append(res)
            if on_tool_result:
                on_tool_result(res)

        discovery.tool_call_results = results
        return results

    def build_transcript(
        self,
        discovery: DiscoveryResult,
        server_command: List[str]
    ) -> Dict[str, Any]:
        """
        Build a deterministic v1 transcript dictionary from discovery and tool call results.
        """
        sanitized_command = []
        for arg in server_command:
            try:
                p = Path(arg)
                if p.is_absolute() or "\\" in str(arg):
                    sanitized_command.append(p.name)
                else:
                    sanitized_command.append(arg)
            except (TypeError, ValueError):
                sanitized_command.append(arg)

        session_id = secrets.token_hex(4)
        recorded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        meta = {
            "version": 1,
            "recorded_at": recorded_at,
            "session_id": session_id,
            "server_command": sanitized_command,
            "protocol_version": discovery.protocol_version,
            "client_hint": CLIENT_NAME,
            "schema_version": "1.0"
        }

        init_c2s = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": discovery.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": CLIENT_NAME,
                    "version": CLIENT_VERSION
                }
            }
        }

        initialized_c2s = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }

        tools_list_c2s = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }

        messages = [
            {"t": 0, "dir": "c2s", "payload": init_c2s},
            {"t": 25, "dir": "s2c", "payload": discovery.initialize_response},
            {"t": 30, "dir": "c2s", "payload": initialized_c2s},
            {"t": 40, "dir": "c2s", "payload": tools_list_c2s},
            {"t": 65, "dir": "s2c", "payload": discovery.tools_list_response},
        ]

        for i, tool_res in enumerate(discovery.tool_call_results):
            if tool_res.response_payload is not None:
                c2s_t = 100 + i * 50
                s2c_t = 125 + i * 50
                messages.append({
                    "t": c2s_t,
                    "dir": "c2s",
                    "payload": tool_res.request_payload
                })
                messages.append({
                    "t": s2c_t,
                    "dir": "s2c",
                    "payload": tool_res.response_payload
                })

        return {
            "meta": meta,
            "messages": messages
        }

    def write_snapshot(
        self,
        transcript_data: Dict[str, Any],
        output_path: Path
    ) -> Path:
        """
        Normalize the transcript data and write to disk with atomic rename and schema validation.
        """
        from .snapshot import normalize_transcript_data
        from .validator import validate_file

        normalized = normalize_transcript_data(transcript_data)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = output_path.parent / f".{output_path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(normalized, f, sort_keys=True, default_flow_style=False)
                f.flush()
                os.fsync(f.fileno())
            validate_file(temp_path, allow_v0=False)
            temp_path.replace(output_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return output_path
