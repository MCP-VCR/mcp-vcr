import asyncio
import glob
import json
import logging
import re
import secrets
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import click
import yaml

from .config import Config, ConfigError
from .diff import format_github_diff, format_json_diff, format_text_diff, run_diff
from .formats import iter_messages
from .interceptor import MessageInterceptor
from .json_output import emit_json, error_envelope
from .recorder import TranscriptRecorder
from .redactor import Redactor
from .replay import ReplayEngine
from .snapshot import _run_verify_impl, run_snapshot, run_verify
from .transports import StdioTransport, run_proxy_with_transport
from .transports.stdio import run_proxy
from .validator import ValidationError, validate_file

logger = logging.getLogger("mcp-vcr.cli")

def _sanitize_url(url: str) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse, urlunparse
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@", 1)[1]
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, "", ""))
    except Exception:
        return "<REDACTED_URL>"

class SseSettingsError(Exception):
    """Raised when SSE settings cannot be resolved."""
    pass


def _resolve_sse_settings(config_path, sse_url, sse_header, snapshot_path=None):
    from .config import Config, ConfigError
    transport_cfg = {}
    if config_path:
        try:
            cfg = Config.load(config_path)
            if snapshot_path:
                resolved = cfg.for_snapshot(snapshot_path)
                transport_cfg = resolved.get("transport", {})
            else:
                transport_cfg = cfg.raw_data.get("transport", {})
        except ConfigError as ce:
            click.secho(f"WARNING: Configuration error: {ce}", fg="yellow", err=True)
        except Exception as e:
            logger.warning(f"Failed to load configuration: {e}")
            
    resolved_url = sse_url or transport_cfg.get("sse_url")
    if not resolved_url:
        raise SseSettingsError("--sse-url is required when using SSE transport.")
        
    headers = dict(transport_cfg.get("headers", {}))
    if sse_header:
        for h in sse_header:
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
                
    return resolved_url, headers

@click.group()
@click.version_option(version="0.2.1", prog_name="mcp-vcr")
def main():
    """mcp-vcr: A deterministic MCP transcript proxy and testing tool."""
    pass

def _validate_path_target(path: Path) -> str:
    """Validate a transcript, suite manifest, or registry file and return success description."""
    if path.name in ("suite.yaml", "suite.yml"):
        from .suite import validate_manifest_dict
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        validate_manifest_dict(data, file_path=path)
        return f"Suite manifest '{path.name}' is valid."
    elif path.name in ("manifest.yaml", "manifest.yml"):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "suites" not in data or not isinstance(data["suites"], list):
            raise ValueError("Top-level manifest must contain a 'suites' list.")
        return f"Top-level manifest '{path.name}' is valid."
    else:
        validate_file(path, allow_v0=False)
        return f"Transcript '{path.name}' is valid."


@main.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
def validate(path: Path):
    """Validate a transcript YAML file or directory of transcripts against the schema.
    
    Example:
      mcp-vcr validate sessions/session_20260518_120000_abcdef12.yaml
    """
    if path.is_dir():
        # Directory-level validation
        yaml_files = sorted(list(path.glob("*.yaml")) + list(path.glob("*.yml")))
        if not yaml_files:
            click.secho(f"No YAML files found in directory '{path}'", fg="yellow")
            sys.exit(0)
            
        all_ok = True
        for file in yaml_files:
            try:
                msg = _validate_path_target(file)
                click.secho(f"OK: {msg}", fg="green")
            except ValidationError as e:
                all_ok = False
                click.secho(f"ERROR: '{file.name}' validation failed:", fg="red", err=True)
                for error in e.errors():
                    loc = " -> ".join(str(part) for part in error['loc'])
                    err_msg = error['msg']
                    click.echo(f"  {loc}: {err_msg}", err=True)
            except yaml.YAMLError as e:
                all_ok = False
                click.secho(f"ERROR: YAML Error in '{file}':", fg="red", err=True)
                click.echo(f"  {e}", err=True)
            except Exception as e:
                all_ok = False
                click.secho(f"ERROR: '{file.name}' unexpected validation error: {e}", fg="red", err=True)
        if not all_ok:
            sys.exit(1)
    else:
        # Single-file validation
        try:
            msg = _validate_path_target(path)
            click.secho(f"OK: {msg}", fg="green")
        except ValidationError as e:
            click.secho(f"ERROR: Validation failed for '{path}':", fg="red", err=True)
            for error in e.errors():
                loc = " -> ".join(str(part) for part in error['loc'])   
                err_msg = error['msg']
                click.echo(f"  {loc}: {err_msg}", err=True)
            sys.exit(1)
        except yaml.YAMLError as e:
            click.secho(f"ERROR: YAML Error in '{path}':", fg="red", err=True)
            click.echo(f"  {e}", err=True)
            sys.exit(1)
        except Exception as e:
            click.secho(f"ERROR: Unexpected error validating '{path}':", fg="red", err=True)
            click.echo(f"  {e}", err=True)
            sys.exit(1)


@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option('--output', '-o', type=click.Path(path_type=Path), help="Sessions directory or custom file path.")
@click.option('--name', type=str, help="Custom session name.")
@click.option('--no-redact', is_flag=True, help="Disable automatic redaction entirely.")
@click.option('--config', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to custom .mcp-vcr.yaml configuration.")
@click.option('--transport', type=click.Choice(['stdio', 'sse']), default='stdio', help="Transport protocol (default: stdio).")
@click.option('--sse-url', type=str, default=None, help="SSE endpoint URL (required if --transport=sse).")
@click.option('--sse-header', type=str, multiple=True, help="HTTP header for SSE transport as 'Key: Value'. Repeatable.")
@click.option('--format', 'output_format', type=click.Choice(['yaml', 'ndjson']), default='yaml', help="Transcript output format (default: yaml).")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON envelope to stderr (stdout is reserved for MCP proxy protocol traffic).")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def record(output, name, no_redact, config, transport, sse_url, sse_header, output_format, json_output, server_args):
    """Record an MCP session by proxying traffic to a server.
    
    Stream Contract:
      When using --json with record, the structured JSON envelope is written to stderr
      because stdout is reserved as the dedicated transport data pipe for MCP stdio proxy traffic.
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if transport == 'stdio' and not args:
        err_msg = "No server command specified. What to try: pass the server command and arguments after a '--' separator."
        if json_output:
            emit_json(error_envelope("record", err_msg), err=True)
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    # Determine output folder and filename
    output_path = output if output else Path("sessions")
    
    ext = "ndjson" if output_format == "ndjson" else "yaml"
    if output_path.suffix in (".yaml", ".yml", ".ndjson"):
        target_dir = output_path.parent
        filepath = output_path
    else:
        target_dir = output_path
        if name:
            filename = name if name.endswith((".yaml", ".yml", ".ndjson")) else f"{name}.{ext}"
            filepath = target_dir / filename
        else:
            session_id = secrets.token_hex(4)
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filepath = target_dir / f"session_{now_str}_{session_id}.{ext}"

    # Align filename extension with chosen format
    if output_format == "ndjson" and filepath.suffix in (".yaml", ".yml"):
        filepath = filepath.with_suffix(".ndjson")
    elif output_format == "yaml" and filepath.suffix == ".ndjson":
        filepath = filepath.with_suffix(".yaml")

    headers = {}
    if transport == 'sse':
        try:
            sse_url, headers = _resolve_sse_settings(config, sse_url, sse_header, snapshot_path=filepath)
        except SseSettingsError as e:
            if json_output:
                emit_json(error_envelope("record", str(e)), err=True)
            else:
                click.secho(f"ERROR: {e}", fg="red", err=True)
            sys.exit(1)
            
    # Validate startup folders
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        err_msg = f"Cannot create output directory '{target_dir}': {e}. What to try: specify a valid, writable path with the --output flag."
        if json_output:
            emit_json(error_envelope("record", err_msg), err=True)
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    # Initialize redactor
    try:
        redactor = Redactor(config_path=config, enabled=not no_redact, snapshot_path=filepath)
    except Exception as e:
        err_msg = f"Failed to initialize redaction/config: {e}. What to try: validate your --config file or retry with --no-redact."
        if json_output:
            emit_json(error_envelope("record", err_msg), err=True)
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
    
    # Sanitize SSE URL for recording metadata (remove credentials/query string)
    recorded_command = args
    sanitized_sse_url = ""
    if transport == 'sse' and sse_url:
        sanitized_sse_url = _sanitize_url(sse_url)
        recorded_command = [sanitized_sse_url]

    # Initialize the streaming TranscriptRecorder and MessageInterceptor
    recorder = TranscriptRecorder(filename=str(filepath), server_command=recorded_command, format=output_format)
    interceptor = MessageInterceptor(recorder=recorder, redactor=redactor)
    
    # Initialize transport instance
    SseTransport = None
    try:
        from .transports import SseTransport
    except ImportError:
        pass

    from .transports import StdioTransport, run_proxy_with_transport
    if transport == 'sse':
        if SseTransport is None:
            err_msg = "SseTransport is not available. Please install the sse extra: pip install mcp-vcr[sse]"
            if json_output:
                emit_json(error_envelope("record", err_msg), err=True)
            else:
                click.secho(f"ERROR: {err_msg}", fg="red", err=True)
            sys.exit(1)
        transport_inst = SseTransport(sse_url=sse_url, headers=headers)
        click.secho(f"Starting proxy for SSE server: {sanitized_sse_url}", fg="cyan", err=True)
    else:
        transport_inst = StdioTransport(args)
        click.secho(f"Starting proxy for server: {' '.join(args)}", fg="cyan", err=True)
        
    click.secho(f"Recording to: {recorder.filepath}", fg="cyan", err=True)
    
    try:
        recorder.start_session()
    except Exception as e:
        err_msg = f"Failed to open session file: {e}. What to try: check write permissions for '{filepath}'."
        if json_output:
            emit_json(error_envelope("record", err_msg), err=True)
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)

    exit_code = 1
    has_failed = False
    try:
        exit_code = asyncio.run(run_proxy_with_transport(transport_inst, interceptor=interceptor, recorder=recorder))
    except Exception as e:
        logger.debug("Proxy failed with exception", exc_info=True)
        has_failed = True
        if json_output:
            emit_json(error_envelope("record", f"Proxy failed: {e}"), err=True)
        else:
            click.secho(f"ERROR: Proxy failed: {e}.", fg="red", err=True)
        sys.exit(1)
    finally:
        try:
            recorder.close()
        except Exception as e:
            if not has_failed:
                if json_output:
                    emit_json(error_envelope("record", f"Failed to safely save transcript: {e}"), err=True)
                else:
                    click.secho(f"ERROR: Failed to safely save transcript: {e}.", fg="red", err=True)
                sys.exit(1)
            
    if json_output:
        msg_count = 0
        if filepath.exists():
            try:
                msg_count = sum(1 for _ in iter_messages(filepath))
            except Exception:
                msg_count = 0
        emit_json({
            "status": "ok" if exit_code == 0 else "fail",
            "command": "record",
            "session_file": str(filepath),
            "message_count": msg_count
        }, err=True)
    sys.exit(exit_code)



@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.argument('session', type=click.Path(exists=True, path_type=Path))
@click.option('--timeout', type=int, default=None, help="Timeout in milliseconds per request (default: 5000).")
@click.option('--strict', is_flag=True, help="Exit 1 if any response differs from the original transcript.")
@click.option('--config', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to custom .mcp-vcr.yaml configuration.")
@click.option('--transport', type=click.Choice(['stdio', 'sse']), default=None, help="Transport protocol (default: from config/stdio).")
@click.option('--sse-url', type=str, default=None, help="SSE endpoint URL.")
@click.option('--sse-header', type=str, multiple=True, help="HTTP header for SSE transport as 'Key: Value'. Repeatable.")
@click.option('--timing-faithful', is_flag=True, default=None, help="Insert deterministic sleeps matching message timestamps.")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def replay(session, timeout, strict, config, transport, sse_url, sse_header, timing_faithful, json_output, server_args):
    """Replay an MCP session against a server.
    
    Example:
      mcp-vcr replay sessions/my_session.yaml --timeout 5000 -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    transport_inst = None
    if transport == 'sse' or sse_url:
        try:
            sse_url, headers = _resolve_sse_settings(config, sse_url, sse_header, snapshot_path=Path(session))
        except SseSettingsError as e:
            if json_output:
                emit_json(error_envelope("replay", str(e)))
            else:
                click.secho(f"ERROR: {e}", fg="red", err=True)
            sys.exit(1)
        
        SseTransport = None
        try:
            from .transports import SseTransport
        except ImportError:
            pass

        if SseTransport is None:
            err_msg = "SseTransport is not available. Please install the sse extra: pip install mcp-vcr[sse]"
            if json_output:
                emit_json(error_envelope("replay", err_msg))
            else:
                click.secho(f"ERROR: {err_msg}", fg="red", err=True)
            sys.exit(1)
        transport_inst = SseTransport(sse_url=sse_url, headers=headers)
        click.secho(f"Starting replay of {session} against SSE server: {_sanitize_url(sse_url)}", fg="cyan", err=True)
    else:
        if transport == 'stdio' and not args:
            err_msg = "No server command specified. What to try: pass the server command and arguments after a '--' separator."
            if json_output:
                emit_json(error_envelope("replay", err_msg))
            else:
                click.secho(f"ERROR: {err_msg}", fg="red", err=True)
            sys.exit(1)
        if args:
            from .transports import StdioTransport
            transport_inst = StdioTransport(args, read_stdin=False)
            click.secho(f"Starting replay of {session} against server: {' '.join(args)}", fg="cyan", err=True)
        else:
            click.secho(f"Starting replay of {session} (using transport settings from config or transcript)", fg="cyan", err=True)
        
    from .replay import ReplayEngine
    try:
        engine = ReplayEngine(config_path=config, timeout_ms=timeout, timing_faithful=timing_faithful)
    except Exception as e:
        err_msg = f"Configuration error: {e}. What to try: check the validity of your configuration file or options."
        if json_output:
            emit_json(error_envelope("replay", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    try:
        output_path = asyncio.run(engine.run_replay(session, server_args=args if not transport_inst else None, transport=transport_inst))
        
        # Check if the output has incomplete: true in meta
        with open(output_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            
        incomplete = bool(data and isinstance(data, dict) and data.get("meta", {}).get("incomplete"))
        reason = data["meta"].get("incomplete_reason", "unknown") if incomplete else None
        
        diff_dict = None
        has_changes = False
        if strict:
            from .diff import run_diff, format_text_diff, format_json_diff
            import json as _json
            try:
                changes = run_diff(session, output_path, mode="strict")
                has_changes = any(group["changes"] for group in changes.values())
                if has_changes:
                    diff_dict = _json.loads(format_json_diff(changes))
                    if not json_output:
                        click.secho("ERROR: Strict replay failed: responses differ from recorded session.", fg="red", err=True)
                        click.echo(format_text_diff(changes), err=True)
            except Exception as e:
                if json_output:
                    emit_json(error_envelope("replay", f"Diff comparison failed: {e}"))
                else:
                    click.secho(f"ERROR: Diff comparison failed: {e}", fg="red", err=True)
                sys.exit(1)
                
        if incomplete:
            if json_output:
                emit_json({
                    "status": "fail",
                    "command": "replay",
                    "session_file": str(session),
                    "replay_file": str(output_path),
                    "incomplete": True,
                    "strict": strict,
                    "diff": diff_dict
                })
            else:
                click.secho(f"ERROR: Replay was incomplete due to: {reason}", fg="red", err=True)
            sys.exit(1)
            
        if strict and has_changes:
            if json_output:
                emit_json({
                    "status": "fail",
                    "command": "replay",
                    "session_file": str(session),
                    "replay_file": str(output_path),
                    "incomplete": False,
                    "strict": True,
                    "diff": diff_dict
                })
            sys.exit(1)
            
        if json_output:
            emit_json({
                "status": "ok",
                "command": "replay",
                "session_file": str(session),
                "replay_file": str(output_path),
                "incomplete": False,
                "strict": strict,
                "diff": None
            })
        else:
            click.secho(f"Replay completed successfully. Output stored at {output_path}", fg="green")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        if json_output:
            emit_json(error_envelope("replay", e))
        else:
            click.secho(f"ERROR: Replay failed: {e}", fg="red", err=True)
        sys.exit(1)

@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option('--timing-faithful', is_flag=True, default=None, help="Insert deterministic sleeps matching message timestamps.")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.argument('session_glob', type=str)
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def check(session_glob, timing_faithful, json_output, server_args):
    """Replay a glob of sessions against a server and exit 1 on regression/failure.
    
    Example:
      mcp-vcr check "sessions/*.yaml" -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        err_msg = "No server command specified. What to try: pass the server command and arguments after a '--' separator."
        if json_output:
            emit_json(error_envelope("check", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    import glob
    import json as _json
    matched_paths = glob.glob(session_glob, recursive=True)
    if not matched_paths:
        err_msg = f"No transcripts matched the glob: {session_glob}. What to try: verify the glob pattern matches existing YAML files."
        if json_output:
            emit_json(error_envelope("check", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    matched_paths = sorted([Path(p) for p in matched_paths if Path(p).is_file()])
    if not matched_paths:
        err_msg = f"No files matched the glob: {session_glob}."
        if json_output:
            emit_json(error_envelope("check", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    from .replay import ReplayEngine
    from .diff import run_diff, format_text_diff, format_json_diff
    
    try:
        engine = ReplayEngine(timing_faithful=timing_faithful)
    except Exception as e:
        err_msg = f"Configuration error: {e}. What to try: check the validity of your configuration file or options."
        if json_output:
            emit_json(error_envelope("check", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
    
    all_ok = True
    passed_count = 0
    failed_count = 0
    results_list = []
    
    for path in matched_paths:
        click.secho(f"Checking session: {path.name}", fg="cyan", err=True)
        try:
            output_path = asyncio.run(engine.run_replay(path, server_args=args))
            
            with open(output_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict) and data.get("meta", {}).get("incomplete"):
                reason = data["meta"].get("incomplete_reason", "unknown")
                if not json_output:
                    click.secho(f"FAIL: {path.name} was incomplete due to: {reason}", fg="red", err=True)
                results_list.append({
                    "session_file": str(path),
                    "status": "fail",
                    "message": f"Incomplete due to: {reason}",
                    "diff": None
                })
                all_ok = False
                failed_count += 1
                continue
                
            changes = run_diff(path, output_path, mode="semantic")
            has_changes = any(group["changes"] for group in changes.values())
            
            if has_changes:
                if not json_output:
                    click.secho(f"FAIL: {path.name} failed with regression(s)", fg="red", err=True)
                    click.echo(format_text_diff(changes), err=True)
                diff_dict = _json.loads(format_json_diff(changes))
                results_list.append({
                    "session_file": str(path),
                    "status": "fail",
                    "message": "failed with regression(s)",
                    "diff": diff_dict
                })
                all_ok = False
                failed_count += 1
            else:
                if not json_output:
                    click.secho(f"PASS: {path.name} replayed and matched perfectly", fg="green", err=True)
                results_list.append({
                    "session_file": str(path),
                    "status": "ok",
                    "message": "replayed and matched perfectly",
                    "diff": None
                })
                passed_count += 1
        except Exception as e:
            if not json_output:
                click.secho(f"FAIL: {path.name} failed with exception: {e}", fg="red", err=True)
            results_list.append({
                "session_file": str(path),
                "status": "fail",
                "message": f"failed with exception: {e}",
                "diff": None
            })
            all_ok = False
            failed_count += 1
            
    if json_output:
        emit_json({
            "status": "ok" if all_ok else "fail",
            "command": "check",
            "glob": session_glob,
            "results": results_list,
            "summary": {
                "total": len(matched_paths),
                "passed": passed_count,
                "failed": failed_count
            }
        })
    if not all_ok:
        sys.exit(1)
    sys.exit(0)

@main.command()
@click.argument('transcript_a', type=click.Path(exists=True, path_type=Path))
@click.argument('transcript_b', type=click.Path(exists=True, path_type=Path))
@click.option('--mode', type=click.Choice(['structural', 'semantic', 'strict']), default='structural', help="Diff mode (default: structural).")
@click.option('--format', 'format_type', type=click.Choice(['text', 'json', 'github']), default=None, help="Output format (default: text).")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.option('--ignore', type=str, default=None, help="Comma-separated JSON paths to exclude from comparison.")
def diff(transcript_a, transcript_b, mode, format_type, json_output, ignore):
    """Compare two session transcripts and report structural or semantic differences.
    
    Example:
      mcp-vcr diff sessions/session_A.yaml sessions/session_B.yaml --mode semantic
    """
    if json_output and format_type is not None:
        click.secho("ERROR: --json and --format are mutually exclusive.", fg="red", err=True)
        sys.exit(2)
        
    actual_format = format_type if format_type is not None else 'text'

    from .diff import run_diff, format_text_diff, format_json_diff, format_github_diff
    import json as _json
    
    ignore_list = None
    if ignore:
        ignore_list = [p.strip() for p in ignore.split(",") if p.strip()]
        
    try:
        changes_by_id = run_diff(transcript_a, transcript_b, mode=mode, ignore_fields=ignore_list)
        has_changes = any(group["changes"] for group in changes_by_id.values())
        
        if json_output:
            raw_diff = _json.loads(format_json_diff(changes_by_id))
            emit_json({
                "status": "fail" if has_changes else "ok",
                "command": "diff",
                "transcript_a": str(transcript_a),
                "transcript_b": str(transcript_b),
                "mode": mode,
                "changes": raw_diff["changes"],
                "summary": raw_diff["summary"]
            })
        else:
            if actual_format == "json":
                output = format_json_diff(changes_by_id)
                click.echo(output)
            elif actual_format == "github":
                output = format_github_diff(changes_by_id, transcript_b.name)
                if output:
                    click.echo(output)
            else:
                output = format_text_diff(changes_by_id)
                click.echo(output)
            
        if has_changes:
            sys.exit(1)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        if json_output:
            emit_json(error_envelope("diff", e))
        else:
            click.secho(f"ERROR: Diff failed: {e}", fg="red", err=True)
        sys.exit(1)


@main.command(name="list")
@click.option('--format', 'format_type', type=click.Choice(['text', 'json']), default=None, help="Output format (default: text).")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.option('--dir', 'sessions_dir', type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path), default=Path("sessions"), help="Path to sessions directory.")
def list_sessions(format_type, json_output, sessions_dir):
    """List all recorded sessions in the sessions directory.
    
    Example:
      mcp-vcr list --format json
      mcp-vcr list --json
    """
    if json_output and format_type is not None:
        click.secho("ERROR: --json and --format are mutually exclusive.", fg="red", err=True)
        sys.exit(2)

    actual_format = format_type if format_type is not None else 'text'

    if not sessions_dir.exists() or not sessions_dir.is_dir():
        if json_output:
            emit_json({
                "status": "ok",
                "command": "list",
                "sessions_dir": str(sessions_dir),
                "sessions": []
            })
        elif actual_format == "json":
            click.echo("[]")
        else:
            click.secho(f"No sessions found (directory '{sessions_dir}' does not exist).", fg="yellow")
        return
        
    yaml_files = sorted(list(sessions_dir.glob("*.yaml")) + list(sessions_dir.glob("*.yml")))
    sessions = []
    
    for f_path in yaml_files:
        try:
            with open(f_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            meta = data.get("meta", {})
            sessions.append({
                "date": str(meta.get("recorded_at") or ""),
                "session_id": str(meta.get("session_id") or ""),
                "client_hint": str(meta.get("client_hint") or ""),
                "protocol_version": str(meta.get("protocol_version") or ""),
                "message_count": len(data.get("messages", []))
            })
        except Exception as e:
            click.secho(
                f"WARNING: Skipping unreadable session file '{f_path.name}': {e}",
                fg="yellow",
                err=True,
            )
            continue
            
    # Sort newest first by date string
    sessions.sort(key=lambda x: x["date"] or "", reverse=True)
    
    if json_output:
        emit_json({
            "status": "ok",
            "command": "list",
            "sessions_dir": str(sessions_dir),
            "sessions": sessions
        })
    elif actual_format == "json":
        click.echo(json.dumps(sessions, indent=2))
    else:
        if not sessions:
            click.secho(f"No sessions found in directory '{sessions_dir}'.", fg="yellow")
            return
            
        click.echo(f"{'DATE':<25} {'SESSION ID':<12} {'CLIENT':<15} {'PROTOCOL':<10} {'MESSAGES':<8}")
        click.echo("-" * 74)
        for s in sessions:
            date_str = s["date"][:23] if s["date"] else ""
            client = s["client_hint"] or "-"
            proto = s["protocol_version"] or "-"
            click.echo(f"{date_str:<25} {s['session_id']:<12} {client:<15} {proto:<10} {s['message_count']:<8}")

@main.command()
@click.argument('prefix', type=str)
@click.option('--messages', is_flag=True, help="Show full message list with timestamps.")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.option('--dir', 'sessions_dir', type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path), default=Path("sessions"), help="Path to sessions directory.")
def inspect(prefix, messages, json_output, sessions_dir):
    """Show details of a single session, identified by ID prefix (e.g. short SHA).
    
    Example:
      mcp-vcr inspect abcdef12 --messages
    """
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        err_msg = f"Sessions directory '{sessions_dir}' does not exist."
        if json_output:
            emit_json(error_envelope("inspect", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    yaml_files = sorted(list(sessions_dir.glob("*.yaml")) + list(sessions_dir.glob("*.yml")))
    matches = []
    
    for f_path in yaml_files:
        try:
            with open(f_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            meta = data.get("meta", {})
            session_id = str(meta.get("session_id") or "")
            if session_id.startswith(prefix):
                matches.append((f_path, session_id, data))
        except Exception as e:
            click.secho(
                f"WARNING: Skipping unreadable session file '{f_path.name}': {e}",
                fg="yellow",
                err=True,
            )
            continue
            
    if not matches:
        err_msg = f"No session found with ID prefix '{prefix}'"
        if json_output:
            emit_json(error_envelope("inspect", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
        
    if len(matches) > 1:
        err_msg = f"Ambiguous prefix '{prefix}'. Multiple sessions matched."
        if json_output:
            emit_json(error_envelope("inspect", err_msg))
        else:
            click.secho(f"ERROR: Ambiguous prefix '{prefix}'. Multiple sessions matched:", fg="red", err=True)
            for f_path, session_id, _ in matches:
                click.echo(f"  {session_id} -> {f_path.name}", err=True)
        sys.exit(1)
        
    f_path, session_id, data = matches[0]
    meta = data.get("meta", {})
    msgs = data.get("messages", [])
    if not isinstance(msgs, list):
        err_msg = f"Session '{f_path.name}' has invalid 'messages' format (expected a list)."
        if json_output:
            emit_json(error_envelope("inspect", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)
    
    # Analyze messages
    c2s_count = sum(1 for m in msgs if isinstance(m, dict) and m.get("dir") == "c2s")
    s2c_count = sum(1 for m in msgs if isinstance(m, dict) and m.get("dir") == "s2c")
    
    methods = set()
    for m in msgs:
        if not isinstance(m, dict):
            continue
        payload = m.get("payload")
        if isinstance(payload, dict):
            method = payload.get("method")
            if method:
                methods.add(method)
                
    if json_output:
        payload_data = {
            "status": "ok",
            "command": "inspect",
            "session_id": session_id,
            "session_file": str(f_path),
            "metadata": meta,
            "stats": {
                "total_messages": len(msgs),
                "c2s": c2s_count,
                "s2c": s2c_count
            },
            "methods": sorted(list(methods))
        }
        if messages:
            payload_data["messages"] = msgs
        emit_json(payload_data)
        return

    # Print session info
    click.secho(f"Session Inspection: {session_id}", fg="cyan", bold=True)
    click.echo(f"File Path: {f_path}")
    click.echo("-" * 50)
    click.secho("Metadata:", fg="yellow")
    for k, v in sorted(meta.items()):
        click.echo(f"  {k}: {v}")
        
    click.echo()
    click.secho("Stats:", fg="yellow")
    click.echo(f"  Total Messages: {len(msgs)}")
    click.echo(f"  Client-to-Server (c2s): {c2s_count}")
    click.echo(f"  Server-to-Client (s2c): {s2c_count}")
    
    click.echo()
    click.secho("Methods Seen:", fg="yellow")
    if methods:
        for m in sorted(methods):
            click.echo(f"  - {m}")
    else:
        click.echo("  (None)")
        
    if messages:
        click.echo()
        click.secho("Messages List:", fg="yellow")
        for m in msgs:
            if not isinstance(m, dict):
                click.echo("  [   ??? ms] ???   (Malformed message entry)")
                continue
            t_raw = m.get("t", 0)
            try:
                t = f"{int(t_raw):>6}"
            except (TypeError, ValueError):
                t = "   ???"
            direction_raw = m.get("dir", "???")
            direction = direction_raw if isinstance(direction_raw, str) else "???"
            payload = m.get("payload", {})
            
            info = ""
            if isinstance(payload, dict):
                msg_id = payload.get("id")
                method = payload.get("method")
                if method:
                    if msg_id is not None:
                        info = f"(Request: {method}, id={msg_id})"
                    else:
                        info = f"(Notification: {method})"
                elif msg_id is not None:
                    if "error" in payload:
                        info = f"(Response Error, id={msg_id})"
                    else:
                        info = f"(Response Success, id={msg_id})"
                        
            click.echo(f"  [{t} ms] {direction:<5} {info}")

@main.command()
@click.argument('session_yaml', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path))
def snapshot(session_yaml):
    """Create a normalized golden snapshot from a recorded transcript.
    
    Example:
      mcp-vcr snapshot sessions/my_session.yaml
    """
    try:
        golden_path = run_snapshot(session_yaml)
        click.secho(f"Golden snapshot created: {golden_path}", fg="green")
    except Exception as e:
        click.secho(f"ERROR: Failed to create snapshot: {e}", fg="red", err=True)
        sys.exit(1)

@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option('--update', is_flag=True, help="Update golden snapshots by overwriting with new replayed responses.")
@click.option('--timing-faithful', is_flag=True, default=None, help="Insert deterministic sleeps matching message timestamps.")
@click.option('--config', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to custom .mcp-vcr.yaml configuration.")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.argument('snapshots_dir', type=click.Path(exists=False, file_okay=True, dir_okay=True, path_type=Path))
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def verify(update, timing_faithful, config, json_output, snapshots_dir, server_args):
    """Replay a server against its golden snapshots and report regressions.
    
    Example:
      mcp-vcr verify snapshots/ -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if json_output:
        try:
            result = asyncio.run(_run_verify_impl(
                snapshots_dir,
                server_args=args or None,
                update=update,
                timing_faithful=timing_faithful,
                config_path=config
            ))
            if "error" in result and not result.get("results"):
                emit_json(error_envelope("verify", result["error"]))
                sys.exit(result["exit_code"])
                
            has_failures = result["summary"]["failed"] > 0
            clean_results = []
            for r in result["results"]:
                clean_results.append({
                    "snapshot": r["snapshot"],
                    "source": r["source"],
                    "status": r["status"],
                    "message": r["message"],
                    "diff": r["diff"]
                })
            emit_json({
                "status": "fail" if has_failures else "ok",
                "command": "verify",
                "snapshots_dir": str(snapshots_dir),
                "update": update,
                "results": clean_results,
                "summary": result["summary"]
            })
            sys.exit(result["exit_code"])
        except SystemExit:
            raise
        except Exception as e:
            emit_json(error_envelope("verify", e))
            sys.exit(1)
    else:
        try:
            exit_code = asyncio.run(run_verify(snapshots_dir, server_args=args or None, update=update, timing_faithful=timing_faithful, config_path=config))
        except Exception as e:
            click.secho(f"ERROR: Verification failed: {e}", fg="red", err=True)
            sys.exit(1)
        sys.exit(exit_code)


def _is_stdin_tty() -> bool:
    return sys.stdin.isatty()

_UNSAFE_DISPLAY_CHARS = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)


def _terminal_safe(value: Any) -> str:
    return _UNSAFE_DISPLAY_CHARS.sub("", str(value))

def _format_tool_line(tool: Dict[str, Any]) -> str:
    t_name = _terminal_safe(tool.get("name", "unnamed"))
    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    reqs = schema.get("required", []) if isinstance(schema, dict) else []

    prop_strs = []
    if isinstance(props, dict):
        for p_name, p_schema in props.items():
            p_type = _terminal_safe(
                p_schema.get("type", "any") if isinstance(p_schema, dict) else "any"
            )
            is_req = p_name in reqs if isinstance(reqs, list) else False
            prop_strs.append(f"{_terminal_safe(p_name)}: {p_type}{'*' if is_req else ''}")

    props_desc = f" ({', '.join(prop_strs)})" if prop_strs else ""
    return f"  - {t_name}{props_desc}"

def _echo_tool_list(tools: List[Dict[str, Any]]) -> None:
    for tool in tools:
        click.echo(_format_tool_line(tool), err=True)

def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "server"

async def run_generate(
    args: list[str],
    output: Optional[Path],
    name: Optional[str],
    transport: str,
    sse_url: Optional[str],
    headers: dict,
    timeout: int,
    yes: bool,
    no_call: bool,
    dry_run: bool,
    config: Optional[Path]
) -> int:
    SseTransport = None
    if transport == 'sse':
        try:
            from .transports import SseTransport
        except ImportError:
            pass
        if SseTransport is None:
            click.secho("ERROR: SseTransport is not available. Please install the sse extra: pip install mcp-vcr[sse]", fg="red", err=True)
            return 1
        transport_inst = SseTransport(sse_url=sse_url, headers=headers)
        server_display = _sanitize_url(sse_url)
    else:
        from .transports import StdioTransport
        transport_inst = StdioTransport(args, read_stdin=False)
        server_display = ' '.join(args)

    click.secho(f"Discovering tools from server: {server_display}", fg="cyan", err=True)

    from .generator import GeneratorEngine, ToolCallResult
    engine = GeneratorEngine(config_path=config)

    try:
        discovery = await engine.discover(transport_inst, timeout_ms=timeout)
    except Exception as e:
        click.secho(f"ERROR: Tool discovery failed: {e}", fg="red", err=True)
        await transport_inst.shutdown()
        return 1

    server_info = discovery.server_info
    server_name = server_info.get("name", "server") if isinstance(server_info, dict) else "server"
    server_ver = server_info.get("version", "") if isinstance(server_info, dict) else ""
    server_info_str = f"{server_name} v{server_ver}".strip() if server_ver else server_name

    click.secho(f"✓ initialize: {server_info_str} (protocol: {discovery.protocol_version})", fg="green", err=True)
    click.secho(f"✓ tools/list: {len(discovery.tools)} tools discovered", fg="green", err=True)

    _echo_tool_list(discovery.tools)

    if dry_run:
        await transport_inst.shutdown()
        click.secho("Dry run complete. No snapshot written.", fg="cyan", err=True)
        return 0

    # Determine whether to execute tools/call
    # Flag precedence:
    # 1. --no-call wins over --yes
    # 2. --yes overrides isatty() check
    # 3. If neither flag, check sys.stdin.isatty()
    should_call = False
    if no_call:
        should_call = False
    elif yes:
        should_call = True
    else:
        # Check TTY
        is_tty = _is_stdin_tty()
        if not is_tty:
            click.secho(
                "⚠ Non-interactive mode detected (stdin is not a TTY). Skipping tools/call.\n"
                "  To call tools in non-interactive mode, pass --yes explicitly.",
                fg="yellow",
                err=True
            )
            should_call = False
        else:
            if discovery.tools:
                click.echo(err=True)
                click.secho("⚠ WARNING: The following tools will be called with placeholder arguments against the LIVE server:", fg="yellow", bold=True, err=True)
                _echo_tool_list(discovery.tools)
                click.echo(err=True)
                click.secho(
                    "  Placeholder args are synthetic (e.g. \"example_path\", 0) — tools with side effects\n"
                    "  may execute real actions. Use --no-call to skip, or --yes to auto-confirm.\n",
                    fg="yellow",
                    err=True
                )
                try:
                    should_call = click.confirm("Proceed with tools/call?", default=False, err=True)
                except (click.Abort, EOFError):
                    should_call = False

    try:
        if should_call and discovery.tools:
            click.secho(f"\n✓ tools/call: calling {len(discovery.tools)} tools with placeholder args...", fg="cyan", err=True)

            def on_tool_result(res: ToolCallResult):
                if res.status == "success":
                    click.secho(f"  ✓ {res.tool_name} — success", fg="green", err=True)
                elif res.status == "error":
                    click.secho(f"  ⚠ {res.tool_name} — server rejected placeholder args ({res.error_message})", fg="yellow", err=True)
                else:
                    click.secho(f"  - {res.tool_name} — skipped ({res.error_message})", fg="yellow", err=True)

            await engine.call_tools(transport_inst, discovery, timeout_ms=timeout, on_tool_result=on_tool_result)

            success_count = sum(1 for r in discovery.tool_call_results if r.status == "success")
            error_count = sum(1 for r in discovery.tool_call_results if r.status == "error")
            skipped_count = sum(1 for r in discovery.tool_call_results if r.status == "skipped")
            total_count = len(discovery.tool_call_results)

            if error_count > 0 or skipped_count > 0:
                details = []
                if error_count > 0:
                    details.append(f"{error_count} returned error responses (recorded as-is in snapshot)")
                if skipped_count > 0:
                    details.append(f"{skipped_count} skipped")
                click.secho(f"⚠ tools/call: {success_count}/{total_count} succeeded, {', '.join(details)}", fg="yellow", err=True)
            else:
                click.secho(f"✓ tools/call: {success_count}/{total_count} stubs generated", fg="green", err=True)
    finally:
        await transport_inst.shutdown()

    recorded_cmd = args if transport == 'stdio' else ([_sanitize_url(sse_url)] if sse_url else ["remote-server"])
    transcript_data = engine.build_transcript(discovery, server_command=recorded_cmd)

    default_name = name or _safe_slug(str(server_name))
    if output:
        out_path = Path(output)
        if out_path.suffix in (".yaml", ".yml"):
            target_file = out_path
        else:
            target_file = out_path / f"{default_name}_golden.yaml"
    else:
        target_file = Path("snapshots") / f"{default_name}_golden.yaml"

    try:
        final_path = engine.write_snapshot(transcript_data, target_file)
        click.secho(f"Golden snapshot written to: {final_path}", fg="green")
        return 0
    except Exception as e:
        logger.debug("Snapshot write failed", exc_info=True)
        click.secho(f"ERROR: Failed to write snapshot: {e}", fg="red", err=True)
        return 1

@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option('--server', type=str, default=None, help="Server command string to launch (e.g. 'python server.py').")
@click.option('--output', '-o', type=click.Path(path_type=Path), help="Output directory or golden snapshot file path (default: snapshots/).")
@click.option('--name', type=str, default=None, help="Custom snapshot name.")
@click.option('--transport', type=click.Choice(['stdio', 'sse']), default='stdio', help="Transport protocol (default: stdio).")
@click.option('--sse-url', type=str, default=None, help="SSE endpoint URL (required if --transport=sse).")
@click.option('--sse-header', type=str, multiple=True, help="HTTP header for SSE transport as 'Key: Value'. Repeatable.")
@click.option('--timeout', type=int, default=10000, help="Timeout in milliseconds per request (default: 10000).")
@click.option('--yes', '-y', is_flag=True, help="Skip confirmation prompt and execute tools/call stubs against the live server.")
@click.option('--no-call', is_flag=True, help="Generate transcript with initialize + tools/list only (skip tools/call stubs).")
@click.option('--dry-run', is_flag=True, help="Discover and print tools without writing a snapshot.")
@click.option('--config', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to custom .mcp-vcr.yaml configuration.")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def generate(server, output, name, transport, sse_url, sse_header, timeout, yes, no_call, dry_run, config, server_args):
    """Auto-discover tools from a server and generate a golden snapshot.
    
    Example:
      mcp-vcr generate --server "python server.py"
      mcp-vcr generate --server "python server.py" --yes
      mcp-vcr generate --server "python server.py" --no-call
      mcp-vcr generate --server "python server.py" --dry-run
    """
    import shlex
    args = []
    if server:
        args = shlex.split(server)
    elif server_args:
        args = list(server_args)
        if args and args[0] == '--':
            args = args[1:]

    if transport == 'stdio' and not args:
        click.secho("ERROR: No server command specified. What to try: use --server \"command\" or pass arguments after '--'.", fg="red", err=True)
        sys.exit(1)

    headers = {}
    if transport == 'sse':
        try:
            sse_url, headers = _resolve_sse_settings(config, sse_url, sse_header)
        except SseSettingsError as e:
            click.secho(f"ERROR: {e}", fg="red", err=True)
            sys.exit(1)

    exit_code = asyncio.run(run_generate(
        args=args,
        output=output,
        name=name,
        transport=transport,
        sse_url=sse_url,
        headers=headers,
        timeout=timeout,
        yes=yes,
        no_call=no_call,
        dry_run=dry_run,
        config=config
    ))
    sys.exit(exit_code)

def _error_and_exit(command: str, err: Any, json_output: bool, exit_code: int = 1) -> None:
    """Emit an error envelope or formatted error message and terminate the process."""
    if json_output:
        emit_json(error_envelope(command, err))
    else:
        click.secho(f"ERROR: {err}", fg="red", err=True)
    sys.exit(exit_code)


@main.command(name="test", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option('--suite', type=str, default=None, help="Name of the test suite to run.")
@click.option('--all', 'all_suites', is_flag=True, help="Run all available test suites sequentially against the same server.")
@click.option('--suites-dir', type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path), default=None, help="Path to custom directory containing suites.")
@click.option('--list-suites', is_flag=True, help="List all available test suites and exit.")
@click.option('--use-hint', is_flag=True, help="Use the server launch command from the bundled test suite manifest.")
@click.option('--diff-mode', type=click.Choice(['structural', 'semantic', 'strict']), default='structural', help="Diff mode for response verification (default: structural).")
@click.option('--timeout', type=click.IntRange(min=1), default=None, help="Timeout in milliseconds per request (default: 10000).")
@click.option('--timing-faithful', is_flag=True, default=None, help="Insert deterministic sleeps matching message timestamps.")
@click.option('--config', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to custom .mcp-vcr.yaml configuration.")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON to stdout.")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def test_command(suite, all_suites, suites_dir, list_suites, use_hint, diff_mode, timeout, timing_faithful, config, json_output, server_args):
    """Run predefined test suites against an MCP server.
    
    Example:
      mcp-vcr test --list-suites
      mcp-vcr test --suite filesystem -- npx @modelcontextprotocol/server-filesystem /tmp
      mcp-vcr test --suite filesystem --use-hint
      mcp-vcr test --all -- python server.py
    """
    from .suite import SuiteRunner

    runner = SuiteRunner(
        config_path=config,
        timeout_ms=timeout,
        timing_faithful=timing_faithful
    )

    if suite and all_suites:
        _error_and_exit("test", "--suite and --all are mutually exclusive.", json_output, exit_code=2)

    if use_hint and all_suites:
        _error_and_exit("test", "--use-hint cannot be used with --all.", json_output, exit_code=2)

    if use_hint and suites_dir:
        _error_and_exit(
            "test",
            "--use-hint cannot be used with --suites-dir. Server hints from external suite directories are informational only and will not be executed.",
            json_output,
            exit_code=2,
        )

    if list_suites:
        try:
            suites = runner.list_suites(suites_dir=suites_dir)
            if json_output:
                emit_json({
                    "status": "ok",
                    "command": "test",
                    "suites": [
                        {
                            "name": s.name,
                            "description": s.description,
                            "server_package": s.server_package,
                            "protocol_version": s.protocol_version,
                            "transport": s.transport,
                            "tags": s.tags,
                            "transcripts": s.transcripts,
                            "suite_dir": str(s.suite_dir),
                            "server_hint": s.server_hint
                        }
                        for s in suites
                    ]
                })
            else:
                if not suites:
                    scope_str = f" in '{suites_dir}'" if suites_dir else ""
                    click.secho(f"No test suites found{scope_str}.", fg="yellow")
                else:
                    click.secho("Available community test suites:\n", fg="cyan", bold=True)
                    for s in suites:
                        name_styled = click.style(f"  {s.name:<14} ", fg="green", bold=True)
                        click.echo(f"{name_styled}{s.description}")
                        if s.server_package:
                            click.echo(f"                  Package: {s.server_package}")
                        if s.server_hint:
                            click.echo(f"                  Server hint: {s.server_hint}")
                        click.echo()
            sys.exit(0)
        except Exception as e:
            _error_and_exit("test", f"Failed to list suites: {e}", json_output)

    if not suite and not all_suites:
        _error_and_exit(
            "test",
            "No suite specified. What to try: use --suite <name>, --all, or --list-suites to see available suites.",
            json_output,
        )

    args = list(server_args) if server_args else []
    if args and args[0] == '--':
        args = args[1:]

    if all_suites:
        if not args:
            _error_and_exit(
                "test",
                "No server command specified. What to try: pass the server command and arguments after a '--' separator.",
                json_output,
            )

        try:
            manifests = runner.list_suites(suites_dir=suites_dir)
        except Exception as e:
            _error_and_exit("test", f"Failed to discover suites: {e}", json_output)

        if not manifests:
            scope_str = f" in '{suites_dir}'" if suites_dir else ""
            if json_output:
                emit_json({
                    "status": "ok",
                    "command": "test",
                    "mode": "all",
                    "suite_results": [],
                    "summary": {
                        "suites_total": 0,
                        "suites_passed": 0,
                        "suites_failed": 0,
                        "transcripts_total": 0,
                        "transcripts_passed": 0,
                        "transcripts_failed": 0,
                        "transcripts_skipped": 0
                    }
                })
            else:
                click.secho(f"No test suites found{scope_str}.", fg="yellow")
            sys.exit(0)

        def on_suite_start(m):
            if not json_output:
                click.echo()
                click.secho(f"━━ {m.name} ({len(m.transcripts)} transcripts) ━━", fg="cyan", bold=True, err=True)

        def on_transcript_result(m, r):
            if not json_output:
                t_name = r.get("transcript", "")
                status = r.get("status", "")
                msg = r.get("message", "")
                detail = r.get("detail", "")
                if status == "pass":
                    click.secho(f"  ✓ {t_name} — pass", fg="green")
                else:
                    click.secho(f"  ⚠ {t_name} — fail ({msg})", fg="red", err=True)
                    if detail:
                        click.echo(f"    {detail.strip()}", err=True)

        try:
            multi_res = asyncio.run(runner.run_all_suites(
                manifests,
                server_args=args,
                diff_mode=diff_mode,
                on_suite_start=on_suite_start,
                on_transcript_result=on_transcript_result
            ))
        except Exception as e:
            _error_and_exit("test", f"Multi-suite execution failed: {e}", json_output)

        if json_output:
            emit_json({
                "status": "ok" if multi_res.exit_code == 0 else "fail",
                "command": "test",
                "mode": "all",
                "suite_results": [
                    {
                        "suite": s_res.suite_name,
                        "status": "ok" if s_res.exit_code == 0 else "fail",
                        "results": [
                            {
                                "transcript": r["transcript"],
                                "status": r["status"],
                                "message": r["message"],
                                "diff": r["diff"]
                            }
                            for r in s_res.results
                        ],
                        "summary": {
                            "total": s_res.total,
                            "passed": s_res.passed,
                            "failed": s_res.failed,
                            "skipped": s_res.skipped
                        }
                    }
                    for s_res in multi_res.suite_results
                ],
                "summary": {
                    "suites_total": multi_res.suites_total,
                    "suites_passed": multi_res.suites_passed,
                    "suites_failed": multi_res.suites_failed,
                    "transcripts_total": multi_res.transcripts_total,
                    "transcripts_passed": multi_res.transcripts_passed,
                    "transcripts_failed": multi_res.transcripts_failed,
                    "transcripts_skipped": multi_res.transcripts_skipped
                }
            })
        else:
            click.echo()
            res_color = "green" if multi_res.exit_code == 0 else "red"
            click.secho(
                f"RESULT: {multi_res.suites_passed}/{multi_res.suites_total} suites passed | "
                f"{multi_res.transcripts_passed}/{multi_res.transcripts_total} transcripts passed, "
                f"{multi_res.transcripts_failed}/{multi_res.transcripts_total} failed",
                fg=res_color,
                bold=True
            )

        sys.exit(multi_res.exit_code)

    try:
        manifest = runner.find_suite(suite, suites_dir=suites_dir)
    except Exception as e:
        _error_and_exit("test", e, json_output)

    if not args:
        if use_hint:
            if not runner.is_bundled_suite(manifest):
                _error_and_exit(
                    "test",
                    f"--use-hint can only be used with bundled suites. Suite '{manifest.name}' is from an external directory.",
                    json_output,
                )

            if not manifest.server_hint:
                _error_and_exit("test", f"No server hint defined for suite '{manifest.name}'.", json_output)

            dangerous_chars = [";", "&", "|", "<", ">", "$", "`", "\n", "\r"]
            if any(ch in manifest.server_hint for ch in dangerous_chars):
                _error_and_exit(
                    "test",
                    f"Server hint contains unsupported shell characters: {manifest.server_hint}",
                    json_output,
                )

            import shlex
            try:
                args = shlex.split(manifest.server_hint)
            except Exception as e:
                _error_and_exit(
                    "test",
                    f"Failed to parse server hint '{manifest.server_hint}': {e}",
                    json_output,
                )

            if not args:
                _error_and_exit("test", f"Server hint for suite '{manifest.name}' is empty.", json_output)
        else:
            if manifest.server_hint:
                err_msg = (
                    f"No server command specified.\n"
                    f"  Hint from suite manifest: {manifest.server_hint}\n"
                    f"  To auto-launch: mcp-vcr test --suite {manifest.name} --use-hint\n"
                    f"  To use a custom server: mcp-vcr test --suite {manifest.name} -- your-command"
                )
            else:
                err_msg = "No server command specified. What to try: pass the server command and arguments after a '--' separator."
            _error_and_exit("test", err_msg, json_output)

    if not json_output:
        click.secho(f"Suite: {manifest.name} ({len(manifest.transcripts)} transcripts)", fg="cyan", bold=True, err=True)

    try:
        result = asyncio.run(runner.run_suite(manifest, server_args=args, diff_mode=diff_mode))
    except Exception as e:
        _error_and_exit("test", f"Suite execution failed: {e}", json_output)

    if json_output:
        emit_json({
            "status": "ok" if result.exit_code == 0 else "fail",
            "command": "test",
            "suite": manifest.name,
            "results": [
                {
                    "transcript": r["transcript"],
                    "status": r["status"],
                    "message": r["message"],
                    "diff": r["diff"]
                }
                for r in result.results
            ],
            "summary": {
                "total": result.total,
                "passed": result.passed,
                "failed": result.failed,
                "skipped": result.skipped
            }
        })
    else:
        for r in result.results:
            t_name = r.get("transcript", "")
            status = r.get("status", "")
            msg = r.get("message", "")
            detail = r.get("detail", "")
            if status == "pass":
                click.secho(f"  ✓ {t_name} — pass", fg="green")
            else:
                click.secho(f"  ⚠ {t_name} — fail ({msg})", fg="red", err=True)
                if detail:
                    click.echo(f"    {detail.strip()}", err=True)

        click.echo()
        res_color = "green" if result.exit_code == 0 else "red"
        click.secho(f"RESULT: {result.passed}/{result.total} passed, {result.failed}/{result.total} failed", fg=res_color, bold=True)

    sys.exit(result.exit_code)


async def run_audit(
    args: List[str],
    transport: str,
    sse_url: Optional[str],
    headers: dict,
    timeout: int,
    severity: str,
    json_output: bool,
) -> int:
    SseTransport = None
    if transport == "sse":
        try:
            from .transports import SseTransport
        except ImportError:
            pass
        if SseTransport is None:
            err_msg = "SseTransport is not available. Please install the sse extra: pip install mcp-vcr[sse]"
            if json_output:
                emit_json(error_envelope("audit", err_msg))
            else:
                click.secho(f"ERROR: {err_msg}", fg="red", err=True)
            return 1
        transport_inst = SseTransport(sse_url=sse_url, headers=headers)
        server_display = _sanitize_url(sse_url)
    else:
        from .transports import StdioTransport
        transport_inst = StdioTransport(args, read_stdin=False)
        server_display = " ".join(args)

    if not json_output:
        click.secho(f"Security Audit — Passive Mode", fg="cyan", bold=True, err=True)
        click.secho(f"Auditing server: {server_display}", fg="cyan", err=True)

    from .auditor import AuditEngine
    engine = AuditEngine(timeout_ms=timeout)

    try:
        res = await engine.run_passive_audit(transport_inst, severity_filter=severity)
    except Exception as e:
        err_msg = f"Audit failed: {e}"
        if json_output:
            emit_json(error_envelope("audit", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        return 1

    if json_output:
        emit_json({
            "status": "ok" if res.exit_code == 0 else "fail",
            "command": "audit",
            "mode": "passive",
            "server_info": res.server_info,
            "protocol_version": res.protocol_version,
            "tools_discovered": res.tools_discovered,
            "severity_filter": res.severity_filter,
            "findings": [
                {
                    "check": f.check,
                    "severity": f.severity,
                    "tool": f.tool,
                    "message": f.message,
                    "detail": f.detail,
                }
                for f in res.findings
            ],
            "checks_run": res.checks_run,
            "summary": res.summary,
            "raw_summary": res.raw_summary,
        })
    else:
        s_info = res.server_info
        s_name = s_info.get("name", "server") if isinstance(s_info, dict) else "server"
        s_ver = s_info.get("version", "") if isinstance(s_info, dict) else ""
        s_str = f"{s_name} v{s_ver}".strip() if s_ver else s_name

        click.echo(f"Server: {s_str} (protocol: {res.protocol_version})", err=True)
        click.echo(f"Tools: {res.tools_discovered} discovered\n", err=True)
        click.secho(
            "Note: Passive audit uses static pattern matching. Findings are leads for\n"
            "human review, not proof of vulnerability. Easily evaded by obfuscation.\n",
            fg="yellow",
            err=True,
        )

        click.secho("━━ Findings ━━\n", fg="cyan", bold=True, err=True)
        if not res.findings:
            click.secho("  ✓ No findings detected at or above severity threshold.", fg="green", err=True)
        else:
            for f in res.findings:
                sev_color = "red" if f.severity == "high" else ("yellow" if f.severity == "medium" else "cyan")
                sev_str = click.style(f"  {f.severity.upper():<6}", fg=sev_color, bold=True)
                tool_scope = f"tool \"{_terminal_safe(f.tool)}\"" if f.tool else "server"
                click.echo(f"{sev_str} [{_terminal_safe(f.check)}] {tool_scope}", err=True)
                click.echo(f"        {_terminal_safe(f.message)}", err=True)
                if f.detail:
                    click.echo(f"        Detail: {_terminal_safe(f.detail)}", err=True)
                click.echo(err=True)


        click.secho("━━ Summary ━━", fg="cyan", bold=True, err=True)
        sum_str = f"{len(res.checks_run)} checks completed | {res.summary['total']} findings ({res.summary['high']} high, {res.summary['medium']} medium, {res.summary['low']} low, {res.summary['info']} info)"
        res_color = "green" if res.exit_code == 0 else "red"
        click.secho(f"  {sum_str}", fg=res_color, err=True)
        status_msg = "Clean run" if res.exit_code == 0 else "Security issues detected at or above severity threshold"
        click.secho(f"  Exit code: {res.exit_code} ({status_msg})\n", fg=res_color, bold=True, err=True)

    return res.exit_code


@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.option('--passive', is_flag=True, default=False, help="Run passive security checks on server initialize + tools/list responses.")
@click.option('--server', type=str, default=None, help="Server command string to launch (e.g. 'python server.py').")
@click.option('--transport', type=click.Choice(['stdio', 'sse']), default='stdio', help="Transport protocol (default: stdio).")
@click.option('--sse-url', type=str, default=None, help="SSE endpoint URL (required if --transport=sse).")
@click.option('--sse-header', type=str, multiple=True, help="HTTP header for SSE transport as 'Key: Value'. Repeatable.")
@click.option('--timeout', type=int, default=10000, help="Timeout in milliseconds per request (default: 10000).")
@click.option('--severity', type=click.Choice(['high', 'medium', 'low', 'info']), default='info', help="Minimum severity threshold to report and affect exit code (default: info).")
@click.option('--json', 'json_output', is_flag=True, help="Output structured JSON envelope to stdout.")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def audit(passive, server, transport, sse_url, sse_header, timeout, severity, json_output, server_args):
    """Run security audit suite against an MCP server.
    
    Example:
      mcp-vcr audit --passive -- python server.py
      mcp-vcr audit --passive --server "python server.py" --severity high
      mcp-vcr audit --passive --json -- python server.py
    """
    if not passive:
        err_msg = "--passive flag is required for audit. Usage: mcp-vcr audit --passive -- python server.py"
        if json_output:
            emit_json(error_envelope("audit", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(2)

    import shlex
    args = []
    if server:
        args = shlex.split(server)
    elif server_args:
        args = list(server_args)
        if args and args[0] == '--':
            args = args[1:]

    if transport == 'stdio' and not args:
        err_msg = "No server command specified. What to try: use --server \"command\" or pass arguments after '--'."
        if json_output:
            emit_json(error_envelope("audit", err_msg))
        else:
            click.secho(f"ERROR: {err_msg}", fg="red", err=True)
        sys.exit(1)

    headers = {}
    if transport == 'sse':
        try:
            sse_url, headers = _resolve_sse_settings(None, sse_url, sse_header)
        except SseSettingsError as e:
            if json_output:
                emit_json(error_envelope("audit", str(e)))
            else:
                click.secho(f"ERROR: {e}", fg="red", err=True)
            sys.exit(1)

    exit_code = asyncio.run(run_audit(
        args=args,
        transport=transport,
        sse_url=sse_url,
        headers=headers,
        timeout=timeout,
        severity=severity,
        json_output=json_output,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()



