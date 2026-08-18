from pathlib import Path
from typing import Any, Dict, Optional
import pytest
import yaml


@pytest.fixture
def toy_pass_transcript() -> Dict[str, Any]:
    """Return a valid known-pass transcript payload compatible with toy_server.py."""
    return {
        "meta": {
            "version": 1,
            "recorded_at": "2026-08-16T12:00:00.000Z",
            "session_id": "11112222",
            "server_command": ["python", "tests/integration/toy_server.py"],
            "protocol_version": "2024-11-05",
            "client_hint": "pytest",
            "schema_version": "1.0",
        },
        "messages": [
            {
                "t": 0,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            },
            {
                "t": 25,
                "dir": "s2c",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"resources": {}, "tools": {}, "prompts": {}},
                        "serverInfo": {"name": "toy-server", "version": "1.0.0"},
                    },
                },
            },
            {
                "t": 30,
                "dir": "c2s",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            },
        ],
    }


def write_suite_helper(
    suite_dir: Path,
    suite_name: str,
    transcript_dict: Dict[str, Any],
    transcript_filename: str = "t.yaml",
    description: str = "Test suite",
    server_hint: Optional[str] = None,
) -> Path:
    """Helper function to write a valid suite directory with transcript and suite.yaml."""
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / transcript_filename).write_text(yaml.dump(transcript_dict), encoding="utf-8")
    suite_manifest = {
        "name": suite_name,
        "description": description,
        "transcripts": [transcript_filename],
    }
    if server_hint:
        suite_manifest["server_hint"] = server_hint
    (suite_dir / "suite.yaml").write_text(yaml.dump(suite_manifest), encoding="utf-8")
    return suite_dir


@pytest.fixture
def write_suite():
    """Fixture providing write_suite_helper callable."""
    return write_suite_helper
