# SPEC-AGENT-UX-2 — Agent UX hardening (Phase 2)

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-AGENT-UX-2`  
> **Anchors:** [RFP-027](../RFP-027-agent-ux-recovery.md) · [SPEC-AGENT-UX-1](./SPEC-AGENT-UX-1.md) (Phase 1) · [RFP-019](../RFP-019-mcp-scale-lifecycle.md) (guidance)

## 0. Motivation

Phase 2 of RFP-027 — the SHOULD/MAY items waived from Phase 1: noop-attest (no marker
files), per-session playbook dedupe (less context tax), and a self-edit staleness
signal (restart-needed hint). Hit live while dogfooding SPEC-CONTRIB-TIMESHEET-1.

## R0 RFP traceability gate (meta)

RFP-027 Acceptance: F/G/H land here; A–E + I are owned by SPEC-AGENT-UX-1.

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| U27-A | — | waiver: implemented in SPEC-AGENT-UX-1 |
| U27-B | — | waiver: implemented in SPEC-AGENT-UX-1 |
| U27-C | — | waiver: implemented in SPEC-AGENT-UX-1 |
| U27-D | — | waiver: implemented in SPEC-AGENT-UX-1 |
| U27-E | — | waiver: implemented in SPEC-AGENT-UX-1 |
| U27-F | R1 | covered |
| U27-G | R2 | covered |
| U27-H | R3 | covered |
| U27-I | — | waiver: implemented in SPEC-AGENT-UX-1 |

(verify: python3 -m pytest tests/test_agent_ux_phase2.py::test_r0_self_coverage_rfp_027 -q)

## R1 Noop-attest (covered-by, no marker file)

A requirement satisfied by another Rk's mutation reaches `attested` via an
attestation carrying `covered_by` — no fabricated marker file. Coverage recognizes
`covered_by` (`spec._requirement_state` + `traceability`), and a `noop_attest`
runtime path / MCP tool emits it.

(verify: python3 -m pytest tests/test_agent_ux_phase2.py::test_r1_noop_attest_coverage -q)

## R2 Playbook payload dedupe (once per session)

In `full` guidance mode the heavy playbook blocks are emitted on the first
non-doctor tool call per session; subsequent calls return a compact `guidance_ref`
(`guidance_deduped`), cutting repeated multi-KB payloads. `apatch_doctor` always full.

(verify: python3 -m pytest tests/test_agent_ux_phase2.py::test_r2_payload_dedupe_once_per_session -q)

## R3 Self-edit staleness signal

`self_edit_restart_signal(workspace, applied_paths)` flags `restart_required` when
applied files touch `apatch/**` modules already imported by the running MCP server
(pure-logic edits the tool-fingerprint check misses); wired best-effort into
`apply_session`.

(verify: python3 -m pytest tests/test_agent_ux_phase2.py::test_r3_self_edit_restart_signal -q)

## Non-goals

- Rewriting guidance content — only dedupe transport.
- Auto-restarting MCP (human-only; only surface the signal).
- Cross-process guidance dedupe — per running server process is sufficient.
