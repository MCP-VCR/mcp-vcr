import re
import copy
import yaml
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger("mcp-vcr.normalizer")

class Normalizer(Protocol):
    """
    Normalizer defines the interface all payload normalizers must implement.
    Normalizers are design to be composable and must return a new dictionary 
    or structure without mutating their input.
    """
    name: str
    def apply(self, payload: Any) -> Any:
        ...

class TimestampNormalizer:
    """
    TimestampNormalizer recursively detects ISO 8601 timestamps and replaces them
    with the 'NORM_TIMESTAMP' placeholder to avoid noise in transcript diffs.
    """
    def __init__(self):
        self.name = "timestamps"
        # Match ISO 8601 strings starting with \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}
        self.timestamp_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def apply(self, payload: Any) -> Any:
        copied = copy.deepcopy(payload)
        return self._walk(copied)

    def _walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            for key in list(node.keys()):
                node[key] = self._walk(node[key])
            return node
        elif isinstance(node, list):
            for i in range(len(node)):
                node[i] = self._walk(node[i])
            return node
        elif isinstance(node, str):
            if self.timestamp_re.match(node):
                return "NORM_TIMESTAMP"
            return node
        else:
            return node

class RequestIdNormalizer:
    """
    RequestIdNormalizer maps numeric and string JSON-RPC request IDs to sequential
    normalized placeholders (NORM_ID_1, NORM_ID_2, ...) consistently within a session.
    """
    def __init__(self):
        self.name = "request_ids"
        self.seen_ids: Dict[Any, str] = {}

    def apply(self, payload: Any) -> Any:
        copied = copy.deepcopy(payload)
        return self._walk(copied)

    def _walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            if "id" in node and node["id"] is not None:
                val = node["id"]
                if isinstance(val, (int, str)):
                    if val not in self.seen_ids:
                        self.seen_ids[val] = f"NORM_ID_{len(self.seen_ids) + 1}"
                    node["id"] = self.seen_ids[val]
            
            for key in list(node.keys()):
                if key != "id":
                    node[key] = self._walk(node[key])
            return node
        elif isinstance(node, list):
            for i in range(len(node)):
                node[i] = self._walk(node[i])
            return node
        else:
            return node

class UuidNormalizer:
    """
    UuidNormalizer replaces random UUID v4 values with sequential placeholders
    (NORM_UUID_1, NORM_UUID_2, ...) to prevent false diff positives.
    """
    def __init__(self):
        self.name = "uuids"
        self.seen_uuids: Dict[str, str] = {}
        # Case-insensitive UUID v4 match regex
        self.uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.IGNORECASE
        )

    def apply(self, payload: Any) -> Any:
        copied = copy.deepcopy(payload)
        return self._walk(copied)

    def _walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            for key in list(node.keys()):
                node[key] = self._walk(node[key])
            return node
        elif isinstance(node, list):
            for i in range(len(node)):
                node[i] = self._walk(node[i])
            return node
        elif isinstance(node, str):
            if self.uuid_re.match(node):
                val_lower = node.lower()
                if val_lower not in self.seen_uuids:
                    self.seen_uuids[val_lower] = f"NORM_UUID_{len(self.seen_uuids) + 1}"
                return self.seen_uuids[val_lower]
            return node
        else:
            return node

class CursorNormalizer:
    """
    CursorNormalizer replaces pagination cursors and token values with NORM_CURSOR
    based on targeted field name matching.
    """
    def __init__(self):
        self.name = "cursors"
        self.target_fields = {"cursor", "nextCursor", "pageToken", "continuationToken"}

    def apply(self, payload: Any) -> Any:
        copied = copy.deepcopy(payload)
        return self._walk(copied)

    def _walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            for key in list(node.keys()):
                if isinstance(key, str) and key in self.target_fields:
                    if isinstance(node[key], str):
                        node[key] = "NORM_CURSOR"
                else:
                    node[key] = self._walk(node[key])
            return node
        elif isinstance(node, list):
            for i in range(len(node)):
                node[i] = self._walk(node[i])
            return node
        else:
            return node

class NormalizerChain:
    """
    NormalizerChain applies a list of payload normalizers in order.
    """
    def __init__(self, normalizers: List[Normalizer]):
        self.normalizers = normalizers

    def apply(self, payload: Any) -> Any:
        """
        Sequentially apply normalizers. Each normalizer receives a fresh deep copy
        to ensure no mutation side-effects.
        """
        current = copy.deepcopy(payload)
        for norm in self.normalizers:
            fresh_copy = copy.deepcopy(current)
            current = norm.apply(fresh_copy)
        return current

    @classmethod
    def from_config(cls, config_path: Optional[Path] = None) -> "NormalizerChain":
        """
        Factory method to load configuration from .mcp-vcr.yaml in the project root,
        validate keys, toggle active normalizers, and construct the NormalizerChain.
        """
        if config_path is None:
            config_path = Path.cwd() / ".mcp-vcr.yaml"
            
        normalize_cfg = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    if isinstance(config, dict):
                        normalize_cfg = config.get("normalize", {})
            except Exception as e:
                logger.warning(f"Failed to load configuration file {config_path}: {e}")
                
        # Validate keys in the configuration
        valid_keys = {"timestamps", "request_ids", "uuids", "cursors"}
        if isinstance(normalize_cfg, dict):
            for key in normalize_cfg.keys():
                if key not in valid_keys:
                    logger.warning(f"Unknown key in normalize configuration: {key}")
        else:
            normalize_cfg = {}

        # Default all normalizers to True (enabled)
        normalizers: List[Normalizer] = []
        if normalize_cfg.get("timestamps", True) is not False:
            normalizers.append(TimestampNormalizer())
        if normalize_cfg.get("request_ids", True) is not False:
            normalizers.append(RequestIdNormalizer())
        if normalize_cfg.get("uuids", True) is not False:
            normalizers.append(UuidNormalizer())
        if normalize_cfg.get("cursors", True) is not False:
            normalizers.append(CursorNormalizer())
            
        return cls(normalizers)
