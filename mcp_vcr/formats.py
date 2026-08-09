import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, Literal
import yaml

logger = logging.getLogger("mcp-vcr.formats")

def detect_format(path: Path) -> Literal["yaml", "ndjson"]:
    """Detect transcript format. Extension-based, with content sniff fallback."""
    if path.suffix == ".ndjson":
        return "ndjson"
    if path.suffix in (".yaml", ".yml"):
        return "yaml"
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line.startswith("{"):
                obj = json.loads(first_line)
                if isinstance(obj, dict) and "_type" in obj:
                    return "ndjson"
    except Exception:
        pass
    return "yaml"

def iter_messages(path: Path) -> Iterator[Dict[str, Any]]:
    """Lazily yield messages from the transcript file."""
    fmt = detect_format(path)
    if fmt == "ndjson":
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, start=1):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    obj = json.loads(line_str)
                    if isinstance(obj, dict):
                        if obj.get("_type") != "meta":
                            yield obj
                    else:
                        logger.warning(f"Skipping non-object NDJSON line at line {idx} in {path}")
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse NDJSON line at line {idx} in {path}: {e}")
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        messages = data.get("messages") or []
        yield from messages

def load_meta(path: Path) -> Dict[str, Any]:
    """Efficiently load only the meta section of a transcript."""
    fmt = detect_format(path)
    if fmt == "ndjson":
        try:
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line.startswith("{"):
                    obj = json.loads(first_line)
                    if isinstance(obj, dict) and obj.get("_type") == "meta":
                        meta = dict(obj)
                        meta.pop("_type", None)
                        return meta
        except Exception as e:
            logger.warning(f"Failed to load NDJSON meta from {path}: {e}")
        return {}
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("meta") or {}
