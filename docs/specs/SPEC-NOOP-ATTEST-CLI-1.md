# SPEC-NOOP-ATTEST-CLI-1 — noop-attest is reachable from the CLI

> **apatch artifact:** `spec:SPEC-NOOP-ATTEST-CLI-1`  
> **Anchors:** [RFP-027](../RFP-027-agent-ux-recovery.md)

## 0. Motivation

`apatch_noop_attest` (attest a requirement covered by another Rk's mutation, no marker
file) existed only as an MCP tool. Shell-driving agents and humans had no CLI surface — one
of the overhead levers the contribution timesheet flagged. Add `apatch attestation noop`
mirroring the MCP tool, so attestation ceremony for sibling-covered Rk does not require an
MCP client.

## R1 apatch attestation noop wires --covered-by to noop_attest (verify: python3 -m pytest tests/test_noop_attest_cli.py -q)

`apatch attestation noop --covered-by R1,R4` parses the comma list into `['R1', 'R4']` and
calls `MutationRuntime.noop_attest(covered_by, message=...)`, mirroring the
`apatch_noop_attest` MCP tool. `--message` is optional; `--json` emits the raw result.
