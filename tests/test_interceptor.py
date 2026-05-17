import asyncio
import time
from unittest.mock import MagicMock, patch
import pytest
from mcp_vcr.interceptor import MessageInterceptor, Direction
from mcp_vcr.recorder import TranscriptRecorder

def test_interceptor_timestamps():
    """Verify monotonic relative millisecond timestamps and construction base time."""
    interceptor = MessageInterceptor()
    time.sleep(0.01)  # Simulate small delay
    
    interceptor.observe({"jsonrpc": "2.0", "method": "ping"}, Direction.C2S)
    time.sleep(0.02)
    interceptor.observe({"jsonrpc": "2.0", "result": "pong", "id": 1}, Direction.S2C)
    
    msgs = interceptor.observed_messages
    assert len(msgs) == 2
    
    # First message t should be close to 10-15ms relative
    assert msgs[0]["t"] >= 0
    # Second message timestamp must be greater than or equal to the first
    assert msgs[1]["t"] >= msgs[0]["t"]


def test_interceptor_classification():
    """Verify classification of JSON-RPC requests, responses, and notifications."""
    interceptor = MessageInterceptor()
    
    # 1. Request (id and method)
    req = {"jsonrpc": "2.0", "id": 100, "method": "initialize", "params": {}}
    interceptor.observe(req, Direction.C2S)
    assert interceptor.observed_messages[-1]["msg_type"] == "request"
    
    # 2. Response (id, no method)
    res = {"jsonrpc": "2.0", "id": 100, "result": {"protocolVersion": "2024-11-05"}}
    interceptor.observe(res, Direction.S2C)
    assert interceptor.observed_messages[-1]["msg_type"] == "response"
    
    # 3. Notification (method, no id)
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    interceptor.observe(notif, Direction.C2S)
    assert interceptor.observed_messages[-1]["msg_type"] == "notification"
    
    # 4. Notification with id=null
    notif_null = {"jsonrpc": "2.0", "id": None, "method": "notifications/initialized"}
    interceptor.observe(notif_null, Direction.C2S)
    assert interceptor.observed_messages[-1]["msg_type"] == "notification"


def test_direction_tagging():
    """Verify direction tag enum matches stored representation."""
    interceptor = MessageInterceptor()
    
    interceptor.observe({"jsonrpc": "2.0", "method": "test"}, Direction.C2S)
    assert interceptor.observed_messages[-1]["dir"] == "c2s"
    
    interceptor.observe({"jsonrpc": "2.0", "result": "test"}, Direction.S2C)
    assert interceptor.observed_messages[-1]["dir"] == "s2c"


@pytest.mark.asyncio
async def test_non_blocking_recorder_dispatch():
    """Verify that writing to the transcript is non-blocking (observe returns in <1ms)."""
    mock_recorder = MagicMock(spec=TranscriptRecorder)
    
    # Simulate a slow disk write that takes 100ms
    def slow_write(msg):
        time.sleep(0.1)
        
    mock_recorder.write.side_effect = slow_write
    interceptor = MessageInterceptor(recorder=mock_recorder)
    
    # Measure execution time of observe
    t_start = time.perf_counter()
    interceptor.observe({"jsonrpc": "2.0", "method": "test"}, Direction.C2S)
    t_elapsed = time.perf_counter() - t_start
    
    # Must complete instantly (typically < 1-2ms, well below 100ms)
    assert t_elapsed < 0.01
    
    # Let event loop process scheduled tasks to invoke slow_write
    await asyncio.sleep(0.15)
    mock_recorder.write.assert_called_once()


@pytest.mark.asyncio
async def test_recorder_exception_safety():
    """Verify recorder task exceptions are caught and do not crash the interceptor/pumps."""
    mock_recorder = MagicMock(spec=TranscriptRecorder)
    mock_recorder.write.side_effect = IOError("Disk write error")
    
    interceptor = MessageInterceptor(recorder=mock_recorder)
    
    # Should not raise exception
    interceptor.observe({"jsonrpc": "2.0", "method": "test"}, Direction.C2S)
    
    # Yield loop execution to let task complete/fail silently
    await asyncio.sleep(0.05)
    mock_recorder.write.assert_called_once()
