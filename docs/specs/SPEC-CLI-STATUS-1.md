# SPEC-CLI-STATUS-1 — Developer CLI dashboard (`apatch status`)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CLI-STATUS-1`
> **Anchors:** [RFP-020](../RFP-020-three-views.md) · depends on SPEC-PROJECT-STATUS-1

## 0. Motivation

Developers need one command — like `git status` — showing session phase,
spec progress, conflicts, hygiene warnings, and recommended next action.
Today four different `* status` commands mean different things.

Non-goals: interactive TUI replacement for Mutation Console.

## R1 apatch status command

CLI `apatch status [--json] [--target-dir .]` renders Rich human output from
`project_status_workspace` DTO: session/failure, spec traffic-light lines,
conflicts + safe_order when ≥2 specs, hygiene one-liner, `next_action`.

(verify: python3 -m pytest tests/test_cli_status.py::test_status_json_shape tests/test_cli_status.py::test_status_human_smoke -q)

## R2 apatch spec list

CLI `apatch spec list [--json]` discovers spec ids from `docs/specs/SPEC-*.md`
(reuse `_discover_spec_ids` from `spec_interference.py`).

(verify: python3 -m pytest tests/test_cli_status.py::test_spec_list -q)

## R3 doctor human output shows hygiene and warnings

`apatch doctor` (non-JSON) must print `hygiene.status`, top warnings, sandbox
mode, and lane — not only MCP/trustchain subset.

(verify: python3 -m pytest tests/test_doctor.py::test_doctor_human_includes_hygiene -q)

## R4 apply-session output conventions

Non-loop `apatch apply-session` must not dump raw JSON without `--json`; use
Rich summary lines consistent with `session status`.

(verify: python3 -m pytest tests/test_apply_session.py::test_apply_session_human_output -q)

## Non-goals

- HTML report (SPEC-REPORT-1)
- Manager markdown report (SPEC-REPORT-1)
