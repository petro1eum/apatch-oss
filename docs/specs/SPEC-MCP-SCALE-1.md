# SPEC-MCP-SCALE-1 — Lane isolation + MCP L1 scale (RFP-019)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-MCP-SCALE-1`
> **Anchors:** [RFP-019](../RFP-019-mcp-scale-lifecycle.md)

## 0. Motivation

Several Cursor chats or IDE windows may work on the **same repo** with different specs.
They must not share `.apatch/write_lease.json`, `session_state.json`, `spec_run.json`, or
`apply_session.json`. Lanes key runtime state by spec id; optional git worktrees isolate
filesystem writes per lane manifest.

Success criterion:

```text
Chat A: apatch_spec_run(spec='SPEC-A', …) → .apatch/lanes/SPEC-A/session_state.json
Chat B: apatch_spec_run(spec='SPEC-B', …) → .apatch/lanes/SPEC-B/session_state.json
apatch_mcp_hygiene → ok=true, healthy=true (report tool must not block agent lifecycle)
```

Non-goals: hosted multi-tenant MCP (RFP-019 L2/L3); lazy imports (separate backlog item).

## R1 Lane per spec (lane_context)

`bind_lane_from_kwargs({spec|requirement})` registers active spec; `resolve_lane()` routes
to `.apatch/lanes/<spec-id>/` when `APATCH_LANE=auto`.

(verify: python3 -m pytest tests/test_lane_context.py -q)

## R2 Lane-scoped hot-path state

`lane_state_path(root, filename)` resolves `session_state.json` and `spec_run.json` under
the active lane (default lane uses flat `.apatch/`).

(verify: python3 -m pytest tests/test_lane_auto.py::test_lane_default_without_spec tests/test_lane_auto.py::test_lane_from_lane_json -q)

## R3 Lane-scoped apply_session

`default_session_path(target_dir)` isolates chunked apply progress per lane — same pattern
as spec_run / session_state.

(verify: python3 -m pytest tests/test_lane_apply_session.py -q)

## R4 Worktree auto-routing

`prepare_execution_root(root, spec=…)` retargets execution to a git worktree when
`manifests/worktrees.yaml` maps the spec to a lane path.

(verify: python3 -m pytest tests/test_lane_auto.py::test_prepare_execution_no_manifest -q)

## R5 MCP hygiene report (non-blocking)

`run_mcp_hygiene` / `apatch_mcp_hygiene` return `ok: true` when the report succeeds;
`healthy: false` when ghost PIDs or lane contention are detected (does not set phase blocked).

(verify: python3 -m pytest tests/test_mcp_scale_l1.py::test_mcp_hygiene_returns_process_report -q)

## R6 L1 profiles + session write-behind

`APATCH_MCP_PROFILE=core|spec|full` filters MCP tools; `session_state_changed` uses
write-behind (persist on phase/checkpoint/failure/attested only).

(verify: python3 -m pytest tests/test_mcp_scale_l1.py -q)
