import asyncio
import click
import sys
import yaml
import secrets
from datetime import datetime, timezone
from pathlib import Path
from .validator import validate_file, ValidationError
from .transport import run_proxy
from .interceptor import MessageInterceptor
from .recorder import TranscriptRecorder
from .redactor import Redactor

@click.group()
@click.version_option(version="0.1.0", prog_name="mcp-vcr")
def main():
    """mcp-vcr: A deterministic MCP transcript proxy and testing tool."""
    pass

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
                validate_file(file)
                click.secho(f"OK: '{file.name}' is valid.", fg="green")
            except ValidationError as e:
                all_ok = False
                click.secho(f"ERROR: '{file.name}' validation failed:", fg="red", err=True)
                for error in e.errors():
                    loc = " -> ".join(str(part) for part in error['loc'])
                    msg = error['msg']
                    click.echo(f"  {loc}: {msg}", err=True)
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
            validate_file(path)
            click.secho(f"OK: Transcript '{path}' is valid.", fg="green")
        except ValidationError as e:
            click.secho(f"ERROR: Validation failed for '{path}':", fg="red", err=True)
            for error in e.errors():
                loc = " -> ".join(str(part) for part in error['loc'])   
                msg = error['msg']
                click.echo(f"  {loc}: {msg}", err=True)
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
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def record(output, name, no_redact, config, server_args):
    """Record an MCP session by proxying traffic to a server subprocess.
    
    Example:
      mcp-vcr record -o sessions/ --name my_session -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified. What to try: pass the server command and arguments after a '--' separator.", fg="red", err=True)
        sys.exit(1)
        
    # Determine output folder and filename
    output_path = output if output else Path("sessions")
    
    if output_path.suffix in (".yaml", ".yml"):
        target_dir = output_path.parent
        filepath = output_path
    else:
        target_dir = output_path
        if name:
            filename = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
            filepath = target_dir / filename
        else:
            session_id = secrets.token_hex(4)
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filepath = target_dir / f"session_{now_str}_{session_id}.yaml"
            
    # Validate startup folders
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        click.secho(f"ERROR: Cannot create output directory '{target_dir}': {e}. What to try: specify a valid, writable path with the --output flag.", fg="red", err=True)
        sys.exit(1)
        
    # Initialize redactor
    redactor = Redactor(config_path=config, enabled=not no_redact)
    
    # Initialize the streaming TranscriptRecorder and MessageInterceptor
    recorder = TranscriptRecorder(filename=str(filepath), server_command=args)
    interceptor = MessageInterceptor(recorder=recorder, redactor=redactor)
    
    click.secho(f"Starting proxy for server: {' '.join(args)}", fg="cyan", err=True)
    click.secho(f"Recording to: {recorder.filepath}", fg="cyan", err=True)
    
    try:
        recorder.start_session()
    except Exception as e:
        click.secho(f"ERROR: Failed to open session file: {e}. What to try: check write permissions for '{filepath}'.", fg="red", err=True)
        sys.exit(1)

    exit_code = 1
    try:
        exit_code = asyncio.run(run_proxy(args, interceptor=interceptor, recorder=recorder))
    except Exception as e:
        click.secho(f"ERROR: Proxy failed: {e}. What to try: check server executable path and arguments.", fg="red", err=True)
    finally:
        try:
            recorder.close()
        except Exception as e:
            click.secho(f"ERROR: Failed to safely save transcript: {e}.", fg="red", err=True)
            exit_code = 1
            
    sys.exit(exit_code)

@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.argument('session', type=click.Path(exists=True, path_type=Path))
@click.option('--timeout', type=int, default=5000, help="Timeout in milliseconds per request (default: 5000).")
@click.option('--strict', is_flag=True, help="Exit 1 if any response differs from the original transcript.")
@click.option('--config', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to custom .mcp-vcr.yaml configuration.")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def replay(session, timeout, strict, config, server_args):
    """Replay an MCP session against a server subprocess.
    
    Example:
      mcp-vcr replay sessions/my_session.yaml --timeout 5000 -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified. What to try: pass the server command and arguments after a '--' separator.", fg="red", err=True)
        sys.exit(1)
        
    from .replay import ReplayEngine
    try:
        engine = ReplayEngine(config_path=config, timeout_ms=timeout)
    except Exception as e:
        click.secho(f"ERROR: Configuration error: {e}. What to try: check the validity of your configuration file or options.", fg="red", err=True)
        sys.exit(1)
        
    click.secho(f"Starting replay of {session} against server: {' '.join(args)}", fg="cyan", err=True)
    
    try:
        output_path = asyncio.run(engine.run_replay(session, server_args=args))
        
        # Check if the output has incomplete: true in meta
        with open(output_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict) and data.get("meta", {}).get("incomplete"):
            reason = data["meta"].get("incomplete_reason", "unknown")
            click.secho(f"ERROR: Replay was incomplete due to: {reason}", fg="red", err=True)
            sys.exit(1)
            
        if strict:
            from .diff import run_diff, format_text_diff
            try:
                changes = run_diff(session, output_path, mode="strict")
                has_changes = any(group["changes"] for group in changes.values())
                if has_changes:
                    click.secho("ERROR: Strict replay failed: responses differ from recorded session.", fg="red", err=True)
                    click.echo(format_text_diff(changes), err=True)
                    sys.exit(1)
            except Exception as e:
                click.secho(f"ERROR: Diff comparison failed: {e}", fg="red", err=True)
                sys.exit(1)
                
        click.secho(f"Replay completed successfully. Output stored at {output_path}", fg="green")
            
    except Exception as e:
        click.secho(f"ERROR: Replay failed: {e}", fg="red", err=True)
        sys.exit(1)

@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.argument('session_glob', type=str)
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def check(session_glob, server_args):
    """Replay a glob of sessions against a server and exit 1 on regression/failure.
    
    Example:
      mcp-vcr check "sessions/*.yaml" -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified. What to try: pass the server command and arguments after a '--' separator.", fg="red", err=True)
        sys.exit(1)
        
    # Resolve the glob
    import glob
    matched_paths = glob.glob(session_glob, recursive=True)
    if not matched_paths:
        click.secho(f"ERROR: No transcripts matched the glob: {session_glob}. What to try: verify the glob pattern matches existing YAML files.", fg="yellow", err=True)
        sys.exit(1)
        
    matched_paths = sorted([Path(p) for p in matched_paths if Path(p).is_file()])
    if not matched_paths:
        click.secho(f"ERROR: No files matched the glob: {session_glob}.", fg="yellow", err=True)
        sys.exit(1)
        
    from .replay import ReplayEngine
    from .diff import run_diff, format_text_diff
    
    engine = ReplayEngine()
    
    all_ok = True
    for path in matched_paths:
        click.secho(f"Checking session: {path.name}", fg="cyan")
        try:
            output_path = asyncio.run(engine.run_replay(path, server_args=args))
            
            # Check if output transcript has incomplete: true
            with open(output_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict) and data.get("meta", {}).get("incomplete"):
                reason = data["meta"].get("incomplete_reason", "unknown")
                click.secho(f"FAIL: {path.name} was incomplete due to: {reason}", fg="red", err=True)
                all_ok = False
                continue
                
            # Perform semantic diff comparison
            changes = run_diff(path, output_path, mode="semantic")
            has_changes = any(group["changes"] for group in changes.values())
            
            if has_changes:
                click.secho(f"FAIL: {path.name} failed with regression(s)", fg="red", err=True)
                click.echo(format_text_diff(changes), err=True)
                all_ok = False
            else:
                click.secho(f"PASS: {path.name} replayed and matched perfectly", fg="green")
        except Exception as e:
            click.secho(f"FAIL: {path.name} failed with exception: {e}", fg="red", err=True)
            all_ok = False
            
    if not all_ok:
        sys.exit(1)
    sys.exit(0)

@main.command()
@click.argument('transcript_a', type=click.Path(exists=True, path_type=Path))
@click.argument('transcript_b', type=click.Path(exists=True, path_type=Path))
@click.option('--mode', type=click.Choice(['structural', 'semantic', 'strict']), default='structural', help="Diff mode (default: structural).")
@click.option('--format', 'format_type', type=click.Choice(['text', 'json', 'github']), default='text', help="Output format (default: text).")
@click.option('--ignore', type=str, default=None, help="Comma-separated JSON paths to exclude from comparison.")
def diff(transcript_a, transcript_b, mode, format_type, ignore):
    """Compare two session transcripts and report structural or semantic differences.
    
    Example:
      mcp-vcr diff sessions/session_A.yaml sessions/session_B.yaml --mode semantic
    """
    from .diff import run_diff, format_text_diff, format_json_diff, format_github_diff
    
    ignore_list = None
    if ignore:
        ignore_list = [p.strip() for p in ignore.split(",") if p.strip()]
        
    try:
        changes_by_id = run_diff(transcript_a, transcript_b, mode=mode, ignore_fields=ignore_list)
        
        has_changes = any(group["changes"] for group in changes_by_id.values())
        
        if format_type == "json":
            output = format_json_diff(changes_by_id)
            click.echo(output)
        elif format_type == "github":
            output = format_github_diff(changes_by_id, transcript_b.name)
            if output:
                click.echo(output)
        else:
            output = format_text_diff(changes_by_id)
            click.echo(output)
            
        if has_changes:
            sys.exit(1)
        sys.exit(0)
    except Exception as e:
        click.secho(f"ERROR: Diff failed: {e}", fg="red", err=True)
        sys.exit(1)

@main.command(name="list")
@click.option('--format', 'format_type', type=click.Choice(['text', 'json']), default='text', help="Output format (default: text).")
@click.option('--dir', 'sessions_dir', type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path), default=Path("sessions"), help="Path to sessions directory.")
def list_sessions(format_type, sessions_dir):
    """List all recorded sessions in the sessions directory.
    
    Example:
      mcp-vcr list --format json
    """
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        if format_type == "json":
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
                "date": meta.get("recorded_at", ""),
                "session_id": meta.get("session_id", ""),
                "client_hint": meta.get("client_hint", ""),
                "protocol_version": meta.get("protocol_version", ""),
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
    sessions.sort(key=lambda x: x["date"], reverse=True)
    
    if format_type == "json":
        import json
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
@click.option('--dir', 'sessions_dir', type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path), default=Path("sessions"), help="Path to sessions directory.")
def inspect(prefix, messages, sessions_dir):
    """Show details of a single session, identified by ID prefix (e.g. short SHA).
    
    Example:
      mcp-vcr inspect abcdef12 --messages
    """
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        click.secho(f"ERROR: Sessions directory '{sessions_dir}' does not exist.", fg="red", err=True)
        sys.exit(1)
        
    yaml_files = sorted(list(sessions_dir.glob("*.yaml")) + list(sessions_dir.glob("*.yml")))
    matches = []
    
    for f_path in yaml_files:
        try:
            with open(f_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            meta = data.get("meta", {})
            session_id = meta.get("session_id", "")
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
        click.secho(f"ERROR: No session found with ID prefix '{prefix}'", fg="red", err=True)
        sys.exit(1)
        
    if len(matches) > 1:
        click.secho(f"ERROR: Ambiguous prefix '{prefix}'. Multiple sessions matched:", fg="red", err=True)
        for f_path, session_id, _ in matches:
            click.echo(f"  {session_id} -> {f_path.name}", err=True)
        sys.exit(1)
        
    f_path, session_id, data = matches[0]
    meta = data.get("meta", {})
    msgs = data.get("messages", [])
    
    # Analyze messages
    c2s_count = sum(1 for m in msgs if m.get("dir") == "c2s")
    s2c_count = sum(1 for m in msgs if m.get("dir") == "s2c")
    
    methods = set()
    for m in msgs:
        payload = m.get("payload")
        if isinstance(payload, dict):
            method = payload.get("method")
            if method:
                methods.add(method)
                
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
            t = m.get("t", 0)
            direction = m.get("dir", "???")
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
                        
            click.echo(f"  [{t:>6} ms] {direction:<5} {info}")

@main.command()
@click.argument('session_yaml', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path))
def snapshot(session_yaml):
    """Create a normalized golden snapshot from a recorded transcript.
    
    Example:
      mcp-vcr snapshot sessions/my_session.yaml
    """
    from .snapshot import run_snapshot
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
@click.argument('snapshots_dir', type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path))
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=False)
def verify(update, snapshots_dir, server_args):
    """Replay a server against its golden snapshots and report regressions.
    
    Example:
      mcp-vcr verify snapshots/ -- python server.py
    """
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    from .snapshot import run_verify
    try:
        exit_code = asyncio.run(run_verify(snapshots_dir, server_args=args or None, update=update))
    except Exception as e:
        click.secho(f"ERROR: Verification failed: {e}", fg="red", err=True)
        sys.exit(1)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
