# MCP-VCR 

**Record and replay stdio MCP server conversations. Catch regressions in CI before your users do.**

```bash
# Record a live session from any client
mcp-vcr record -- python my_server.py

# Create a normalized golden snapshot
mcp-vcr snapshot sessions/voice_session.yaml

# Verify your current code against all snapshots
mcp-vcr verify snapshots/ -- python server.py
```

---

## What is MCP-VCR?

`mcp-vcr` is a transparent, developer-first stdio proxy and regression testing framework for [Model Context Protocol (MCP)](https://modelcontextprotocol.org) servers. 

It sits cleanly between an MCP client (such as Claude Desktop, Cursor, Windsurf, or MCP Inspector) and your server, records every JSON-RPC 2.0 exchange in both directions, and saves them as highly human-readable, git-diffable YAML transcripts.

### Key Capabilities

*   **Dual Transports (Stdio & HTTP/SSE)**: Intercept, proxy, and replay messages over standard input/output pipes or HTTP/Server-Sent Events (SSE) networks.
*   **Deterministic & Timing-Faithful Replay**: Replay client requests and match server responses. Optionally sleep for exact recorded intervals (`--timing-faithful`) to validate timing-sensitive logic.
*   **Golden Snapshots**: Strip non-deterministic noise (timestamps, IDs, UUIDs, absolute paths) to establish a byte-identical contract test suite.
*   **In-Flight Redaction**: Recursively inspects and scrubs secrets (keys, bearer tokens, passwords) before writing files to disk.
*   **Streaming Large-Session Support**: Switch format to newline-delimited JSON (`--format ndjson`) to stream massive conversations with minimal memory footprint.
*   **Layered Diff Engines**: Compare schema structures (`--structural`), semantics (`--semantic`), or exact matching (`--strict`).
*   **Pytest Integration**: Build automated test suites with the first-party `pytest-mcp-vcr` plugin.
*   **CI-Native Checking**: Verify server functionality in CI with zero shell injection risks using our official composite GitHub Action.

---

## The Problems It Solves

| Developer Pain | How `mcp-vcr` Fixes It |
| :--- | :--- |
| **Flaky CI Pipelines** | Golden snapshots normalize IDs, UUIDs, and timestamps, preventing false negatives. |
| **Invisible Stdio Streams** | Intercepts, structures, and logs bidirectional traffic with relative millisecond offsets. |
| **Hard-to-Reproduce Bugs** | Capture a failing sequence from Claude Desktop and replay it locally in one command. |
| **Silent API Schema Drift** | Strict structural diffs flag added/removed tools, arguments, or changed types immediately. |
| **Credential Leaks in Git** | Built-in redaction keeps secrets and private absolute filesystem paths out of your codebase. |

---

## Installation

Install `mcp-vcr` inside your virtual environment using poetry or pip:

```bash
# Using poetry (recommended)
poetry add mcp-vcr

# Or using pip
pip install mcp-vcr
```

*Requires Python 3.10+. Zero databases, heavy external services, or cloud setups required.*

---

## Quickstart Guide

### 1. Auto-Generate Snapshots (Instant Onboarding)
Auto-discover all tools from your server and generate a golden snapshot skeleton in seconds:

```bash
# Auto-discover tools and generate golden snapshot (interactive confirmation)
mcp-vcr generate --server "python my_server.py"

# Auto-confirm tool execution
mcp-vcr generate --server "python my_server.py" --yes

# Discovery only (skip tool execution stubs)
mcp-vcr generate --server "python my_server.py" --no-call
```

### 2. Record a Live Session
Run your server through the record proxy. Any standard stdio client interacting with this pipe will be recorded:

```bash
mcp-vcr record --name init_tools_list -- python my_server.py
```
This produces an auto-redacted transcript inside the default `sessions/` directory (which you should add to `.gitignore`).

### 3. Create a Golden Snapshot
Turn a raw recorded session into a normalized, stable regression baseline:

```bash
mcp-vcr snapshot sessions/session_20260518_init_tools_list.yaml
```
This creates a golden snapshot file under the `snapshots/` directory: `snapshots/init_tools_list_golden.yaml`. **Commit this folder to your Git repository.**

### 4. Verify for Regressions in CI
Run the verification command. It replays client traffic from all golden snapshots against your live server code:

```bash
mcp-vcr verify snapshots/ -- python my_server.py
```
*   **Exit code `0`**: All responses match their golden snapshots perfectly.
*   **Exit code `1`**: Regressions or schema mismatches detected. Prints a readable diff to stderr.

### 5. Overwrite Goldens After Intentional Schema Changes
If you intentionally add a new tool or change response formatting, update your golden snapshots using the `--update` flag:

```bash
mcp-vcr verify --update snapshots/ -- python my_server.py
```
Review the changes using `git diff snapshots/` before committing.

---

## Claude Desktop Configuration

To capture real interactions from the Claude Desktop app, add `mcp-vcr` as your server launcher inside `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "mcp-vcr",
      "args": [
        "record",
        "--name",
        "desktop_session",
        "--",
        "python",
        "/absolute/path/to/my_server.py"
      ]
    }
  }
}
```

> [!NOTE]
> The `--` separator is required to clearly segregate MCP-VCR command-line flags from the command used to launch your actual server process.

---

## Redaction & Security

`mcp-vcr` walks JSON-RPC message payloads recursively to redact known secret keys, environment paths, and pattern matches *before* writing transcripts. The client and server receive the raw streams intact; only the stored file is sanitized.

*   **Field Rules**: Fields matching `api_key`, `token`, `secret`, `password`, `credential`, or `authorization` are replaced with `<REDACTED_fieldname>`.
*   **Pattern Rules**: Regex strings (e.g., Bearer tokens, AWS keys, OpenAI `sk-` keys) are scrubbed.
*   **Path Rules**: Absolute filesystem paths (e.g., `/home/username/...`) are replaced with `<PATH>`.

Configure custom rules in your project's `.mcp-vcr.yaml` file:

```yaml
redact:
  fields:
    - secret_config_token
  patterns:
    - "custom-auth-[a-zA-Z0-9]{16}"
  paths: true
```

---

## What Lies Ahead (Roadmap)

We are laser-focused on keeping our core stable and backward-compatible. The following major capabilities are scheduled for the next phases:

### 1. Fuzz Testing Mode
Add `mcp-vcr fuzz` to replay recorded snapshots with randomized mutations (truncated payloads, type confusion, missing required schema fields, and boundary values) to audit server error handling and crash resistance.

### 2. MCP Inspector Integration
Coordinating with the MCP community to support loading `mcp-vcr` transcripts directly into the official MCP Inspector web interface for timeline visualization, message inspection, and interactive debugging.

### 3. Compatibility Matrix Report
Record and diff identical session exchanges across major MCP client runtimes (Claude Desktop, Cursor, Windsurf, etc.) to generate a public compatibility matrix, letting authors prove their servers work flawlessly across the client ecosystem.

---

## Design Principles

*   **Local-First & Offline**: Zero telemetry, zero external database integrations, and zero mandatory cloud services. Your tests run fast and offline.
*   **Protocol-Transparent**: The proxy acts as a silent observer. It never intercepts, manipulates, or alters protocol-level capability negotiations between the client and server.
*   **Git-Friendly Transcripts**: Structured YAML outputs with stable key ordering, deterministic naming, and deep secret scrubbing are designed to be checked in alongside your code.

---

## Developer Resources

*   [Developer Getting Started Guide](docs/getting-started.md) (onboarding in under 10 minutes)
*   [CI/CD Pipeline Integration Guide](docs/ci-integration.md) (setup for GitHub Actions & GitLab CI/CD)
*   [Internal Architecture & Design Internals](Architecture.md) (deep-dive on proxy buffers and the normalization pipeline)
*   [Pytest Integration Guide](docs/pytest-integration.md)

---

## License

This project is licensed under the [MIT License](LICENSE).
