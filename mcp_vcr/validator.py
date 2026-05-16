import yaml
from pathlib import Path
from typing import Union, Dict, Any
from pydantic import ValidationError
from .schema import Transcript

def load_yaml(file_path: Union[str, Path]) -> Dict[str, Any]:
    """Loads a YAML file from disk."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def validate_transcript(data: Dict[str, Any]) -> Transcript:
    """Validates transcript data against the schema."""
    return Transcript.model_validate(data)

def validate_file(file_path: Union[str, Path]) -> Transcript:
    """Loads and validates a transcript file."""
    data = load_yaml(file_path)
    return validate_transcript(data)
