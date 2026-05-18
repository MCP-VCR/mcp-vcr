import asyncio
import click
import sys
import yaml
from pathlib import Path
from .validator import validate_file, ValidationError
from .transport import run_proxy
from .interceptor import MessageInterceptor
from .recorder import TranscriptRecorder

@click.group()
def main():
    """mcp-vcr: A deterministic MCP transcript proxy and testing tool."""
    pass

@main.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
def validate(path: Path):
    """Validate a transcript YAML file or directory of transcripts against the schema."""
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
@click.option('--name', type=str, default=None, help="Custom output filename (e.g. sessions/my_session.yaml).")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def record(name, server_args):
    """Record an MCP session by proxying traffic to a server subprocess."""
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified.", fg="red", err=True)
        sys.exit(1)
        
    # Initialize the streaming TranscriptRecorder and MessageInterceptor
    recorder = TranscriptRecorder(filename=name, server_command=args)
    interceptor = MessageInterceptor(recorder=recorder)
    
    click.secho(f"Starting proxy for server: {' '.join(args)}", fg="cyan", err=True)
    click.secho(f"Recording to: {recorder.filepath}", fg="cyan", err=True)
    
    try:
        recorder.start_session()
    except Exception as e:
        click.secho(f"ERROR: Failed to open session file: {e}", fg="red", err=True)
        sys.exit(1)

    exit_code = 1
    try:
        exit_code = asyncio.run(run_proxy(args, interceptor=interceptor, recorder=recorder))
    except Exception as e:
        click.secho(f"ERROR: Proxy failed: {e}", fg="red", err=True)
    finally:
        try:
            recorder.close()
        except Exception as e:
            click.secho(f"ERROR: Failed to safely save transcript: {e}", fg="red", err=True)
            exit_code = 1
            
    sys.exit(exit_code)

@main.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.argument('session', type=click.Path(exists=True, path_type=Path))
@click.option('--timeout', type=int, default=None, help="Timeout in milliseconds per request.")
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def replay(session, timeout, server_args):
    """Replay an MCP session against a server subprocess."""
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified.", fg="red", err=True)
        sys.exit(1)
        
    from .replay import ReplayEngine
    engine = ReplayEngine(timeout_ms=timeout)
    
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
    """Replay a glob of sessions against a server and exit 1 on regression/failure."""
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified.", fg="red", err=True)
        sys.exit(1)
        
    # Resolve the glob
    import glob
    matched_paths = glob.glob(session_glob, recursive=True)
    if not matched_paths:
        click.secho(f"No transcripts matched the glob: {session_glob}", fg="yellow", err=True)
        sys.exit(0)
        
    matched_paths = sorted([Path(p) for p in matched_paths if Path(p).is_file()])
    
    from .replay import ReplayEngine
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
                click.secho(f"FAIL: {path.name} failed with incomplete reason: {reason}", fg="red", err=True)
                all_ok = False
            else:
                click.secho(f"PASS: {path.name} replayed successfully", fg="green")
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
@click.option('--ignore-fields', type=str, default=None, help="Comma-separated JSON paths to exclude from comparison.")
def diff(transcript_a, transcript_b, mode, format_type, ignore_fields):
    """Compare two session transcripts and output differences."""
    from .diff import run_diff, format_text_diff, format_json_diff, format_github_diff
    
    ignore_list = None
    if ignore_fields:
        ignore_list = [p.strip() for p in ignore_fields.split(",") if p.strip()]
        
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

@main.command()
@click.argument('session_yaml', type=click.Path(exists=True, path_type=Path))
def snapshot(session_yaml):
    """Create a normalized golden snapshot from a recorded or replayed transcript."""
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
@click.argument('snapshots_dir', type=click.Path(exists=True, path_type=Path))
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def verify(update, snapshots_dir, server_args):
    """Replay a server against its golden snapshots and report regressions."""
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified.", fg="red", err=True)
        sys.exit(1)
        
    from .snapshot import run_verify
    exit_code = asyncio.run(run_verify(snapshots_dir, server_args=args, update=update))
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
