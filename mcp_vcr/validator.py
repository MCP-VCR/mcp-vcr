import importlib.resources
import json
import yaml
from pathlib import Path
from typing import Any, Dict, List
import jsonschema
from pydantic import ValidationError
from .schema import Transcript

def load_schema() -> Dict[str, Any]:
    """Load the v1 JSON schema file."""
    schema_content = importlib.resources.files("mcp_vcr").joinpath("schemas", "transcript-schema-v1.json").read_text(encoding="utf-8")
    return json.loads(schema_content)

def validate_transcript(file_path: Path) -> List[Dict[str, Any]]:
    """
    Validate a transcript YAML file against the v1 schema.
    Returns a list of dictionaries with 'loc' and 'msg'. If valid, returns empty list.
    """
    errors = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            transcript = yaml.safe_load(f)
            
        if transcript is None:
            return [{"loc": ("transcript",), "msg": "Transcript is empty or invalid YAML"}]
            
        schema = load_schema()
        validator = jsonschema.Draft7Validator(schema)
        
        for error in validator.iter_errors(transcript):
            loc = tuple(str(p) for p in error.path) if error.path else ("root",)
            errors.append({
                "loc": loc,
                "msg": error.message
            })
            
    except yaml.YAMLError as e:
        errors.append({"loc": ("yaml",), "msg": f"YAML Syntax Error: {e}"})
    except FileNotFoundError:
        errors.append({"loc": ("file",), "msg": f"File not found: {file_path}"})
    except Exception as e:
        errors.append({"loc": ("unexpected",), "msg": f"Unexpected error: {e}"})
        
    return errors

def validate_file(file_path: Path) -> Transcript:
    """
    Validate a file against the transcript schema. 
    Raises pydantic.ValidationError if any issues are found.
    On success, returns the parsed Pydantic Transcript model.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if data is None:
        raise ValueError("Transcript is empty or invalid YAML")
        
    # Model validate will raise standard pydantic.ValidationError on failure
    # and return the parsed Transcript on success.
    return Transcript.model_validate(data)
