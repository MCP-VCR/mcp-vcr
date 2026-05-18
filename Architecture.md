# Architecture

This document describes the internal design of `mcp-vcr`: how the proxy works, how transcripts are structured, how replay is implemented, and where the interesting edge cases are.

---

## Table of contents

1. [Overview](#overview)
2. [Process model](#process-model)
3. [Transport layer and framing](#transport-layer-and-framing)
4. [Message interceptor](#message-interceptor)
5. [Transcript recorder & Schema Versioning](#transcript-recorder-schema-versioning)
6. [Replay engine](#replay-engine)
7. [Diff engine](#diff-engine)
8. [Redaction layer](#redaction-layer)
9. [Normalization Chain](#normalization-chain)
10. [Session identity and naming](#session-identity-and-naming)
11. [Error handling and failure modes](#error-handling-and-failure-modes)
12. [Future work](#future-work)

---

## Overview

`mcp-vcr` is a transparent stdio proxy. It sits in the stdio pipe between an MCP client and an MCP server, observes every JSON-RPC message in both directions, and writes them to a YAML transcript.

```
MCP Client
(Claude Desktop / Cursor / Windsurf / Inspector)
    │
    │  stdin/stdout  (client writes here, reads here)
    ▼
┌──────────────────────────────┐
│         mcp-vcr proxy        │
│                              │
│  ┌────────────────────────┐  │
│  │  Transport Interceptor │  │
│  └────────────┬───────────┘  │
│               │              │
│  ┌────────────▼───────────┐  │
│  │   Message Interceptor  │  │
│  └────────────┬───────────┘  │
│               │              │
│  ┌────────────▼───────────┐  │
│  │  Transcript Recorder   │  │
│  └────────────────────────┘  │
└──────────────┬───────────────┘
               │
               │  stdin/stdout  (proxy forwards to real server)
               ▼
       Actual MCP Server
       (subprocess)
```

In record mode, the proxy forwards all messages unchanged and writes them to a transcript. In replay mode, no real server is involved — the proxy reads client messages from the transcript and feeds pre-recorded server responses back.

The proxy is **protocol-transparent**: it never modifies message content, alters capability negotiation, or inject additional protocol behavior. It only observes.

---

## Process model

### Record mode

```
mcp-vcr record -- python server.py
```

1. `mcp-vcr` starts as a process. The MCP client is configured to launch `mcp-vcr` as its server.
2. `mcp-vcr` launches `python server.py` as a subprocess, using `asyncio.create_subprocess_exec` with `stdin=PIPE, stdout=PIPE, stderr=PIPE`.
3. Two async tasks run concurrently:
   - **c2s pump**: reads from `sys.stdin`, writes to `server.stdin`, passes message to interceptor
   - **s2c pump**: reads from `server.stdout`, writes to `sys.stdout`, passes message to interceptor
4. Both pumps terminate when either pipe closes (EOF), which triggers graceful shutdown.
5. The subprocess receives all signals forwarded from the parent (SIGTERM, SIGINT).

```python
async def record(server_args: list[str], output_path: Path) -> None:
    server = await asyncio.create_subprocess_exec(
        *server_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    recorder = TranscriptRecorder(output_path)
    await asyncio.gather(
        pump_c2s(sys.stdin.buffer, server.stdin, recorder),
        pump_s2c(server.stdout, sys.stdout.buffer, recorder),
        pump_stderr(server.stderr, sys.stderr.buffer),
    )
```

### Replay mode

```
mcp-vcr replay session.yaml -- python server.py
```

1. `mcp-vcr` loads the transcript.
2. `mcp-vcr` launches the server subprocess (same as record mode).
3. Instead of reading from `sys.stdin`, the **replay engine** reads client-direction messages (`dir: c2s`) from the transcript in order and writes them to `server.stdin`.
4. The proxy reads server responses from `server.stdout` and writes them to `sys.stdout` (if a real client is connected) and to a new transcript (for diffing).
5. Replay does not enforce timing by default. Messages are sent as fast as the server responds to each one.

### Check mode

`check` is replay mode with an implicit diff at the end. The exit code is 0 if responses match the recorded transcript (within the configured diff mode), 1 if any responses differ.

---

## Transport layer and framing

MCP over stdio uses **newline-delimited JSON** (NDJSON). Each message is a single JSON object terminated by `\n`. There is no length prefix or envelope framing.

This sounds simple but has practical complications:

### Partial reads

`asyncio`'s `StreamReader.readline()` handles this correctly — it buffers internally and only returns when `\n` is seen. However, the underlying pipe might deliver data in arbitrary chunks. The implementation must not use `read(n)` with a fixed buffer size.

**Correct approach:**

```python
async def read_messages(reader: asyncio.StreamReader):
    async for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            yield message
        except json.JSONDecodeError as e:
            # Log and skip malformed lines — never crash the proxy
            log.warning(f"Malformed JSON line: {e}")
```

### Large messages

Tool responses can be large (base64-encoded images, large text blobs). `asyncio.StreamReader` has a default buffer limit of 64KB. This must be raised:

```python
reader = asyncio.StreamReader(limit=16 * 1024 * 1024)  # 16MB
```

### Notifications vs requests vs responses

JSON-RPC has three message types:

| Type | Identifying field | Direction |
|---|---|---|
| Request | has `id` and `method` | c2s or s2c |
| Response | has `id`, no `method` | opposite of its request |
| Notification | has `method`, no `id` | c2s or s2c |

The interceptor records all three. The diff engine treats them differently — notifications are typically excluded from response diffing since they don't have a paired response.

### stderr handling

The server's stderr is forwarded to the proxy's own stderr unchanged. It is not recorded in transcripts. This is intentional — stderr is for server-internal logs, not protocol traffic.

---

## Message interceptor

The interceptor receives every parsed JSON-RPC message from both pumps and is responsible for:

1. **Timestamping** — recording milliseconds since session start
2. **Direction tagging** — `c2s` or `s2c`
3. **Routing to recorder** — passing to `TranscriptRecorder`
4. **Routing to redactor** — applying redaction before recording

The interceptor does NOT:

- Modify the message being forwarded to the other side
- Block the pipe while writing the transcript
- Alter ordering

```python
@dataclass
class InterceptedMessage:
    t: int          # ms since session start
    dir: Direction  # c2s | s2c
    payload: dict

class MessageInterceptor:
    def __init__(self, recorder: TranscriptRecorder, redactor: Redactor):
        self._t0 = time.monotonic()
        self._recorder = recorder
        self._redactor = redactor

    def observe(self, payload: dict, direction: Direction) -> None:
        t = int((time.monotonic() - self._t0) * 1000)
        redacted = self._redactor.apply(payload)
        msg = InterceptedMessage(t=t, dir=direction, payload=redacted)
        # Fire-and-forget: don't block the pipe pump
        asyncio.create_task(self._recorder.write(msg))
```

The `asyncio.create_task` call is important — transcript writing must not block the pipe pumps. A slow disk write should never introduce latency visible to the client or server.

---

## Transcript recorder & Schema Versioning

The recorder serializes `InterceptedMessage` objects to YAML and writes them to disk.

### Format decisions

**YAML over JSON** because:
- Multi-line string values (tool descriptions, prompt text) are readable with YAML block scalars
- Comments are possible (though not generated by the tool, they're useful for hand-annotated fixtures)
- Diffs in git are cleaner for nested structures

**Streaming writes** — messages are appended as they arrive, not buffered and written at session end. This means a partial transcript exists on disk even if the proxy crashes mid-session. The file is a valid YAML stream (sequence of documents) up to the point of interruption.

**Stable key ordering** — `yaml.dump` with `sort_keys=True` ensures consistent field ordering for deterministic diffs across sessions.

### Transcript structure

```yaml
meta:
  version: 1                                  # Transcript format version
  recorded_at: "2024-01-15T14:30:22.471Z"   # ISO 8601 UTC
  session_id: "a3f2b1c9"                      # 8-char hex, random
  server_command: ["python", "my_server.py"]
  protocol_version: "2024-11-05"              # from initialize result
  client_hint: "claude-desktop"               # inferred from clientInfo.name
  schema_version: "1.0"

messages:
  - t: 0
    dir: c2s
    payload:
      id: 1
      jsonrpc: "2.0"
      method: initialize
      params: { ... }

  - t: 47
    dir: s2c
    payload:
      id: 1
      jsonrpc: "2.0"
      result: { ... }
```

`meta.protocol_version` and `meta.client_hint` are populated lazily — the recorder inspects the `initialize` exchange after the fact and backfills these fields before closing the file.

### Schema Versioning & Backward Compatibility

To maintain long-term usability of transcript test suites:
- **Strict v1 Schema Validation**: The `validate` CLI subcommand enforces strict conformance against the v1 JSON Schema. New recordings are marked with `meta.version: 1`.
- **Legacy v0 Support**: Transcripts lacking a version or marked with `version: 0` are treated as legacy transcripts. When loaded for replay, diff, or snapshot creation, they trigger a deprecation warning, automatically backfill missing metadata fields, and proceed successfully.
- **Strict Checking on Demand**: In pipeline checks and schema validation tests, the parser exposes a strict mode (`allow_v0=False`) that rejects legacy structures to ensure golden transcripts remain clean and modern.

### File naming

Default: `sessions/session_<date>_<time>_<id>.yaml`

Example: `sessions/session_20240115_143022_a3f2b1c9.yaml`

When `--name` is provided: `sessions/<name>.yaml`

The session ID is a random 8-character hex string, not a content hash. This is intentional — two sessions with identical traffic should produce separate files (they represent different points in time).

---

## Replay engine

The replay engine reads a transcript, extracts all `c2s` messages in order, and feeds them into the server subprocess.

### Basic replay loop

```python
async def replay(transcript_path: Path, server_args: list[str]) -> None:
    transcript = load_transcript(transcript_path)
    server = await asyncio.create_subprocess_exec(
        *server_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    client_messages = [m for m in transcript.messages if m.dir == Direction.C2S]
    response_recorder = TranscriptRecorder(derive_output_path(transcript_path))

    for msg in client_messages:
        line = json.dumps(msg.payload, sort_keys=True) + "\n"
        server.stdin.write(line.encode())
        await server.stdin.drain()

        response_line = await server.stdout.readline()
        response = json.loads(response_line)
        response_recorder.write(InterceptedMessage(
            t=..., dir=Direction.S2C, payload=response
        ))

    server.stdin.close()
    await server.wait()
```

### Notification handling

Notifications (no `id`) don't have a paired response. The replay engine sends them and does not wait for a response before sending the next message. 

This creates a subtle ordering problem: if a notification is followed by a request, the server might process them in either order. The engine handles this with a short configurable settle delay after notifications (default 50ms).

### Timeout handling

Each request waits for a response with a configurable timeout (default 5000ms). If the server doesn't respond in time, the replay is considered failed and the session is written with an error marker.

### Timing-agnostic vs timing-faithful

The current implementation is **timing-agnostic**: messages are sent as fast as the server responds, ignoring the `t` values in the transcript.

**Timing-faithful replay** (planned) would insert `asyncio.sleep` delays based on the `t` delta between consecutive c2s messages. This matters for servers with timeout-sensitive logic (e.g., a server that expects a follow-up request within N seconds). It is a future feature because most servers are stateless between requests and timing doesn't affect behavior.

---

## Diff engine

The diff engine compares two sets of server responses and reports structural differences.

### Input

Two transcripts (or two replay outputs). The diff engine extracts all `s2c` response messages (not notifications) and pairs them by `id`.

### Pairing strategy

Responses are paired by their JSON-RPC `id` field. If the same `id` appears in both transcripts, they're compared. If an `id` appears in one but not the other, it's reported as added/removed.

### Diff modes

**Structural (default)**

Compares field presence, types, and array lengths. Does not compare leaf values. Useful for detecting:

- New or removed capabilities in `initialize` results
- New or removed tools in `tools/list` responses
- Schema changes in tool `inputSchema`
- Added or removed required fields

Does not flag: a tool description changing from "Does X" to "Does X and Y".

**Semantic**

Extends structural to compare leaf values, but with configurable tolerance for fields that are expected to vary (timestamps, session IDs, cursor values). Configured via `ignore_fields` in `.mcp-vcr.yaml`.

**Strict**

Byte-level comparison after re-serializing both sides with `json.dumps(sort_keys=True)`. Appropriate for deterministic servers in CI where any change is unexpected.

### Diff output

Text format (default):

```diff
  initialize response (id=1):
    result.capabilities:
+     resources: { subscribe: true }

  tools/list response (id=2):
    result.tools[2]:
+     name: "new_tool"
      inputSchema.properties:
-       query: { type: "string" }
+       query: { type: "string", minLength: 1 }
```

JSON format (for programmatic use):

```json
{
  "changes": [
    {
      "request_id": 2,
      "method": "tools/list",
      "path": "result.tools[2].inputSchema.properties.query.minLength",
      "type": "added",
      "value": 1
    }
  ]
}
```

GitHub format: uses `::error` and `::warning` annotations for inline PR comments.

---

## Redaction layer

Redaction runs on every message **before** it is passed to the recorder. The original (unredacted) message is forwarded to the pipe unchanged — redaction only affects what's written to disk.

### Redaction strategy

The redactor walks the message payload recursively and applies rules in order:

1. **Field name rules** — if a key matches a configured field name (case-insensitive), its value is replaced with `<REDACTED_fieldname>`
2. **Pattern rules** — if a string value matches a configured regex pattern, it is replaced with `<REDACTED>`
3. **Path rules** — absolute filesystem paths (`/home/...`, `/Users/...`, `C:\...`) are replaced with `<PATH>`

Default field names: `token`, `api_key`, `secret`, `password`, `credential`, `authorization`, `bearer`

Default patterns:
```
sk-[a-zA-Z0-9]{20,}           # OpenAI-style keys
[A-Z0-9]{20}:[a-zA-Z0-9+/]{40}  # AWS-style keys
Bearer [a-zA-Z0-9\-._~+/]+=*  # Bearer tokens
```

### Redaction is lossy by design

Redacted transcripts cannot be "unredacted" — that's the point. If a transcript needs to contain real credentials to be replayable against a live service, users must use `--no-redact` explicitly and manage the file accordingly (e.g., gitignore, vault).

### Redaction in replay

When replaying a redacted transcript, the `<REDACTED_*>` placeholders are sent as-is to the server. This means replay against a server that validates credentials will fail. This is expected behavior — replay is for testing server logic, not authenticated end-to-end flows. For authenticated replay, users should use `--no-redact` during recording and manage the transcript as a secret.

---

## Normalization Chain

The normalization layer strips transcripts of non-deterministic, session-specific data to create reproducible, byte-identical "golden snapshots". This allows you to verify that server code changes did not cause regressions, without test suite noise.

### Composing without Mutation

To ensure thread safety and predictability, all normalizers operate recursively on deep copies of message payloads. The pipeline implements a composable protocol with a list of discrete steps, avoiding in-place mutations.

### Sequential Normalizers

MCP-VCR features five standard sequential normalizers:

1. **Timestamp Normalizer (`timestamps`)**: Regex-detects ISO 8601 strings in any leaf values and replaces them with `"NORM_TIMESTAMP"`.
2. **Request ID Normalizer (`request_ids`)**: Tracks JSON-RPC request and response `id` fields (integers or strings) dynamically, mapping them to stable sequential counters (`"NORM_ID_1"`, `"NORM_ID_2"`, ...).
3. **UUID Normalizer (`uuids`)**: Identifies standard random UUID v4 values within string payloads and replaces them with stable identifiers (`"NORM_UUID_1"`, `"NORM_UUID_2"`, ...).
4. **Cursor Normalizer (`cursors`)**: Scans for cursor fields (e.g., `cursor`, `nextCursor`, `pageToken`, `continuationToken`) and replaces dynamic pagination tokens with `"NORM_CURSOR"`.
5. **Path Normalizer (`paths`)**: Identifies absolute filesystem paths and normalizes them to prevent absolute local paths from breaking tests in other developer machines or CI environments.

### Toggle Configuration

You can fully customize which normalizers are active by configuring your project's `.mcp-vcr.yaml` file:

```yaml
normalize:
  timestamps: true
  request_ids: true
  uuids: true
  cursors: true
```

If a configuration key is omitted or invalid, normalizers default to enabled, ensuring robust and high-quality regression detection out of the box.

---

## Session identity and naming

Sessions are identified by a random 8-character hex ID, not by a hash of their content. This is intentional.

**Why not content-hash?** Two sessions with identical traffic are still meaningfully different (different time, possibly different client version). A content hash would silently deduplicate them, which is wrong.

**Why 8 hex characters?** It's short enough to be memorable, long enough that collisions are negligible for normal usage volumes (65536 possibilities before collision probability exceeds 1%).

**Session lookup** (planned): `mcp-vcr list` will show sessions sorted by date with their `client_hint`, `protocol_version`, and message count. `mcp-vcr inspect <id-prefix>` will open the session with that ID prefix (like `git show`).

---

## Error handling and failure modes

### Server crash during record

If the server subprocess exits unexpectedly, the s2c pump receives EOF. The proxy:

1. Flushes the current transcript (partial is valid — all messages up to the crash are recorded)
2. Forwards the EOF to the client's stdout
3. Exits with the server's exit code

### Server crash during replay

Same behavior. The replay session is marked as incomplete. `check` mode treats an incomplete replay as a failure.

### Malformed JSON from server

The proxy logs the raw line and a warning, skips the message (does not record it), and continues. It does not crash. This is intentional — a server that emits occasional malformed output should still produce a useful partial transcript.

### Client disconnect

If the client closes stdin, the c2s pump receives EOF. The proxy sends a `shutdown` notification to the server (if the server is still running), waits for it to exit gracefully, then exits.

### Pipe buffer pressure

On some platforms, writing to stdout while the client isn't reading can block the s2c pump. The proxy uses `asyncio` throughout, so the c2s pump continues running while the s2c pump is blocked — the server can continue accepting requests. However, if the client is completely unresponsive, both pumps will eventually back up. This is a known limitation with no clean solution at the proxy layer; it is the same behavior the client would see without the proxy.

---

## Future work

### Rust transport core

The current Python asyncio implementation is correct and fast enough for development use. For high-throughput scenarios (large binary responses, many concurrent sessions), the transport layer (framing, pipe pumps) could be rewritten in Rust and exposed via PyO3. The Python recorder and diff engine would remain Python.

### Timing-faithful replay

See [Replay engine](#replay-engine). Implementation would insert `asyncio.sleep(delta_ms / 1000)` between c2s messages using the `t` values from the transcript.

### Fuzz mode

`mcp-vcr fuzz` would replay a transcript with mutations applied to client messages — truncated payloads, missing required fields, wrong types, oversized values. The goal is to probe server error handling and find crashes or protocol violations. Mutation strategies: field removal, type confusion, boundary values, and random byte injection.

### Inspector integration

MCP Inspector is the official debugging UI for MCP servers. An integration would allow Inspector to load `mcp-vcr` transcripts directly as a session replay, enabling timeline visualization and message inspection in the existing UI.

### Compatibility matrix

Record sessions against the same server from multiple clients (Claude Desktop, Cursor, Windsurf). Diff the sessions to produce a matrix showing which clients trigger which server behaviors. Publish the matrix as a static site. This is the highest-leverage community artifact `mcp-vcr` can produce.

```
                | Claude Desktop | Cursor | Windsurf | Inspector
----------------|----------------|--------|----------|----------
initialize      |       ✓        |   ✓    |    ✓     |    ✓
tools/list      |       ✓        |   ✓    |    ✓     |    ✓
tools/call      |       ✓        |   ✓    |    ~     |    ✓
resources/list  |       ✓        |   ~    |    ✗     |    ✓
prompts/list    |       ~        |   ✗    |    ✗     |    ✓
```

This matrix would be a community resource for every MCP server author.