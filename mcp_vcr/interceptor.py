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

    def flush(self) -> None:
        """
        Flush captured messages.
        """
        logger.info(f"Flushed {len(self.observed_messages)} messages.")

    def save(self, file_path: str = "session.yaml") -> None:
        """
        Save the captured messages to a YAML file matching the schema requirements.
        """
        import yaml
        from datetime import datetime, timezone
        
        # Build standard versioned transcript structure
        data = {
            "meta": {
                "version": 1,
                "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "session_id": "session00",
                "server_command": ["mcp-vcr"],
                "schema_version": "1.0"
            },
            "messages": self.observed_messages
        }
        
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Saved {len(self.observed_messages)} messages to {file_path}")

