# MCP-VCR Development Roadmap

**Last Updated:** May 2026  
**Status:** Core v0.2 Stable & Shipped  

---

## The Core Invariant

All architectural decisions, integrations, and extensions are governed by a single core anchor:  
**A deterministic, replayable, human-readable MCP transcript format.**

*   **Human-Readable**: Stored as clean, git-diffable YAML transcripts.
*   **Deterministic**: Normalizes dynamic variables (timestamps, IDs, UUIDs, cursors) so that subsequent test runs are byte-identical.
*   **Transparent**: Proxy intercepts traffic without mutating stream messages in-flight.
*   **Version-Aware**: Supports robust transcript versioning (`version: 1`) and backward compatibility gates (`version: 0`).

---

## Roadmap

With the core testing infrastructure stable and validated, the roadmap tracks high-value areas intentionally deferred to keep the initial design lightweight.

```
                   MCP-VCR Stable Core (v0.2)
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
Timing-Faithful           Fuzz Testing            HTTP/SSE
    Replay                   Mode                Transports
 (t-delay sleep)       (Mutation payload)       (sse-proxy)
       │                       │                       │
       └───────────────────────┼───────────────────────┘
                               ▼
                        Platform Gates
                    (Inspector Integration
                     & Compatibility Matrix)
```

### 1. Timing-Faithful Replay
*   **Objective**: Replay client messages respecting the timing patterns of the original recorded session.
*   **Mechanism**: Use the `t` field (relative milliseconds since session start) inside the transcript to insert non-blocking `asyncio.sleep` delays during replay.
*   **Why it matters**: Crucial for testing rate-limiting systems, connection handling, or servers with timeout-sensitive state machines.

### 2. Fuzz Testing Mode (`mcp-vcr fuzz`)
*   **Objective**: Proactively verify MCP server resilience under adverse conditions.
*   **Mechanism**: Read a golden snapshot and stream client-to-server (`c2s`) messages with controlled structural mutations:
    *   *Field Removal*: Omit required JSON schema parameters.
    *   *Type Confusion*: Swap integers for strings, list types for dictionaries.
    *   *Boundary Violations*: Inject extremely large numbers, empty structures, or massive strings.
*   **Why it matters**: Ensures the server gracefully rejects bad input with valid JSON-RPC error codes instead of crashing, locking standard pipes, or exposing sensitive execution tracebacks.

### 3. Official MCP Inspector Integration
*   **Objective**: Bridge the gap between CLI regression suites and interactive visual debugging.
*   **Mechanism**: Partner with the official Model Context Protocol maintainers to support loading, inspecting, and editing `mcp-vcr` transcripts directly within the official Inspector web UI.
*   **Why it matters**: Allows developers to view session timelines, modify responses step-by-step, and save changes back to golden snapshot files interactively.

### 4. Automated Client Compatibility Matrix
*   **Objective**: Ensure your server behaves identically regardless of the client (Claude Desktop, Cursor, Windsurf, or custom wrappers).
*   **Mechanism**: Setup automated testing runner profiles to record transcripts from different clients, run structural diff comparisons, and generate a dynamic compatibility matrix.
*   **Why it matters**: Provides server authors with a "Compatible with Claude/Cursor/Windsurf" verified seal of approval, identifying and warning about client-specific quirks automatically.

### 5. HTTP/SSE Transport Recording
*   **Objective**: Complete transport coverage by supporting MCP's second official transport layer.
*   **Mechanism**: Implement a Server-Sent Events (SSE) bidirectional proxy that intercepts, logs, and replays HTTP/SSE streams without changing the underlying JSON-RPC transaction model.
*   **Why it matters**: Unlocks regression testing for cloud-deployed or remote MCP servers that communicate over HTTP/SSE rather than local stdio subprocess pipes.

### 6. Rust Transport Core (Performance Optimization)
*   **Objective**: Reduce pipe pumping overhead for high-throughput development pipelines.
*   **Mechanism**: Rewrite the core asyncio transport loops and JSON framing/parsing pumps in Rust, exposing them to Python via PyO3.
*   **Why it matters**: An optional optimization path kept in reserve only after profiling proves python pipe structures are a practical bottleneck during large file (e.g. megabyte payload) exchanges.

---

## When to Revisit the Roadmap

Work on these deferred areas will be prioritized based on key usage checkpoints:

1.  **Stable Adoption**: The core v0.2 CLI is actively used by $\ge$ 1 external project.
2.  **Performance Auditing**: Comprehensive profiling is completed against large transcripts (e.g., sessions with >100 interactions or >50MB files).
3.  **Community Signals**: Developer feedback identifies which deferred area is most highly wanted for their workflows.
