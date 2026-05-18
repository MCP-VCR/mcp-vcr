import importlib.resources
import json
import yaml
from pathlib import Path
from typing import Any, Dict, List
import jsonschema
from pydantic import ValidationError
from .schema import Transcript

_SCHEMA_CACHE = None
def load_schema() -> Dict[str, Any]:
    """Load the v1 JSON schema file."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:     
        return _SCHEMA_CACHE
    schema_content = importlib.resources.files("mcp_vcr").joinpath("schemas", "transcript-schema-v1.json").read_text(encoding="utf-8")
    _SCHEMA_CACHE = json.loads(schema_content)
    return _SCHEMA_CACHE

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
    except (OSError, UnicodeDecodeError) as e:
        errors.append({"loc": ("file",), "msg": f"File error: {e}"})
    return errors

def validate_file(file_path: Path, allow_v0: bool = True) -> Transcript:
    """
    Validate a file against the transcript schema. 
    Raises pydantic.ValidationError if any issues are found.
    On success, returns the parsed Pydantic Transcript model.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if data is None:
        raise ValueError("Transcript is empty or invalid YAML")
        
    if not isinstance(data, dict):
        raise ValueError("Transcript top-level structure must be a dictionary")
        
    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise ValueError("Metadata must be a dictionary")
        
    version = meta.get("version")
    
    if not allow_v0:
        if version is None or version == 0:
            from pydantic import BaseModel, Field
            class StrictMetadata(BaseModel):
                version: int = Field(ge=1, le=1, description="Strict version check")
            # This raises standard pydantic.ValidationError for version field
            StrictMetadata.model_validate({"version": version})
            
    is_v0 = False
    
    import logging
    logger = logging.getLogger("mcp-vcr.validator")
    
    if version is None:
        is_v0 = True
        meta["version"] = 0
        logger.warning(
            f"Transcript at {file_path} is missing 'version' field. Treating as legacy v0 transcript."
        )
    elif version == 0:
        is_v0 = True
        logger.warning(
            f"Transcript at {file_path} has legacy version 0. Treating as legacy v0 transcript."
        )
        
    if is_v0:
        # Backfill required fields if missing to ensure successful parsing as legacy v0
        if "recorded_at" not in meta:
            from datetime import datetime, timezone
            meta["recorded_at"] = datetime.now(timezone.utc).isoformat()
        if "session_id" not in meta:
            meta["session_id"] = "00000000"
        if "server_command" not in meta:
            meta["server_command"] = ["python", "server.py"]
        if "schema_version" not in meta:
            meta["schema_version"] = "0.1"
            
    # Model validate will raise standard pydantic.ValidationError on failure
    # and return the parsed Transcript on success.
    return Transcript.model_validate(data)
