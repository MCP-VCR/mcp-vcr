# MCP-VCR Development Roadmap

## Overview

```text
                    MCP-VCR Core Engine (Phase 1 & 2 Shipped)
                                  │
                                  ▼
                     Platform & Performance (Demand-Gated)
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

## Platform & Performance (Demand-Gated)

Features that require stable adoption signals or community demand before starting.

### 1. Official MCP Inspector Integration
*   **Objective**: Load mcp-vcr transcripts directly into MCP Inspector web interface for timeline visualization and interactive debugging.
*   **Gate**: Partnership with MCP maintainers, confirmed interest.

### 2. Automated Client Compatibility Matrix
*   **Objective**: Generate public report showing which MCP clients (Claude Desktop, Cursor, Windsurf) a server is compatible with.
*   **Mechanism**: Automated recording from multiple clients, structural diff comparison, dynamic matrix generation.
*   **Gate**: Community test collections (Shipped).

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

## When to Revisit the Roadmap

Work on gated areas will be prioritized based on key checkpoints:

1.  **Community Signals**: Developer feedback identifies which deferred area is most highly wanted for their workflows.
2.  **Performance Auditing**: Comprehensive profiling against large transcripts (e.g., sessions with >100 interactions or >50MB files).
