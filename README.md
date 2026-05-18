# mcp-vcr

**Record and replay stdio MCP server conversations. Catch regressions before your users do.**

```
mcp-vcr record -- python my_server.py
mcp-vcr replay sessions/init_001.yaml -- python my_server.py
```

---

## What is this?

`mcp-vcr` is a transparent stdio proxy and replay framework for [MCP](https://modelcontextprotocol.org) servers.

It sits between an MCP client (Claude Desktop, Cursor, Windsurf, Inspector) and your server, records every JSON-RPC message in both directions, and stores them as human-readable YAML transcripts.

**Use cases:**

- **Regression tests** — replay them against a new version of your server and diff the responses
- **Bug reproduction cases** — capture the exact sequence that triggered a failure and share it
- **CI fixtures** — run integration tests without a live client
- **Protocol audit logs** — understand exactly what your server is doing during a session

Think of it as `vcrpy` or Ruby's VCR gem, but for MCP stdio traffic.

---

## The problem it solves

| Pain | What mcp-vcr does |
|---|---|
| "Works in Inspector but breaks in Claude Desktop" | Record real sessions from each client; replay and compare |
| Stdio traffic is invisible during debugging | Transparent interception with timestamped transcripts |
| Can't reproduce that user-reported bug | Deterministic replay from a saved session |
| Protocol regressions ship silently | Response diffing between old and new server versions |
| Integration tests require a running client | Fixture-based replay with no client needed |
| Transcripts contain secrets | Automatic redaction before storage |

---

## Installation

```bash
pip install mcp-vcr
```

Requires Python 3.10+. No additional services, databases, or cloud accounts required.

---

## Quickstart

### Record a session

```bash
mcp-vcr record -- python my_server.py
```

Point your MCP client (Claude Desktop, Cursor, etc.) at `mcp-vcr` as the server command. All traffic is recorded and written to `sessions/` as a YAML transcript.

```yaml
# sessions/session_20240115_143022.yaml
meta:
  recorded_at: "2024-01-15T14:30:22Z"
  server_command: ["python", "my_server.py"]
  session_id: "a3f2b1c9"

messages:
  - t: 0
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 1
      method: initialize
      params:
        protocolVersion: "2024-11-05"
        capabilities:
          roots: { listChanged: true }
        clientInfo:
          name: "claude-desktop"
          version: "0.10.0"

  - t: 47
    dir: s2c
    payload:
      jsonrpc: "2.0"
      id: 1
      result:
        protocolVersion: "2024-11-05"
        capabilities:
          tools: {}
        serverInfo:
          name: "my-server"
          version: "1.0.0"

  - t: 83
    dir: c2s
    payload:
      jsonrpc: "2.0"
      id: 2
      method: tools/list
      params: {}
```

### Replay against your server

```bash
mcp-vcr replay sessions/session_20240115_143022.yaml -- python my_server.py
```

`mcp-vcr` feeds the recorded client messages into your server in order and captures the responses.

### Diff responses

```bash
mcp-vcr diff \
  sessions/session_20240115_143022.yaml \
  sessions/session_20240116_091500.yaml
```

Output:

```diff
  tools/list response:
    tools[2]:
+     name: "new_tool"
+     description: "Does a new thing"
      inputSchema:
-       required: ["query"]
+       required: ["query", "limit"]
```

### Run as a regression test

```bash
mcp-vcr check sessions/session_20240115_143022.yaml -- python my_server.py
# exit 0 = no regressions
# exit 1 = responses differ (diff printed to stderr)
```

Plug directly into CI:

```yaml
# .github/workflows/mcp-regression.yml
- name: MCP regression check
  run: mcp-vcr check sessions/*.yaml -- python my_server.py
```

For integrating regression tests with `pytest`, see the [Pytest Integration Guide](docs/pytest-integration.md).

---

## Golden Snapshots

To establish stable contract baselines (ignoring timestamps, UUIDs, request IDs, and cursors), use the golden snapshot workflow:

```bash
mcp-vcr snapshot sessions/my_session.yaml
# Generates normalized golden file: snapshots/my_session_golden.yaml
```

To verify regressions against all golden snapshots (e.g. in CI):
```bash
mcp-vcr verify snapshots/ -- python my_server.py
```

To intentionally overwrite goldens when making server changes:
```bash
mcp-vcr verify --update snapshots/ -- python my_server.py
```

> [!IMPORTANT]
> The `snapshots/` directory contains normalized, stable, and redacted regression baselines. It **should be committed to git** (unlike the `sessions/` directory, which is gitignored).

---

## Claude Desktop integration

Add `mcp-vcr` as the server command in your Claude Desktop config:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "mcp-vcr",
      "args": ["record", "--", "python", "/path/to/my_server.py"],
      "env": {}
    }
  }
}
```

Every Claude Desktop session is now automatically recorded. Switch to `replay` to run tests without a live server.

---

## Redaction

By default, `mcp-vcr` redacts known secret patterns before writing transcripts. This makes sessions safe to commit to version control and share with collaborators.

**Default redaction targets:**

- `Authorization` header values
- Fields named `token`, `api_key`, `secret`, `password`, `credential`
- Values matching common secret patterns (Bearer tokens, AWS keys, etc.)
- Absolute filesystem paths (replaced with `<PATH>`)

**Custom redaction rules:**

```yaml
# .mcp-vcr.yaml
redact:
  fields:
    - my_custom_token
    - internal_user_id
  patterns:
    - "sk-[a-zA-Z0-9]{32,}"
  paths: true
```

Redacted values appear as `<REDACTED_field_name>` in transcripts.

---

## Transcript format

Transcripts are designed to be human-readable, diffable, and git-friendly.

```yaml
meta:
  recorded_at: "2024-01-15T14:30:22Z"
  server_command: ["python", "my_server.py"]
  session_id: "a3f2b1c9"
  client_hint: "claude-desktop"   # inferred from initialize params
  protocol_version: "2024-11-05"

messages:
  - t: 0          # milliseconds since session start
    dir: c2s       # c2s = client→server, s2c = server→client
    payload: { ... }

  - t: 47
    dir: s2c
    payload: { ... }
```

**Timestamps** record relative timing from session start. Replay is timing-agnostic by default (messages are fed as fast as the server responds). Timing-faithful replay is a planned future feature.

---

## Diff modes

| Mode | What it checks |
|---|---|
| `--structural` (default) | Field presence and type — catches capability changes and schema drift |
| `--semantic` | Values within expected variance — useful for non-deterministic servers |
| `--strict` | Exact byte-level match — useful for deterministic servers in CI |

```bash
mcp-vcr diff --structural old.yaml new.yaml
mcp-vcr diff --strict fixture.yaml current.yaml
```

---

## CLI reference

```
mcp-vcr record [OPTIONS] -- <server_command>
mcp-vcr replay <session> [OPTIONS] -- <server_command>
mcp-vcr diff [OPTIONS] <session_a> <session_b>
mcp-vcr check <session_glob> [OPTIONS] -- <server_command>
mcp-vcr redact <session> [OPTIONS]
mcp-vcr inspect <session>
```

**`record` options:**

| Flag | Default | Description |
|---|---|---|
| `--output`, `-o` | `sessions/` | Directory or file path for transcript |
| `--name` | auto-timestamped | Session name |
| `--no-redact` | false | Disable automatic redaction |
| `--config` | `.mcp-vcr.yaml` | Path to config file |

**`replay` options:**

| Flag | Default | Description |
|---|---|---|
| `--timeout` | `5000` | ms to wait for each server response |
| `--strict` | false | Fail if server response differs from recorded |

**`diff` options:**

| Flag | Default | Description |
|---|---|---|
| `--mode` | `structural` | `structural`, `semantic`, or `strict` |
| `--ignore` | none | Comma-separated field paths to ignore |
| `--format` | `text` | `text`, `json`, or `github` |

---

## Configuration

```yaml
# .mcp-vcr.yaml
sessions_dir: sessions/
default_diff_mode: structural

redact:
  fields: []
  patterns: []
  paths: true

record:
  auto_name: true       # use timestamp-based names

replay:
  timeout_ms: 5000
  fail_on_diff: false   # set true for CI

ignore_fields:          # always skip in diffs
  - "$.result.serverInfo.version"
```

---

## Release Timeline & Status

**Current Status: Phase 3 Complete ✓ (Transcript Schema v1)**

You've successfully completed the core recording, replay, and transcript infrastructure. The foundation is solid.

### Phase Breakdown & Release Dates

| Phase | Status | Timeline | Deliverables |
|---|---|---|---|
| **1. Transport & Proxy Core** | ✓ | Weeks 1–2 | asyncio subprocess launcher, bidirectional pipes, stderr forwarding, large message buffering, graceful error handling |
| **2. Message Interceptor & Timestamps** | ✓ | Weeks 2–3 | Monotonic timestamps, direction tagging (c2s/s2c), JSON-RPC message classification, non-blocking recorder |
| **3. Transcript Schema v1** | ✓ | Weeks 3–4 | YAML schema, streaming writes, stable key ordering, lazy metadata backfill, session ID generation, JSON Schema validator, `mcp-vcr validate` |
| **4. Redaction Layer** | 🔄 In Progress | Week 4 | Field-name redaction, regex patterns, path redaction, config-driven rules |
| **5. Normalization Layer** | 📋 Planned | Weeks 5–6 | Timestamp canonicalization, UUID masking, request ID replacement, cursor token normalization |
| **6. Replay Engine** | 📋 Planned | Weeks 6–7 | Deterministic replay loop, notification handling, per-request timeouts, incomplete replay markers |
| **7. Diff Engine** | 📋 Planned | Weeks 7–8 | Response pairing, structural/semantic/strict modes, text/JSON/GitHub annotations formats |
| **8. Golden Snapshot Testing** | 📋 Planned | Weeks 8–9 | `mcp-vcr snapshot`, storage conventions, `mcp-vcr verify`, snapshot updates, GitHub Actions examples |
| **9. CLI Polish** | 📋 Planned | Weeks 10–11 | Full flag coverage, help text, error messages, `mcp-vcr list` and `mcp-vcr inspect` |
| **10. Tests & Documentation** | 📋 Planned | Weeks 11–12 | Sample fixtures, unit tests (80%+ coverage), Getting Started, CI guide, ARCHITECTURE updates |

### Release Milestones

| Release | ETA | What's Included | Best For |
|---|---|---|---|
| **v0.1.0** | **Late May 2026** | Phases 1–3: Core recording/replay, YAML transcripts, manual secret redaction | Recording sessions locally; early adopters testing multiple clients |
| **v0.2.0** | **Late June 2026** | Phases 4–8: Automatic redaction, normalization, full replay/diff, golden snapshots, GitHub Actions integration | **Production teams**: CI/CD pipelines, regression testing in CI, snapshot-driven workflows |
| **v0.3.0** | **Late July 2026** | Phases 9–10: Polished CLI, comprehensive tests (80%+ coverage), full documentation, inspection tools | Full-scale team adoption; mature, documented testing framework |

### When Will It Be Useful?

| Timeframe | Capability | Recommended For |
|---|---|---|
| **Now** | Manual recording & replay | Solo developers testing locally |
| **v0.1.0 (late May)** | Working tool; basic regression detection | Early adopters; manual CI integration |
| **v0.2.0 (late June)** | **Production-ready** | Teams shipping to CI; security-conscious projects (auto-redaction); multi-client testing |
| **v0.3.0 (late July)** | Mature & polished | Organization-wide adoption; large teams |

### Success Criteria for v0.2.0 (Production Release)

- [x] Versioned transcript schema v1 is locked
- [ ] Normalization is built-in and documented
- [ ] Golden snapshot testing works end-to-end in CI
- [ ] Core modules have >80% test coverage
- [ ] Getting Started guide complete
- [ ] v0.2 released and used by ≥1 external project

---

## Roadmap

### v0.1 — MVP (May 2026)
- [x] stdio interception and subprocess management
- [x] bidirectional transcript recording
- [x] YAML transcript format
- [x] deterministic replay
- [x] structural JSON diff
- [x] automatic secret redaction

### v0.2 — CI integration (June 2026)
- [ ] `check` command with exit codes
- [ ] GitHub Actions output format
- [ ] transcript normalization (stable ordering for diffing)
- [ ] golden snapshot workflow (`mcp-vcr snapshot update`)

### v0.3 — Tooling (July 2026)
- [ ] MCP Inspector integration
- [ ] `inspect` TUI for timeline visualization
- [ ] timing-faithful replay mode
- [ ] fuzz mode (inject malformed messages, observe server behavior)

### v0.4 — Compatibility matrix (Q3+ 2026)
- [ ] multi-client recording (same server, different clients)
- [ ] automated compatibility report across client/server pairs
- [ ] public matrix publishing (opt-in)

---

## How it compares

| | mcp-vcr | Manual logging | MCP Inspector | Custom test harness |
|---|---|---|---|---|
| Records real client sessions | ✓ | ✗ | partial | ✗ |
| Protocol-transparent | ✓ | — | ✓ | depends |
| Git-friendly transcripts | ✓ | ✗ | ✗ | depends |
| Deterministic replay | ✓ | ✗ | ✗ | ✗ |
| Response diffing | ✓ | ✗ | ✗ | manual |
| No infra required | ✓ | ✓ | ✓ | ✓ |
| Secret redaction | ✓ | ✗ | ✗ | depends |

---

## Design principles

**Local-first.** No telemetry, no cloud backend, no accounts. Transcripts live on your filesystem, go into your git repo, run in your CI.

**Protocol-transparent.** The proxy never mutates messages, injects protocol behavior, or alters capability negotiation. It observes and records exactly what the client and server exchange.

**Developer tool, not observability SaaS.** This is debugging and testing infrastructure for MCP server authors — not a monitoring product for production deployments.

**Git-friendly by default.** YAML transcripts with stable field ordering, deterministic naming, and redacted secrets are designed to be committed alongside your server code.

---

## Contributing

```bash
git clone https://github.com/MCP-VCR/mcp-vcr
cd mcp-vcr
pip install -e ".[dev]"
pytest
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full technical walkthrough of the proxy internals, transcript format, and replay engine.

---

## License

MIT
