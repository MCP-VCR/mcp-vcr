import asyncio
import copy
import json
import logging
import os
import re
import secrets
import time
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from .transports.base import Transport

logger = logging.getLogger("mcp-vcr.generator")

CLIENT_NAME = "mcp-vcr-generate"
CLIENT_VERSION = "0.2.0"
CLIENT_PROTOCOL_VERSION = "2024-11-05"
MAX_TOOLS_LIST_PAGES = 100

SENSITIVE_FLAG_NAMES = {
    "token", "api-key", "apikey", "api_key", "secret", "password",
    "auth", "auth-token", "auth_token", "credential", "bearer", "key"
}
SENSITIVE_TOKEN_PARTS = ("token", "secret", "password", "api-key", "apikey", "auth")
SENSITIVE_PATTERNS = [
    re.compile(pat) for pat in [
        r"sk-[a-zA-Z0-9]{20,}",
        r"Bearer [a-zA-Z0-9\-._~+/]+=*",
        r"[A-Z0-9]{20}:[a-zA-Z0-9+/]{40}",
    ]
]


def _is_sensitive_key(norm_key: str) -> bool:
    if norm_key in SENSITIVE_FLAG_NAMES:
        return True
    parts = norm_key.split("-")
    return any(part in SENSITIVE_FLAG_NAMES for part in parts) or any(
        s in norm_key for s in SENSITIVE_TOKEN_PARTS
    )


def sanitize_server_command(server_command: List[str]) -> List[str]:
    """
    Sanitize server command arguments by stripping paths and redacting sensitive credentials.
    """
    sanitized = []
    skip_next = False
    for idx, arg in enumerate(server_command):
        if skip_next:
            sanitized.append("<REDACTED>")
            skip_next = False
            continue

        # Check for --flag=value or KEY=value
        if "=" in arg:
            key, val = arg.split("=", 1)
            norm_key = key.lstrip("-").lower().replace("_", "-")
            if _is_sensitive_key(norm_key):
                sanitized.append(f"{key}=<REDACTED>")
                continue
            if any(p.search(val) for p in SENSITIVE_PATTERNS):
                sanitized.append(f"{key}=<REDACTED>")
                continue

        # Check for separate flags like --token <secret> or --api-key <secret>
        norm_arg = arg.lstrip("-").lower().replace("_", "-")
        if arg.startswith("-") and _is_sensitive_key(norm_arg):
            sanitized.append(arg)
            if idx + 1 < len(server_command):
                skip_next = True
            continue

        # Check raw argument patterns
        if any(p.search(arg) for p in SENSITIVE_PATTERNS):
            sanitized.append("<REDACTED>")
            continue

        # Sanitize file paths to basename
        try:
            p = Path(arg)
            if p.is_absolute() or "\\" in str(arg) or "/" in str(arg):
                sanitized.append(p.name or arg)
            else:
                sanitized.append(arg)
        except (TypeError, ValueError):
            sanitized.append(arg)

    return sanitized


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
    tools_list_pages: List[Tuple[Dict[str, Any], Dict[str, Any]]] = field(default_factory=list)
    tool_call_results: List[ToolCallResult] = field(default_factory=list)
    client_protocol_version: str = CLIENT_PROTOCOL_VERSION


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
        Connect to the server and perform initialize + tools/list discovery with cursor pagination.
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
                "protocolVersion": CLIENT_PROTOCOL_VERSION,
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
        protocol_version = result_obj.get("protocolVersion", CLIENT_PROTOCOL_VERSION)
        server_info = result_obj.get("serverInfo", {})
        capabilities = result_obj.get("capabilities", {})

        # 2. Send notifications/initialized
        init_notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        await self._send_notification(transport, init_notif)

        # 3. Send tools/list request (id=2) with cursor pagination
        tools: List[Dict[str, Any]] = []
        tools_list_pages: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        req_id = 2
        next_cursor = None
        seen_cursors: set = set()

        while True:
            params = {}
            if next_cursor:
                params["cursor"] = next_cursor

            tools_list_req = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/list",
                "params": params
            }
            tools_list_resp = await self._send_and_receive(
                transport, tools_list_req, expected_id=req_id, timeout_ms=timeout_ms
            )
            if "error" in tools_list_resp:
                err = tools_list_resp["error"]
                raise RuntimeError(f"Server rejected tools/list (id={req_id}): {err}")

            tools_result = tools_list_resp.get("result", {})
            page_tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
            tools.extend(page_tools)
            tools_list_pages.append((tools_list_req, tools_list_resp))

            raw_cursor = tools_result.get("nextCursor") if isinstance(tools_result, dict) else None
            if raw_cursor and isinstance(raw_cursor, str) and raw_cursor.strip():
                cursor = raw_cursor.strip()
                if cursor in seen_cursors:
                    logger.warning("Server repeated tools/list cursor; stopping pagination.")
                    break
                if len(tools_list_pages) >= MAX_TOOLS_LIST_PAGES:
                    logger.warning(
                        f"Reached tools/list page limit ({MAX_TOOLS_LIST_PAGES}); stopping pagination."
                    )
                    break
                seen_cursors.add(cursor)
                next_cursor = cursor
                req_id += 1
            else:
                break

        first_tools_resp = tools_list_pages[0][1] if tools_list_pages else {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}

        return DiscoveryResult(
            protocol_version=protocol_version,
            server_info=server_info,
            capabilities=capabilities,
            tools=tools,
            initialize_response=init_resp,
            tools_list_response=first_tools_resp,
            tools_list_pages=tools_list_pages,
            tool_call_results=[],
            client_protocol_version=CLIENT_PROTOCOL_VERSION
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

        start_idx = 2 + max(1, len(discovery.tools_list_pages))
        for idx, tool in enumerate(discovery.tools, start=start_idx):
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
        sanitized_command = sanitize_server_command(server_command)

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
                "protocolVersion": discovery.client_protocol_version,
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

        messages = [
            {"t": 0, "dir": "c2s", "payload": init_c2s},
            {"t": 25, "dir": "s2c", "payload": discovery.initialize_response},
            {"t": 30, "dir": "c2s", "payload": initialized_c2s},
        ]

        pages = discovery.tools_list_pages
        if not pages:
            tools_list_fallback = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            pages = [(tools_list_fallback, discovery.tools_list_response)]

        t_curr = 40
        for page_req, page_resp in pages:
            messages.append({"t": t_curr, "dir": "c2s", "payload": page_req})
            t_curr += 25
            messages.append({"t": t_curr, "dir": "s2c", "payload": page_resp})
            t_curr += 15

        t_call_start = max(100, t_curr + 20)
        for i, tool_res in enumerate(discovery.tool_call_results):
            if tool_res.response_payload is not None:
                c2s_t = t_call_start + i * 50
                s2c_t = t_call_start + 25 + i * 50
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
