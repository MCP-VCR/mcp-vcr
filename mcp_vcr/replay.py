import asyncio
import json
import logging
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from .schema import Direction, Message, Metadata
from .transport import launch_server
from .validator import validate_file, validate_transcript

logger = logging.getLogger("mcp-vcr.replay")

class ReplayEngine:
    """
    ReplayEngine is responsible for reading saved client-to-server (c2s) messages
    from a transcript, launching the server subprocess, replaying c2s messages in order,
    and capturing responses into a new derived replay transcript.
    """
    def __init__(self, config_path: Optional[Path] = None, timeout_ms: Optional[int] = None, settle_delay_ms: Optional[int] = None):
        self.config_path = config_path or Path.cwd() / ".mcp-vcr.yaml"
        self.timeout_ms = timeout_ms
        self.settle_delay_ms = settle_delay_ms
        
        # Load from config file if not overridden
        self._load_config()
        for key in ("timeout_ms", "settle_delay_ms"):
            val = getattr(self, key)
            if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val < 0):
                raise ValueError(f"{key} must be a non-negative integer")

    def _load_config(self) -> None:
        config = {}
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("Failed to load configuration file %s: %s", self.config_path, e)
        
        replay_cfg: Dict[str, Any] = {}
        if isinstance(config, dict):
            raw_replay_cfg = config.get("replay", {})
            if isinstance(raw_replay_cfg, dict):
                replay_cfg = raw_replay_cfg
            else:
                logger.warning("Invalid config: 'replay' must be an object; got %s", type(raw_replay_cfg).__name__)

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

    async def run_replay(self, transcript_path: Path, server_args: Optional[List[str]] = None) -> Path:
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
            
        # 2. Filter c2s messages
        c2s_messages = [msg for msg in transcript.messages if msg.dir == Direction.C2S]
        
        # 3. Determine server command
        args = server_args or transcript.meta.server_command
        if not args:
            raise ValueError("No server command specified for replay (not found in args or transcript meta).")
            
        logger.info(f"Replaying transcript '{transcript_path}' against server: {args}")
        
        # 4. Launch subprocess
        process = await launch_server(args)
        
        # 5. Replay loop execution
        t0 = time.monotonic()
        responses: List[Message] = []
        incomplete = False
        incomplete_reason = None
        
        try:
            for msg in c2s_messages:
                payload = msg.payload
                
                # Detect notifications by absence of id field (or id: null)
                is_notification = ("id" not in payload) or (payload["id"] is None)
                
                # Serialize message
                line_str = json.dumps(payload, sort_keys=True) + "\n"
                
                try:
                    process.stdin.write(line_str.encode("utf-8"))
                    await process.stdin.drain()
                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    logger.error(f"Pipe error writing message to server: {e}")
                    incomplete = True
                    incomplete_reason = "pipe_error"
                    break
                    
                if is_notification:
                    # Settle delay wait, do not wait for response
                    logger.debug(f"Notification sent, sleeping for {self.settle_delay_ms}ms settle delay.")
                    await asyncio.sleep(self.settle_delay_ms / 1000.0)
                else:
                    # Request: wait for exactly one response
                    expected_id = payload.get("id")
                    req_method = payload.get("method")
                    logger.debug(f"Request sent (ID: {expected_id}), waiting for response...")
                    try:
                        deadline = time.monotonic() + (self.timeout_ms / 1000.0)
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                                
                            response_bytes = await asyncio.wait_for(
                                process.stdout.readline(),
                                timeout=remaining
                            )
                            
                            if not response_bytes:
                                # EOF from server stdout indicates a crash
                                logger.error("Server subprocess exited unexpectedly (EOF on stdout).")
                                incomplete = True
                                incomplete_reason = "server_crash"
                                break
                                
                            response_str = response_bytes.decode("utf-8").strip()
                            if not response_str:
                                continue
                                
                            response_payload = json.loads(response_str)
                            resp_id = response_payload.get("id")
                            
                            if resp_id is None:
                                # Treat missing "id" as a notification: append it to responses but do not advance the request loop
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
                    except (OSError, ConnectionResetError) as e:
                        logger.error(f"Pipe error reading response from server: {e}")
                        incomplete = True
                        incomplete_reason = "pipe_error"
                        break
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.error(f"Malformed JSON or decode error response from server during replay: {e}")
                        incomplete = True
                        incomplete_reason = "malformed_response"
                        break
        finally:
            # 6. Graceful cleanup of server subprocess
            try:
                process.stdin.close()
                await process.stdin.wait_closed()
            except Exception as e:
                logger.debug("Exception during stdin cleanup: %s", e)
                
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Subprocess did not exit within timeout, killing subprocess...")
                try:
                    process.kill()
                    await process.wait()
                except Exception as e:
                    logger.debug("Exception during process kill: %s", e)
                
        # 7. Write derived replay output transcript
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        new_session_id = f"{transcript.meta.session_id}-replay-{timestamp}"
        
        sanitized_args = []
        for arg in args:
            try:
                p = Path(arg)
                if p.is_absolute() or "\\" in str(arg):
                    sanitized_args.append(p.name)
                else:
                    sanitized_args.append(arg)
            except (TypeError, ValueError):
                sanitized_args.append(arg)
                
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
        all_msgs = []
        for msg in c2s_messages:
            all_msgs.append(Message(t=msg.t, dir=Direction.C2S, payload=msg.payload))
        for resp in responses:
            all_msgs.append(resp)
            
        all_msgs.sort(key=lambda x: x.t)
        
        for m in all_msgs:
            messages_list.append({
                "t": m.t,
                "dir": m.dir.value,
                "payload": m.payload
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
