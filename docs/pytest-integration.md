# Pytest Integration Guide for MCP-VCR

`mcp-vcr` provides a fast, robust CLI for recording, replaying, and diffing MCP sessions. For Python developers who already use `pytest` for their testing pipeline, integrating `mcp-vcr` directly into pytest tests allows automated regression checks alongside other unit/integration tests.

---

## When to Use Pytest vs. Direct CLI

| Approach | Best Suited For | Advantages |
| :--- | :--- | :--- |
| **Direct CLI (`verify` / `check`)** | - Simple terminal usage<br>- Standard GitHub Actions pipelines<br>- Language-agnostic MCP servers | - Zero boilerplate code<br>- Trivial GHA step registration<br>- Faster execution without pytest overhead |
| **Pytest Integration** | - Complex setup/teardowns (e.g. databases, mock APIs)<br>- Multi-server orchestration<br>- Mixed unit & integration testing | - Unified test reporting<br>- Powerful fixture sharing (`pytest.fixture`) |

---

## Pytest Integration Patterns

Here are three common ways to run `mcp-vcr` validations inside a `pytest` suite.

### Pattern 1: Replay inside a Pytest Fixture
This pattern launches the server and replays a transcript within a reusable pytest fixture, allowing test functions to verify properties of the replay output.

```python
import pytest
import subprocess
from pathlib import Path

@pytest.fixture(scope="function")
def replay_session(tmp_path):
    """
    Launches mcp-vcr replay against the target server and yields the output path.
    """
    transcript_path = Path("sessions/my_session.yaml")
    output_dir = tmp_path / "replays"
    output_dir.mkdir()
    
    # Run the mcp-vcr replay command as a subprocess
    cmd = [
        "mcp-vcr", "replay",
        str(transcript_path),
        "--", "python", "server.py"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Replay failed:\n{result.stderr}"
    
    # Locate the output replay file
    output_files = list(Path("sessions").glob("*-replay-*.yaml"))
    yield output_files[-1] if output_files else None
```

---

### Pattern 2: Subprocess call in a Pytest Test (`mcp-vcr check`)
Using `mcp-vcr check` in a test function allows replaying a glob pattern of transcripts and automatically asserting success in a single test case.

```python
import subprocess
import pytest

def test_mcp_server_regressions():
    """
    Replays all recorded transcripts in the sessions/ folder and exits 1 if any fails.
    """
    cmd = [
        "mcp-vcr", "check",
        "sessions/*.yaml",
        "--", "python", "server.py"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # The check command returns exit code 0 if all replays are successful/complete
    assert result.returncode == 0, f"Regression check failed:\n{result.stdout}\n{result.stderr}"
```

---

### Pattern 3: Golden Snapshot Verification in a Session Hook (`mcp-vcr verify`)
You can use `mcp-vcr verify` inside the `pytest_sessionfinish` hook to run regression checks against all golden snapshots after the rest of the test suite completes successfully.

```python
# conftest.py
import pytest
import subprocess

def pytest_sessionfinish(session, exitstatus):
    """
    Automatically runs mcp-vcr verify after all pytest tests pass.
    """
    # Only run verify if previous tests succeeded to keep the report clean
    if exitstatus == 0:
        print("\n=== Running Golden Snapshot Verification ===")
        cmd = [
            "mcp-vcr", "verify",
            "snapshots/",
            "--", "python", "server.py"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print(f"ERROR: Golden snapshots verification failed:\n{result.stderr}", file=sys.stderr)
            session.exitstatus = 1
```
