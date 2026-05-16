# Transcript Schema v1

This document defines the structure of `mcp-vcr` transcripts for version 1.

## Format: YAML

Transcripts are stored in YAML format to ensure human readability, support for multi-line strings, and clean git diffs.

## Structure

A transcript consists of two main sections: `meta` and `messages`.

### 1. Metadata (`meta`)

The `meta` block contains information about the session recording.

| Field | Type | Required | Description |
|---|---|---|---|
| `version` | Integer | Yes | The transcript format version (current: `1`). |
| `recorded_at` | String (ISO 8601) | Yes | UTC timestamp of when the session started. |
| `session_id` | String | Yes | Random 8-character hex string identifying the session. |
| `server_command` | List[String] | Yes | The command line arguments used to launch the MCP server. |
| `protocol_version` | String | No | The MCP protocol version negotiated during `initialize`. |
| `client_hint` | String | No | Inferred client name (e.g., `claude-desktop`). |
| `schema_version` | String | No | Internal schema version for validation. |

### 2. Messages (`messages`)

The `messages` block is a list of JSON-RPC messages intercepted by the proxy.

| Field | Type | Required | Description |
|---|---|---|---|
| `t` | Integer | Yes | Milliseconds elapsed since the session start. |
| `dir` | String | Yes | Message direction: `c2s` (client to server) or `s2c` (server to client). |
| `payload` | Object | Yes | The actual JSON-RPC 2.0 message payload. |

## Example

```yaml
meta:
  version: 1
  recorded_at: "2026-05-16T14:30:22.471Z"
  session_id: "a3f2b1c9"
  server_command: ["python", "server.py"]
  protocol_version: "2024-11-05"

messages:
  - t: 0
    dir: c2s
    payload:
      id: 1
      jsonrpc: "2.0"
      method: initialize
      params: { ... }
```
