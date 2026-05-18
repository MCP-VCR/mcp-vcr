from datetime import datetime
from enum import Enum
from typing import List, Optional, Any, Dict, Literal
from pydantic import BaseModel, Field

class Direction(str, Enum):
    C2S = "c2s"
    S2C = "s2c"

class Metadata(BaseModel):
    version: int = Field(ge=1, le=1, description="Transcript format version")
    recorded_at: datetime = Field(description="ISO 8601 UTC timestamp of recording start")
    session_id: str = Field(description="Unique 8-character hex session ID")
    server_command: List[str] = Field(description="Command used to launch the MCP server")
    protocol_version: Optional[str] = Field(default=None, description="MCP protocol version from initialize result")
    client_hint: Optional[str] = Field(default=None, description="Inferred client identity")
    schema_version: Optional[str] = Field(default="1.0", description="Schema version for validation")
    incomplete: Optional[bool] = Field(default=None, description="True if the session replay was incomplete")
    incomplete_reason: Optional[Literal["timeout", "server_crash", "pipe_error"]] = Field(
        default=None,
        description="Reason for incomplete replay",
    )

class Message(BaseModel):
    t: int = Field(description="Milliseconds since session start")
    dir: Direction = Field(description="Direction of the message: c2s or s2c")
    payload: Dict[str, Any] = Field(description="The JSON-RPC message payload")

class Transcript(BaseModel):
    meta: Metadata
    messages: List[Message]
