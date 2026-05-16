import click
import sys
import yaml
from pathlib import Path
from pydantic import ValidationError
from .validator import validate_file

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
        # Simplify error output for CLI
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error['loc'])
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

if __name__ == "__main__":
    main()
