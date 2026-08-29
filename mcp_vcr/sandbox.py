import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .transports.base import Transport
from .transports.stdio import StdioTransport

logger = logging.getLogger("mcp-vcr.sandbox")


@dataclass
class SandboxConfig:
    """Process hygiene for active audit. NOT a containment sandbox.

    Provides:
    - Environment variable scrubbing (prevents credential leakage TO the server)
    - PATH restriction (limits which binaries the server can exec)
    - Optional tmpdir isolation for server CWD and TMPDIR
    - Allowlist for specific env vars servers need

    Does NOT provide:
    - Filesystem containment (server can read/write host filesystem)
    - Network isolation (server can make outbound connections)
    - Process namespace isolation (server runs in same PID/mount namespace)

    For real containment, run inside a container (Docker/Podman).
    """

    restrict_env: bool = True
    restrict_path: bool = True
    tmpdir: Optional[Path] = None
    allow_env: List[str] = field(default_factory=list)
    timeout_s: int = 30
    max_restarts: int = 5


class SandboxedTransport(Transport):
    """
    Wraps StdioTransport with subprocess environment restrictions and stderr redaction.
    """

    def __init__(
        self,
        server_args: List[str],
        config: Optional[SandboxConfig] = None,
        limit: int = 16 * 1024 * 1024,
    ):
        self.server_args = server_args
        self.config = config or SandboxConfig()
        self.limit = limit
        self._inner: Optional[StdioTransport] = None
        self._active_markers: List[str] = []

    def set_active_markers(self, markers: List[str]) -> None:
        """Set canary markers for stderr redaction."""
        self._active_markers = markers

    def _build_env(self) -> Dict[str, str]:
        """Build restricted environment dict for subprocess."""
        env: Dict[str, str] = {}

        if self.config.restrict_env:
            # Always inherit basic localization, python/node paths
            safe_vars = {"LANG", "LC_ALL", "TERM", "PYTHONPATH", "NODE_PATH"}
            if not self.config.restrict_path:
                safe_vars.add("PATH")
            safe_vars.update(self.config.allow_env)

            for k in safe_vars:
                if k in os.environ:
                    env[k] = os.environ[k]
        else:
            env = dict(os.environ)

        if self.config.restrict_path:
            venv_bin = os.path.dirname(sys.executable)
            env["PATH"] = f"{venv_bin}:/usr/bin:/bin:/usr/local/bin"

        if self.config.tmpdir:
            env["TMPDIR"] = str(self.config.tmpdir)

        env["MCP_VCR_SANDBOX"] = "1"
        return env

    async def start(self) -> None:
        new_env = self._build_env()
        cwd_path = str(self.config.tmpdir) if self.config.tmpdir else None
        self._inner = StdioTransport(
            self.server_args,
            limit=self.limit,
            read_stdin=False,
            capture_stderr=True,
            env=new_env,
            cwd=cwd_path,
        )
        await self._inner.start()


    async def read_client_message(self) -> Optional[bytes]:
        if not self._inner:
            return None
        return await self._inner.read_client_message()

    async def write_to_server(self, data: bytes) -> None:
        if self._inner:
            await self._inner.write_to_server(data)

    async def read_server_message(self) -> Optional[bytes]:
        if not self._inner:
            return None
        return await self._inner.read_server_message()

    async def write_to_client(self, data: bytes) -> None:
        if self._inner:
            await self._inner.write_to_client(data)

    async def shutdown(self, sig: Optional[int] = None) -> int:
        if self._inner:
            return await self._inner.shutdown(sig)
        return 0

    @property
    def server_running(self) -> bool:
        return self._inner.server_running if self._inner else False

    def drain_captured_stderr(self) -> str:
        """Return captured stderr lines with canary redaction applied."""
        if not self._inner or not hasattr(self._inner, "drain_captured_stderr"):
            return ""
        raw_stderr = self._inner.drain_captured_stderr()
        if not raw_stderr:
            return ""

        from .active_auditor import _normalize_for_matching, redact_canaries

        normalized = _normalize_for_matching(raw_stderr)
        return redact_canaries(normalized, self._active_markers)
