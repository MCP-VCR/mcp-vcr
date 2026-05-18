import json
import yaml
from pathlib import Path
import pytest
from mcp_vcr.diff import (
    run_diff,
    format_text_diff,
    format_json_diff,
    format_github_diff,
    parse_json_path,
    is_ignored
)

# Helper to create temporary transcripts
def create_transcript(tmp_path: Path, filename: str, messages: list) -> Path:
    data = {
        "meta": {
            "version": 1,
            "recorded_at": "2026-05-18T12:00:00Z",
            "session_id": "abcdef12",
            "server_command": ["python", "dummy.py"],
            "schema_version": "1.0"
        },
        "messages": messages
    }
    p = tmp_path / filename
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p

def test_json_path_parser():
    """Verify that JSON path parsing supports $, ., and [n] notations."""
    assert parse_json_path("$.result.tools[2].name") == ["result", "tools", "2", "name"]
    assert parse_json_path("result.tools[*].name") == ["result", "tools", "*", "name"]
    assert parse_json_path("$.result.serverInfo.version") == ["result", "serverInfo", "version"]
    assert parse_json_path("result") == ["result"]

def test_path_ignoring():
    """Verify JSON path matching ignores correct fields."""
    ignore_list = [
        parse_json_path("$.result.serverInfo.version"),
        parse_json_path("result.tools[*].name")
    ]
    
    # Ignored path
    assert is_ignored(["result", "serverInfo", "version"], ignore_list) is True
    # Ignored path wildcard
    assert is_ignored(["result", "tools", 2, "name"], ignore_list) is True
    # Not ignored
    assert is_ignored(["result", "serverInfo", "name"], ignore_list) is False
    assert is_ignored(["result", "tools", 2, "description"], ignore_list) is False

def test_response_pairing(tmp_path):
    """Verify matching responses are paired, missing/extra responses are reported, notifications are skipped."""
    messages_a = [
        # Request 1 and 2
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
        # Notification (should be ignored from pairing)
        {"dir": "s2c", "payload": {"jsonrpc": "2.0", "method": "notifications/progress"}},
        # Responses for 1 and 2
        {"dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 1, "result": {"v": "1.0"}}},
        {"dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}}
    ]
    
    messages_b = [
        # Request 1 and 3 (2 missing, 3 added)
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}},
        # Responses
        {"dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 1, "result": {"v": "1.0"}}},
        {"dir": "s2c", "payload": {"jsonrpc": "2.0", "id": 3, "result": {"resources": []}}}
    ]
    
    t_a = create_transcript(tmp_path, "a.yaml", messages_a)
    t_b = create_transcript(tmp_path, "b.yaml", messages_b)
    
    diff = run_diff(t_a, t_b, mode="structural")
    
    # ID 1 is paired, and identical, so no changes inside ID 1
    assert len(diff[1]["changes"]) == 0
    
    # ID 2 is present in A but missing in B (removed)
    assert len(diff[2]["changes"]) == 1
    assert diff[2]["changes"][0]["type"] == "removed"
    assert diff[2]["changes"][0]["path"] == ""
    assert diff[2]["changes"][0]["value"] == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    
    # ID 3 is present in B but missing in A (added)
    assert len(diff[3]["changes"]) == 1
    assert diff[3]["changes"][0]["type"] == "added"
    assert diff[3]["changes"][0]["path"] == ""
    assert diff[3]["changes"][0]["value"] == {"jsonrpc": "2.0", "id": 3, "result": {"resources": []}}

def test_structural_diff(tmp_path):
    """Verify structural diff matches on field additions/removals, types, array lengths, and ignores string values."""
    messages_a = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "serverInfo": {"name": "serverA", "version": "1.0"},
                "capabilities": {"resources": True},
                "list": [1, 2, 3],
                "type_check": "string"
            }
        }}
    ]
    
    messages_b = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                # value changed from "serverA" to "serverB" (should be ignored in structural)
                # version is removed
                "serverInfo": {"name": "serverB"},
                # extra field added
                "capabilities": {"resources": True, "prompts": False},
                # array length changed (3 -> 2)
                "list": [1, 2],
                # type check changed string -> list
                "type_check": [1, 2]
            }
        }}
    ]
    
    t_a = create_transcript(tmp_path, "struct_a.yaml", messages_a)
    t_b = create_transcript(tmp_path, "struct_b.yaml", messages_b)
    
    diff = run_diff(t_a, t_b, mode="structural")
    changes = diff[1]["changes"]
    
    # Find specific changes
    removed = [c for c in changes if c["type"] == "removed"]
    added = [c for c in changes if c["type"] == "added"]
    type_chg = [c for c in changes if c.get("reason") == "type_changed"]
    len_chg = [c for c in changes if c.get("reason") == "array_length_changed"]
    
    # Removed version
    assert any(c["path"] == "result.serverInfo.version" for c in removed)
    # Added prompts
    assert any(c["path"] == "result.capabilities.prompts" for c in added)
    # Type check string -> array
    assert any(c["path"] == "result.type_check" for c in type_chg)
    # Array length changed list (3 -> 2)
    assert any(c["path"] == "result.list" for c in len_chg)
    # No string value changes reported
    assert not any(c["path"] == "result.serverInfo.name" for c in changes)

def test_semantic_diff_and_ignore(tmp_path):
    """Verify semantic diff reports value changes, and ignores fields listed in ignore_fields config."""
    messages_a = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "serverInfo": {"name": "serverA", "version": "1.0"},
                "tools": [
                    {"name": "t1", "description": "d1"},
                    {"name": "t2", "description": "d2"}
                ]
            }
        }}
    ]
    
    messages_b = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "serverInfo": {"name": "serverB", "version": "2.0"},
                "tools": [
                    {"name": "t1", "description": "changed_d1"},
                    {"name": "t2_new", "description": "d2"}
                ]
            }
        }}
    ]
    
    t_a = create_transcript(tmp_path, "sem_a.yaml", messages_a)
    t_b = create_transcript(tmp_path, "sem_b.yaml", messages_b)
    
    # 1. Run semantic diff without ignores (all changes reported)
    diff_all = run_diff(t_a, t_b, mode="semantic")
    changes_all = diff_all[1]["changes"]
    assert any(c["path"] == "result.serverInfo.name" for c in changes_all)
    assert any(c["path"] == "result.serverInfo.version" for c in changes_all)
    assert any(c["path"] == "result.tools[0].description" for c in changes_all)
    assert any(c["path"] == "result.tools[1].name" for c in changes_all)
    
    # 2. Run semantic diff with ignores
    ignores = ["$.result.serverInfo.version", "result.tools[*].description"]
    diff_ignored = run_diff(t_a, t_b, mode="semantic", ignore_fields=ignores)
    changes_ignored = diff_ignored[1]["changes"]
    
    # Version should be ignored
    assert not any(c["path"] == "result.serverInfo.version" for c in changes_ignored)
    # Description should be ignored
    assert not any(c["path"] == "result.tools[0].description" for c in changes_ignored)
    # Name should STILL be reported
    assert any(c["path"] == "result.serverInfo.name" for c in changes_ignored)
    assert any(c["path"] == "result.tools[1].name" for c in changes_ignored)

def test_strict_diff(tmp_path):
    """Verify strict diff does byte/serialization equality, ignoring whitespace but matching sorting."""
    messages_a = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "id": 1,
            "result": {
                "a": 1,
                "b": 2
            }
        }}
    ]
    
    messages_b_same = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "id": 1,
            "result": {
                # keys re-ordered
                "b": 2,
                "a": 1
            }
        }}
    ]
    
    messages_b_diff = [
        {"dir": "c2s", "payload": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        {"dir": "s2c", "payload": {
            "id": 1,
            "result": {
                "a": 1,
                "b": 3
            }
        }}
    ]
    
    t_a = create_transcript(tmp_path, "strict_a.yaml", messages_a)
    t_b_same = create_transcript(tmp_path, "strict_b_same.yaml", messages_b_same)
    t_b_diff = create_transcript(tmp_path, "strict_b_diff.yaml", messages_b_diff)
    
    # Same payload key-ordering is normalized -> no diff
    diff_same = run_diff(t_a, t_b_same, mode="strict")
    assert len(diff_same[1]["changes"]) == 0
    
    # Different value -> strict detects change
    diff_diff = run_diff(t_a, t_b_diff, mode="strict")
    assert len(diff_diff[1]["changes"]) == 1
    assert diff_diff[1]["changes"][0]["type"] == "changed"
    assert diff_diff[1]["changes"][0]["path"] == ""

def test_text_output_formatter():
    """Verify text diff output displays additions (+), removals (-), type changes and array lengths."""
    changes_by_id = {
        1: {
            "method": "initialize",
            "changes": [
                {"path": "result.capabilities.resources", "type": "added", "value": {"subscribe": True}, "is_structural": True},
                {"path": "result.capabilities.prompts", "type": "removed", "value": False, "is_structural": True}
            ]
        },
        2: {
            "method": "tools/list",
            "changes": [
                {"path": "result.tools", "type": "changed", "reason": "array_length_changed", "old_len": 1, "new_len": 2, "value": 2, "is_structural": True}
            ]
        }
    }
    
    output = format_text_diff(changes_by_id)
    
    assert "initialize response (id=1):" in output
    assert "    result.capabilities:" in output
    assert '+     resources: {"subscribe": true}' in output
    assert '-     prompts: false' in output
    
    assert "tools/list response (id=2):" in output
    assert "    result:" in output
    assert '-     tools: [array length: 1]' in output
    assert '+     tools: [array length: 2]' in output

def test_json_output_formatter():
    """Verify JSON output format represents changes and calculates summary accurately."""
    changes_by_id = {
        2: {
            "method": "tools/list",
            "changes": [
                {"path": "result.tools[2].name", "type": "added", "value": "new_tool", "is_structural": True},
                {"path": "result.tools[1].description", "type": "changed", "old_value": "old", "value": "new", "is_structural": False}
            ]
        }
    }
    
    output_str = format_json_diff(changes_by_id)
    data = json.loads(output_str)
    
    assert "changes" in data
    assert len(data["changes"]) == 2
    
    first = data["changes"][0]
    assert first["request_id"] == 2
    assert first["method"] == "tools/list"
    assert first["path"] == "result.tools[2].name"
    assert first["type"] == "added"
    assert first["value"] == "new_tool"
    
    assert data["summary"]["added"] == 1
    assert data["summary"]["removed"] == 0
    assert data["summary"]["changed"] == 1

def test_github_output_formatter():
    """Verify GitHub Actions output format prints annotations correctly."""
    changes_by_id = {
        1: {
            "method": "initialize",
            "changes": [
                {"path": "result.capabilities.resources", "type": "added", "value": True, "is_structural": True},
                {"path": "result.serverInfo.version", "type": "changed", "old_value": "1.0", "value": "2.0", "is_structural": False}
            ]
        }
    }
    
    output = format_github_diff(changes_by_id, "session1.yaml")
    lines = output.splitlines()
    
    assert len(lines) == 2
    # Structural changes -> ::error
    assert lines[0] == "::error file=session1.yaml,line=1::initialize response changed: result.capabilities.resources added"
    # Semantic changes -> ::warning
    assert lines[1] == "::warning file=session1.yaml,line=1::initialize response changed: result.serverInfo.version changed"
