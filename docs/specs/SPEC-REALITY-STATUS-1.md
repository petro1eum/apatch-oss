# SPEC-REALITY-STATUS-1 — reality status is a result, not a failure

> **apatch artifact:** `spec:SPEC-REALITY-STATUS-1`  
> **Anchors:** [RFP-005](../RFP-005-reference-monitor.md) · discharges reality `REC-7855d16e1965`

## 0. Motivation

`apatch_reality(action='status')` returns coverage in which uncovered/pending debt is a
**valid business result** — that is the whole point of the reality ledger. But
`enrich_tool_response` treats any `ok: false` as an operational failure (injects
`failure`, `error_type`, `recommended_action: rollback`, `phase: blocked`). So merely
*reporting* outstanding debt wrongly marked the session as failed. This was self-caught on
the first live use of the reality loop (reality record `REC-7855d16e1965`).

The status branch must keep operational `ok: true` (the query succeeded) and carry the
coverage signal in a separate `clean` field (`true` = no debt) alongside `uncovered`.

## R1 reality status with debt is not a tool failure (verify: python3 -m pytest tests/test_reality_status_not_failure.py -q) (discharges: REC-7855d16e1965)

`apatch_reality(action='status')` reports operational `ok: true` and the coverage state in
`clean` + `uncovered`; the MCP envelope must not inject `failure` / `error_type` /
`recommended_action: rollback` / `phase: blocked` when debt exists.
