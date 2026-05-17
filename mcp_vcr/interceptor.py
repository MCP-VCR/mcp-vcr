import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from .schema import Direction
from .recorder import TranscriptRecorder
from .redactor import Redactor

logger = logging.getLogger("mcp-vcr.interceptor")

class MessageInterceptor:
    """
    MessageInterceptor observes, tags, classifies, and forwards JSON-RPC messages 
    passing through the proxy asynchronously to the TranscriptRecorder.
    """
    def __init__(self, recorder: Optional[TranscriptRecorder] = None, redactor: Optional[Redactor] = None):
        self.start_time = time.monotonic()  # t0 defined at instantiation
        self.observed_messages: List[Dict[str, Any]] = []
        self.recorder = recorder
        self.redactor = redactor or Redactor()
        self._recorder_tasks: List[asyncio.Task] = []

    def observe(self, payload: Dict[str, Any], direction: Direction) -> None:
        """
        Observe a payload, compute monotonic millisecond timestamp relative to session start,
        classify JSON-RPC type, and dispatch non-blockingly to the recorder.
        """
        t = int((time.monotonic() - self.start_time) * 1000)
        
        # 1. Direction Tagging
        dir_val = direction.value if hasattr(direction, "value") else direction
        
        # 2. JSON-RPC Message Classification
        msg_type = "unknown"
        if isinstance(payload, dict):
            has_id = "id" in payload and payload["id"] is not None
            has_method = "method" in payload
            
            if has_id and has_method:
                msg_type = "request"
            elif has_id and not has_method:
                msg_type = "response"
            elif has_method and not has_id:
                msg_type = "notification"

        # 3. Watch initialize exchange for lazy metadata backfilling
        if isinstance(payload, dict) and self.recorder:
            if dir_val == "c2s" and payload.get("method") == "initialize":
                params = payload.get("params")
                if isinstance(params, dict):
                    client_info = params.get("clientInfo")
                    if isinstance(client_info, dict):
                        client_name = client_info.get("name")
                        if client_name:
                            self.recorder.update_lazy_metadata(client_hint=str(client_name))
                            
            elif dir_val == "s2c":
                result = payload.get("result")
                if isinstance(result, dict):
                    proto_ver = result.get("protocolVersion")
                    if proto_ver:
                        self.recorder.update_lazy_metadata(protocol_version=str(proto_ver))

        # Store message internally
        msg = {
            "t": t,
            "dir": dir_val,
            "payload": payload,
            "msg_type": msg_type  # Internal use only (omitted from serialization)
        }
        self.observed_messages.append(msg)

        # 4. Non-blocking Async Recorder Dispatch
        if self.recorder:
            redacted_payload = self.redactor.redact(payload)
            redacted_msg = {
                "t": t,
                "dir": dir_val,
                "payload": redacted_payload
            }
            
            async def async_write():
                try:
                    self.recorder.write(redacted_msg)
                except Exception as e:
                    logger.error(f"TranscriptRecorder write task failed: {e}")

            try:
                # Schedule write in active event loop
                loop = asyncio.get_running_loop()
                task = loop.create_task(async_write())
                self._recorder_tasks.append(task)
            except RuntimeError:
                # Fallback for synchronous/test environments
                try:
                    self.recorder.write(redacted_msg)
                except Exception as e:
                    logger.error(f"TranscriptRecorder synchronous write failed: {e}")

    async def flush(self) -> None:
        """
        Flush captured messages. Logs status for Phase 1/2 integration.
        """
        logger.info(f"Flushed {len(self.observed_messages)} messages.")
        if self._recorder_tasks:
            try:
                await asyncio.gather(*self._recorder_tasks)
            except RuntimeError:
                # Handle RuntimeError for non-async contexts
                pass
            except Exception as e:
                logger.error(f"Error during message interceptor task gathering: {e}")
            finally:
                self._recorder_tasks.clear()
