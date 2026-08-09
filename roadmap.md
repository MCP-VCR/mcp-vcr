# MCP-VCR Development Roadmap

## The Core Invariant

All architectural decisions, integrations, and extensions are governed by a single core anchor:  
**A deterministic, replayable, human-readable MCP transcript format.**

*   **Human-Readable**: Stored as clean, git-diffable YAML transcripts.
*   **Deterministic**: Normalizes dynamic variables (timestamps, IDs, UUIDs, cursors) so that subsequent test runs are byte-identical.
*   **Transparent**: Proxy intercepts traffic without mutating stream messages in-flight.
*   **Version-Aware**: Supports robust transcript versioning (`version: 1`) and backward compatibility gates (`version: 0`).

---

## Phase Overview

```text
                     MCP-VCR Stable Core
                                 │
                     ┌───────────┴───────────┐
                     ▼                       ▼
             Phase 1 (Shipped)       Phase 1 (Shipped)
          Transport Abstraction     Core Developer Experience
          StdioTransport · SSE      Pytest plugin · GH Action
          Timing-Faithful Replay    Config expansion · NDJSON
                     │                       │
                     └───────────┬───────────┘
                                 ▼
                         Phase 2 (Active)
                      Adoption Accelerators
                   mcp-vcr generate · --json CLI
                   Community test collections
                   mcp-vcr test command
                                │
                                ▼
                         Phase 3 (Next)
                      Security & Resilience
                  Passive security audit (narrow)
                  Fuzz testing · Active audit
                  Structured test reports
                                │
                                ▼
                         Phase 4 (Demand-Gated)
                      Platform & Performance
                   Inspector integration · Compat matrix
                   Parallel verification · Incremental updates
                   Plugin system · Rust transport core
                                │
                                ▼
                      Deferred Indefinitely
                   VS Code extension
                   Cross-language replay (Python→TS)
```

---

## Phase 2: Adoption Accelerators (Active)

Focus: Make it trivially easy for new users to try mcp-vcr on their own server, and make CLI output composable for CI pipelines.

### 1. Test Generation (`mcp-vcr generate`)
*   **Objective**: Auto-discover all tools from a running server and generate stub snapshots.
*   **Mechanism**: `mcp-vcr generate --server "python server.py"` launches the server, sends `initialize` + `tools/list`, generates a skeleton transcript with one `tools/call` per discovered tool (with placeholder args from `inputSchema`), and writes it as a golden snapshot.
*   **Why it's pulled forward**: This is the single highest-leverage adoption feature — it lets someone try mcp-vcr on their own server in under a minute instead of hand-writing a first transcript. It only needs `StdioTransport`/`SseTransport` + a `tools/list` call, not the full config/plugin system.
*   **Dependencies**: Transport Abstraction (Phase 1, Task 1).

### 2. Structured JSON Output (`--json` flag)
*   **Objective**: Machine-readable output for every CLI command.
*   **Mechanism**: Add `--json` flag to `verify`, `record`, `replay`, `diff`, `check`, `list`, `inspect`. Outputs structured JSON to stdout, human text to stderr. Independent of the "Structured Test Reports" feature (HTML/JSON report files) — this is a CLI contract, not a report generator.
*   **Why it's early**: Retrofitting `--json` after CLI output format is established elsewhere is more painful. The GitHub Action and any future audit tooling need composable output with `jq`/other CI steps.
*   **Dependencies**: None (can start immediately, parallel with Phase 1).

### 3. Community Test Collections
*   **Objective**: Curate YAML test definitions for popular MCP servers (filesystem, memory, time, github, chrome-devtools).
*   **Mechanism**: `tests/community/` directory with pre-built transcripts and a manifest. `mcp-vcr test --suite filesystem -- npx @modelcontextprotocol/server-filesystem /tmp` runs the suite.
*   **Dependencies**: Test Generation (makes it easy to create these).

### 4. `mcp-vcr test` Command
*   **Objective**: Run predefined test suites against any server with pass/fail reporting.
*   **Mechanism**: Reads a test suite manifest (YAML), runs each transcript as a replay+verify cycle, collects results, outputs pass/fail summary. Uses `--json` for CI integration.
*   **Dependencies**: Community Test Collections, `--json` flag.

---

## Phase 3: Security & Resilience

Focus: Differentiate mcp-vcr as a security-conscious testing tool. Start narrow, expand with demand.

### 1. Security Audit Suite — Passive Mode (Priority)
*   **Objective**: Detect prompt injection risks, overly-permissive tool descriptions, and suspicious capability declarations without sending adversarial payloads.
*   **Mechanism**: `mcp-vcr audit --passive "python server.py"` launches the server, inspects `initialize` result and `tools/list` response, and runs 3-4 specific checks:
    *   **Tool description injection**: Detect descriptions containing instruction-like text ("ignore previous instructions", markdown injection, hidden unicode).
    *   **Overly-broad capabilities**: Flag servers declaring capabilities (e.g., `resources.subscribe` or `tools.listChanged`) where the capability behavior check reveals it is advertised but not actually integrated or implemented in the message workflow.
    *   **Schema permissiveness**: Flag tools with no `inputSchema`, or with `additionalProperties: true` on all objects.
    *   **Sensitive field exposure**: Check if tool outputs contain patterns matching known secret formats (API keys, tokens).
*   **Why it's the strongest differentiator**: Passive mode (tool description analysis for prompt-injection-prone descriptions) is a genuinely underserved niche in the MCP tooling space right now. A narrow first version (3-4 checks) ships fast and gets noticed by maintainers.
*   **Dependencies**: Transport Abstraction (Phase 1).

### 2. Fuzz Testing Mode (`mcp-vcr fuzz`)
*   **Objective**: Proactively verify MCP server resilience under adverse conditions.
*   **Mechanism**: Read a golden snapshot and stream c2s messages with controlled structural mutations:
    *   *Field Removal*: Omit required JSON schema parameters.
    *   *Type Confusion*: Swap integers for strings, list types for dictionaries.
    *   *Boundary Violations*: Inject extremely large numbers, empty structures, or massive strings.
    *   *Truncated Payloads*: Send partial JSON to test framing robustness.
    *   *Resource Limits*: Impose hard budgets on payload size, test counts, wall-clock execution time, and host memory limits, with guaranteed target-process cleanup (terminating or restarting the target after timeouts or crashes) to avoid hanging developer or CI machines.
*   **Why it matters**: Ensures the server gracefully rejects bad input with valid JSON-RPC error codes instead of crashing, locking pipes, or exposing tracebacks.
*   **Dependencies**: Phase 1 Transport, Test Generation (for initial snapshot corpus).

### 3. Security Audit Suite — Active Mode
*   **Objective**: Adversarial payload injection for prompt injection, path traversal, command injection.
*   **Mechanism**: Extends passive mode with actual `tools/call` invocations using adversarial arguments. Requires explicit user opt-in and target authorization. Runs only inside sandboxed, isolated targets with restricted filesystem access and blocked network egress. Employs harmless canary payloads (rather than raw commands like `;rm -rf /`) combined with timeouts, cancellation, and output redaction to inspect command/path traversal susceptibility safely.
*   **Dependencies**: Passive Audit, Fuzz Testing (shares mutation infrastructure).

### 4. Structured Test Reports
*   **Objective**: Generate HTML/JSON report files with pass/fail summaries, diff outputs, and performance metrics.
*   **Mechanism**: `mcp-vcr report --format html` generates a standalone HTML report from verify/test/audit results. JSON format for CI artifact storage.
*   **Dependencies**: `--json` CLI output (Phase 2), test/audit commands.

---

## Phase 4: Platform & Performance (Demand-Gated)

Features that require stable adoption signals or community demand before starting.

### 1. Official MCP Inspector Integration
*   **Objective**: Load mcp-vcr transcripts directly into MCP Inspector web interface for timeline visualization and interactive debugging.
*   **Gate**: Partnership with MCP maintainers, confirmed interest.

### 2. Automated Client Compatibility Matrix
*   **Objective**: Generate public report showing which MCP clients (Claude Desktop, Cursor, Windsurf) a server is compatible with.
*   **Mechanism**: Automated recording from multiple clients, structural diff comparison, dynamic matrix generation.
*   **Gate**: At least 3 community test collections completed (Phase 2).

### 3. Parallel Snapshot Verification
*   **Objective**: Verify multiple snapshots concurrently for faster CI runs.
*   **Mechanism**: `asyncio.gather()` or process pool for independent snapshot replays.
*   **Gate**: Profiling confirms serial verification is a bottleneck in real CI pipelines.

### 4. Incremental Snapshot Updates
*   **Objective**: Only re-verify snapshots affected by code changes.
*   **Mechanism**: Hash tracking of server source files + snapshot content. Skip unchanged pairs.
*   **Gate**: Users report CI time as a pain point.

### 5. Plugin System
*   **Objective**: Allow third-party plugins for custom redaction rules, diff engines, and output formats.
*   **Mechanism**: Entry-point based plugin discovery (like pytest plugins).
*   **Gate**: Demand signal from users needing custom behavior.

### 6. Rust Transport Core
*   **Objective**: Reduce pipe pumping overhead for high-throughput pipelines.
*   **Mechanism**: Rewrite core asyncio transport loops in Rust, expose via PyO3.
*   **Gate**: Profiling proves Python pipe handling is a practical bottleneck during large payload exchanges.

---

## Deferred Indefinitely

These features have large surface area and unclear payoff at the current adoption stage. Will not start unless there is concrete user demand signal (issues asking for it, not roadmap-driven).

### VS Code Extension
*   **Rationale**: Visualizing recordings and inspecting diffs in VS Code is useful but requires maintaining a full extension lifecycle (marketplace publishing, API compatibility, UI framework). Cost/benefit doesn't justify it until mcp-vcr has significant VS Code user base.

### Cross-Language Replay (Python → TypeScript/Node.js)
*   **Rationale**: "Record in Python, replay in TypeScript" requires a second full implementation of the replay engine, transcript parser, and diff engine. The transcript format (YAML/NDJSON) is language-neutral, but the tooling around it is not. Better to let the TypeScript ecosystem build their own tool that reads the same format.

---

## Prioritization Principles

1.  **Adoption leverage first**: Features that let new users try mcp-vcr in under a minute (`generate`, `--json`) come before features that improve the experience for power users (plugin system, VS Code extension).
2.  **Narrow > broad**: Ship a 3-check passive audit before a 15-check active+adversarial suite. Ship `--json` on `verify` before a full HTML report generator.
3.  **Demand-gated, not roadmap-driven**: Phase 4 features start when users ask for them, not when the roadmap says they're next.
4.  **Security is a differentiator**: Given the current MCP tooling landscape, passive security analysis is underserved and high-signal. Worth prioritizing over commodity features.

---

## When to Revisit the Roadmap

Work on gated areas will be prioritized based on key checkpoints:

1.  **Stable Adoption**: Core v0.1 CLI is actively used by ≥ 1 external project.
2.  **Phase 1 Shipped**: Transport abstraction and all developer integrations are complete and tested.
3.  **Community Signals**: Developer feedback identifies which deferred area is most highly wanted for their workflows.
4.  **Performance Auditing**: Comprehensive profiling against large transcripts (e.g., sessions with >100 interactions or >50MB files).
