# SPEC-GOVERNANCE-OPERATIONS-1 -- Low-friction Governed Operations

> **apatch artifact:** `spec:SPEC-GOVERNANCE-OPERATIONS-1`
> **RFP:** [RFP-041](../RFP-041-governance-operations.md)

## R0 RFP traceability gate

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| GO-P0-1 | R1 | covered |
| GO-P0-2 | R2 | covered |
| GO-P0-3 | R3 | covered |
| GO-P0-4 | R4 | covered |
| GO-P0-5 | R5 | covered |
| GO-P0-6 | R10 | covered |
| GO-P0-7 | R11 | covered |
| GO-P0-8 | R12 | covered |
| GO-P0-9 | R13 | covered |
| GO-P0-10 | R14 | covered |
| GO-P1-1 | R6 | covered |
| GO-P1-2 | R7 | covered |
| GO-P1-3 | R8 | covered |
| GO-P1-4 | R9 | covered |
| GO-P1-5 | R15 | covered |

(verify: python3 -c "from pathlib import Path; t=Path('docs/specs/SPEC-GOVERNANCE-OPERATIONS-1.md').read_text(); ids='GO-P0-1 GO-P0-2 GO-P0-3 GO-P0-4 GO-P0-5 GO-P0-6 GO-P0-7 GO-P0-8 GO-P0-9 GO-P0-10 GO-P1-1 GO-P1-2 GO-P1-3 GO-P1-4 GO-P1-5'.split(); assert all(f'| {x} | R' in t for x in ids)")

## R1 Exact stateful session capability

Every manual stateful lifecycle MCP entry point SHALL route by exact governed
session id. Intent-level spec orchestration SHALL carry its created capability
internally. More than one active lane without explicit identity fails closed
and leaves all lane states byte-for-byte unchanged.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_session_binding_routes_exact_lane tests/test_transactional_sessions.py::test_ambiguous_lane_has_no_newest_fallback tests/test_mcp_stateful_contract.py)

## R2 Idempotent atomic finalization

Finalization SHALL persist a retryable cleanup journal and converge all cleanup
steps for the exact session. Repeating finalization after any completed cleanup
step is successful and cannot affect a replacement or foreign session.

(verify: python3 -m pytest -q tests/test_session_finalization.py)

## R3 Collision-proof runtime namespace

Session-owned patch logs and verify baselines SHALL be physically routed through
the exact lane, spec hash, requirement, and session namespace. Lane-scoped apply
and requirement run state SHALL retain the exact session owner. Foreign owners
fail before filesystem mutation.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_patch_log_owner_and_digest_are_enforced tests/test_runtime_namespace.py)

## R4 Automatic differential verification

When an apply operation receives a verify command, APatch SHALL capture its
failure set before the first mutation and compare after mutation. A pre-existing
failure passes as unchanged; a new failure triggers rollback.

(verify: python3 -m pytest -q tests/test_verify_baseline.py tests/test_apply_baseline.py)

## R5 One-call exact recovery

Recovery SHALL accept one governed session id, inspect its exact lane and owned
resources, and converge to either `resumed`, `closed`, or `blocked_foreign` in
one call without a manual resume/rebind/gc/end chain.

(verify: python3 -m pytest -q tests/test_session_recover_one_call.py)

## R6 Compact MCP default

The default MCP profile SHALL expose 10-17 intent-level tools. It SHALL carry
read-only orientation for the two moments unguided work begins — impact before a
target is chosen, structured build diagnostics after a failed verify — so the
default surface bounds work without hiding the short correct path. It SHALL NOT
carry a tool that mutates source outside a governed session or that honours a
caller-supplied TrustChain opt-out. Core/spec/remote, database, and diagnostic
packs remain opt-in; `full` remains compatible.

(verify: python3 -m pytest -q tests/test_mcp_profiles.py)

## R7 Bounded automatic hygiene

Successful finalization SHALL delete owned ephemeral files and run bounded
history/debug rotation. The response returns aggregate counts, not path dumps.

(verify: python3 -m pytest -q tests/test_session_finalization.py::test_finalization_runs_bounded_hygiene)

## R8 Workspace onboarding

The MCP-bound workspace SHALL require no registry step. A sibling workspace has
one explicit registration command, and workspace doctor reports a directly
executable repair for identity or contract drift.

(verify: python3 -m pytest -q tests/test_mcp_workspace_roaming.py)

## R9 Production trust level

Attestation output SHALL distinguish local self-signed evidence from
Secrets-identity plus Platform-included evidence, and production gates SHALL
reject the former.

(verify: python3 -m pytest -q tests/test_trust_anchor.py tests/test_inclusion.py)

## R10 Authoritative resumable cursor

`execute_next` SHALL persist and report one authoritative apply cursor. While
chunks remain, both the tool response and session state remain in apply. A
subsequent `execute_next` for the same requirement SHALL consume the remaining
cursor without the caller supplying its patch-log path.

(verify: python3 -m pytest -q tests/test_spec_executor.py::test_execute_next_resumes_authoritative_cursor_without_manual_jsonl)

## R11 Executable fix-forward recovery

When verification has already restored the failed mutation, exact recovery
SHALL rotate the session capability, reopen the apply lifecycle, and accept a
corrected patch log owned by that same governed session. No returned next action
may be forbidden by the resulting lifecycle.

(verify: python3 -m pytest -q tests/test_session_recover_one_call.py::test_verify_failed_recover_opens_owned_fix_forward_apply)

## R12 Safe mutation and evidence primitives

Overlapping needles SHALL return a no-mutation split/retry plan. Atomic file
replacement SHALL preserve the original executable mode unless chmod is
explicit. Working-tree notarization SHALL ignore machine-local
`.cursor/mcp.json`, and hygiene reports SHALL compact repeated operational debt.

(verify: python3 -m pytest -q tests/test_generate_batch.py::test_generate_batch_overlap_returns_safe_retry_without_mutation tests/test_workflows.py::test_replace_preserves_executable_bit_and_chmod_changes_it_explicitly tests/test_ledger_hot_path.py::test_working_tree_notarization_ignores_machine_local_cursor_mcp tests/test_gc_cli.py::test_doctor_hygiene_compacts_operational_debt_and_returns_repairs)

## R13 Idempotent request journal

Session start and intent-level SPEC orchestration SHALL accept a stable client
request id. The same operation and canonical payload execute once; a timeout
retry replays the recorded result and rotates the exact active capability.
Request ids, session tokens, and nested capability tokens SHALL NOT be persisted
in plaintext. After a process crash, a retry SHALL recover the exact session
correlated by request hash, including an orphan lane written before registry
registration; when the dead process created no session, the request SHALL be
claimed and executed once by the new process.

(verify: python3 -m pytest -q tests/test_request_journal.py)

## R14 Safe selective stale finalize

Finalize SHALL establish exact active requirement ownership before running its
verify command. A file-drift-only stale requirement with no active session MAY
bootstrap one selective verify and rebind. Include/exclude filters SHALL be
validated before opening a session and SHALL leave every unselected stale Rk
unchanged.

(verify: python3 -m pytest -q tests/test_spec_executor.py::test_stale_finalize_uses_exact_selective_rebind_before_runtime_verify tests/test_spec_executor.py::test_stale_finalize_reports_red_selective_rebind_as_failure tests/test_spec_rebind.py::test_rebind_selects_and_excludes_exact_requirements)

## R15 Pre-session and profile truthfulness

An orchestration failure before session creation SHALL remain a non-persisted
request diagnostic. Compact doctor and playbooks SHALL name only tools available
in the active compact profile and SHALL explicitly identify profile upgrades.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_pre_session_orchestrator_failure_does_not_create_lane_state tests/test_mcp_profiles.py::test_compact_doctor_and_playbooks_only_name_available_hot_path_tools)

## RFP traceability

| RFP ID | SPEC Rk | Disposition |
|---|---|---|
| GO-P0-1 | R1 | covered |
| GO-P0-2 | R2 | covered |
| GO-P0-3 | R3 | covered |
| GO-P0-4 | R4 | covered |
| GO-P0-5 | R5 | covered |
| GO-P0-6 | R10 | covered |
| GO-P0-7 | R11 | covered |
| GO-P0-8 | R12 | covered |
| GO-P0-9 | R13 | covered |
| GO-P0-10 | R14 | covered |
| GO-P1-1 | R6 | covered |
| GO-P1-2 | R7 | covered |
| GO-P1-3 | R8 | covered |
| GO-P1-4 | R9 | covered |
| GO-P1-5 | R15 | covered |
