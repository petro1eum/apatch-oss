# SPEC-REMOTE-SSH-MCP-1 — MCP routing for remote governed operations

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-REMOTE-SSH-MCP-1`  
> **Anchors:** [RFP-029](../RFP-029-remote-ssh-workspaces.md) · [SPEC-REMOTE-SSH-WORKER-1](./SPEC-REMOTE-SSH-WORKER-1.md)

## 0. Motivation

The product promise is not a separate remote CLI. The agent should keep using local MCP
tools, pass an SSH target, and receive the same governed response shape. Runtime state
must be remote; local MCP is only the control plane.

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A29-A | R1 | covered |
| A29-B | — | waiver: SPEC-REMOTE-SSH-CORE-1 |
| A29-C | R1 | covered through remote doctor route |
| A29-D | — | waiver: SPEC-REMOTE-SSH-WORKER-1 |
| A29-E | — | waiver: SPEC-REMOTE-SSH-WORKER-1 |
| A29-F | R2 | covered |
| A29-G | R2 | covered |
| A29-H | — | waiver: SPEC-REMOTE-SSH-VERIFY-1 |
| A29-I | R3 | covered for session state; verify-specific resume in VERIFY |
| A29-J | R4 | covered via audit metadata and policy propagation |
| A29-K | R4 | covered |
| A29-L | R5 | covered |
| A29-M | R1 | covered by fake remote transport |
| A29-N | R6 | covered |
| A29-O | R7 | covered |

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r0_remote_mcp_rfp_coverage -q)

## R1 Remote doctor and session start route through worker

`apatch_doctor(target_dir='ssh://host/path')` and `apatch_session_start(..., target_dir='ssh://host/path')`
call the remote worker. Responses include `remote` metadata and normal `state_update`.
No local `.apatch/session_state.json` is created for the remote repo.

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r1_doctor_and_session_start_remote_route -q)

## R2 Generate/simulate/apply/attest/session_end route through worker

Remote targets for `generate_batch`, `simulate`, `apply_session`, `attest`, and
`session_end` execute remotely. `generate_batch.out_path` is remote `.apatch/tmp/...` and
is reused by simulate/apply without local path translation.

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r2_governed_mutation_tools_route_remote -q)

## R3 Remote session state is authoritative

After an interrupted local MCP call, `apatch_session_state(target_dir='ssh://host/path')`
re-fetches remote state. Local ghost state never drives lifecycle decisions for remote
workspaces.

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r3_remote_session_state_authoritative -q)

## R4 Audit metadata distinguishes controller and executor

Remote responses and attestation payloads include `remote_host`, `remote_root`, remote
git HEAD when available, local controller host/id, and remote apatch version/fingerprint.
The local Mac path is not recorded as the remote workspace.

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r4_remote_audit_metadata -q)

## R5 Mixed local/remote state is rejected

If a remote logs path is passed to a local target or a local path is passed to a remote
apply, the result is `REMOTE_PROTOCOL_ERROR` or `WORKSPACE_MISMATCH` with remediation.
No apply occurs.

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r5_mixed_workspace_rejected -q)

## R6 Codex setup docs

Docs show the intended model: Codex custom MCP stays local; remote work is selected by
`target_dir='ssh://host/path'`. The docs explicitly warn against `ssh host 'sed …'` and
against using a remote POSIX path as a local target.

(verify: python3 -m pytest tests/test_remote_mcp_routing.py::test_r6_codex_remote_docs_present -q)

## R7 Intent-level autopilot orchestration

`apatch_remote_task_run(target, intent, plan, verify)` performs the normal governed remote
workflow behind one user-facing approval intent: remote doctor/preflight, session_start,
generate/plan/simulate, apply_session chunks, verify, attest, and session_end. Internal
read-only gates and bounded governed sub-steps are timeline entries, not separate
human-approval prompts. When verify fails and corrective needles are known,
`plan={"fix_forward_current": true, "needles": [...]}` resumes the active session, resets only
its apply cursor, applies the correction, then verifies, attests, and closes that same governed
session. The runner stops only on typed risk escalation, policy boundary, admin/install action,
destructive rollback request, an unrepairable verify failure, or transport failure.

(verify: /opt/homebrew/bin/python3.14 -m pytest -q tests/test_remote_mcp_routing.py::test_r7_remote_task_run_single_approval_boundary tests/test_remote_ssh_transport.py)

## Non-goals

- Remote verify internals (SPEC-REMOTE-SSH-VERIFY-1).
- Hosted MCP on remote hosts.
