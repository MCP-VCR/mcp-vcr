import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any, List, Optional
from .interceptor import MessageInterceptor
from .schema import Direction

logger = logging.getLogger("mcp-vcr.transport")

async def get_stdin_reader(limit: int = 16 * 1024 * 1024) -> asyncio.StreamReader:
    """
    Get an asynchronous StreamReader for sys.stdin with a custom buffer limit.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit)
    protocol = asyncio.StreamReaderProtocol(reader)
    # Using sys.stdin for the read pipe connection
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
    
    # We pass the limit parameter directly to set it for stdout/stderr StreamReader instances
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
    writer: asyncio.subprocess.Process, # process.stdin is a StreamWriter-like object
    interceptor: Optional[MessageInterceptor] = None
) -> None:
    """
    Read JSON-RPC messages from client stdin and forward to server stdin.
    Pass parsed messages to interceptor. Handles malformed lines gracefully.
    """
    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("Client stdin EOF reached")
                break
                
            stripped = line.strip()
            if not stripped:
                continue
                
            # Attempt to validate as JSON
            payload = None
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON line from client (ignored): {e}. Line: {stripped!r}")
                
            # Call interceptor if provided
            if payload is not None and interceptor:
                try:
                    interceptor.observe(payload, Direction.C2S)
                except Exception as ie:
                    logger.error(f"Error in c2s interceptor call: {ie}")
                    
            # Forward unchanged line to server
            writer.write(line)
            await writer.drain()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in c2s pump: {e}")
            break

async def pump_s2c(
    reader: asyncio.StreamReader,
    writer: Any, # wrapper around sys.stdout.buffer
    interceptor: Optional[MessageInterceptor] = None
) -> None:
    """
    Read JSON-RPC messages from server stdout and forward to client stdout.
    Pass parsed messages to interceptor. Handles malformed lines gracefully.
    """
    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("Server stdout EOF reached")
                break
                
            stripped = line.strip()
            if not stripped:
                continue
                
            # Attempt to validate as JSON
            payload = None
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON line from server (ignored): {e}. Line: {stripped!r}")
                
            # Call interceptor if provided
            if payload is not None and interceptor:
                try:
                    interceptor.observe(payload, Direction.S2C)
                except Exception as ie:
                    logger.error(f"Error in s2c interceptor call: {ie}")
                    
            # Forward unchanged line to client
            writer.write(line)
            await writer.drain()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in s2c pump: {e}")
            break

async def pump_stderr(
    reader: asyncio.StreamReader,
    writer: Any # wrapper around sys.stderr.buffer
) -> None:
    """
    Forward server subprocess stderr to the proxy's own stderr in real-time.
    Do NOT pass to interceptor or recorder.
    """
    while True:
        try:
            line = await reader.readline()
            if not line:
                logger.info("Server stderr EOF reached")
                break
                
            # Forward stderr content exactly as is
            writer.write(line)
            await writer.drain()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in stderr pump: {e}")
            break

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

async def run_proxy(
    server_args: List[str],
    interceptor: Optional[MessageInterceptor] = None,
    recorder: Any = None,
    limit: int = 16 * 1024 * 1024
) -> int:
    """
    Launch and manage the entire proxy process. Handles subprocess, pumps,
    signals, and exit codes.
    """
    process = await launch_server(server_args, limit=limit)
    stdin_reader = await get_stdin_reader(limit=limit)

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    received_signal = None

    def handle_sig(sig):
        nonlocal received_signal
        received_signal = sig
        shutdown_event.set()

    # Register OS signal handlers for graceful shutdown (SIGINT, SIGTERM)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: handle_sig(s))
        except NotImplementedError:
            # Fallback for platforms where add_signal_handler is not supported
            signal.signal(sig, lambda s, f: loop.call_soon_threadsafe(handle_sig, s))

    # Wrappers for standard output/error
    client_writer = StreamWriterWrapper(sys.stdout.buffer)
    stderr_writer = StreamWriterWrapper(sys.stderr.buffer)

    # Spawn async tasks for the pumps and shutdown event
    c2s_task = asyncio.create_task(pump_c2s(stdin_reader, process.stdin, interceptor))
    s2c_task = asyncio.create_task(pump_s2c(process.stdout, client_writer, interceptor))
    stderr_task = asyncio.create_task(pump_stderr(process.stderr, stderr_writer))
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    # Wait until one of the pumps hits EOF or we receive a signal
    _done, _pending = await asyncio.wait(
        [c2s_task, s2c_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    exit_code = 0

    if shutdown_event.is_set():
        logger.warning(f"Proxy received signal {received_signal}, propagating to server subprocess...")
        if received_signal:
            try:
                process.send_signal(received_signal)
            except ProcessLookupError:
                pass
                
        # Wait for subprocess to exit cleanly after signal
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Subprocess did not exit gracefully, killing subprocess...")
            try:
                process.kill()
                exit_code = await process.wait()
            except ProcessLookupError:
                exit_code = -1
    else:
        # A pump terminated naturally (EOF)
        if c2s_task.done():
            # Client EOF: close subprocess stdin to signal graceful shutdown
            logger.info("Client stdin EOF. Closing server stdin...")
            try:
                process.stdin.close()
                await process.stdin.wait_closed()
            except Exception:
                pass
                
        # Wait for the subprocess to exit cleanly
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Subprocess did not exit within timeout, killing subprocess...")
            try:
                process.kill()
                exit_code = await process.wait()
            except ProcessLookupError:
                exit_code = -1

    # Cancel any pending tasks
    for task in (c2s_task, s2c_task, stderr_task, shutdown_task):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Flush interceptor and transcript recorder if provided
    if interceptor and hasattr(interceptor, "flush"):
        try:
            if asyncio.iscoroutinefunction(interceptor.flush):
                await interceptor.flush()
            else:
                interceptor.flush()
        except Exception as e:
            logger.error(f"Error flushing interceptor: {e}")

    # Flush transcript recorder if provided
    if recorder and hasattr(recorder, "flush"):
        try:
            if asyncio.iscoroutinefunction(recorder.flush):
                await recorder.flush()
            else:
                recorder.flush()
        except Exception as e:
            logger.error(f"Error flushing recorder: {e}")

    return exit_code
