# SPEC-INTERFERENCE-5 — MCP strategy param parity fix

> **apatch artifact:** `spec:SPEC-INTERFERENCE-5`
> **Anchors:** [SPEC-INTERFERENCE-3](./SPEC-INTERFERENCE-3.md) R3, [RFP-014](../RFP-014-spec-interference-detection.md)

## 0. Motivation

`apatch_spec_interference` MCP incorrectly forwarded `strategy` to
`spec_interference_workspace()`, which does not accept it (TypeError at runtime).
Per SPEC-INTERFERENCE-3 R3, `strategy` belongs on `apatch_spec_schedule` only.

## R1 Remove strategy from interference MCP handler

`apatch_spec_interference` MCP tool no longer declares or passes `strategy`.

(verify: python3 -m pytest tests/test_spec_interference_mcp_strategy.py::test_mcp_interference_enriched_ok -q)

## R2 Wire strategy on schedule MCP handler

`apatch_spec_schedule` MCP exposes `strategy` and forwards it to
`spec_schedule_enriched`.

(verify: python3 -m pytest tests/test_spec_interference_mcp_strategy.py::test_mcp_schedule_strategy_param -q)