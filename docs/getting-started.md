# MCP-VCR Developer Getting Started Guide

Welcome to **MCP-VCR**, a record-and-replay testing tool for Model Context Protocol (MCP) servers. This guide is designed to get you up and running with recording, replaying, and snapshotting your first MCP session in **under 10 minutes**.

---

## 1. Installation

Install MCP-VCR inside your project virtual environment using poetry or pip:

```bash
# Using poetry (recommended)
poetry add mcp-vcr

# Or using pip
pip install mcp-vcr
```

Verify the installation by running the CLI help:

```bash
mcp-vcr --help
```

---

## 2. Claude Desktop Configuration

To record real interactions between an LLM client (like Claude Desktop) and your MCP server, configure your Claude Desktop configuration file (usually located at `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or `%APPDATA%\Claude\claude_desktop_config.json` on Windows).

Prepend the `mcp-vcr record` command before your server arguments:

```json
{
  "mcpServers": {
    "my-voice-agent": {
      "command": "mcp-vcr",
      "args": [
        "record",
        "--name",
        "voice_session",
        "--",
        "python",
        "/path/to/my_server.py",
        "--port",
        "8080"
      ]
    }
  }
}
```

> [!NOTE]
> The `--` separator is required to clearly demarcate MCP-VCR command flags from the command used to launch your server.

---

## 3. Recording a Session

1. Open (or restart) Claude Desktop.
2. Interact with your server (e.g., call custom tools, list resources).
3. Close Claude Desktop.
4. Locate your recorded session transcript under the default `./sessions` directory:

```bash
ls sessions/
# Output: session_20260518_100000_voice_session.yaml
```

---

## 4. Replaying a Session

To dry-run or verify server behavior offline using the recorded session, run the replay command:

```bash
mcp-vcr replay sessions/session_20260518_100000_voice_session.yaml -- python /path/to/my_server.py
```

MCP-VCR will spin up your server in a subprocess, feed it the recorded client requests (`c2s`), wait for responses (`s2c`), and exit cleanly.

---

## 5. Creating a Golden Snapshot

Golden snapshots serve as normalized regression targets that are stripped of non-deterministic noise (like timestamps, UUIDs, and request IDs).

Create a golden snapshot from your transcript:

```bash
mcp-vcr snapshot sessions/session_20260518_100000_voice_session.yaml
```

This creates a golden file under the `./snapshots` directory:

```bash
ls snapshots/
# Output: voice_session_golden.yaml
```

---

## 6. Verifying for Regressions

Run verification checks in your development workflow or local git pre-commit hooks to ensure code changes have not broken existing behaviors:

```bash
mcp-vcr verify snapshots/ -- python /path/to/my_server.py
```

This command spins up your server, replays the snapshot sequence, and checks for structural, field, and type-level regressions. If regressions are found, the command exits with code `1`, preventing buggy deployments!

---

## Next Steps
- Set up automated regression testing in [CI Integration Guide](ci-integration.md).
- Customize normalizers and redaction rules in [Architecture.md](../Architecture.md).
