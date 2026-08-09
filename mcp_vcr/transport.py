"""Backward-compatibility shim. Import from mcp_vcr.transports instead."""

import asyncio
import json
import logging
import warnings
from typing import Any, List, Optional
from .schema import Direction
from .transports import stdio

logger = logging.getLogger("mcp-vcr.transport")

class StreamWriterWrapper:
    def __init__(self, raw_stream: Any):
        warnings.warn(
            "mcp_vcr.transport.StreamWriterWrapper is deprecated. Use mcp_vcr.transports.stdio.StreamWriterWrapper instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self._impl = stdio.StreamWriterWrapper(raw_stream)

    def write(self, data: bytes) -> None:
        self._impl.write(data)

    async def drain(self) -> None:
        await self._impl.drain()

async def get_stdin_reader(limit: int = 16 * 1024 * 1024) -> asyncio.StreamReader:
    warnings.warn(
        "mcp_vcr.transport.get_stdin_reader is deprecated. Use mcp_vcr.transports.stdio.get_stdin_reader instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return await stdio.get_stdin_reader(limit)

async def launch_server(server_args: List[str], limit: int = 16 * 1024 * 1024) -> asyncio.subprocess.Process:
    warnings.warn(
        "mcp_vcr.transport.launch_server is deprecated. Use mcp_vcr.transports.stdio.launch_server instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return await stdio.launch_server(server_args, limit)

async def pump_c2s(
    reader: asyncio.StreamReader,
    writer: asyncio.subprocess.Process,
    interceptor: Optional[Any] = None
) -> None:
    warnings.warn(
        "mcp_vcr.transport.pump_c2s is deprecated. Use mcp_vcr.transports.stdio.pump_c2s instead.",
        DeprecationWarning,
        stacklevel=2
    )
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
    warnings.warn(
        "mcp_vcr.transport.pump_s2c is deprecated. Use mcp_vcr.transports.stdio.pump_s2c instead.",
        DeprecationWarning,
        stacklevel=2
    )
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
    warnings.warn(
        "mcp_vcr.transport.pump_stderr is deprecated. Use mcp_vcr.transports.stdio.pump_stderr instead.",
        DeprecationWarning,
        stacklevel=2
    )
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
    warnings.warn(
        "mcp_vcr.transport.run_proxy is deprecated. Use mcp_vcr.transports.stdio.run_proxy instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # We call our own module's functions directly to support mock/patching of launch_server and get_stdin_reader
    import sys
    import signal

    process = await launch_server(server_args, limit=limit)
    stdin_reader = await get_stdin_reader(limit=limit)

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    received_signal = None

    def handle_sig(sig):
        nonlocal received_signal
        received_signal = sig
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: handle_sig(s))
        except NotImplementedError:
            signal.signal(sig, lambda s, f: loop.call_soon_threadsafe(handle_sig, s))

    client_writer = StreamWriterWrapper(sys.stdout.buffer)
    stderr_writer = StreamWriterWrapper(sys.stderr.buffer)

    c2s_task = asyncio.create_task(pump_c2s(stdin_reader, process.stdin, interceptor))
    s2c_task = asyncio.create_task(pump_s2c(process.stdout, client_writer, interceptor))
    stderr_task = asyncio.create_task(pump_stderr(process.stderr, stderr_writer))
    shutdown_task = asyncio.create_task(shutdown_event.wait())

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
        if c2s_task.done():
            logger.info("Client stdin EOF. Closing server stdin...")
            try:
                process.stdin.close()
                await process.stdin.wait_closed()
            except Exception:
                pass
                
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Subprocess did not exit within timeout, killing subprocess...")
                try:
                    process.kill()
                    exit_code = await process.wait()
                except ProcessLookupError:
                    exit_code = -1
        else:
            logger.info("Server stdout EOF first. Waiting for process exit code...")
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Subprocess did not exit within timeout, killing subprocess...")
                try:
                    process.kill()
                    exit_code = await process.wait()
                except Exception:
                    exit_code = -1
            except Exception:
                exit_code = -1

    for task in (c2s_task, s2c_task, stderr_task, shutdown_task):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if interceptor and hasattr(interceptor, "flush"):
        try:
            if asyncio.iscoroutinefunction(interceptor.flush):
                await interceptor.flush()
            else:
                interceptor.flush()
        except Exception as e:
            logger.error(f"Error flushing interceptor: {e}")

    if recorder and hasattr(recorder, "flush"):
        try:
            if asyncio.iscoroutinefunction(recorder.flush):
                await recorder.flush()
            else:
                recorder.flush()
        except Exception as e:
            logger.error(f"Error flushing recorder: {e}")

    return exit_code
