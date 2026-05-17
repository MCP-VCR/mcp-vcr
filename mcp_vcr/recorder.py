import os
import secrets
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

class TranscriptRecorder:
    """
    TranscriptRecorder handles the incremental serialization of intercepted messages
    to a stable-keyed YAML file, supporting lazy meta backfilling and crash robustness.
    """
    def __init__(self, filename: Optional[str] = None, server_command: Optional[List[str]] = None):
        self.session_id = secrets.token_hex(4)  # Unique 8-character hex
        self.server_command = server_command or []
        self.recorded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Track lazy metadata
        self.protocol_version: Optional[str] = None
        self.client_hint: Optional[str] = None
        
        # Determine output filename
        if filename:
            self.filepath = Path(filename)
        else:
            now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.filepath = Path(f"sessions/session_{now_str}_{self.session_id}.yaml")
            
        # Automatically create the parent directories
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.file_handle = None

    def start_session(self) -> None:
        """
        Open the transcript file and write the initial meta header and messages key.
        """
        fd = os.open(self.filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self.file_handle = os.fdopen(fd, "w", encoding="utf-8")
        
        # Write initial YAML headers deterministically
        meta_dict = {
            "recorded_at": self.recorded_at,
            "session_id": self.session_id,
            "server_command": self.server_command,
            "schema_version": "1.0"
        }
        
        initial_doc = {
            "version": 1,
            "meta": meta_dict
        }
        
        # Dump meta and then output messages: key
        meta_yaml = yaml.safe_dump(initial_doc, sort_keys=True, default_flow_style=False)
        self.file_handle.write(meta_yaml)
        self.file_handle.write("messages:\n")
        self.file_handle.flush()

    def write(self, msg: Dict[str, Any]) -> None:
        """
        Append a single InterceptedMessage to the messages list in the file incrementally.
        """
        if not self.file_handle:
            return
            
        # Build message record
        item = {
            "t": msg["t"],
            "dir": msg["dir"],
            "payload": msg["payload"]
        }
        
        # Safe serialize one list item
        yaml_str = yaml.safe_dump([item], sort_keys=True, default_flow_style=False)
        self.file_handle.write(yaml_str)
        self.file_handle.flush()

    def update_lazy_metadata(self, client_hint: Optional[str] = None, protocol_version: Optional[str] = None) -> None:
        """
        Update the lazy metadata properties.
        """
        if client_hint:
            self.client_hint = client_hint
        if protocol_version:
            self.protocol_version = protocol_version

    def close(self) -> None:
        """
        Close the file handle and perform post-processing backfill of client_hint and protocol_version.
        """
        if not self.file_handle:
            return
            
        self.file_handle.close()
        self.file_handle = None
        
        # Perform clean post-processing backfill
        try:
            if not self.filepath.exists():
                return
                
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                
            if not data or "meta" not in data:
                return
                
            # Populate lazy fields if captured and not already present
            if self.protocol_version:
                data["meta"]["protocol_version"] = self.protocol_version
            if self.client_hint:
                data["meta"]["client_hint"] = self.client_hint
                
            # Rewrite fully populated, deterministic transcript with stable key ordering
            with open(self.filepath, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=True, default_flow_style=False)
                
        except Exception as e:
            # Safe catch to ensure close doesn't crash normal exit
            sys.stderr.write(f"Warning: Failed to backfill metadata: {e}\n")
            sys.stderr.flush()
