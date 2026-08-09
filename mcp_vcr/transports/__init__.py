import asyncio
import json
import logging
import signal
from typing import Any, Optional
from ..schema import Direction
from ..interceptor import MessageInterceptor
from .base import Transport
from .stdio import StdioTransport

def __getattr__(name: str) -> Any:
    if name == "SseTransport":
        try:
            from .sse import SseTransport
            return SseTransport
        except ImportError as e:
            raise ImportError(
                "SseTransport is not available because aiohttp is not installed. "
                "Please install the sse extra: pip install mcp-vcr[sse]"
            ) from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

logger = logging.getLogger("mcp-vcr.transports")

async def run_proxy_with_transport(
    transport: Transport,
    interceptor: Optional[MessageInterceptor] = None,
    recorder: Any = None,
) -> int:
    """
    Launch and manage the entire proxy process using the provided Transport.
    Handles message pumps, signals, and exit codes.
    """
    await transport.start()

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

    async def pump_c2s() -> None:
        while True:
            try:
                line = await transport.read_client_message()
                if not line:
                    break
                stripped = line.strip()
                if stripped:
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
                await transport.write_to_server(line)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in c2s pump: {e}")
                break

    async def pump_s2c() -> None:
        while True:
            try:
                line = await transport.read_server_message()
                if not line:
                    break
                stripped = line.strip()
                if stripped:
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
                await transport.write_to_client(line)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in s2c pump: {e}")
                break

    # Spawn async tasks for the pumps and shutdown event
    c2s_task = asyncio.create_task(pump_c2s())
    s2c_task = asyncio.create_task(pump_s2c())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    # Wait until one of the pumps hits EOF or we receive a signal
    _done, _pending = await asyncio.wait(
        [c2s_task, s2c_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    exit_code = 0

    try:
        if shutdown_event.is_set():
            logger.warning(f"Proxy received signal {received_signal}, propagating to transport...")
            exit_code = await transport.shutdown(received_signal)
        else:
            # A pump terminated naturally (EOF)
            exit_code = await transport.shutdown()
    finally:
        # Cancel any pending tasks
        for task in (c2s_task, s2c_task, shutdown_task):
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

__all__ = [
    "Transport",
    "StdioTransport",
    "SseTransport",
    "run_proxy_with_transport",
]
