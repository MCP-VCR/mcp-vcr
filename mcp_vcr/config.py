import os
import re
import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

class ConfigError(ValueError):
    """Custom error raised on configuration validation or environment variable resolution failure."""
    pass

_ENV_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}')

def _resolve_env_vars(value: str, yaml_path: str) -> str:
    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        default = m.group(2)  # None if no :- syntax
        val = os.environ.get(var_name)
        if val is not None:
            return val
        if default is not None:
            return default
        raise ConfigError(
            f"Environment variable '{var_name}' is not set, "
            f"referenced in .mcp-vcr.yaml at {yaml_path}"
        )
    resolved = value.replace('$$', '\x00')  # escape literal $
    resolved = _ENV_RE.sub(_replace, resolved)
    return resolved.replace('\x00', '$')

def _resolve_value_recursive(node: Any, current_path: str) -> Any:
    if isinstance(node, dict):
        return {k: _resolve_value_recursive(v, f"{current_path}.{k}" if current_path else k) for k, v in node.items()}
    elif isinstance(node, list):
        return [_resolve_value_recursive(item, f"{current_path}[{i}]") for i, item in enumerate(node)]
    elif isinstance(node, str):
        return _resolve_env_vars(node, current_path)
    return node

def _merge_field(base: Any, override: Any) -> Any:
    """Merge override value into base value."""
    # List replace escape hatch
    if isinstance(base, list) and isinstance(override, dict) and override.get("replace") is True:
        return override.get("values", [])
    
    # List extend by default
    if isinstance(base, list) and isinstance(override, list):
        merged_list = list(base)
        for v in override:
            if v not in merged_list:
                merged_list.append(v)
        return merged_list
        
    # Dictionary deep merge
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {**base}
        for k, v in override.items():
            merged[k] = _merge_field(merged.get(k), v)
        return merged
        
    # Scalar / other types: override wins
    return override

class Config:
    def __init__(self, raw_data: Dict[str, Any]):
        self.raw_data = raw_data

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        if config_path is None:
            config_path = Path.cwd() / ".mcp-vcr.yaml"
            
        import yaml
        raw_data: Dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    raw_data = yaml.safe_load(f) or {}
            except Exception as e:
                raise ConfigError(f"Failed to parse config file {config_path}: {e}")
                
        if not isinstance(raw_data, dict):
            raise ConfigError("Configuration file must represent a dictionary")
            
        # Validate top-level keys
        valid_keys = {"replay", "redact", "normalize", "diff", "transport", "overrides"}
        for k in raw_data.keys():
            if k not in valid_keys:
                import logging
                logging.getLogger("mcp-vcr.config").warning(f"Unknown key in configuration: {k}")
                
        # Resolve environment variables recursively
        resolved_data = _resolve_value_recursive(raw_data, "")
        return cls(resolved_data)

    def for_snapshot(self, snapshot_path: Path) -> Dict[str, Any]:
        """Resolve effective config for a given snapshot by matching glob overrides."""
        # 1. Global config defaults
        resolved = {
            "replay": self.raw_data.get("replay", {}),
            "redact": self.raw_data.get("redact", {}),
            "normalize": self.raw_data.get("normalize", {}),
            "diff": self.raw_data.get("diff", {}),
            "transport": self.raw_data.get("transport", {}),
        }
        
        # Ensure they are dicts
        for key in ["replay", "redact", "normalize", "diff", "transport"]:
            if not isinstance(resolved[key], dict):
                resolved[key] = {}
                
        # 2. Match glob overrides (last-match-wins)
        overrides = self.raw_data.get("overrides", [])
        if isinstance(overrides, list):
            snapshot_str = str(snapshot_path)
            for item in overrides:
                if not isinstance(item, dict) or "match" not in item:
                    continue
                pattern = item["match"]
                if fnmatch.fnmatch(snapshot_str, pattern) or fnmatch.fnmatch(snapshot_path.name, pattern):
                    # Deep merge matching overrides
                    for key in ["replay", "redact", "normalize", "diff", "transport"]:
                        if key in item and isinstance(item[key], dict):
                            resolved[key] = _merge_field(resolved[key], item[key])
                            
        return resolved

    def replay_config(self, snapshot_path: Optional[Path] = None) -> dict:
        val = self.for_snapshot(snapshot_path).get("replay") if snapshot_path else self.raw_data.get("replay")
        return val if isinstance(val, dict) else {}

    def redact_config(self, snapshot_path: Optional[Path] = None) -> dict:
        val = self.for_snapshot(snapshot_path).get("redact") if snapshot_path else self.raw_data.get("redact")
        return val if isinstance(val, dict) else {}

    def normalize_config(self, snapshot_path: Optional[Path] = None) -> dict:
        val = self.for_snapshot(snapshot_path).get("normalize") if snapshot_path else self.raw_data.get("normalize")
        return val if isinstance(val, dict) else {}

    def diff_config(self, snapshot_path: Optional[Path] = None) -> dict:
        val = self.for_snapshot(snapshot_path).get("diff") if snapshot_path else self.raw_data.get("diff")
        return val if isinstance(val, dict) else {}
