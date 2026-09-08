# SPEC-ONBOARDING-1 — Product onboarding and RFP-020

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-ONBOARDING-1`
> **Anchors:** [RFP-020](../RFP-020-three-views.md) · depends on SPEC-CLI-STATUS-1, SPEC-REPORT-1

## 0. Motivation

External users landing on README see transcript replay, not governed specs.
The product story — one data layer, three views, TrustChain as proof-of-concept
for attribution — must be documented for humans and agents.

Non-goals: marketing site; HC Platform labour-market docs.

## R1 README governed-spec quickstart

Root `README.md` leads with 10-minute path:
`init-consumer → doctor → spec lint → spec_run → status/report`.
Transcript replay moves to secondary section.

(verify: python3 -m pytest tests/test_readme_quickstart.py -q)

## R2 RFP-020 three-views architecture

Add `docs/RFP-020-three-views.md` describing data layer + developer/architect/
manager views, TrustChain attribution bridge to HC Platform, and dependency on
SPEC-PROJECT-STATUS-1 through SPEC-REPORT-1.

(verify: test -f docs/RFP-020-three-views.md && rg -q 'project_status' docs/RFP-020-three-views.md)

## R3 docs index and CLI↔MCP parity matrix

Update `docs/README.md` and `docs/mcp_setup.md` with new commands (`status`,
`report`, `spec list`, `project_status`) and note MCP tool `apatch_project_status`.

(verify: python3 -m pytest tests/test_mcp.py -q)

## Non-goals

- English full translation of README (Russian primary OK)
- PyPI listing copy
