import json
from typing import Any, Dict
import click


def emit_json(data: Dict[str, Any]) -> None:
    """Write a JSON object to stdout. Used by --json on all CLI commands."""
    click.echo(json.dumps(data, indent=2, default=str))


def error_envelope(command: str, error: Any) -> Dict[str, Any]:
    """Build a standard error envelope."""
    return {
        "status": "error",
        "command": command,
        "error": str(error),
    }
