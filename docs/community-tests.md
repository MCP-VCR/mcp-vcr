# Community Test Collections

MCP-VCR ships with curated, ready-to-run contract test suites for popular reference Model Context Protocol (MCP) servers.

These suites allow server authors and consumers to immediately test their servers against standardized MCP protocol transcripts without needing to write tests from scratch.

---

## Available Community Suites

| Suite Name | Target Server Reference | Description |
|---|---|---|
| `filesystem` | `@modelcontextprotocol/server-filesystem` | Contract tests for directory listing, file read/write, and search |
| `memory` | `@modelcontextprotocol/server-memory` | Contract tests for knowledge graph entities, relations, and retrieval |
| `time` | `mcp-server-time` | Contract tests for time retrieval and timezone conversion |

---

## Running Community Test Suites

### 1. List Available Suites

Use `--list-suites` to discover all bundled suites:

```bash
mcp-vcr test --list-suites
```

For machine-readable JSON output:

```bash
mcp-vcr test --list-suites --json
```

### 2. Run a Suite Against Your Server

Pass the suite name with `--suite <name>` and your server command after `--`:

```bash
# Filesystem server
mcp-vcr test --suite filesystem -- npx @modelcontextprotocol/server-filesystem /tmp

# Memory server
mcp-vcr test --suite memory -- npx @modelcontextprotocol/server-memory

# Time server
mcp-vcr test --suite time -- uvx --with 'mcp>=1.23,<2' mcp-server-time

# Local custom Python server
mcp-vcr test --suite filesystem -- python my_filesystem_server.py /tmp
```

### 3. Verification Modes & Flags

- `--diff-mode [structural|semantic|strict]`: Set diff verification strictness (default: `structural`).
  - `structural`: Verifies message shapes, field types, and tool presence without failing on dynamic values.
  - `semantic`: Compares exact values while respecting normalization and `ignore_fields` rules.
  - `strict`: Exact byte-identical JSON-RPC payload matching.
- `--timeout <ms>`: Per-request timeout in milliseconds (default: 10000).
- `--timing-faithful`: Simulates recorded inter-message delays.
- `--json`: Output structured JSON envelope for CI/CD pipelines and reporting.

---

## CI / CD Integration

Run contract tests in GitHub Actions or any CI runner:

```yaml
- name: Run MCP Contract Tests
  run: |
    mcp-vcr test --suite filesystem --json -- npx @modelcontextprotocol/server-filesystem /tmp
```

When run with `--json`, `mcp-vcr test` outputs a structured envelope:

```json
{
  "status": "ok",
  "command": "test",
  "suite": "filesystem",
  "results": [
    {
      "transcript": "initialize_and_tools_list.yaml",
      "status": "pass",
      "message": "Passed (structural match)",
      "diff": null
    },
    {
      "transcript": "tool_call_read_file.yaml",
      "status": "pass",
      "message": "Passed (structural match)",
      "diff": null
    },
    {
      "transcript": "tool_call_list_directory.yaml",
      "status": "pass",
      "message": "Passed (structural match)",
      "diff": null
    }
  ],
  "summary": {
    "total": 3,
    "passed": 3,
    "failed": 0,
    "skipped": 0
  }
}
```

---

## Authoring Custom Test Suites

You can author custom test suites using the same format as bundled community suites.

### Suite Directory Structure

```text
my_custom_suites/
└── postgres/
    ├── suite.yaml
    ├── initialize_and_tools_list.yaml
    └── tool_call_query.yaml
```

### Manifest Schema (`suite.yaml`)

```yaml
name: postgres
description: "Contract tests for custom PostgreSQL MCP server."
server_package: "mcp-server-postgres"
protocol_version: "2024-11-05"
transport: stdio
tags: ["database", "sql"]
ignore_fields:
  - "result.content[0].text"
transcripts:
  - initialize_and_tools_list.yaml
  - tool_call_query.yaml
```

### Running Custom Suites

Point `mcp-vcr test` to your custom suites directory via `--suites-dir`:

```bash
mcp-vcr test --suites-dir my_custom_suites/ --suite postgres -- python server.py
```
