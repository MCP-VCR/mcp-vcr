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

if __name__ == "__main__":
    main()
