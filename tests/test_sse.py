import asyncio
import json
import pytest
from aiohttp import web
from unittest.mock import MagicMock, AsyncMock, patch
from mcp_vcr.transports.sse import SseTransport

@pytest.mark.asyncio
async def test_sse_transport_lifecycle_and_messages():
    received_posts = []
    
    async def sse_handler(request):
        resp = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        })
        await resp.prepare(request)
        
        # Send dynamic endpoint discovery event
        await resp.write(b"event: endpoint\n")
        await resp.write(b"data: /custom_message\n\n")
        
        # Send a data event
        await resp.write(b"data: {\"jsonrpc\": \"2.0\", \"id\": 1, \"result\": \"success\"}\n\n")
        
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        return resp

    async def post_handler(request):
        payload = await request.json()
        received_posts.append(payload)
        return web.Response(status=202)

    app = web.Application()
    app.router.add_get('/sse', sse_handler)
    app.router.add_post('/custom_message', post_handler)
    
    runner = web.AppRunner(app, shutdown_timeout=0.5)
    await runner.setup()
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        
        port = runner.addresses[0][1]
        sse_url = f"http://127.0.0.1:{port}/sse"
        
        # Initialize SseTransport
        transport = SseTransport(sse_url=sse_url)
        
        # Patch get_stdin_reader to avoid accessing real sys.stdin during test
        mock_stdin_reader = asyncio.StreamReader()
        with patch("mcp_vcr.transports.sse.get_stdin_reader", new_callable=AsyncMock) as mock_get_stdin, \
             patch("sys.stdout", MagicMock()), \
             patch("sys.stderr", MagicMock()):
             
            mock_get_stdin.return_value = mock_stdin_reader
            await transport.start()
            
            # Wait for dynamic endpoint resolution to happen in background task
            for _ in range(50):
                if transport.post_url != sse_url:
                    break
                await asyncio.sleep(0.01)
                
            assert "/custom_message" in transport.post_url
            
            # Read server message and verify content
            server_msg = await transport.read_server_message()
            assert server_msg is not None
            payload = json.loads(server_msg.decode("utf-8").strip())
            assert payload["id"] == 1
            assert payload["result"] == "success"
            
            # Write C2S message to server
            test_req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
            await transport.write_to_server(json.dumps(test_req).encode("utf-8"))
            
            # Verify mock server received the post request on the resolved endpoint
            await asyncio.sleep(0.1)
            assert len(received_posts) == 1
            assert received_posts[0]["method"] == "ping"
            
            # Shutdown
            await transport.shutdown()
    finally:
        await runner.cleanup()

@pytest.mark.asyncio
async def test_sse_replay_engine(tmp_path):
    received_posts = []
    
    # 1. Setup mock SSE + POST server
    async def sse_handler(request):
        resp = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        })
        await resp.prepare(request)
        
        # When client initializes, respond back with initialize result
        await resp.write(b"data: {\"jsonrpc\": \"2.0\", \"id\": 1, \"result\": {\"protocolVersion\": \"2024-11-05\", \"capabilities\": {}, \"serverInfo\": {\"name\": \"mock\", \"version\": \"1.0.0\"}}}\n\n")
        
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        return resp

    async def post_handler(request):
        payload = await request.json()
        received_posts.append(payload)
        return web.Response(status=202)

    app = web.Application()
    app.router.add_get('/sse', sse_handler)
    app.router.add_post('/message', post_handler)
    
    runner = web.AppRunner(app, shutdown_timeout=0.5)
    await runner.setup()
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        
        port = runner.addresses[0][1]
        sse_url = f"http://127.0.0.1:{port}/sse"
        post_url = f"http://127.0.0.1:{port}/message"
        
        # 2. Write a minimal session transcript to replay
        transcript_content = """
meta:
  version: 1
  recorded_at: "2026-05-18T12:00:00Z"
  session_id: "abcdef12"
  server_command: ["python"]
messages:
  - t: 0
    dir: "c2s"
    payload: {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
"""
        session_file = tmp_path / "session_source.yaml"
        with open(session_file, "w", encoding="utf-8") as f:
            f.write(transcript_content)
            
        # 3. Create SseTransport and execute replay
        from mcp_vcr.replay import ReplayEngine
        engine = ReplayEngine(timeout_ms=1000)
        
        transport = SseTransport(sse_url=sse_url, post_url=post_url)
        
        # Patch get_stdin_reader to avoid accessing real sys.stdin during test
        mock_stdin_reader = asyncio.StreamReader()
        with patch("mcp_vcr.transports.sse.get_stdin_reader", new_callable=AsyncMock) as mock_get_stdin, \
             patch("sys.stdout", MagicMock()), \
             patch("sys.stderr", MagicMock()):
             
            mock_get_stdin.return_value = mock_stdin_reader
            output_path = await engine.run_replay(session_file, transport=transport)
        
        # 4. Verify output and server interaction
        assert output_path.exists()
        
        import yaml
        with open(output_path, "r", encoding="utf-8") as f:
            output_data = yaml.safe_load(f)
            
        assert output_data["meta"]["version"] == 1
        messages = output_data["messages"]
        # Check that it recorded the s2c response
        s2c_messages = [msg for msg in messages if msg["dir"] == "s2c"]
        assert len(s2c_messages) == 1
        assert s2c_messages[0]["payload"]["result"]["serverInfo"]["name"] == "mock"
        
        # Check post request received
        assert len(received_posts) == 1
        assert received_posts[0]["method"] == "initialize"
    finally:
        await runner.cleanup()

