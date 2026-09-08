# SPEC-COVERAGE-1 - Requirement coverage & staleness (RFP-010)

> **Status:** attested (2026-06-10) - **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-COVERAGE-1`
> **Anchors:** RFP-007 (executable specs), RFP-009 (spec run), RFP-010 (requirement coverage)

## 0. Motivation

`apatch_spec_status` today is effectively binary: `attested` / `pending` (`stale` exists
only for spec-text changes). The real third state is **code drifting out from under an
attestation**: R7 was attested a month ago, then files touched by its session changed -
nobody knows whether R7 still holds.

**Principle:** staleness is computed **deterministically** from the ledger plus file
hashes. No LLM, no agent-writable fields - the state cannot be faked, exactly like
`attested` in RFP-007.

Success criterion:

```text
apatch_spec_coverage(spec='SPEC-X')
# -> R1 attested | R2 stale (drifted: [src/auth/service.py]) | R3 pending
```

Non-goals (this spec): semantic verification of requirement meaning; LLM gap analysis;
cross-repository coverage.

## R1 Ledger-derived file sets per requirement

A `spec_coverage` engine derives, for every attested `Rk`, the file set of its governed
session (mutations recorded in the TrustChain ledger bound to that requirement's artifact anchor) together
with each file's sha256 at attestation time. Source of truth is the signed ledger only;
the field is never agent-writable.

(verify: python3 -m pytest tests/test_spec_coverage.py::test_file_sets_from_ledger -q)

## R2 Staleness from file drift

A requirement flips `attested -> stale` when (a) its `## Rk` text changed after
attestation (existing RFP-007 rule) OR (b) the sha256 of any file in its attested file
set differs from the current working tree. The response carries `drifted[]` - the list
of mismatching files.

(verify: python3 -m pytest tests/test_spec_coverage.py::test_stale_on_file_drift -q)

## R3 MCP tool and CLI - coverage matrix

`apatch_spec_coverage` (MCP) and `apatch spec coverage` (CLI) return a per-Rk matrix:
`{state: attested|stale|pending, files[], drifted[], attested_at, session_id, op_ids[]}`
plus an aggregate `{total, attested, stale, pending}`. The tool is registered in MCP
(`tests/test_mcp.py` expected updated, `mcp_health.tool_count` >= 61).

(verify: python3 -m pytest tests/test_spec_coverage.py::test_mcp_spec_coverage_registered -q)

## R4 spec_status and spec_run integration

`apatch_spec_status` reports `stale` rows with a reason (`spec_text_changed` |
`file_drift`) and keeps `done: false` while any Rk is stale. `apatch_spec_run` treats
stale Rk as pending (re-run eligible; needles required or verify precheck applies).

(verify: python3 -m pytest tests/test_spec_coverage.py::test_spec_status_reports_stale -q)

## R5 Doctor and playbook surface

`apatch_doctor` -> `spec_execution.states` documents stale with file-drift semantics;
`docs/AGENTS.template.md` section 3K adds an `apatch_spec_coverage` step after
`spec_status`.

(verify: python3 -m pytest tests/test_spec_coverage.py::test_doctor_and_template_surface -q)

## Non-goals

- Behavior contracts per requirement (verify-lint hardening is a separate RFP).
- Automatic re-run of stale Rk without an explicit call.
- Coverage below Rk granularity.