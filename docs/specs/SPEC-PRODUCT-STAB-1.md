# SPEC-PRODUCT-STAB-1 — Product stabilization (tests, hygiene, docs)

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-PRODUCT-STAB-1`
> **Anchors:** productization plan (historical private plan; not included in this OSS snapshot) · Stage 0

## 0. Motivation

Before shipping unified project status and reporting, the repo must be green on CI,
free of one-off staging debris, and documented consistently. This spec covers
test fixes for RFP-016 ephemeral routing, inference-sunset semantics, workspace
launcher env isolation, repository cleanup, and documentation reconciliation.

Non-goals: new product features (`apatch status`, HTML reports) — see
SPEC-PROJECT-STATUS-1 and follow-on specs.

## R1 Ephemeral JSONL tests use resolved out_path

`generate_patch_jsonl_batch` and MCP `apatch_generate` route `patches.jsonl` to
`.apatch/tmp/<session_id>/` when governed. Tests must read `result["out_path"]`
(or MCP response `out_path`), not the caller-supplied path.

(verify: python3 -m pytest tests/test_generate_batch.py::test_generate_batch_multi_needle_same_file tests/test_generate_batch.py::test_generate_batch_create_file tests/test_mcp_stdio_live.py::test_mcp_stdio_apatch_generate_and_apply_roundtrip -q)

## R2 Inference sunset: auto-reconcile then block

`assert_governed_hygiene_allowed` auto-registers inferred artifacts once before
blocking. Tests must cover (a) reconcile clears inferred and allows governed ops,
(b) block when inferred remains after reconcile (mock or unregistrable artifact).

(verify: python3 -m pytest tests/test_gc_cli.py::test_inference_sunset_auto_reconcile tests/test_gc_cli.py::test_inference_sunset_blocks_when_reconcile_fails -q)

## R3 workspace_launcher env isolation in tests

`find_workspace_root` prefers `APATCH_WORKSPACE` / `CURSOR_PROJECT_DIR` before
walking from `cwd`. Walk-only tests must clear those env vars via `monkeypatch`.

(verify: python3 -m pytest tests/test_workspace_launcher.py::test_find_workspace_root -q)

## R4 Remove staging debris from repo root

Delete one-off apply scripts and EPHEMERAL patch logs under `patches/staging/`,
`manifests/staging/`, and root `patches/*` that are not referenced by tests.
Keep `tests/fixtures/**` runtime artifacts required by tests.

(verify: test - ! -d patches/staging && test ! -d manifests/staging && python3 -m pytest tests/ -q --co -q | head -1)

## R5 Documentation reconciliation

Single source for MCP tool count: `apatch_doctor → mcp_health.tool_count`.
Update stale counts in README and RFPs; fix RFP-008 header (executor attested);
add SPEC-INTERFERENCE-4/5 and productization specs to `docs/specs/README.md`.

(verify: python3 -m pytest tests/test_agents_template.py -q && rg -l '65 tools|56 tools|60 tools' docs/ README.md --glob '!docs/specs/**' --glob '!docs/manifests/**' | wc -l | xargs test 0 -eq)

## Non-goals

- Commit/tag 0.6.0 (human release step after attestation)
- PyPI publish or LICENSE selection
