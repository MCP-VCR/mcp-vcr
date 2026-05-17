import os
import yaml
import pytest
from pathlib import Path
from mcp_vcr.interceptor import MessageInterceptor, Direction
from mcp_vcr.recorder import TranscriptRecorder
from mcp_vcr.redactor import Redactor

def test_redactor_interceptor_recorder_integration(tmp_path):
    """
    Verify the complete correctness invariant:
    1. MessageInterceptor receives original unredacted payload.
    2. MessageInterceptor preserves the original payload in observed_messages.
    3. TranscriptRecorder receives and writes the REDACTED payload to the transcript file.
    4. Original payload is not mutated.
    """
    test_file = tmp_path / "integration_transcript.yaml"
    
    # 1. Initialize recorder and interceptor
    recorder = TranscriptRecorder(filename=str(test_file), server_command=["python", "server.py"])
    interceptor = MessageInterceptor(recorder=recorder)
    
    recorder.start_session()
    
    original_payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "token": "secret_session_token_123",
            "clientInfo": {"name": "AwesomeAgent"},
            "debug_path": "/var/log/app.log"
        }
    }
    
    # Keep a pure copy to verify immutability
    original_payload_copy = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "token": "secret_session_token_123",
            "clientInfo": {"name": "AwesomeAgent"},
            "debug_path": "/var/log/app.log"
        }
    }
    
    # 2. Call observe
    interceptor.observe(original_payload, Direction.C2S)
    
    # Verify original payload was NOT mutated
    assert original_payload == original_payload_copy
    
    # Verify internal observed_messages holds the original unredacted payload
    assert len(interceptor.observed_messages) == 1
    observed = interceptor.observed_messages[0]
    assert observed["payload"]["params"]["token"] == "secret_session_token_123"
    assert observed["payload"]["params"]["debug_path"] == "/var/log/app.log"
    
    # Close recorder (flushes post-processing backfill)
    recorder.close()
    
    # 3. Read transcript file and verify values are redacted
    with open(test_file, "r", encoding="utf-8") as f:
        transcript_data = yaml.safe_load(f)
        
    assert len(transcript_data["messages"]) == 1
    recorded_msg = transcript_data["messages"][0]
    
    # Token must be redacted to <REDACTED_token>
    assert recorded_msg["payload"]["params"]["token"] == "<REDACTED_token>"
    # Path must be redacted to <PATH>
    assert recorded_msg["payload"]["params"]["debug_path"] == "<PATH>"
    # Client hint was backfilled from clientInfo.name
    assert transcript_data["meta"]["client_hint"] == "AwesomeAgent"
