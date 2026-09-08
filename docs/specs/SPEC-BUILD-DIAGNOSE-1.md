# SPEC-BUILD-DIAGNOSE-1 — Compiler feedback loop (RFP-018 MVP)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-BUILD-DIAGNOSE-1`
> **Anchors:** [RFP-018](../RFP-018-build-diagnose.md)

## 0. Motivation

Close the loop `build error → structured diagnostic → fix suggestions` without
auto-applying patches. Agents use suggestions to craft governed needles.

## R1 Parse clang missing-member errors

`apatch/build_diagnose.py` exposes `parse_compiler_output` for
`no member named 'X' in 'Y'` diagnostics.

(verify: python3 -m pytest tests/test_build_diagnose.py::test_r1_parse_missing_member -q)

## R2 C++ type member extraction

`extract_cpp_class_members` reads method names from a class/struct body (regex MVP).

(verify: python3 -m pytest tests/test_build_diagnose.py::test_r2_extract_cpp_members -q)

## R3 Suggestions for missing members

`run_build_diagnose` enriches diagnostics with `type_definition` and
`suggestions` (similar member, restore API, adapter).

(verify: python3 -m pytest tests/test_build_diagnose.py::test_r3_suggest_similar_member -q)

## R4 Build artifacts under .apatch/

`run_build_diagnose(write_artifacts=true)` writes `build_log.json` and
`diagnostics.json`.

(verify: python3 -m pytest tests/test_build_diagnose.py::test_r4_artifacts_written -q)

## R5 Workflow + MCP tool

`build_diagnose_workspace` in `workflows.py` and `apatch_build_diagnose` MCP tool.

(verify: python3 -m pytest tests/test_build_diagnose.py::test_r5_workflow_wrapper tests/test_build_diagnose.py::test_r6_mcp_tool_registered -q)

## R6 Auto-link on VERIFY_FAILED

`enrich_verify_failure` attaches `build_diagnose` when verify stderr looks like
compiler output (`verify_run`, `apply_from_logs`, `apply_session` rollback path).

(verify: python3 -m pytest tests/test_build_diagnose.py::test_r6_enrich_verify_failure_attaches_diagnose tests/test_build_diagnose.py::test_r7_apply_verify_rollback_includes_build_diagnose -q)
