import os
import subprocess
from pathlib import Path
import pytest

def test_dogfood_record_pytest(tmp_path):
    """
    Use MCP-VCR to test MCP-VCR.
    Record the execution of a test that uses MCP-VCR.
    """
    snapshot_path = tmp_path / "snapshot_dogfood.yaml"
    
    # Run the e2e recorder test inside mcp-vcr record
    test_file = Path(__file__).parent / "test_e2e_recorder.py"
    
    # We won't actually proxy an MCP server here because pytest isn't an MCP server,
    # but we can verify that mcp-vcr doesn't crash when wrapping another process that does IO.
    # A true dogfood test proxying MCP traffic would require a real MCP client and server, 
    # but for CI, we can just ensure it can wrap pytest successfully.
    
    cmd = [
        "uv", "run", "mcp-vcr", "record", "-o", str(snapshot_path),
        "--", "uv", "run", "pytest", str(test_file)
    ]
    
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    
    # Pytest should succeed inside the wrapper
    assert proc.returncode == 0
    assert snapshot_path.exists()
