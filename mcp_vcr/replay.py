import asyncio
import json
import logging
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from .schema import Direction, Message, Metadata
from .transports.stdio import launch_server
from .validator import validate_file, validate_transcript

logger = logging.getLogger("mcp-vcr.replay")

class ReplayEngine:
    """
    ReplayEngine is responsible for reading saved client-to-server (c2s) messages
    from a transcript, launching the server subprocess, replaying c2s messages in order,
    and capturing responses into a new derived replay transcript.
    """
    def __init__(self, config_path: Optional[Path] = None, timeout_ms: Optional[int] = None, settle_delay_ms: Optional[int] = None, timing_faithful: Optional[bool] = None):
        self.config_path = config_path or Path.cwd() / ".mcp-vcr.yaml"
        self.timeout_ms_override = timeout_ms
        self.settle_delay_ms_override = settle_delay_ms
        self.timing_faithful_override = timing_faithful
        self.timeout_ms = timeout_ms
        self.settle_delay_ms = settle_delay_ms
        self.timing_faithful = timing_faithful
        
        # Load from config file if not overridden
        self._load_config()
        for key in ("timeout_ms", "settle_delay_ms"):
            val = getattr(self, key)
            if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val < 0):
                raise ValueError(f"{key} must be a non-negative integer")

    def _load_config(self) -> None:
        from .config import Config
        try:
            config = Config.load(self.config_path)
            replay_cfg = config.replay_config()
        except Exception as e:
            logger.warning("Failed to load configuration file: %s", e)
            replay_cfg = {}

        def _read_non_negative_int(key: str, default: int) -> int:
            val = replay_cfg.get(key, default)
            if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
                return val
            logger.warning("Invalid replay.%s=%r; using default %d", key, val, default)
            return default
            
        if self.timeout_ms is None:
            self.timeout_ms = _read_non_negative_int("timeout_ms", 5000)
            
        if self.settle_delay_ms is None:
            self.settle_delay_ms = _read_non_negative_int("settle_delay_ms", 50)
            
        if self.timing_faithful is None:
            self.timing_faithful = bool(replay_cfg.get("timing_faithful", False))

    async def run_replay(
        self,
        transcript_path: Path,
        server_args: Optional[List[str]] = None,
        transport: Optional[Any] = None
    ) -> Path:
        """
        Executes the replay session against a live server subprocess.
        Returns the Path to the generated replay output transcript.
        """
        if not transcript_path.exists():
            raise FileNotFoundError(f"Source transcript file not found: {transcript_path}")
            
        # 1. Load and validate source transcript
        transcript = validate_file(transcript_path)
        if transcript.meta.version != 1:
            raise ValueError(f"Unsupported transcript version: {transcript.meta.version}")
            
        # Resolve config overrides for this specific transcript
        from .config import Config
        resolved = {}
        try:
            config = Config.load(self.config_path)
            resolved = config.for_snapshot(transcript_path)
            replay_cfg = resolved.get("replay", {})
        except Exception as e:
            logger.warning("Failed to load configuration overrides for %s: %s", transcript_path, e)
            replay_cfg = {}

        # Prioritize CLI/explicit init overrides over the matching config file overrides
        timeout_ms = self.timeout_ms_override
        if timeout_ms is None:
            timeout_ms = replay_cfg.get("timeout_ms", self.timeout_ms or 5000)
            
        settle_delay_ms = self.settle_delay_ms_override
        if settle_delay_ms is None:
            settle_delay_ms = replay_cfg.get("settle_delay_ms", self.settle_delay_ms or 50)
            
        timing_faithful = self.timing_faithful_override
        if timing_faithful is None:
            timing_faithful = replay_cfg.get("timing_faithful", self.timing_faithful or False)

        # 2. Filter c2s messages
        c2s_messages = [msg for msg in transcript.messages if msg.dir == Direction.C2S]
        
        # Resolve transport configuration overrides
        transport_cfg = resolved.get("transport", {})
        
        # 3. Determine server command / endpoint
        transport_type = transport_cfg.get("type", "stdio")
        args = server_args
        if transport is None:
            if args is None:
                if transport_type == "sse":
                    args = [transport_cfg.get("sse_url")] if transport_cfg.get("sse_url") else []
                else:
                    args = transcript.meta.server_command
                    
            if transport_type == "stdio" and not args:
                raise ValueError("No server command specified for replay (not found in args or transcript meta).")
            
        logger.info(f"Replaying transcript '{transcript_path}' using {transport_type} transport")
        
        # 4. Initialize and start Transport
        if transport is None:
            from .transports.stdio import StdioTransport
            try:
                from .transports import SseTransport
                sse_transport_available = True
            except ImportError:
                sse_transport_available = False

            if transport_type == "sse":
                if not sse_transport_available:
                    raise ValueError("SseTransport is not available. Please install the sse extra: pip install mcp-vcr[sse]")
                from .transports import SseTransport
                sse_url = transport_cfg.get("sse_url")
                headers = transport_cfg.get("headers", {})
                transport = SseTransport(sse_url=sse_url, headers=headers)
            else:
                transport = StdioTransport(args, read_stdin=False)
            
        # 5. Replay loop execution
        t0 = time.monotonic()
        responses: List[Message] = []
        incomplete = False
        incomplete_reason = None
        
        last_t = 0
        try:
            await transport.start()
            for msg in c2s_messages:
                payload = msg.payload
                
                # Insert timing-faithful delay if enabled
                if timing_faithful:
                    target_time = t0 + (msg.t / 1000.0)
                    now = time.monotonic()
                    delay = target_time - now
                    if delay > 0:
                        logger.debug(f"Timing-faithful replay: sleeping for {delay:.3f}s before sending message.")
                        await asyncio.sleep(delay)
                
                # Detect notifications by absence of id field (or id: null)
                is_notification = ("id" not in payload) or (payload["id"] is None)
                
                # Serialize message
                line_str = json.dumps(payload, sort_keys=True) + "\n"
                
                try:
                    await transport.write_to_server(line_str.encode("utf-8"))
                except Exception as e:
                    logger.error(f"Error writing message to server: {e}")
                    incomplete = True
                    incomplete_reason = "pipe_error"
                    break
                    
                if is_notification:
                    # Settle delay wait, do not wait for response
                    logger.debug(f"Notification sent, sleeping for {settle_delay_ms}ms settle delay.")
                    await asyncio.sleep(settle_delay_ms / 1000.0)
                else:
                    # Request: wait for exactly one response
                    expected_id = payload.get("id")
                    req_method = payload.get("method")
                    logger.debug(f"Request sent (ID: {expected_id}), waiting for response...")
                    try:
                        deadline = time.monotonic() + (timeout_ms / 1000.0)
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                                
                            response_bytes = await asyncio.wait_for(
                                transport.read_server_message(),
                                timeout=remaining
                            )
                            
                            if not response_bytes:
                                logger.error("Server connection closed or exited.")
                                incomplete = True
                                incomplete_reason = "server_crash"
                                break
                                
                            try:
                                response_str = response_bytes.decode("utf-8").strip()
                                if not response_str:
                                    continue
                                response_payload = json.loads(response_str)
                            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                                logger.error(f"Malformed JSON or decode error response from server during replay: {e}")
                                incomplete = True
                                incomplete_reason = "malformed_response"
                                break
                                
                            resp_id = response_payload.get("id")
                            
                            if resp_id is None:
                                # Treat missing "id" as a notification
                                t_elapsed = int((time.monotonic() - t0) * 1000)
                                responses.append(Message(
                                    t=t_elapsed,
                                    dir=Direction.S2C,
                                    payload=response_payload
                                ))
                                continue
                                
                            if resp_id == expected_id:
                                # Found matching response
                                t_elapsed = int((time.monotonic() - t0) * 1000)
                                responses.append(Message(
                                    t=t_elapsed,
                                    dir=Direction.S2C,
                                    payload=response_payload
                                ))
                                break
                            else:
                                # Non-matching response ID (unexpected)
                                logger.debug(
                                    "Received response with ID %r while expecting %r; continuing to wait",
                                    resp_id, expected_id
                                )
                                
                        if incomplete:
                            break
                    except asyncio.TimeoutError:
                        logger.error(f"Replay timeout waiting for response to request ID {expected_id} ({req_method})")
                        incomplete = True
                        incomplete_reason = "timeout"
                        break
                    except Exception as e:
                        logger.error(f"Error reading response from server: {e}")
                        incomplete = True
                        incomplete_reason = "pipe_error"
                        break
        finally:
            await transport.shutdown()
                
        # 7. Write derived replay output transcript
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        new_session_id = f"{transcript.meta.session_id}-replay-{timestamp}"
        
        sanitized_args = []
        if args:
            for arg in args:
                try:
                    p = Path(arg)
                    if p.is_absolute() or "\\" in str(arg):
                        sanitized_args.append(p.name)
                    else:
                        sanitized_args.append(arg)
                except (TypeError, ValueError):
                    sanitized_args.append(arg)
        else:
            if transport_type == "sse" and transport_cfg.get("sse_url"):
                sanitized_args = [transport_cfg.get("sse_url")]
            else:
                sanitized_args = ["remote-server"]
                
        meta_dict = {
            "version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "session_id": new_session_id,
            "server_command": sanitized_args,
            "schema_version": "1.0"
        }
        
        if transcript.meta.client_hint:
            meta_dict["client_hint"] = transcript.meta.client_hint
        if transcript.meta.protocol_version:
            meta_dict["protocol_version"] = transcript.meta.protocol_version
            
        if incomplete:
            meta_dict["incomplete"] = True
            meta_dict["incomplete_reason"] = incomplete_reason
            
        messages_list = []
        for resp in responses:
            messages_list.append({
                "t": resp.t,
                "dir": resp.dir.value,
                "payload": resp.payload
            })
            
        output_doc = {
            "meta": meta_dict,
            "messages": messages_list
        }
        
        # Stored alongside the source transcript
        output_path = transcript_path.parent / f"{transcript_path.stem}-replay-{timestamp}.yaml"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(output_doc, f, sort_keys=True, default_flow_style=False)
            
        logger.info(f"Replay output transcript saved to {output_path}")
        
        # Quick validation of the output file
        errors = validate_transcript(output_path)
        if errors:
            raise ValueError(f"Generated replay output failed schema validation: {errors}")
            
        return output_path
