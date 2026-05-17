import re
import yaml
import pytest
import logging
from pathlib import Path
from mcp_vcr.normalizer import (
    NormalizerChain,
    TimestampNormalizer,
    RequestIdNormalizer,
    UuidNormalizer,
    CursorNormalizer
)

# A simple mock normalizer for testing composability
class UpperCaseNormalizer:
    def __init__(self):
        self.name = "uppercase"
        
    def apply(self, payload: dict) -> dict:
        # Return upper-cased string values
        res = {}
        for k, v in payload.items():
            if isinstance(v, str):
                res[k] = v.upper()
            else:
                res[k] = v
        return res

class SuffixNormalizer:
    def __init__(self):
        self.name = "suffix"
        
    def apply(self, payload: dict) -> dict:
        # Append suffix to string values
        res = {}
        for k, v in payload.items():
            if isinstance(v, str):
                res[k] = v + "_SUFFIX"
            else:
                res[k] = v
        return res

def test_composability_and_immutability():
    """Verify NormalizerChain applies normalizers in order, and each receives a fresh copy."""
    chain = NormalizerChain([UpperCaseNormalizer(), SuffixNormalizer()])
    
    payload = {"msg": "hello", "code": 100}
    original_copy = {"msg": "hello", "code": 100}
    
    normalized = chain.apply(payload)
    
    # Verify order of operations: UPPER -> SUFFIX
    assert normalized["msg"] == "HELLO_SUFFIX"
    assert normalized["code"] == 100
    
    # Verify immutability: original payload not mutated
    assert payload == original_copy

def test_no_op_normalizer():
    """Verify that a normalizer returning unmodified payload does not break the chain."""
    class NoOpNormalizer:
        def __init__(self):
            self.name = "noop"
        def apply(self, payload: dict) -> dict:
            return payload
            
    chain = NormalizerChain([NoOpNormalizer(), UpperCaseNormalizer()])
    payload = {"msg": "hello"}
    normalized = chain.apply(payload)
    assert normalized["msg"] == "HELLO"

def test_timestamp_normalizer():
    """Verify ISO 8601 timestamps are canonicalized to NORM_TIMESTAMP leaf values."""
    norm = TimestampNormalizer()
    
    payload = {
        "timestamp_utc": "2024-01-15T14:30:22Z",
        "timestamp_ms": "2024-01-15T14:30:22.471Z",
        "timestamp_offset": "2024-01-15T14:30:22+02:00",
        "normal_text": "Not a timestamp",
        "partial_time": "2024-01-15",
        "nested": {
            "created_at": "2026-05-17T20:00:00"
        }
    }
    
    redacted = norm.apply(payload)
    
    assert redacted["timestamp_utc"] == "NORM_TIMESTAMP"
    assert redacted["timestamp_ms"] == "NORM_TIMESTAMP"
    assert redacted["timestamp_offset"] == "NORM_TIMESTAMP"
    assert redacted["normal_text"] == "Not a timestamp"
    assert redacted["partial_time"] == "2024-01-15"
    assert redacted["nested"]["created_at"] == "NORM_TIMESTAMP"

def test_request_id_normalizer():
    """Verify numeric and string JSON-RPC IDs are mapped to sequential NORM_ID_<n> consistently."""
    norm = RequestIdNormalizer()
    
    # Sequence of requests and responses
    req1 = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
    req2 = {"jsonrpc": "2.0", "id": "uuid-id-42", "method": "tools/list"}
    req3 = {"jsonrpc": "2.0", "id": 7, "method": "nested/call"}  # repeated ID
    notif = {"jsonrpc": "2.0", "id": None, "method": "notifications/initialized"}
    
    res1 = norm.apply(req1)
    res2 = norm.apply(req2)
    res3 = norm.apply(req3)
    res4 = norm.apply(notif)
    
    # Check sequential normalization
    assert res1["id"] == "NORM_ID_1"
    assert res2["id"] == "NORM_ID_2"
    
    # Check consistent mapping of repeated ID
    assert res3["id"] == "NORM_ID_1"
    
    # Check null ID remains unchanged
    assert res4["id"] is None

def test_uuid_normalizer():
    """Verify UUID v4 values map to sequential NORM_UUID_<n> case-insensitively and consistently."""
    norm = UuidNormalizer()
    
    payload = {
        "uuid1": "12345678-abcd-4000-8000-1234567890ab",
        "uuid1_upper": "12345678-ABCD-4000-8000-1234567890AB",
        "uuid2": "abcdef01-2345-4678-9abc-def012345678",
        "non_uuid": "not-a-uuid-string-of-length-36",
        "nested_list": [
            "12345678-abcd-4000-8000-1234567890ab"
        ]
    }
    
    redacted = norm.apply(payload)
    
    # Check sequential normalization
    assert redacted["uuid1"] == "NORM_UUID_1"
    # Check case-insensitive consistent mapping
    assert redacted["uuid1_upper"] == "NORM_UUID_1"
    assert redacted["uuid2"] == "NORM_UUID_2"
    # Check non-UUID leaf stays unchanged
    assert redacted["non_uuid"] == "not-a-uuid-string-of-length-36"
    assert redacted["nested_list"][0] == "NORM_UUID_1"

def test_cursor_normalizer():
    """Verify cursor and pagination fields have string values normalized to NORM_CURSOR."""
    norm = CursorNormalizer()
    
    payload = {
        "cursor": "opaque_cursor_value_1",
        "nextCursor": "opaque_cursor_value_2",
        "pageToken": "token_123",
        "continuationToken": "token_456",
        "other_field": "do_not_normalize_me",
        "integer_cursor": 42  # non-string cursor value, should stay unchanged
    }
    
    redacted = norm.apply(payload)
    
    assert redacted["cursor"] == "NORM_CURSOR"
    assert redacted["nextCursor"] == "NORM_CURSOR"
    assert redacted["pageToken"] == "NORM_CURSOR"
    assert redacted["continuationToken"] == "NORM_CURSOR"
    assert redacted["other_field"] == "do_not_normalize_me"
    assert redacted["integer_cursor"] == 42

def test_config_toggling_and_validation(tmp_path, caplog):
    """Verify normalizers are loaded/toggled via yaml config and unknown keys raise warnings."""
    # 1. Disabled timestamps and uuids
    config_data = {
        "normalize": {
            "timestamps": False,
            "uuids": False,
            "invalid_key": True  # unknown key
        }
    }
    
    config_file = tmp_path / ".mcp-vcr.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f)
        
    with caplog.at_level(logging.WARNING):
        chain = NormalizerChain.from_config(config_path=config_file)
        
    # Check unknown key warning logged
    assert any("Unknown key in normalize configuration" in record.message for record in caplog.records)
    
    # Check active normalizers: timestamps/uuids missing, request_ids/cursors present
    active_names = {norm.name for norm in chain.normalizers}
    assert "timestamps" not in active_names
    assert "uuids" not in active_names
    assert "request_ids" in active_names
    assert "cursors" in active_names

def test_config_defaults(tmp_path):
    """Verify all normalizers are active when no config is provided or the section is empty."""
    # Empty config
    config_file = tmp_path / ".mcp-vcr.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump({}, f)
        
    chain = NormalizerChain.from_config(config_path=config_file)
    active_names = {norm.name for norm in chain.normalizers}
    assert active_names == {"timestamps", "request_ids", "uuids", "cursors"}
    
    # No config file at all
    non_existent = tmp_path / "does_not_exist.yaml"
    chain_default = NormalizerChain.from_config(config_path=non_existent)
    active_names_default = {norm.name for norm in chain_default.normalizers}
    assert active_names_default == {"timestamps", "request_ids", "uuids", "cursors"}
