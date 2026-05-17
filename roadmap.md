# mcp-vcr: 3-Month Development Roadmap

**Last Updated:** May 2026  
**Planning Period:** May 16, 2026 – August 16, 2026

---

## Core Invariant

**A deterministic, replayable, human-readable MCP transcript format.**

This is our architectural center and long-term stability anchor. Everything else is secondary.

- Architectural decisions must preserve this invariant.
- Integrations (Inspector, tooling, third-party) are built *on top* of, not inside, this core.
- Format evolution is versioned and backward-compatible.

---

## Architecture Layers

```
Transport (stdio/pipes)
    ↓
Transcript (YAML, versioned schema)
    ↓
Normalization (dedupe + mask nondeterminism)
    ↓
Replay (deterministic, version-aware engine)
    ↓
Diff (layered: strict/semantic, supports snapshot testing)
    ↓
Tooling (CLI, plugins, test harnesses)
```

---

## High-Level Goals (3 Months)

1. **Stabilize the core invariant** — versioned, deterministic transcript format with complete validator.
2. **Add normalization as first-class architecture** — not just "ignore fields", but proper transforms for nondeterministic data.
3. **Golden snapshot testing** — `mcp-vcr snapshot` and `mcp-vcr verify` as pytest-grade regression testing for MCP.
4. **Robust replay/diff** — ensure version-aware, deterministic replay and diff that never breaks on older transcripts.
5. **Documentation & fixtures** — make adoption frictionless with onboarding guides and sample transcripts.

---

## Milestone Breakdown (12 weeks)

### **Weeks 1–2: Transcript Schema v1 & Versioning**

**Goal:** Define and implement the versioned transcript specification.

**Deliverables:**
- [x] Write formal transcript schema (JSON Schema or equivalent).
- [x] Add `version: 1` header to all transcripts.
- [x] Implement validator (CLI tool and library) that checks transcript validity.
- [x] Document backward-compatibility strategy for future schema versions.
- [x] Update all existing transcript examples in docs/fixtures.
- [x] Add `schema_version` field to transcript metadata.
- [x] Create `.mcp-vcr.yaml` schema documentation.

**Milestone Check:**
- All test fixtures are valid against v1 schema.
- Validator rejects malformed transcripts with clear errors.
- Schema is published (in repo and docs).

**Success Metrics:**
- ✅ `mcp-vcr validate session.yaml` works
- ✅ Schema docs are clear and complete
- ✅ All existing sessions upgrade cleanly to v1

---

### **Weeks 3–4: Normalization Layer**

**Goal:** Implement deduplicated normalizers as core architecture.

**Deliverables:**
- [ ] Define `Normalizer` abstraction (apply transform to payload).
- [ ] Implement built-in normalizers:
  - Timestamp canonicalization (e.g., `2024-01-15T14:30:22Z` → `NORM_TIMESTAMP`)
  - Request ID replacement (numeric IDs → `NORM_ID_1`, `NORM_ID_2`, …)
  - UUID masking (UUIDs → `NORM_UUID_<index>`)
  - Session token scrubbing
  - Nondeterministic metadata (cursor values, pagination tokens)
- [ ] Config-driven normalizer selection (`.mcp-vcr.yaml`).
- [ ] Integrate normalizers into replay and diff pipelines.
- [ ] Documentation: which fields are normalized by default, how to customize.

**Milestone Check:**
- Replay/diff diffs are noise-free even for sessions with timestamps.
- Two replays of the same transcript produce identical responses (when normalized).
- Normalizers can be toggled on/off.

**Success Metrics:**
- ✅ Sample non-deterministic session diffs cleanly with normalization
- ✅ Normalizers are configurable per-project
- ✅ Normalization is transparent to end users (works by default)

---

### **Weeks 5–6: Golden Snapshot Testing Infrastructure**

**Goal:** Add CLI and integration for golden snapshot regression testing.

**Deliverables:**
- [ ] CLI: `mcp-vcr snapshot <session>` — apply normalizers, save golden.
- [ ] CLI: `mcp-vcr verify snapshots/` — compare replayed responses against golds, return diff.
- [ ] `mcp-vcr verify` exit code: 0 = pass, 1 = regression detected.
- [ ] Output format: human-readable text + JSON for CI integration.
- [ ] Integration guide: how to use in pytest, GitHub Actions, other CI.
- [ ] Documentation: "Golden Snapshot Testing for MCP".
- [ ] Snapshot storage convention: `snapshots/<session_id>_golden.yaml`.

**Milestone Check:**
- CI can run `mcp-vcr verify snapshots/ -- python server.py` and get clear pass/fail.
- Snapshots are human-reviewable and diffable in git.
- Snapshot workflow is documented with real examples.

**Success Metrics:**
- ✅ Example CI workflow (GitHub Actions YAML) is included
- ✅ Snapshots can be updated with a single command
- ✅ Developers can use snapshots as pytest fixtures

---

### **Weeks 7–8: Replay & Diff Refactor for Stability**

**Goal:** Refactor replay and diff to depend on versioning and normalization.

**Deliverables:**
- [ ] Replay engine checks transcript version before loading.
- [ ] Replay engine applies normalizers to replayed responses for comparison.
- [ ] Diff engine is version-aware (warns on version mismatch).
- [ ] Diff engine applies normalizers before structural/semantic/strict comparison.
- [ ] Add regression tests: older transcript versions still replay/diff correctly.
- [ ] Document diff output formats: text, JSON, GitHub annotations.

**Milestone Check:**
- Replay of v1 transcripts is stable and deterministic.
- Diff correctly handles non-deterministic fields without noise.
- Version mismatch warnings are clear.

**Success Metrics:**
- ✅ All diff tests pass with normalized output
- ✅ Replaying v0.1 (pre-versioning) sessions handles gracefully
- ✅ Diff output is actionable and noise-free

---

### **Weeks 9–10: Fixtures, Tests & Documentation**

**Goal:** Ship with comprehensive examples and onboarding.

**Deliverables:**
- [ ] Sample transcripts (fixtures/) for:
  - Simple `initialize` + `tools/list`
  - Tool invocation with binary response
  - Streaming/notification messages
  - Error cases (server crash, malformed JSON)
  - Sessions with timestamps (normalization demo)
- [ ] Developer guide: "Getting Started with mcp-vcr"
- [ ] Architecture guide: updated with versioning + normalization.
- [ ] Contributing guide: how to add tests, update fixtures.
- [ ] FAQ: common gotchas and debugging.
- [ ] Unit test suite covering:
  - Transcript validation
  - Normalization transforms
  - Replay/diff stability
  - Version compatibility

**Milestone Check:**
- Developers can follow the guide and record/replay/snapshot in 10 minutes.
- All fixtures validate and pass regression tests.
- Test coverage >80% for core modules.

**Success Metrics:**
- ✅ README links to "Getting Started"
- ✅ Sample CI workflow (`.github/workflows/mcp-regression.yml`) works out of the box
- ✅ Test suite runs cleanly in CI

---

### **Weeks 11–12: Polish, External Integration Exploration, Handover**

**Goal:** Finalize core, explore optional integrations, prepare for external adoption.

**Deliverables:**
- [ ] CLI polish:
  - `mcp-vcr list` — show recorded sessions, metadata, timestamps
  - `mcp-vcr inspect <session_id>` — basic session details
  - Better help text and error messages
- [ ] Performance audit: ensure record/replay/diff are fast (no timeouts on large transcripts).
- [ ] **Exploratory (not blocking):**
  - Inspector integration POC (can load transcript and replay)
  - JSON schema export (for third-party tooling)
- [ ] Release checklist and version bump (→ v0.2).
- [ ] Handover docs: what's next, known limitations, future work.
- [ ] Write "What's New in v0.2" release notes.

**Milestone Check:**
- Core is stable and production-ready for early external users.
- Optional integrations (Inspector, etc.) are additive, not blocking.
- Handover documentation is clear.

**Success Metrics:**
- ✅ v0.2 release published with changelog
- ✅ "Getting Started" guide is used successfully by 1-2 external projects
- ✅ No core regressions on v0.1 transcripts

---

## What We're **Not** Doing (Yet)

**Rust transport core** — Python is fast enough. We don't know the performance bottlenecks yet.

**Compatibility matrix** — Valuable but not core. This is Q3 work, after the base is stable.

**Deep Inspector integration** — Keep it exploratory and additive. Not architectural.

**Timing-faithful replay** — Planned, but secondary to versioning and normalization.

**Fuzz mode** — Important for server robustness, but comes after core is locked down.

---

## Evaluation & Checkpoints

**Weekly Standup**
- What shipped?
- What's blocked?
- What changed in the plan?

**Biweekly Milestone Review (every 2 weeks)**
- Demo deliverables against milestone checklist.
- Update docs/README.
- Re-plan if off track.

**Monthly (end of weeks 4, 8, 12)**
- Full retrospective: what worked, what didn't?
- Adjust priorities for next month.
- Publish progress notes.

**Success Criteria for Full Roadmap**
- [ ] Versioned transcript schema v1 is locked.
- [ ] Normalization is built-in and docs are clear.
- [ ] Golden snapshot testing works end-to-end in CI.
- [ ] All core modules have >80% test coverage.
- [ ] "Getting Started" guide is complete and tested.
- [ ] v0.2 is released and used by ≥1 external project.

---

## Future Work (Q3+, After Core is Stable)

Once the above is shipped, the next priorities are:

1. **Timing-faithful replay** — use transcript timestamps for deterministic timing tests.
2. **Fuzz mode** — mutate client messages, probe server robustness.
3. **Inspector integration** — load transcripts directly into Inspector UI.
4. **Compatibility matrix** — record from Claude Desktop, Cursor, Windsurf; diff results.
5. **CLI polish** — session browser, filtering, advanced inspection.
6. **Rust prototype** — only after we understand perf bottlenecks.

---

## Architecture Notes

### Why Versioning First?

- **Prevents future breakage.** Once Inspector or external tools depend on transcripts, changing the format silently breaks them.
- **Enables confident evolution.** Schema v2 can be introduced with clear upgrade paths.
- **Foundation for everything else.** Replay, normalization, and diff all need to know which version they're working with.

### Why Normalization is Core Architecture?

- **Determinism is the invariant.** Nondeterministic fields (timestamps, UUIDs) break replay/diff unless they're normalized.
- **Not just diffing.** Normalization applies to replay too — ensures replayed responses are comparable.
- **Prevents noise.** Without it, every diff is cluttered with "timestamp changed" false positives.

### Why Golden Snapshots?

- **Pytest for MCP.** Developers are familiar with golden snapshots (`snapshot_test.py`).
- **CI-friendly.** Exit codes, no language barriers, works everywhere.
- **Reduces test boilerplate.** Instead of hand-crafted assertions, just compare transcripts.

---

## Summary

This roadmap is laser-focused on the core invariant: **a deterministic, replayable, human-readable MCP transcript format**. Everything else — Inspector, fuzzing, timing, compatibility matrices — flows from this foundation and can be added later without breaking what came before.

The first 8 weeks lock down versioning, normalization, and snapshot testing. Weeks 9–12 are polish and optional exploration. By end of month 3, mcp-vcr is ready for external adoption with clear guarantees about stability and evolveability.
