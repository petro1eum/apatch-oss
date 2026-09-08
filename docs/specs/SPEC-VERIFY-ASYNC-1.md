# SPEC-VERIFY-ASYNC-1 — Async verify jobs (MCP-safe long suites)

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-VERIFY-ASYNC-1`  
> **Anchors:** [RFP-021 §AR-2](../RFP-021-agent-reliability-design-partner.md) · depends on [SPEC-SESSION-RECOVERY-1](./SPEC-SESSION-RECOVERY-1.md), baseline verify (AR-4)

## 0. Motivation

Sync `apatch_verify_run` blocks the MCP stdio connection for the full pytest/npm duration.
Large consumer suites (~3+ min) cause **Connection closed** and trigger AR-1 recovery loops.

Success criterion:

```text
apatch_verify_run(async=true)
  → { ok: true, verify_job_id, verify_job_state: "running", poll: "apatch_verify_status(job_id=…)" }
  → MCP returns in < 30s

apatch_verify_status(job_id=…)
  → { state: running | passed | failed, baseline fields when terminal }
```

Non-goals: hosted K8s verify workers (RFP-019 L3); changing failure taxonomy (SPEC-FAILURE-TAXONOMY-2).

## R1 verify job storage and lifecycle

Job records live at `.apatch/verify_jobs/<job_id>.json` with fields:

| Field | Meaning |
|-------|---------|
| `job_id` | `vjob_<unix>_<hex>` |
| `state` | `running` \| `passed` \| `failed` |
| `verify_command` | argv list or shell string |
| `pid` | subprocess while running |
| `started_at` / `finished_at` | ISO timestamps |
| `duration_sec` | wall time when terminal |
| `session_id` | governed session when started |
| `baseline`, `allowed_failures` | AR-4 options |

Stdout/stderr captured to sibling `.log` files. Classify under artifact governance as EPHEMERAL (GC safe).

(verify: python3 -m pytest tests/test_verify_async.py::test_job_file_roundtrip -q)

## R2 apatch_verify_run async mode

`verify_run(..., async_mode=True)` (MCP param `async`):

1. `assert_operation(OP_VERIFY)` when not `skip_transition_check`.
2. Spawn detached subprocess; persist job; return immediately.
3. Response: `verify_job_id`, `verify_job_state: "running"`, `poll` hint.
4. Session `phase` stays **verify** (not complete) until job passes.

Default `async=false` preserves sync behaviour for short verifies.

(verify: python3 -m pytest tests/test_verify_async.py::test_verify_run_async_returns_job_id -q)

## R3 apatch_verify_status job polling

Extend existing `apatch_verify_status` with optional `job_id`:

- No `job_id`: current unified verification status (unchanged).
- With `job_id`: poll subprocess; when terminal, apply baseline compare/capture logic identical to sync `verify_run`.
- Terminal `passed` → session phase **complete** (same as sync green verify).
- Terminal `failed` → `failure` taxonomy via existing `classify_failure`.

(verify: python3 -m pytest tests/test_verify_async.py::test_verify_status_poll_pass_and_fail -q)

## R4 runtime forced async promotion

When `async_mode=false` but prior completed job for the same verify command exceeded
`APATCH_VERIFY_SYNC_MAX_SEC` (default **45**, env override), runtime **forces async** before
spawning sync subprocess. Response includes `async_forced: true`, `async_force_reason`.

(verify: python3 -m pytest tests/test_verify_async.py::test_forced_async_from_prior_duration -q)

## R5 baseline fields on async completion

Async terminal results include the same `baseline`, `pre_existing_only`, and `allowed_failures`
shapes as sync `verify_run` (AR-4 parity).

(verify: python3 -m pytest tests/test_verify_async.py::test_async_baseline_compare_pre_existing -q)

## R6 MCP and CLI parity

- MCP `apatch_verify_run(async=…)` + `apatch_verify_status(job_id=…)`.
- CLI `apatch verify run --async` and `apatch verify status --job-id …`.
- `mcp_setup.md` documents new params (tool count unchanged — extends existing tools).

(verify: python3 -m pytest tests/test_verify_async.py::test_cli_verify_run_async_flag -q)

## R7 integration — MCP returns before subprocess ends

Simulated slow verify (sleep 2s): `verify_run(async=true)` wall time **< 1s** while job still
`running`; poll until `passed`.

(verify: python3 -m pytest tests/test_verify_async.py::test_async_verify_returns_before_subprocess_finishes -q)

## Non-goals

- Semantic / notarization / pipeline verify async (shell verify only)
- Auto default `async=true` globally (L2 revisit per RFP-021 §5.1)
