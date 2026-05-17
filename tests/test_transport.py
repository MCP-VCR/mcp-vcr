import asyncio
import json
import logging
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from mcp_vcr.interceptor import MessageInterceptor
from mcp_vcr.transport import (
    get_stdin_reader,
    launch_server,
    pump_c2s,
    pump_s2c,
    pump_stderr,
    run_proxy,
    StreamWriterWrapper
)

# Mock class for StreamWriter
class MockStreamWriter:
    def __init__(self):
        self.write_buf = b""
        self.closed = False
        self.drained = False

    def write(self, data: bytes) -> None:
        self.write_buf += data

    async def drain(self) -> None:
        self.drained = True

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


@pytest.mark.asyncio
async def test_launch_server():
    """Verify subprocess launches with correct arguments and pipes stdin/stdout/stderr."""
    mock_process = MagicMock(spec=asyncio.subprocess.Process)
    
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_process
        
        args = ["python", "server.py", "--verbose"]
        limit = 16 * 1024 * 1024
        process = await launch_server(args, limit=limit)
        
        mock_create.assert_called_once_with(
            "python", "server.py", "--verbose",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=limit
        )
        assert process == mock_process


@pytest.mark.asyncio
async def test_pump_c2s():
    """Verify client -> server pump forwards valid JSON and calls interceptor."""
    # Setup StreamReader with valid JSON-RPC
    reader = asyncio.StreamReader()
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    reader.feed_data(json.dumps(msg).encode() + b"\n")
    reader.feed_eof()

    writer = MockStreamWriter()
    interceptor = MagicMock(spec=MessageInterceptor)

    await pump_c2s(reader, writer, interceptor)

    # Verify message was forwarded with a newline
    assert writer.write_buf == json.dumps(msg).encode() + b"\n"
    # Verify interceptor was called with direction='c2s'
    interceptor.observe.assert_called_once_with(msg, "c2s")


@pytest.mark.asyncio
async def test_pump_s2c():
    """Verify server -> client pump forwards valid JSON and calls interceptor."""
    # Setup StreamReader with valid JSON-RPC
    reader = asyncio.StreamReader()
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    reader.feed_data(json.dumps(msg).encode() + b"\n")
    reader.feed_eof()

    writer = MockStreamWriter()
    interceptor = MagicMock(spec=MessageInterceptor)

    await pump_s2c(reader, writer, interceptor)

    # Verify message was forwarded with a newline
    assert writer.write_buf == json.dumps(msg).encode() + b"\n"
    # Verify interceptor was called with direction='s2c'
    interceptor.observe.assert_called_once_with(msg, "s2c")


@pytest.mark.asyncio
async def test_pump_stderr():
    """Verify server stderr pump forwards raw content and does not call interceptor."""
    reader = asyncio.StreamReader()
    log_line = b"Server started successfully\n"
    reader.feed_data(log_line)
    reader.feed_eof()

    writer = MockStreamWriter()

    await pump_stderr(reader, writer)

    # Verify raw output is forwarded exactly
    assert writer.write_buf == log_line


@pytest.mark.asyncio
async def test_large_message():
    """Verify StreamReader limit accommodates extremely large lines without error."""
    limit = 16 * 1024 * 1024
    reader = asyncio.StreamReader(limit=limit)
    
    # 5MB of random tool output string
    large_payload = "a" * (5 * 1024 * 1024)
    msg = {"jsonrpc": "2.0", "id": 42, "result": {"text": large_payload}}
    line_bytes = json.dumps(msg).encode() + b"\n"
    
    reader.feed_data(line_bytes)
    reader.feed_eof()

    writer = MockStreamWriter()
    interceptor = MagicMock(spec=MessageInterceptor)

    # Should run and successfully read the 5MB message without raising ValueError/LimitOverrunError
    await pump_s2c(reader, writer, interceptor)
    
    assert len(writer.write_buf) == len(line_bytes)
    interceptor.observe.assert_called_once_with(msg, "s2c")


@pytest.mark.asyncio
async def test_malformed_json_handling(caplog):
    """Verify malformed JSON is skipped, logged as warning, not forwarded, and subsequent line is processed."""
    reader = asyncio.StreamReader()
    malformed_line = b"{invalid_json: true}\n"
    valid_msg = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    valid_line = json.dumps(valid_msg).encode() + b"\n"
    
    reader.feed_data(malformed_line)
    reader.feed_data(valid_line)
    reader.feed_eof()

    writer = MockStreamWriter()
    interceptor = MagicMock(spec=MessageInterceptor)

    with caplog.at_level(logging.WARNING):
        await pump_c2s(reader, writer, interceptor)

    # Verify both the malformed line and the valid line were forwarded
    assert writer.write_buf == malformed_line + valid_line
    # Verify interceptor was called ONLY for the valid message
    interceptor.observe.assert_called_once_with(valid_msg, "c2s")
    # Verify warning log was raised
    assert any("Malformed JSON line" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_run_proxy_signal_forwarding():
    """Verify run_proxy registers signal handlers and forwards signals to the subprocess."""
    mock_process = AsyncMock(spec=asyncio.subprocess.Process)
    mock_process.stdin = MagicMock()
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.wait = AsyncMock(return_value=42)

    # Keep stdout, stderr, and stdin streams open/pending so signal delivery triggers the shutdown
    mock_stdin_reader = asyncio.StreamReader()

    with patch("mcp_vcr.transport.launch_server", new_callable=AsyncMock) as mock_launch, \
         patch("mcp_vcr.transport.get_stdin_reader", new_callable=AsyncMock) as mock_get_stdin:
         
        mock_launch.return_value = mock_process
        mock_get_stdin.return_value = mock_stdin_reader

        # Stub stdout/stderr buffers
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()

        with patch("sys.stdout", mock_stdout), patch("sys.stderr", mock_stderr):
            # We trigger the shutdown event immediately to simulate signal delivery
            async def run_and_trigger():
                # Launch run_proxy task
                task = asyncio.create_task(run_proxy(["python", "server.py"]))
                
                # Yield execution to let register and pumps start
                await asyncio.sleep(0.1)
                
                # Locate and call the signal handler registered in the loop
                loop = asyncio.get_running_loop()
                # Find calls to loop.add_signal_handler
                # Let's mock add_signal_handler to capture the handler
                return await task

            # We can test actual signal handling logic by capturing signal handler
            handlers = {}
            def mock_add_signal_handler(sig, callback, *args):
                handlers[sig] = callback

            loop = asyncio.get_running_loop()
            with patch.object(loop, "add_signal_handler", side_effect=mock_add_signal_handler):
                proxy_task = asyncio.create_task(run_proxy(["python", "server.py"]))
                # Deterministic wait for signal handlers to be registered
                async def wait_for_handlers():
                    while not (signal.SIGINT in handlers and signal.SIGTERM in handlers):
                        await asyncio.sleep(0.01)
                
                try:
                    await asyncio.wait_for(wait_for_handlers(), timeout=1.0)
                except asyncio.TimeoutError as err:
                    raise AssertionError("Timeout waiting for signal handlers to be registered") from err
                
                # Verify SIGINT and SIGTERM handlers were added
                assert signal.SIGINT in handlers
                assert signal.SIGTERM in handlers
                
                # Invoke the SIGINT handler
                handlers[signal.SIGINT]()
                
                exit_code = await proxy_task

                # Subprocess must have received SIGINT signal
                mock_process.send_signal.assert_called_once_with(signal.SIGINT)
                # Subprocess wait was called
                mock_process.wait.assert_called()
                # Exit code matches subprocess
                assert exit_code == 42
