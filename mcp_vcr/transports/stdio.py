import asyncio
import json
import logging
import os
import sys
import threading
from collections import deque
from typing import Any, List, Optional
from .base import Transport

logger = logging.getLogger("mcp-vcr.transports.stdio")

class StreamWriterWrapper:
    """
    Simple wrapper to expose write and drain methods for synchronous binary streams
    (like sys.stdout.buffer and sys.stderr.buffer) in an async interface.
    """
    def __init__(self, raw_stream: Any):
        self.raw_stream = raw_stream
        
    def write(self, data: bytes) -> None:
        self.raw_stream.write(data)
        
    async def drain(self) -> None:
        self.raw_stream.flush()

async def get_stdin_reader(limit: int = 16 * 1024 * 1024) -> asyncio.StreamReader:
    """
    Get an asynchronous StreamReader for sys.stdin with a custom buffer limit.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit)
    
    if sys.platform == "win32":
        def read_worker():
            try:
                fd = sys.stdin.fileno()
                while True:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    loop.call_soon_threadsafe(reader.feed_data, data)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(reader.feed_eof)

        t = threading.Thread(target=read_worker, daemon=True)
        t.start()
    else:
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        
    return reader

async def launch_server(server_args: List[str], limit: int = 16 * 1024 * 1024) -> asyncio.subprocess.Process:
    """
    Launch the MCP server as a managed subprocess with piped stdin/stdout/stderr
    and specified StreamReader buffer limit.
    """
    if not server_args:
        raise ValueError("Server arguments list cannot be empty")
        
    logger.info(f"Launching subprocess: {server_args} with limit={limit}")
    
    process = await asyncio.create_subprocess_exec(
        *server_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=limit
    )
    return process

async def pump_c2s(
    reader: asyncio.StreamReader,
    writer: asyncio.subprocess.Process,
    interceptor: Optional[Any] = None
) -> None:
    """Legacy c2s pump helper."""
    from ..schema import Direction
    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("Client stdin EOF reached")
                break
                
            stripped = line.strip()
            if not stripped:
                continue
                
            payload = None
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON line from client (ignored): {e}. Line: {stripped!r}")
                
            if payload is not None and interceptor:
                try:
                    interceptor.observe(payload, Direction.C2S)
                except Exception as ie:
                    logger.error(f"Error in c2s interceptor call: {ie}")
                    
            writer.write(line)
            await writer.drain()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in c2s pump: {e}")
            break

async def pump_s2c(
    reader: asyncio.StreamReader,
    writer: Any,
    interceptor: Optional[Any] = None
) -> None:
    """Legacy s2c pump helper."""
    from ..schema import Direction
    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("Server stdout EOF reached")
                break
                
            stripped = line.strip()
            if not stripped:
                continue
                
            payload = None
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON line from server (ignored): {e}. Line: {stripped!r}")
                
            if payload is not None and interceptor:
                try:
                    interceptor.observe(payload, Direction.S2C)
                except Exception as ie:
                    logger.error(f"Error in s2c interceptor call: {ie}")
                    
            writer.write(line)
            await writer.drain()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in s2c pump: {e}")
            break

async def pump_stderr(
    reader: asyncio.StreamReader,
    writer: Any
) -> None:
    """Legacy stderr pump helper."""
    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("Server stderr EOF reached")
                break
                
            writer.write(line)
            await writer.drain()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in stderr pump: {e}")
            break

async def run_proxy(
    server_args: List[str],
    interceptor: Optional[Any] = None,
    recorder: Any = None,
    limit: int = 16 * 1024 * 1024
) -> int:
    """Legacy run_proxy runner."""
    from . import run_proxy_with_transport
    transport = StdioTransport(server_args, limit=limit)
    return await run_proxy_with_transport(transport, interceptor=interceptor, recorder=recorder)

class StdioTransport(Transport):
    """
    StdioTransport implements the Transport protocol for local MCP servers
    running as subprocesses communicating over stdin/stdout.
    """
    def __init__(self, server_args: List[str], limit: int = 16 * 1024 * 1024, read_stdin: bool = True, capture_stderr: bool = False):
        self.server_args = server_args
        self.limit = limit
        self.read_stdin = read_stdin
        self.capture_stderr = capture_stderr
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stdin_reader: Optional[asyncio.StreamReader] = None
        self.client_writer: Optional[StreamWriterWrapper] = None
        self.stderr_writer: Optional[StreamWriterWrapper] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._stderr_lines: deque[str] = deque(maxlen=200)

    def drain_captured_stderr(self) -> str:
        """Return captured stderr lines and clear internal buffer."""
        captured = "".join(self._stderr_lines)
        self._stderr_lines.clear()
        return captured

    async def start(self) -> None:
        self.process = await launch_server(self.server_args, limit=self.limit)
        if self.read_stdin:
            self.stdin_reader = await get_stdin_reader(limit=self.limit)
        else:
            self.stdin_reader = asyncio.StreamReader()
        self.client_writer = StreamWriterWrapper(sys.stdout.buffer)
        self.stderr_writer = StreamWriterWrapper(sys.stderr.buffer)
        
        # Start background task to forward server stderr to proxy's stderr
        self._stderr_task = asyncio.create_task(self._pump_stderr())

    async def read_client_message(self) -> Optional[bytes]:
        if not self.stdin_reader:
            return None
        line = await self.stdin_reader.readline()
        if not line:
            return None
        return line

    async def write_to_server(self, data: bytes) -> None:
        if not self.process or not self.process.stdin:
            return
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def read_server_message(self) -> Optional[bytes]:
        if not self.process or not self.process.stdout:
            return None
        line = await self.process.stdout.readline()
        if not line:
            return None
        return line

    async def write_to_client(self, data: bytes) -> None:
        if not self.client_writer:
            return
        self.client_writer.write(data)
        await self.client_writer.drain()

    @property
    def server_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def shutdown(self, sig: Optional[int] = None) -> int:
        # Cancel stderr forwarding task first
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        if not self.process:
            return 0

        exit_code = 0

        # Close server stdin to signal graceful shutdown if no signal is being sent
        if sig is None:
            logger.info("Closing server stdin for graceful shutdown...")
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()
                except Exception as e:
                    logger.debug(f"Exception during stdin close: {e}")
            
            try:
                exit_code = await asyncio.wait_for(self.process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Subprocess did not exit within timeout, killing subprocess...")
                try:
                    self.process.kill()
                    exit_code = await self.process.wait()
                except ProcessLookupError:
                    exit_code = -1
        else:
            logger.warning(f"Propagating signal {sig} to server subprocess...")
            try:
                self.process.send_signal(sig)
            except ProcessLookupError:
                pass
                
            try:
                exit_code = await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Subprocess did not exit gracefully, killing subprocess...")
                try:
                    self.process.kill()
                    exit_code = await self.process.wait()
                except ProcessLookupError:
                    exit_code = -1

        logger.info(f"Subprocess stdio transport shut down with exit code {exit_code}")
        self.process = None
        return exit_code

    async def _pump_stderr(self) -> None:
        if not self.process or not self.process.stderr or not self.stderr_writer:
            return
        while True:
            try:
                line = await self.process.stderr.readline()
                if not line:
                    break
                if self.capture_stderr:
                    self._stderr_lines.append(line.decode("utf-8", errors="replace"))
                self.stderr_writer.write(line)
                await self.stderr_writer.drain()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in stderr pump: {e}")
                break
