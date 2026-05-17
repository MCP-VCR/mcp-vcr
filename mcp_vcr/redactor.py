import re
import copy
import yaml
import logging
from pathlib import Path
from typing import Any, List, Set, Optional

logger = logging.getLogger("mcp-vcr.redactor")

DEFAULT_FIELDS = {"token", "api_key", "secret", "password", "credential", "authorization", "bearer"}

DEFAULT_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}",
    r"Bearer [a-zA-Z0-9\-._~+/]+=*",
    r"[A-Z0-9]{20}:[a-zA-Z0-9+/]{40}"
]

class Redactor:
    """
    Redactor replaces values of known sensitive fields, patterns, and filesystem paths
    in JSON-RPC payloads before recording them in transcripts.
    """
    def __init__(self, config_path: Optional[Path] = None):
        self.sensitive_fields: Set[str] = set(DEFAULT_FIELDS)
        self.compiled_patterns: List[re.Pattern] = []
        self.paths_enabled: bool = True
        
        # Load default patterns
        for pat in DEFAULT_PATTERNS:
            self.compiled_patterns.append(re.compile(pat))
            
        # Path patterns
        self.unix_path_re = re.compile(r"^/[a-zA-Z0-9_/.-]{3,}")
        self.windows_path_re = re.compile(r"^[a-zA-Z]:\\")
        
        # Load custom configuration from default or custom path
        self._load_config(config_path)

    def _load_config(self, config_path: Optional[Path] = None) -> None:
        if config_path is None:
            config_path = Path.cwd() / ".mcp-vcr.yaml"
            
        if not config_path.exists():
            return
            
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to load configuration file {config_path}: {e}")
            return
            
        if not isinstance(config, dict):
            return
            
        redact_cfg = config.get("redact")
        if not isinstance(redact_cfg, dict):
            return
            
        # 1. Custom fields (case-insensitive merge)
        custom_fields = redact_cfg.get("fields")
        if isinstance(custom_fields, list):
            for field in custom_fields:
                if isinstance(field, str):
                    self.sensitive_fields.add(field.lower())
                    
        # 2. Custom patterns
        custom_patterns = redact_cfg.get("patterns")
        if isinstance(custom_patterns, list):
            for pat in custom_patterns:
                if isinstance(pat, str):
                    try:
                        self.compiled_patterns.append(re.compile(pat))
                    except re.error as e:
                        logger.warning(f"Invalid regex pattern in configuration (skipped): {pat}. Error: {e}")
                        
        # 3. Path redaction enable/disable (default true unless explicitly set to false)
        if "paths" in redact_cfg:
            self.paths_enabled = redact_cfg["paths"] is not False

    def redact(self, payload: Any) -> Any:
        """
        Deeply copy the payload and apply recursive redaction rules.
        """
        copied = copy.deepcopy(payload)
        return self._redact_recursive(copied)

    def _redact_recursive(self, node: Any) -> Any:
        if isinstance(node, dict):
            # Dict key iteration: if key matches sensitive fields, redact entirely
            for key in list(node.keys()):
                val = node[key]
                if isinstance(key, str) and key.lower() in self.sensitive_fields:
                    node[key] = f"<REDACTED_{key}>"
                else:
                    node[key] = self._redact_recursive(val)
            return node
            
        elif isinstance(node, list):
            # Recursively walk list items
            for i in range(len(node)):
                node[i] = self._redact_recursive(node[i])
            return node
            
        elif isinstance(node, str):
            # 1. Apply pattern redaction
            for pattern in self.compiled_patterns:
                if pattern.search(node):
                    return "<REDACTED>"
                    
            # 2. Apply path redaction if enabled
            if self.paths_enabled:
                if self.unix_path_re.search(node) or self.windows_path_re.search(node):
                    return "<PATH>"
                    
            return node
            
        else:
            # Leaf of other types (numbers, booleans, None, etc.) remain unchanged
            return node
