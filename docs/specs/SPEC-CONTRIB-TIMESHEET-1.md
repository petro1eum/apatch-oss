# SPEC-CONTRIB-TIMESHEET-1 — Per-identity contribution ledger & cross-project timesheet

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-CONTRIB-TIMESHEET-1`  
> **Anchors:** [RFP-026](../RFP-026-contribution-timesheet.md) · [RFP-025](../RFP-025-avatar-foundation.md) (AF-5/AF-8) · [RFP-006](../RFP-006-artifact-anchored-intent.md) · [rfp-authoring.md](../rfp-authoring.md)

## 0. Motivation

Turn the already-signed governed ledger into a per-identity, cross-project time &
contribution record. Each attested session emits one signed `ContributionEvent`
keyed to the user's TrustChain certificate; `apatch timesheet` aggregates them
across projects and re-verifies against the raw ledger. Engine for RFP-025 AF-5/AF-8
and HC ADR-006.

## R0 RFP traceability gate (meta)

RFP-026 Acceptance rows map to requirements below (covers C26-K).

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| C26-A | R1 | covered |
| C26-B | R2 | covered |
| C26-C | R3 | covered |
| C26-D | R4 | covered |
| C26-E | R5 | covered |
| C26-F | R6 | covered |
| C26-G | R7 | covered |
| C26-H | R8 | covered |
| C26-I | R9 | covered |
| C26-J | R1 | covered |
| C26-K | R0 | covered |

(verify: python3 -m pytest tests/test_contribution_timesheet.py::test_r0_self_coverage_rfp_026 -q)

## R1 ContributionEvent schema (shared current version, back-compatible)

`apatch/contribution.py` builds its `ContributionEvent` using the current
`avatar-contract` `SCHEMA_VERSION` (v3 adds a signed, timezone-aware `created_at`) from
the SHARED `avatar-contract` package — the one canonical schema imported by both
apatch and HC (Avatar Architecture Canon §7, Rule 1), **not** a second local
definition. apatch's class is a thin subclass adding only a `to_dict()` alias, and
`emit_contribution` calls the contract's `validate()` before writing. Fields:
`event_id` (= `idempotency_key`), `avatar_id` (= identity `key_id`, the cross-org
avatar anchor), `source="apatch"`, `trust_level`, `identity`, `project`, `session`
(intent, artifacts, started_at/ended_at/duration_sec), `volume`
(ops/files_touched/insertions/deletions), `proof_ref` (op_ids/head/committed_at —
renamed from v1's `attestation`), signed producer `created_at`, and the contract defaults (`methodology_tags`). Re-emission returns an existing receipt byte-for-byte, so the creation time and signature cannot drift under the same idempotency key. `created_at` is producer evidence, not a trusted certificate timestamp; HC validates an unanchored event at receipt time.
Canonical JSON serialization is stable (sorted keys) and maps 1:1 to the HC ADR-006
`ContributionEvent` shape (covers C26-J). v1 and v2 receipts remain valid (see R10).

(verify: python3 -m pytest tests/test_contribution_event.py::test_r1_schema -q)

## R2 Emit signed receipt at session boundary

On `session_end`/`attest`, apatch builds the event from session + ledger and
commits one signed `contribution:<key_id>@<session_id>` leaf via the existing
TrustChain path (no parallel signer). No event when the session made 0 mutations.

(verify: python3 -m pytest tests/test_contribution_event.py::test_r2_emit_on_session_boundary -q)

## R3 Identity from TrustChain certificate

`resolve_identity()` derives `key_id` (SPKI hash) + `cert_fingerprint` from the
Platform-CA-issued `agent.crt`; `agent_id` label included. No CA cert →
`ca="legacy"` and `trust_level="claimed"` (the canon §7.2/§8 un-enrolled floor —
not `attested`; was `audit`, which is not a canon trust level).

(verify: python3 -m pytest tests/test_contribution_event.py::test_r3_identity_from_cert -q)

## R4 Cross-project aggregation by identity

`project.id` is stable across machines (sha256 of normalized git remote URL,
fallback realpath of repo root). Timesheet reads `contribution` leaves from the
global ledger and aggregates per `key_id` across all projects.

(verify: python3 -m pytest tests/test_timesheet.py::test_r4_cross_project_aggregate -q)

## R5 `apatch timesheet` CLI

CLI aggregates events with `--by identity/project/spec/day` (combinable), reports
time (duration) and volume, supports `--since`/`--until`, `--agent`, `--project`,
and `--format json|md`.

(verify: python3 -m pytest tests/test_timesheet.py::test_r5_cli_group_by -q)

## R6 Verifiable timesheet

`apatch timesheet --verify` re-derives each event from raw signed ledger ops and
flags `drift`, missing signature, or unknown key; non-zero exit on tamper.

(verify: python3 -m pytest tests/test_timesheet.py::test_r6_verify_detects_tamper -q)

## R7 Multi-user, privacy-bounded

Per-`key_id` breakdown for teams. Events carry no source code, secrets, or prompt
content — only paths, hashes, and counts (RFP-025 AF-3 non-invasiveness).

(verify: python3 -m pytest tests/test_timesheet.py::test_r7_multi_user_privacy -q)

## R8 Honest work-time estimate (default)

Governed ops are sub-second, so a session's recorded `duration_sec` is ~0 and the
real work happens *between* ops. `hours` is therefore the **honest** estimate:
`max(recorded duration, op-spacing estimate)`, never under-counting a measured
session nor reporting ~0 for a real day of bursty governed work. The op-spacing
estimate is the git-hours heuristic — cluster the op stream at gaps longer than
`--idle-gap` minutes (default 30, a break), sum the in-cluster spans, and add
`--ramp-up` minutes per cluster (default 15) for the work done before its first
recorded op. A single op counts as one ramp-up; `estimated_sec` and `active_sec`
are exposed alongside `duration_sec` for transparency. It is an estimate, not a
stopwatch.

(verify: python3 -m pytest tests/test_timesheet.py::test_r8_idle_gap_split tests/test_timesheet.py::test_r8b_estimate_active_seconds_git_hours tests/test_timesheet.py::test_r8c_hours_are_honest_for_zero_duration_bursts -q)

## R9 Read-only MCP `apatch_timesheet`

MCP tool `apatch_timesheet` mirrors the CLI for in-chat queries and never mutates.

(verify: python3 -m pytest tests/test_timesheet_mcp.py::test_r9_mcp_timesheet_readonly -q)

## R10 Schema v2 migration is signature-back-compatible

The `attestation`→`proof_ref` rename bumps `schema_version` 1→2 for newly emitted
events. Because the Ed25519 signature is computed over the canonical event minus
`signature`, already-emitted v1 receipts (carrying `attestation`) still verify
unchanged via `verify_event` — no re-signing, no key branching. `apatch timesheet
--verify` accepts both `attestation` (v1) and `proof_ref` (v2) as known keys, so v1
receipts raise no false "unknown key" drift (R6). Seam for the cross-org HC
ContributionEvent schema package (Avatar canon Phase 1).

(verify: python3 -m pytest tests/test_contribution_event.py::test_r10_v1_receipt_backcompat -q)

## Non-goals

- Payroll rates, invoicing, or money math (HC layer).
- Activity tracking outside governed sessions (only attested work counts).
- LLM effort/value scoring (deterministic counts only).
- Re-signing historical pre-feature sessions (derivable via `--verify`, not re-attested).
