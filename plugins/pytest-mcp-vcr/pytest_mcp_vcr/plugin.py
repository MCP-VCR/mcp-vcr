import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional, Set
import pytest

from mcp_vcr.transports.base import Transport
from mcp_vcr.recorder import TranscriptRecorder
from mcp_vcr.validator import load_transcript_data

logger = logging.getLogger("pytest-mcp-vcr")

class RecordingTransport(Transport):
    """
    RecordingTransport wraps any inner MCP Transport and records client-to-server (c2s)
    and server-to-client (s2c) messages to a cassette file.
    """
    def __init__(self, inner: Transport, recorder: TranscriptRecorder):
        self.inner = inner
        self.recorder = recorder
        self.t0: Optional[float] = None

    async def start(self) -> None:
        await self.inner.start()
        self.recorder.start_session()
        self.t0 = time.monotonic()

    async def shutdown(self, sig: Optional[int] = None) -> int:
        try:
            return await self.inner.shutdown(sig)
        finally:
            self.recorder.close()

    async def read_client_message(self) -> Optional[bytes]:
        return await self.inner.read_client_message()

    async def write_to_client(self, data: bytes) -> None:
        await self.inner.write_to_client(data)

    async def write_to_server(self, data: bytes) -> None:
        if self.t0 is not None:
            t_elapsed = int((time.monotonic() - self.t0) * 1000)
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if payload is not None:
                self.recorder.write({
                    "t": t_elapsed,
                    "dir": "c2s",
                    "payload": payload
                })
        await self.inner.write_to_server(data)

    async def read_server_message(self) -> Optional[bytes]:
        data = await self.inner.read_server_message()
        if data and self.t0 is not None:
            t_elapsed = int((time.monotonic() - self.t0) * 1000)
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if payload is not None:
                self.recorder.write({
                    "t": t_elapsed,
                    "dir": "s2c",
                    "payload": payload
                })
        return data

    @property
    def server_running(self) -> bool:
        return self.inner.server_running


class ReplayingTransport(Transport):
    """
    ReplayingTransport simulates a mock transport that yields server responses
    completely offline by reading from a recorded cassette.
    """
    def __init__(self, transcript_path: Path):
        self.transcript_path = Path(transcript_path)
        self.messages: List[Dict[str, Any]] = []
        self.meta: Dict[str, Any] = {}
        self.msg_index = 0
        self.c2s_to_client_id: Dict[int, Any] = {}
        self.s2c_to_client_id: Dict[int, Any] = {}
        self.consumed_c2s_indices: Set[int] = set()
        self._c2s_written_event: Optional[asyncio.Event] = None

    async def start(self) -> None:
        self._c2s_written_event = asyncio.Event()
        data = load_transcript_data(self.transcript_path)
        self.meta = data.get("meta", {})
        self.messages = data.get("messages", [])

        # Pair c2s requests with their corresponding s2c responses in FIFO order
        resp_by_id = defaultdict(list)
        for msg in self.messages:
            if msg.get("dir") == "s2c":
                payload = msg.get("payload", {})
                resp_id = payload.get("id")
                if resp_id is not None:
                    resp_by_id[resp_id].append(msg)

        for msg in self.messages:
            if msg.get("dir") == "c2s":
                payload = msg.get("payload", {})
                req_id = payload.get("id")
                if req_id is not None and resp_by_id[req_id]:
                    resp_msg = resp_by_id[req_id].pop(0)
                    msg["paired_response"] = resp_msg

    async def shutdown(self, sig: Optional[int] = None) -> int:
        return 0

    async def read_client_message(self) -> Optional[bytes]:
        return None

    async def write_to_client(self, data: bytes) -> None:
        pass

    async def write_to_server(self, data: bytes) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
            # Find the next unmatched c2s request/notification in self.messages
            for idx, msg in enumerate(self.messages):
                if msg.get("dir") == "c2s" and idx not in self.consumed_c2s_indices:
                    self.consumed_c2s_indices.add(idx)
                    req_id = payload.get("id")
                    if req_id is not None:
                        self.c2s_to_client_id[id(msg)] = req_id
                        paired = msg.get("paired_response")
                        if paired:
                            self.s2c_to_client_id[id(paired)] = req_id
                    if self._c2s_written_event:
                        self._c2s_written_event.set()
                    break
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error("Failed to decode or parse C2S payload in ReplayingTransport: %s", e)
        except Exception as e:
            logger.error("Unexpected error in ReplayingTransport write_to_server: %s", e)

    async def read_server_message(self) -> Optional[bytes]:
        while self.msg_index < len(self.messages):
            msg = self.messages[self.msg_index]
            payload = msg.get("payload", {})

            if msg.get("dir") == "c2s":
                if self.msg_index in self.consumed_c2s_indices:
                    self.msg_index += 1
                    continue
                else:
                    if self._c2s_written_event:
                        self._c2s_written_event.clear()
                        await self._c2s_written_event.wait()
                    continue

            # It is an s2c message (response or notification)
            resp_id = payload.get("id")
            if resp_id is None:
                # Notification: yield immediately
                self.msg_index += 1
                return (json.dumps(payload) + "\n").encode("utf-8")
            else:
                # Response: check if mapped
                client_id = self.s2c_to_client_id.get(id(msg))
                if client_id is not None:
                    resp_payload = dict(payload)
                    resp_payload["id"] = client_id
                    self.msg_index += 1
                    return (json.dumps(resp_payload) + "\n").encode("utf-8")
                else:
                    # Wait until request is mapped
                    if self._c2s_written_event:
                        self._c2s_written_event.clear()
                        await self._c2s_written_event.wait()
                    continue
        return None

    @property
    def server_running(self) -> bool:
        return False


@contextlib.asynccontextmanager
async def vcr_cassette(
    path: Path,
    mode: Literal["record", "replay"],
    inner_transport: Optional[Transport] = None
) -> AsyncGenerator[Transport, None]:
    """
    Async context manager for recording or replaying MCP sessions.
    """
    path = Path(path)
    if mode == "record":
        if inner_transport is None:
            raise ValueError("inner_transport is required for record mode")
        recorder = TranscriptRecorder(filename=str(path))
        recording_transport = RecordingTransport(inner_transport, recorder)
        await recording_transport.start()
        try:
            yield recording_transport
        finally:
            await recording_transport.shutdown()
    else:
        replaying_transport = ReplayingTransport(path)
        await replaying_transport.start()
        try:
            yield replaying_transport
        finally:
            await replaying_transport.shutdown()


@pytest.fixture
def mcp_vcr_recording():
    """
    Fixture returning a function to create a recording context manager.
    """
    def _record(path: Path, inner_transport: Transport):
        return vcr_cassette(path, mode="record", inner_transport=inner_transport)
    return _record


@pytest.fixture
def mcp_vcr_replayer():
    """
    Fixture returning a function to create a replaying context manager.
    """
    def _replay(path: Path):
        return vcr_cassette(path, mode="replay")
    return _replay
