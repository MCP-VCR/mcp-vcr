import json
import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

def parse_json_path(path_str: str) -> List[str]:
    """Parse JSON path strings (supporting $, ., and [n] notation) into segments."""
    # Strip leading $ and dot
    if path_str.startswith("$."):
        path_str = path_str[2:]
    elif path_str.startswith("$"):
        path_str = path_str[1:]
        if path_str.startswith("."):
            path_str = path_str[1:]
            
    # Normalize brackets like [2] or [n] or [*] to .2 or .n or .*
    normalized = re.sub(r'\[(\d+|[a-zA-Z*]+)\]', r'.\1', path_str)
    
    # Split by dot
    segments = [s for s in normalized.split(".") if s]
    return segments

def path_matches(path: List[Any], ignore_segments: List[str]) -> bool:
    """Check if the current path list matches target ignore path segments."""
    if len(path) != len(ignore_segments):
        return False
    for p_seg, i_seg in zip(path, ignore_segments):
        p_str = str(p_seg)
        if i_seg in ("*", "n"):
            continue
        if p_str != i_seg:
            return False
    return True

def is_ignored(path: List[Any], ignore_list: List[List[str]]) -> bool:
    """Verify if the current path is in the ignore list."""
    for ignore_seg in ignore_list:
        if path_matches(path, ignore_seg):
            return True
    return False

def format_path(path: List[Any]) -> str:
    """Format path list into a JSON path string representation."""
    parts = []
    for segment in path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}" if parts else segment)
    return "".join(parts)

def get_type_name(val: Any) -> str:
    """Map python type to standard JSON schema type name."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, (int, float)):
        return "number"
    if isinstance(val, str):
        return "string"
    if isinstance(val, dict):
        return "object"
    if isinstance(val, list):
        return "array"
    return type(val).__name__

def format_value(val: Any) -> str:
    """Format value nicely for text output."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return f'"{val}"'
    if isinstance(val, (dict, list)):
        return json.dumps(val, sort_keys=True)
    return str(val)

def _id_sort_key(v: Any) -> tuple[str, str]:
    return (type(v).__name__, str(v))

def compare_payloads(
    a: Any,
    b: Any,
    path: List[Any],
    mode: str,
    ignore_paths: List[List[str]]
) -> List[Dict[str, Any]]:
    """Recursively compare two payloads under structural or semantic diff modes."""
    if is_ignored(path, ignore_paths):
        return []
        
    changes = []
    path_str = format_path(path)
    
    type_a = get_type_name(a)
    type_b = get_type_name(b)
    
    if type_a != type_b:
        changes.append({
            "path": path_str,
            "type": "changed",
            "reason": "type_changed",
            "description": f"type changed from {type_a} to {type_b}",
            "old_type": type_a,
            "new_type": type_b,
            "old_value": a,
            "value": b,
            "is_structural": True
        })
        return changes
        
    if isinstance(a, dict):
        keys_a = set(a.keys())
        keys_b = set(b.keys())
        
        # Removed fields
        for k in sorted(keys_a - keys_b):
            k_path = path + [k]
            if not is_ignored(k_path, ignore_paths):
                changes.append({
                    "path": format_path(k_path),
                    "type": "removed",
                    "value": a[k],
                    "is_structural": True
                })
                
        # Added fields
        for k in sorted(keys_b - keys_a):
            k_path = path + [k]
            if not is_ignored(k_path, ignore_paths):
                changes.append({
                    "path": format_path(k_path),
                    "type": "added",
                    "value": b[k],
                    "is_structural": True
                })
                
        # Common fields
        for k in sorted(keys_a & keys_b):
            changes.extend(compare_payloads(a[k], b[k], path + [k], mode, ignore_paths))
            
    elif isinstance(a, list):
        len_a = len(a)
        len_b = len(b)
        if len_a != len_b:
            changes.append({
                "path": path_str,
                "type": "changed",
                "reason": "array_length_changed",
                "description": f"array length changed from {len_a} to {len_b}",
                "old_len": len_a,
                "new_len": len_b,
                "old_value": len_a,
                "value": len_b,
                "is_structural": True
            })
            
        min_len = min(len_a, len_b)
        for i in range(min_len):
            changes.extend(compare_payloads(a[i], b[i], path + [i], mode, ignore_paths))
            
        # Extra elements in B
        if len_b > len_a:
            for i in range(len_a, len_b):
                i_path = path + [i]
                if not is_ignored(i_path, ignore_paths):
                    changes.append({
                        "path": format_path(i_path),
                        "type": "added",
                        "value": b[i],
                        "is_structural": True
                    })
        # Missing elements in A
        elif len_a > len_b:
            for i in range(len_b, len_a):
                i_path = path + [i]
                if not is_ignored(i_path, ignore_paths):
                    changes.append({
                        "path": format_path(i_path),
                        "type": "removed",
                        "value": a[i],
                        "is_structural": True
                    })
                    
    else:
        # Leaf nodes
        if mode == "semantic":
            if a != b:
                changes.append({
                    "path": path_str,
                    "type": "changed",
                    "reason": "value_changed",
                    "description": "value changed",
                    "old_value": a,
                    "value": b,
                    "is_structural": False
                })
                
    return changes

def run_diff(
    transcript_a_path: Path,
    transcript_b_path: Path,
    mode: str = "structural",
    ignore_fields: Optional[List[str]] = None
) -> Dict[Any, Dict[str, Any]]:
    """Compare two session transcripts and return differences grouped by request ID."""
    allowed_modes = {"structural", "semantic", "strict"}
    if mode not in allowed_modes:
        raise ValueError(
            f"Unsupported diff mode: {mode}. Expected one of: {', '.join(sorted(allowed_modes))}"
        )
    with open(transcript_a_path, "r", encoding="utf-8") as f:
        data_a = yaml.safe_load(f) or {}
    with open(transcript_b_path, "r", encoding="utf-8") as f:
        data_b = yaml.safe_load(f) or {}
        
    messages_a_raw = data_a.get("messages", [])
    messages_b_raw = data_b.get("messages", [])
    
    # Helper to map id to method
    id_to_method_a = {}
    for m in messages_a_raw:
        if m.get("dir") == "c2s" and m.get("payload"):
            msg_id = m["payload"].get("id")
            method = m["payload"].get("method")
            if msg_id is not None and method:
                id_to_method_a[msg_id] = method
                
    id_to_method_b = {}
    for m in messages_b_raw:
        if m.get("dir") == "c2s" and m.get("payload"):
            msg_id = m["payload"].get("id")
            method = m["payload"].get("method")
            if msg_id is not None and method:
                id_to_method_b[msg_id] = method
                
    # Extract S2C responses with an id field
    responses_a = {}
    for m in messages_a_raw:
        if m.get("dir") == "s2c" and m.get("payload"):
            msg_id = m["payload"].get("id")
            if msg_id is not None:
                responses_a[msg_id] = m["payload"]
                
    responses_b = {}
    for m in messages_b_raw:
        if m.get("dir") == "s2c" and m.get("payload"):
            msg_id = m["payload"].get("id")
            if msg_id is not None:
                responses_b[msg_id] = m["payload"]
                
    # Parse ignore paths
    ignore_paths = []
    if ignore_fields:
        for f_path in ignore_fields:
            ignore_paths.append(parse_json_path(f_path))
    
    if mode == "strict" and ignore_paths:
        raise ValueError("ignore_fields is not supported in strict mode")
            
    # Pair by matching ID values
    all_ids = sorted(set(responses_a.keys()) | set(responses_b.keys()), key=_id_sort_key)
    changes_by_id = {}
    
    for msg_id in all_ids:
        method = id_to_method_a.get(msg_id) or id_to_method_b.get(msg_id) or "unknown"
        changes_by_id[msg_id] = {
            "method": method,
            "changes": []
        }
        
        # 1. Unpaired in A (removed response)
        if msg_id in responses_a and msg_id not in responses_b:
            changes_by_id[msg_id]["changes"].append({
                "path": "",
                "type": "removed",
                "value": responses_a[msg_id],
                "is_structural": True
            })
            continue
            
        # 2. Unpaired in B (added response)
        if msg_id in responses_b and msg_id not in responses_a:
            changes_by_id[msg_id]["changes"].append({
                "path": "",
                "type": "added",
                "value": responses_b[msg_id],
                "is_structural": True
            })
            continue
            
        # 3. Paired: compare
        payload_a = responses_a[msg_id]
        payload_b = responses_b[msg_id]
        
        if mode == "strict":
            # Re-serialize with sort_keys=True
            str_a = json.dumps(payload_a, sort_keys=True)
            str_b = json.dumps(payload_b, sort_keys=True)
            if str_a != str_b:
                changes_by_id[msg_id]["changes"].append({
                    "path": "",
                    "type": "changed",
                    "reason": "value_changed",
                    "description": "strict payload changed",
                    "old_value": payload_a,
                    "value": payload_b,
                    "is_structural": True
                })
        else:
            # Recursive comparison
            group_changes = compare_payloads(payload_a, payload_b, [], mode, ignore_paths)
            changes_by_id[msg_id]["changes"] = group_changes
            
    return changes_by_id

def format_text_diff(changes_by_id: Dict[Any, Dict[str, Any]]) -> str:
    """Format changes into human-readable text diff grouping by request ID."""
    lines = []
    for msg_id, group in sorted(changes_by_id.items(), key=lambda kv: _id_sort_key(kv[0])):
        method = group["method"]
        group_changes = group["changes"]
        if not group_changes:
            continue
        lines.append(f"  {method} response (id={msg_id}):")
        
        for change in group_changes:
            path = change["path"]
            c_type = change["type"]
            val = change.get("value")
            
            if path:
                if "." in path:
                    parent, key = path.rsplit(".", 1)
                else:
                    parent, key = "", path
            else:
                parent, key = "", ""
                
            if parent:
                lines.append(f"    {parent}:")
                prefix_add = "+     "
                prefix_rem = "-     "
            else:
                prefix_add = "+   "
                prefix_rem = "-   "
                
            formatted_val = format_value(val)
            
            if c_type == "added":
                if key:
                    lines.append(f"{prefix_add}{key}: {formatted_val}")
                else:
                    lines.append(f"{prefix_add}{formatted_val}")
            elif c_type == "removed":
                if key:
                    lines.append(f"{prefix_rem}{key}: {formatted_val}")
                else:
                    lines.append(f"{prefix_rem}{formatted_val}")
            elif c_type == "changed":
                old_val = change.get("old_value")
                reason = change.get("reason")
                if reason == "type_changed":
                    lines.append(f"{prefix_rem}{key}: [type: {change.get('old_type')}]")
                    lines.append(f"{prefix_add}{key}: [type: {change.get('new_type')}]")
                elif reason == "array_length_changed":
                    lines.append(f"{prefix_rem}{key}: [array length: {change.get('old_len')}]")
                    lines.append(f"{prefix_add}{key}: [array length: {change.get('new_len')}]")
                else:
                    if key:
                        lines.append(f"{prefix_rem}{key}: {format_value(old_val)}")
                        lines.append(f"{prefix_add}{key}: {formatted_val}")
                    else:
                        lines.append(f"{prefix_rem}{format_value(old_val)}")
                        lines.append(f"{prefix_add}{formatted_val}")
        lines.append("") # blank line between groups
        
    if not lines:
        return "No changes detected"
    return "\n".join(lines).strip()

def format_json_diff(changes_by_id: Dict[Any, Dict[str, Any]]) -> str:
    """Format changes into structured JSON output."""
    flat_changes = []
    added_count = 0
    removed_count = 0
    changed_count = 0
    
    for msg_id, group in sorted(changes_by_id.items(), key=lambda kv: _id_sort_key(kv[0])):
        method = group["method"]
        for change in group["changes"]:
            flat_changes.append({
                "request_id": msg_id,
                "method": method,
                "path": change["path"],
                "type": change["type"],
                "value": change.get("value")
            })
            if change["type"] == "added":
                added_count += 1
            elif change["type"] == "removed":
                removed_count += 1
            elif change["type"] == "changed":
                changed_count += 1
                
    result = {
        "changes": flat_changes,
        "summary": {
            "added": added_count,
            "removed": removed_count,
            "changed": changed_count
        }
    }
    return json.dumps(result, indent=2)

def format_github_diff(changes_by_id: Dict[Any, Dict[str, Any]], session_name: str) -> str:
    """Format changes into GitHub Actions annotations."""
    lines = []
    for _msg_id, group in sorted(changes_by_id.items(), key=lambda kv: _id_sort_key(kv[0])):
        method = group["method"]
        for change in group["changes"]:
            path = change["path"]
            c_type = change["type"]
            is_struct = change.get("is_structural", True)
            
            severity = "error" if is_struct else "warning"
            path_detail = f"{path} {c_type}" if path else c_type
            
            lines.append(f"::{severity} file={session_name},line=1::{method} response changed: {path_detail}")
            
    return "\n".join(lines)
