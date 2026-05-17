import os
import re
import yaml
import pytest
import logging
from pathlib import Path
from mcp_vcr.redactor import Redactor, DEFAULT_FIELDS, DEFAULT_PATTERNS

def test_field_redaction_basic():
    """Verify that default sensitive fields are redacted case-insensitively at any nesting level."""
    redactor = Redactor()
    payload = {
        "token": "abc123secret",
        "api_key": 99999,
        "Secret": {"nested_key": "nested_val"},
        "PASSWORD": ["pass1", "pass2"],
        "normal_field": "not_sensitive",
        "nested": {
            "auth": {
                "bearer": "somebearer"
            }
        }
    }
    
    redacted = redactor.redact(payload)
    
    assert redacted["token"] == "<REDACTED_token>"
    assert redacted["api_key"] == "<REDACTED_api_key>"
    assert redacted["Secret"] == "<REDACTED_Secret>"
    assert redacted["PASSWORD"] == "<REDACTED_PASSWORD>"
    assert redacted["normal_field"] == "not_sensitive"
    assert redacted["nested"]["auth"]["bearer"] == "<REDACTED_bearer>"

def test_immutability():
    """Verify that calling redact() does not mutate the original dictionary."""
    redactor = Redactor()
    payload = {
        "token": "abc123secret",
        "nested": {
            "secret": "hidden"
        }
    }
    original_copy = {
        "token": "abc123secret",
        "nested": {
            "secret": "hidden"
        }
    }
    
    redacted = redactor.redact(payload)
    
    assert redacted != payload
    assert payload == original_copy

def test_pattern_redaction():
    """Verify regex patterns (OpenAI, Bearer, AWS keys) are redacted to <REDACTED> recursively."""
    redactor = Redactor()
    
    payload = {
        "openai": "sk-abcdefghijklmnopqrstuvwxyz",
        "bearer": "some other key",  # wait, bearer is a sensitive field key, so it gets redacted as <REDACTED_bearer>
        "non_sensitive_key_with_bearer_value": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload",
        "aws": "AKIAIOSFODNN7EXAMPLE:wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "clean": "hello world",
        "nested_list": [
            "sk-12345678901234567890",
            "normal string"
        ]
    }
    
    redacted = redactor.redact(payload)
    
    assert redacted["openai"] == "<REDACTED>"
    assert redacted["non_sensitive_key_with_bearer_value"] == "<REDACTED>"
    assert redacted["aws"] == "<REDACTED>"
    assert redacted["clean"] == "hello world"
    assert redacted["nested_list"][0] == "<REDACTED>"
    assert redacted["nested_list"][1] == "normal string"

def test_path_redaction():
    """Verify Unix and Windows absolute paths are redacted to <PATH> while relative paths/URLs are not."""
    redactor = Redactor()
    
    payload = {
        "unix1": "/home/user/projects/server.py",
        "unix2": "/Users/name/.config",
        "windows1": "C:\\Users\\name\\server.py",
        "windows2": "D:\\some\\path",
        "relative": "relative/path/file.py",
        "url": "https://example.com/path",
        "short_slash": "/ab",  # too short for unix path pattern length >= 3
    }
    
    redacted = redactor.redact(payload)
    
    assert redacted["unix1"] == "<PATH>"
    assert redacted["unix2"] == "<PATH>"
    assert redacted["windows1"] == "<PATH>"
    assert redacted["windows2"] == "<PATH>"
    assert redacted["relative"] == "relative/path/file.py"
    assert redacted["url"] == "https://example.com/path"
    assert redacted["short_slash"] == "/ab"

def test_custom_config_loading(tmp_path, caplog):
    """Verify custom fields, patterns, and path disabled option are merged correctly from YAML."""
    config_data = {
        "redact": {
            "fields": ["custom_token", "my_secret_field"],
            "patterns": [
                "custom-pat-[0-9]{5}",
                "invalid-[regex-("  # invalid regex
            ],
            "paths": False
        }
    }
    
    config_file = tmp_path / ".mcp-vcr.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f)
        
    with caplog.at_level(logging.WARNING):
        redactor = Redactor(config_path=config_file)
        
    # Check warning was logged for the invalid regex
    assert any("Invalid regex pattern" in record.message for record in caplog.records)
    
    # Check custom fields are added case-insensitively
    assert "custom_token" in redactor.sensitive_fields
    assert "my_secret_field" in redactor.sensitive_fields
    assert "token" in redactor.sensitive_fields  # Defaults preserved
    
    # Check custom pattern added
    custom_pat_compiled = re.compile("custom-pat-[0-9]{5}")
    assert any(pat.pattern == "custom-pat-[0-9]{5}" for pat in redactor.compiled_patterns)
    
    # Test redaction using this custom redactor
    payload = {
        "custom_token": "highly_sensitive",
        "normal": "custom-pat-12345",
        "path": "/home/user/file.txt"
    }
    
    redacted = redactor.redact(payload)
    
    # Custom field redacted
    assert redacted["custom_token"] == "<REDACTED_custom_token>"
    # Custom pattern redacted
    assert redacted["normal"] == "<REDACTED>"
    # Paths not redacted because paths: False
    assert redacted["path"] == "/home/user/file.txt"
