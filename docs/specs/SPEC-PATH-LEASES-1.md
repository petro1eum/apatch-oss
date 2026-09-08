# SPEC-PATH-LEASES-1 -- Path-scoped Concurrent Writer Leases

> **apatch artifact:** `spec:SPEC-PATH-LEASES-1`
> **RFP:** [RFP-042](../RFP-042-path-scoped-writer-leases.md)

## R0 RFP traceability gate

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| PL-P0-1 | R1 | covered |
| PL-P0-2 | R2 | covered |
| PL-P0-3 | R3 | covered |
| PL-P0-4 | R4 | covered |
| PL-P0-5 | R5 | covered |
| PL-P0-6 | R6 | covered |
| PL-P0-7 | R7 | covered |
| PL-P0-8 | R8 | covered |
| PL-P0-9 | R9 | covered |
| PL-P0-10 | R10 | covered |
| PL-P0-11 | R11 | covered |

(verify: python3 -m apatch.cli rfp coverage --rfp RFP-042 --spec SPEC-PATH-LEASES-1 --target-dir . --json)

## R1 Disjoint concurrency and exact conflicts

Two live governed sessions SHALL hold disjoint path sets in one canonical workspace.
Exact or segment-prefix overlap SHALL fail before mutation and report each conflicting
requested path, held path, lease id, session id, PID, and tool.

(verify: python3 -m pytest -q tests/test_path_leases.py::test_disjoint_sessions_acquire_concurrently tests/test_path_leases.py::test_same_file_conflict_is_exact_and_non_mutating)

## R2 Canonical path identity

Path identity SHALL resolve workspace aliases and symlinks, normalize existing and
future paths against the nearest real ancestor, honor actual filesystem case behavior,
and cover file/directory, rename source+target, and delete semantics.

(verify: python3 -m pytest -q tests/test_path_leases.py::test_file_directory_symlink_case_rename_delete_canonicalization)

## R3 Atomic non-waiting multi-path admission

Acquisition of multiple paths SHALL commit the complete sorted canonical set in one
registry transaction or leave registry bytes semantically unchanged. Crossed request
order SHALL neither wait nor deadlock.

(verify: python3 -m pytest -q tests/test_path_leases.py::test_multi_path_acquisition_is_atomic_and_order_independent)

## R4 One admission contract for every mutation orchestrator

All apply-backed orchestrators SHALL admit planned paths before first source write and
inner apply SHALL reject paths outside the exact owned lease. Shared-maintenance and an
independent apply over disjoint trees SHALL complete concurrently.

(verify: python3 -m pytest -q tests/test_path_lease_integration.py::test_shared_maintenance_coexists_with_independent_apply tests/test_path_lease_integration.py::test_apply_rejects_write_set_expansion)

## R5 Session-scoped rollback

Governed rollback SHALL resolve a checkpoint only from the exact capability-selected
lane and governed backup ownership metadata. It SHALL NOT select the workspace-wide
latest checkpoint or inherit a checkpoint from a previous session. Missing or foreign
ownership SHALL fail before mutation. Rollback SHALL acquire and restore only the
owned backup session's canonical paths; a disjoint concurrent session's file, checkpoint,
lease, and notarized state SHALL remain unchanged.

(verify: python3 -m pytest -q tests/test_governed_rollback_isolation.py tests/test_path_lease_integration.py::test_parallel_rollback_does_not_change_foreign_result)

## R6 Lossless TrustChain and notarized index

Parallel local TrustChain commits SHALL serialize only ledger append, retain every
record, preserve exact governed-session metadata, and merge notarized-index entries
without lost updates. Concurrent-mode rollback appends compensation instead of moving
HEAD behind a foreign commit.

(verify: python3 -m pytest -q tests/test_path_lease_trustchain.py)

## R7 Independent expiry and recovery

Dead or expired leases SHALL be pruned independently. Finalization and recovery release
only leases owned by the exact session and never block unrelated live path sets.

(verify: python3 -m pytest -q tests/test_path_leases.py::test_expiry_and_exact_recovery_are_per_lease)

## R8 Safe mixed-version migration

A valid v1 workspace lease SHALL remain visible to and releasable by its original
owner while v2 treats it as workspace-wide. A compatibility barrier SHALL fail old
writers closed only while real v2 leases exist, expose upgrade guidance, inherit a
finite lease expiry, and clear automatically on release, expiry, crash recovery, or
registered-alias startup sweep.

(verify: python3 -m pytest -q tests/test_path_leases.py::test_legacy_lease_migrates_without_replacing_old_owner tests/test_path_leases.py::test_v2_guard_is_infrastructure_scoped_and_self_expiring tests/test_mcp_lifecycle.py::test_startup_sweeps_registered_aliases)

## R9 Read-only operations never lease

Plan, lint, status, doctor, and verify-only workflows SHALL leave both lease files
absent or semantically unchanged.

(verify: python3 -m pytest -q tests/test_path_lease_integration.py::test_read_only_workflows_do_not_acquire_writer_lease)

## R10 Multiprocess non-serialization proof

At least 32 processes SHALL acquire disjoint paths, overlap their held intervals, and
release cleanly. Total elapsed time SHALL prove work was concurrent rather than one
workspace-wide serialized queue; the registry ends with no live leases.

(verify: python3 -m pytest -q tests/test_path_leases_load.py)

## R11 Canonical runtime negotiation and fail-fast reload

The IDE workspace bootstrap SHALL replace its process with the command and interpreter
declared by canonical `.apatch/mcp.json`, use isolated Python import mode, remove an
inherited `PYTHONPATH`, and bind the selected workspace explicitly. The canonical child
SHALL refuse a loaded-package/install version mismatch before serving tools.

`apatch_doctor` SHALL expose writer protocol v2, its internal path-lease API, loaded
runtime path/version/PID, registry revision, compatibility-guard state, and whether an
MCP reload is required. A client below the workspace's minimum writer protocol SHALL
fail with `MCP_WRITER_PROTOCOL_MISMATCH` before opening session state or mutating source.
The response SHALL instruct the human to restart MCP and SHALL NOT instruct anyone to
delete the compatibility guard. Two full `execute_next` SPEC write-sets over disjoint
paths SHALL apply concurrently and finalize while that guard remains live.

(verify: python3 -m pytest -q tests/test_workspace_launcher.py tests/test_writer_protocol_preflight.py tests/test_path_lease_spec_e2e.py)

## RFP traceability

| RFP ID | SPEC Rk | Disposition |
|---|---|---|
| PL-P0-1 | R1 | covered |
| PL-P0-2 | R2 | covered |
| PL-P0-3 | R3 | covered |
| PL-P0-4 | R4 | covered |
| PL-P0-5 | R5 | covered |
| PL-P0-6 | R6 | covered |
| PL-P0-7 | R7 | covered |
| PL-P0-8 | R8 | covered |
| PL-P0-9 | R9 | covered |
| PL-P0-10 | R10 | covered |
| PL-P0-11 | R11 | covered |
