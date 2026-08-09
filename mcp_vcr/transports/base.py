import sys
from typing import Optional, Protocol

class Transport(Protocol):
    """Abstract transport interface for MCP proxy communication."""

    async def start(self) -> None:
        """Initialize the transport (e.g., launch subprocess or open HTTP/SSE connections)."""
        ...

    async def read_client_message(self) -> Optional[bytes]:
        """Read a single message from the client. Returns None on EOF/disconnect."""
        ...

    async def write_to_server(self, data: bytes) -> None:
        """Write a raw message payload to the server."""
        ...

    async def read_server_message(self) -> Optional[bytes]:
        """Read a single message from the server. Returns None on EOF/disconnect."""
        ...

    async def write_to_client(self, data: bytes) -> None:
        """Write a raw message payload to the client."""
        ...

    async def shutdown(self, sig: Optional[int] = None) -> int:
        """Gracefully shut down the transport and any associated processes/connections.
        
        If a signal is provided, it should be propagated to child processes if applicable.
        Returns the exit code of the server process or 0 for connection-based transports.
        """
        ...

    @property
    def server_running(self) -> bool:
        """Return True if the server connection/process is active."""
        ...
