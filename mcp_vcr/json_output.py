import json
from typing import Any, Dict
import click


def emit_json(data: Dict[str, Any], err: bool = False) -> None:
    """Write a JSON object to stdout or stderr. Used by --json on all CLI commands."""
    click.echo(json.dumps(data, indent=2, default=str), err=err)


def error_envelope(command: str, error: Any) -> Dict[str, Any]:
    """Build a standard error envelope."""
    err_str = str(error)
    if not err_str:
        if isinstance(error, Exception):
            err_str = error.__class__.__name__
        else:
            err_str = "Unknown error"
    return {
        "status": "error",
        "command": command,
        "error": err_str,
    }

