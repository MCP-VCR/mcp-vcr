import logging
import time
from typing import Dict, Any, Optional

logger = logging.getLogger("mcp-vcr.interceptor")

class MessageInterceptor:
    """
    MessageInterceptor observes JSON-RPC messages passing through the proxy.
    In Phase 1, it serves as a lightweight observer/hook.
    """
    def __init__(self):
        self.start_time = time.monotonic()
        self.observed_messages = []

    def observe(self, payload: Dict[str, Any], direction: str) -> None:
        """
        Record and observe a message passing in a specific direction (c2s/s2c).
        """
        t = int((time.monotonic() - self.start_time) * 1000)
        logger.debug(f"Observed message [{direction}] at {t}ms")
        self.observed_messages.append({
            "t": t,
            "dir": direction,
            "payload": payload
        })
