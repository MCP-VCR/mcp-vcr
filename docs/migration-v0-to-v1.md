# Migration Guide: v0 to v1 Transcripts

This guide details the backward-compatibility strategy and migration steps for upgrading legacy Model Context Protocol (MCP) transcripts (v0 or unversioned format) to the formal, versioned `v1` transcript format.

---

## Key Differences

| Feature | Legacy / v0 format | v1 format |
| :--- | :--- | :--- |
| **Versioning** | Absent or raw `schema: null` | `version: 1` at top-level |
| **Metadata** | `session_id` and `recorded_at` missing or inconsistent | Uniform `meta` section with `recorded_at`, `session_id`, `server_command`, `schema_version` |
| **Lazy Meta** | N/A | Captures `protocol_version` and `client_hint` |
| **Direction** | `direction` field (variable case) | `dir` field (strict `c2s` or `s2c` enum) |
| **Timestamps** | Seconds or omitted | Milliseconds (`t` field, monotonic integer) |

---

## Upgrade Steps

To migrate an existing v0 transcript to v1, follow these rules:

1. **Add top-level `version`**:
   Insert `version: 1` as the very first line.

2. **Normalize meta section**:
   Wrap metadata keys inside a `meta` block:
   ```yaml
   version: 1
   meta:
     recorded_at: "2026-05-17T12:00:00Z"
     session_id: "00000000" # Use a random 8-char hex if missing
     server_command: ["python", "server.py"]
     schema_version: "1.0"
   ```

3. **Rename direction fields**:
   Convert any `direction` or variable casing to `dir` with exact value `"c2s"` or `"s2c"`.

4. **Normalize Timestamps**:
   Ensure all relative timestamps are represented as integers under the `t` field, normalized to milliseconds. If timestamps were in seconds, multiply by 1000 and cast to `int`.

---

## Auto-Validation

All migrated files can be validated immediately using our validator utility:
```bash
mcp-vcr validate migrated_session.yaml
```
