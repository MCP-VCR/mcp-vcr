import asyncio
import json
import logging
import sys
from typing import Dict, List, Optional
import aiohttp
import yarl
from .base import Transport
from .stdio import get_stdin_reader, StreamWriterWrapper

logger = logging.getLogger("mcp-vcr.transports.sse")

class SseTransport(Transport):
    """
    SseTransport implements the Transport protocol for remote MCP servers
    communicating over Server-Sent Events (SSE) and HTTP POST.
    """
    def __init__(
        self,
        sse_url: str,
        post_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        limit: int = 16 * 1024 * 1024,
        post_timeout: float = 5.0
    ):
        self.sse_url = sse_url
        self.post_url = post_url or sse_url
        self.headers = headers or {}
        self.limit = limit
        self.post_timeout = post_timeout
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.stdin_reader: Optional[asyncio.StreamReader] = None
        self.client_writer: Optional[StreamWriterWrapper] = None
        
        self._server_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._endpoint_resolved = asyncio.Event()
        if post_url:
            self._endpoint_resolved.set()
        self._read_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self.session = aiohttp.ClientSession(headers=self.headers)
        
        # Setup client stdin/stdout wrappers
        self.stdin_reader = await get_stdin_reader(limit=self.limit)
        self.client_writer = StreamWriterWrapper(sys.stdout.buffer)
        
        # Start background task to read from SSE stream
        self._read_task = asyncio.create_task(self._read_sse_stream())
        
        # Wait for dynamic POST endpoint discovery (max 5 seconds)
        try:
            await asyncio.wait_for(self._endpoint_resolved.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if self.post_url == self.sse_url:
                logger.warning("Endpoint event not received within timeout, using base SSE URL for POSTs")
            else:
                raise TimeoutError("SSE POST endpoint discovery timed out (endpoint event not received).")

    async def read_client_message(self) -> Optional[bytes]:
        if not self.stdin_reader:
            return None
        line = await self.stdin_reader.readline()
        if not line:
            return None
        return line

    async def write_to_server(self, data: bytes) -> None:
        if not self.session:
            raise RuntimeError("Transport not started")
        
        payload = json.loads(data.decode("utf-8"))
        method = payload.get("method")
        msg_id = payload.get("id")

        logger.debug(f"POSTing to {self.post_url} (JSON-RPC method: {method!r}, id: {msg_id!r})")
        timeout = aiohttp.ClientTimeout(total=self.post_timeout)
        allow_redir = not bool(self.headers)
        try:
            async with self.session.post(self.post_url, json=payload, timeout=timeout, allow_redirects=allow_redir) as resp:
                if resp.status not in (200, 202, 204):
                    raise ValueError(f"SSE POST returned unexpected status: {resp.status}")
        except Exception as e:
            logger.error(f"Failed to send POST to server: {e}")
            raise

    async def read_server_message(self) -> Optional[bytes]:
        if not self._running:
            return None
        try:
            msg_dict = await self._server_queue.get()
            if msg_dict is None:  # EOF sentinel
                return None
            return (json.dumps(msg_dict) + "\n").encode("utf-8")
        except asyncio.CancelledError:
            return None

    async def write_to_client(self, data: bytes) -> None:
        if not self.client_writer:
            return
        self.client_writer.write(data)
        await self.client_writer.drain()

    @property
    def server_running(self) -> bool:
        return self._running and self.session is not None

    async def shutdown(self, sig: Optional[int] = None) -> int:
        self._running = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
            
        if self.session:
            await self.session.close()
            self.session = None
            
        return 0

    async def _read_sse_stream(self) -> None:
        if not self.session:
            return
        allow_redir = not bool(self.headers)
        try:
            async with self.session.get(self.sse_url, allow_redirects=allow_redir) as response:
                if response.status != 200:
                    logger.error(f"SSE connection failed with status: {response.status}")
                    self._server_queue.put_nowait(None)
                    return
                    
                current_event = None
                data_buffer = []
                data_size = 0
                dropped_current_event = False
                async for line in response.content:
                    line_str = line.decode("utf-8").strip("\r\n")
                    
                    if not line_str:
                        # Blank line: dispatch buffered data
                        if not dropped_current_event and data_buffer:
                            data_content = "\n".join(data_buffer)
                            if current_event == "endpoint":
                                # Dynamic POST endpoint discovery
                                base_url = yarl.URL(self.sse_url)
                                resolved_url = base_url.join(yarl.URL(data_content))
                                # Validate origin to prevent cross-origin header leaks
                                if resolved_url.origin() != base_url.origin():
                                    logger.error(f"Security Warning: Resolved SSE POST URL has different origin than base URL. Rejected: {resolved_url}")
                                else:
                                    self.post_url = str(resolved_url)
                                    logger.info(f"Dynamically resolved SSE POST URL to: {self.post_url}")
                                self._endpoint_resolved.set()
                            else:
                                if data_content:
                                    if len(data_content) > self.limit:
                                        logger.error(f"SSE payload size {len(data_content)} exceeds limit {self.limit}, dropping message.")
                                    else:
                                        try:
                                            payload = json.loads(data_content)
                                            await self._server_queue.put(payload)
                                        except Exception as e:
                                            logger.error(
                                                "Failed to parse or queue SSE data (%d bytes): %s",
                                                len(data_content),
                                                type(e).__name__,
                                            )
                        current_event = None
                        data_buffer = []
                        data_size = 0
                        dropped_current_event = False
                        continue
                        
                    if line_str.startswith(":"):
                        # SSE Comment: ignore
                        continue
                        
                    if line_str.startswith("event:"):
                        current_event = line_str[6:].strip()
                    elif line_str.startswith("data:"):
                        if dropped_current_event:
                            continue
                        # Strip at most one leading space after data:
                        val = line_str[5:]
                        if val.startswith(" "):
                            val = val[1:]
                        data_size += len(val.encode("utf-8")) + 1
                        if data_size > self.limit:
                            logger.error(f"Buffered SSE payload size exceeds limit {self.limit}, dropping incoming event.")
                            data_buffer = []
                            dropped_current_event = True
                        else:
                            data_buffer.append(val)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in SSE stream reader: {e}")
        finally:
            self._endpoint_resolved.set()
            if self._running:
                try:
                    # Normal stream termination: await to guarantee EOF sentinel delivery
                    await self._server_queue.put(None)
                except asyncio.CancelledError:
                    try:
                        self._server_queue.put_nowait(None)
                    except asyncio.QueueFull:
                        try:
                            self._server_queue.get_nowait()
                            self._server_queue.put_nowait(None)
                        except Exception:
                            pass
                    raise
                except Exception:
                    try:
                        self._server_queue.put_nowait(None)
                    except asyncio.QueueFull:
                        try:
                            self._server_queue.get_nowait()
                            self._server_queue.put_nowait(None)
                        except Exception:
                            pass
            else:
                # Explicit shutdown: non-blocking best-effort sentinel delivery
                try:
                    self._server_queue.put_nowait(None)
                except asyncio.QueueFull:
                    try:
                        self._server_queue.get_nowait()
                        self._server_queue.put_nowait(None)
                    except Exception:
                        pass
