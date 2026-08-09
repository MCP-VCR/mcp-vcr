import os
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open
from mcp_vcr.config import Config, ConfigError

def test_config_env_var_substitution():
    """Verify that env var substitution resolves environment variables and defaults correctly."""
    # Temporarily set environment variables
    with patch.dict(os.environ, {"TEST_API_KEY": "sk-12345", "TEST_PORT": "8080"}):
        config_data = {
            "replay": {
                "timeout_ms": "${TEST_PORT}",
                "key": "Bearer ${TEST_API_KEY}"
            },
            "redact": {
                "fields": ["token", "${TEST_API_KEY}"]
            }
        }
        
        # Test basic resolution
        config = Config(config_data)
        
        # Let's test with a mock config file structure:
        yaml_content = """
replay:
  timeout_ms: ${TEST_PORT}
  key: "Bearer ${TEST_API_KEY}"
  optional_field: ${UNSET_PORT:-default_port}
  empty_field: ${UNSET_VAR:-}
  literal_dollars: "$$LITERAL_VAL"
"""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=yaml_content)):
            cfg = Config.load(Path(".mcp-vcr.yaml"))
            assert cfg.replay_config()["timeout_ms"] == "8080"
            assert cfg.replay_config()["key"] == "Bearer sk-12345"
            assert cfg.replay_config()["optional_field"] == "default_port"
            assert cfg.replay_config()["empty_field"] == ""
            assert cfg.replay_config()["literal_dollars"] == "$LITERAL_VAL"

def test_config_env_var_substitution_unset_raises_error():
    """Verify that using an unset env var without a default raises ConfigError."""
    yaml_content = """
replay:
  key: "Bearer ${UNSET_CRITICAL_TOKEN}"
"""
    with patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)):
        with pytest.raises(ConfigError) as excinfo:
            Config.load(Path(".mcp-vcr.yaml"))
        assert "Environment variable 'UNSET_CRITICAL_TOKEN' is not set" in str(excinfo.value)

def test_config_overrides_last_match_wins():
    """Verify that overlapping overrides resolve with last-match-wins and deepmerge."""
    yaml_content = """
replay:
  timeout_ms: 5000
redact:
  fields: [token, api_key]

overrides:
  - match: "snapshots/slow_*.yaml"
    replay:
      timeout_ms: 15000
    redact:
      fields: [session_token]

  - match: "snapshots/slow_auth_*.yaml"
    replay:
      timeout_ms: 30000
    redact:
      fields: [refresh_token]
"""
    with patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)):
        cfg = Config.load(Path(".mcp-vcr.yaml"))
        
        # Matches slow_*.yaml
        resolved = cfg.for_snapshot(Path("snapshots/slow_test.yaml"))
        assert resolved["replay"]["timeout_ms"] == 15000
        assert resolved["redact"]["fields"] == ["token", "api_key", "session_token"]
        
        # Matches both slow_*.yaml and slow_auth_*.yaml
        resolved_auth = cfg.for_snapshot(Path("snapshots/slow_auth_test.yaml"))
        # Last match wins: 30000 overrides 15000
        assert resolved_auth["replay"]["timeout_ms"] == 30000
        # List extension merges all matching overrides sequentially
        assert resolved_auth["redact"]["fields"] == ["token", "api_key", "session_token", "refresh_token"]

def test_config_overrides_list_replacement():
    """Verify that using replace: true replaces list elements instead of extending them."""
    yaml_content = """
redact:
  fields: [token, api_key, password]

overrides:
  - match: "snapshots/minimal_*.yaml"
    redact:
      fields:
        replace: true
        values: [minimal_token]
"""
    with patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)):
        cfg = Config.load(Path(".mcp-vcr.yaml"))
        
        resolved = cfg.for_snapshot(Path("snapshots/minimal_test.yaml"))
        assert resolved["redact"]["fields"] == ["minimal_token"]
