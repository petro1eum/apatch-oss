# SPEC-STATUS-BLOCKED-1 — Structured session blockers in project status

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-STATUS-BLOCKED-1`  
> **Anchors:** [RFP-021 §AR-6](../RFP-021-agent-reliability-design-partner.md) · depends on [SPEC-PROJECT-STATUS-1](./SPEC-PROJECT-STATUS-1.md), [SPEC-CLI-STATUS-1](./SPEC-CLI-STATUS-1.md)

## 0. Motivation

Design partners supervise agents via `apatch status` / `apatch_project_status`, not
by reading `.apatch/session_state.json` or pytest logs. Today `build_status_view`
merges raw `failure` from session state but omits lifecycle, baseline verify summary,
and a supervisor-safe message. Humans cannot tell **fix_forward vs rollback** without
opening MCP transcripts.

Success criterion:

```text
apatch status --json
  → session.lifecycle, session.blocker{error_type, recommended_action, summary}
  → session.baseline{new_failures, pre_existing_failures} when verify baseline exists
  → no multi-KB verify_output in DTO (link/path only)
```

Non-goals: changing failure taxonomy logic (SPEC-FAILURE-TAXONOMY-2); async verify
jobs (SPEC-VERIFY-ASYNC-1) except optional `verify_job_id` pointer field.

## R1 session block in project_status DTO

`project_status_workspace()` returns top-level `session` (replaces thin
`active_session` for detail — keep `active_session` as deprecated alias for one
release):

| Field | Source |
|-------|--------|
| `session_id` | `session_state.session_id` |
| `intent` | `session_state.intent` |
| `phase` | `session_state.phase` |
| `lifecycle` | `derive_lifecycle(...)` from runtime domain |
| `checkpoint` | last good checkpoint id |
| `attested` | `session_state.attested` |
| `ended_at` | null when open |
| `lease_active` | sandbox lease snapshot |

When no open session (`ended_at` set or no `session_id`), `session: null`.

(verify: python3 -m pytest tests/test_status_blocked.py::test_session_block_open_and_closed -q)

## R2 blocker object for failed / blocked phases

When `phase == blocked` OR `failure` dict present in session state, DTO includes
`session.blocker`:

```json
{
  "error_type": "VERIFY_FAILED",
  "recommended_action": "fix_forward",
  "recoverable": true,
  "summary": "Verify failed: 1 new test (tests/foo.py::test_bar)",
  "tool": "apatch_verify_run",
  "details_ref": ".apatch/verify_jobs/… or null"
}
```

Rules:

- `summary` ≤ 240 chars; human sentence, **not** raw pytest stdout.
- Populate from `FailureInfo` + AR-4 `baseline.new_failures` count when present.
- If `recommended_action == fix_forward`, summary must **not** say «rollback».

(verify: python3 -m pytest tests/test_status_blocked.py::test_blocker_from_verify_failure -q)

## R3 baseline summary for supervisors

When `.apatch/verify_baseline.json` exists or last verify result stored baseline
fields, `session.baseline`:

```json
{
  "mode": "compare",
  "new_failures": ["tests/new.py::test_x"],
  "pre_existing_failures": ["tests/old.py::test_legacy"],
  "allowed_matched": []
}
```

Omit full pytest log; list capped at 20 node ids with `"truncated": true` if more.

(verify: python3 -m pytest tests/test_status_blocked.py::test_baseline_summary_in_dto -q)

## R4 CLI status renders blocker for humans

`format_status_plain_lines` / Rich `render_status_human` show:

```text
Blocker: VERIFY_FAILED → fix_forward — 1 new failure (see baseline.new_failures)
```

When `session.blocker` absent and phase not blocked, no Blocker line.

(verify: python3 -m pytest tests/test_status_blocked.py::test_status_shows_blocker_summary -q)

## R5 schema_version bump policy

Add `schema_version: 2` only when `session` shape is breaking; until then extend
v1 with optional keys (`session`, `session.blocker`) so existing consumers keep
working. Document in DTO `extensions: ["session_blocker_v1"]`.

(verify: python3 -m pytest tests/test_status_blocked.py::test_project_status_dto_shape tests/test_project_status.py::test_project_status_dto_shape -q)

## R6 MCP parity

`apatch_project_status` returns the same DTO as CLI `--json` (no slimmer subset).
`apatch_session_state` may delegate blocker summary to shared helper
`_build_blocker_summary(session_state, root)`.

(verify: python3 -m pytest tests/test_mcp.py tests/test_project_status.py -q)

## Non-goals

- Manager markdown report prose (SPEC-REPORT-1)
- Writing or clearing session state
- Auto-fixing blockers
