import asyncio
import click
import sys
import yaml
from pathlib import Path
from pydantic import ValidationError
from .validator import validate_file
from .transport import run_proxy
from .interceptor import MessageInterceptor

@click.group()
def main():
    """mcp-vcr: A deterministic MCP transcript proxy and testing tool."""
    pass

@main.command()
@click.argument('path', type=click.Path(exists=True, path_type=Path))
def validate(path: Path):
    """Validate a transcript YAML file against the schema."""
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
@click.argument('server_args', nargs=-1, type=click.UNPROCESSED, required=True)
def record(server_args):
    """Record an MCP session by proxying traffic to a server subprocess."""
    args = list(server_args)
    if args and args[0] == '--':
        args = args[1:]
        
    if not args:
        click.secho("ERROR: No server command specified.", fg="red", err=True)
        sys.exit(1)
        
    command_name = args[0]
    interceptor = MessageInterceptor(server_command=[command_name])
    click.secho(f"Starting proxy for server: {command_name}", fg="cyan", err=True)

    exit_code = 1
    try:
        exit_code = asyncio.run(run_proxy(args, interceptor=interceptor, recorder=interceptor))
    except Exception as e:
        click.secho(f"ERROR: Proxy failed: {e}", fg="red", err=True)
    finally:
        try:
            interceptor.save("session.yaml")
        except (OSError, yaml.YAMLError) as e:
            click.secho(f"ERROR: Failed to save session.yaml: {e}", fg="red", err=True)
            exit_code = 1
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
