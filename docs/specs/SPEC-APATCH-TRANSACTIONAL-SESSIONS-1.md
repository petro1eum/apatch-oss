# SPEC-APATCH-TRANSACTIONAL-SESSIONS-1 -- Transactional Multi-Agent Sessions

> **apatch artifact:** `spec:SPEC-APATCH-TRANSACTIONAL-SESSIONS-1`

## R0 RFP traceability gate

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| TS-A | R1 | covered |
| TS-B | R2 | covered |
| TS-C | R3 | covered |
| TS-D | R4 | covered |
| TS-E | R5 | covered |
| TS-F | R6 | covered |
| TS-G | R7 | covered |
| TS-H | R8 | covered |
| TS-I | R9 | covered |
| TS-J | R10 | covered |

(verify: python3 -c "from pathlib import Path; t=Path('docs/specs/SPEC-APATCH-TRANSACTIONAL-SESSIONS-1.md').read_text(); ids='TS-A TS-B TS-C TS-D TS-E TS-F TS-G TS-H TS-I TS-J'.split(); assert all(f'| {x} | R' in t for x in ids)")

## R1 Opaque session capability

Session start SHALL create a collision-resistant session id and a one-time
secret token, persist only the token digest, and expose no plaintext token in
later state views.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_session_capability_is_unique_and_token_is_not_persisted)

## R2 Exact lane resolution

An explicit session id SHALL resolve exactly its owning lane. More than one
active lane without an explicit capability SHALL fail closed instead of choosing
the newest lane.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_session_binding_routes_exact_lane tests/test_transactional_sessions.py::test_ambiguous_lane_has_no_newest_fallback)

## R3 Bound MCP lifecycle

Manual generate, apply, verify, attest, and end entry points SHALL reject missing
or foreign capabilities without mutating the active session.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_foreign_capability_cannot_finalize_session tests/test_transactional_sessions.py::test_enrich_cas_does_not_touch_replacement_session)

## R4 Atomic session CAS

Session state writes SHALL be atomic, monotonically revisioned, and reject an
unexpected session or revision.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_session_state_revision_and_compare_swap)

## R5 Path-scoped writer lease

A mutation lease SHALL cover the exact paths its session writes. A second session in
another lane SHALL acquire a disjoint path set concurrently, and SHALL receive
LEASE_CONFLICT on an overlapping path even from a different process.

This supersedes the workspace-global lease the requirement first demanded. When path
scoping replaced it the gate was renamed with the behaviour, the stated requirement
kept asking for what the product had retired, and the check went unrunnable rather than
red -- so nothing reported the contradiction.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_writer_lease_is_path_scoped_across_lanes)

## R6 Immutable patch ownership

Generated patch logs SHALL bind owner session and SHA-256. Foreign ownership or
content drift SHALL be rejected before project mutation.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_patch_log_owner_and_digest_are_enforced)

## R7 Exact session end

session_end SHALL require the expected capability and unregister, release, and
clean only that session.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_session_end_cannot_close_replacement)

## R8 Rebind ownership

rebind-stale SHALL stop when open_session fails and SHALL never verify, attest,
or close a pre-existing foreign session.

(verify: python3 -m pytest -q tests/test_spec_rebind.py::test_rebind_does_not_touch_foreign_active_session)

## R9 Read-only lifecycle purity

Doctor, session/status, spec status, coverage, and lint projections SHALL not
advance or overwrite session phase.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py::test_read_only_enrichment_does_not_persist_phase)

## R10 Regression and protocol parity

Focused transactional tests, MCP schema tests, session recovery, spec rebind,
runtime lifecycle, and the full suite SHALL pass. Documentation SHALL teach the
capability-bound manual workflow and one-call orchestrator compatibility.

(verify: python3 -m pytest -q tests/test_transactional_sessions.py tests/test_lane_context.py tests/test_spec_rebind.py tests/test_runtime_rfp004.py tests/test_session_recovery.py tests/test_mcp.py)

## RFP traceability

| RFP ID | SPEC Rk | Disposition |
|---|---|---|
| TS-A | R1 | covered |
| TS-B | R2 | covered |
| TS-C | R3 | covered |
| TS-D | R4 | covered |
| TS-E | R5 | covered |
| TS-F | R6 | covered |
| TS-G | R7 | covered |
| TS-H | R8 | covered |
| TS-I | R9 | covered |
| TS-J | R10 | covered |
