# SPEC-WORK-ASSET-INDEX-1 — WorkAsset schema and read-only index

> **apatch artifact:** `spec:SPEC-WORK-ASSET-INDEX-1`

Implements the first RFP-031 slice: a deterministic, proof-backed WorkAsset / Наработка read model over existing governed apatch evidence. This spec deliberately stops before promotion, reuse tracking, suggestion/apply integration, HC ingest, or economics.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A31-A | R1 | covered |
| A31-B | R2 | covered |
| A31-C | — | waiver: deferred to SPEC-WORK-ASSET-LIFECYCLE-1 |
| A31-D | R3 | covered |
| A31-E | — | waiver: deferred to SPEC-WORK-ASSET-SUGGEST-1 |
| A31-F | — | waiver: deferred to SPEC-WORK-ASSET-LIFECYCLE-1 |
| A31-G | R4 | covered |
| A31-H | — | waiver: deferred to HC consumer spec SPEC-AVATAR-WORK-ASSETS-1 |
| A31-I | R5 | covered |
| A31-J | — | waiver: deferred to later integration-status slice |
| A31-K | R1 | covered |
| A31-L | — | waiver: deferred until lifecycle/export shape settles |

## R1 WorkAsset schema v1

WorkAsset schema v1 MUST expose a deterministic metadata-only record:

- `schema_version`, `kind`, `asset_id`, `asset_kind`, `title`, `owner`, and `lifecycle`;
- `problem_signature`, `applicability`, `trigger_signals`, and `outputs`;
- `spec_refs`, `rfp_refs`, `proof_refs`, `verification_refs`, and `source_evidence`;
- `confidentiality_class`, `portability_policy`, `dependency_lens`, and `reuse`.

The schema MUST NOT include source code, private prompts, credentials, raw SSH topology, raw ledger payloads, or economics fields.

(verify: `python3 -m pytest tests/test_work_assets.py::test_r1_work_asset_schema -q`)

## R2 Deterministic candidate extraction

Candidate extraction MUST be deterministic for a fixed ledger and workspace. The first implementation MUST discover one WorkAsset candidate per spec that has at least one complete `spec:<SPEC>#<Rk>` artifact in traceability coverage. The candidate MUST preserve requirement refs and proof refs while summarizing the spec metadata without embedding spec body content.

(verify: `python3 -m pytest tests/test_work_assets.py::test_r2_candidate_extraction_deterministic -q`)

## R3 CLI and MCP read-only surfaces

apatch MUST expose read-only WorkAsset surfaces:

- CLI: `apatch work-assets list`, `apatch work-assets show`, `apatch work-assets search`, `apatch work-assets export`;
- MCP: `apatch_work_assets`, `apatch_work_asset_show`, `apatch_work_asset_search`, `apatch_work_asset_export`.

These surfaces MUST not mutate workspace files, lifecycle state, sessions, or ledger state.

(verify: `python3 -m pytest tests/test_work_assets.py::test_r3_cli_and_mcp_surfaces -q`)

## R4 Content-safe export bundle

The export bundle MUST be metadata-only and explicitly mark redaction boundaries. It MUST include `content_safe: true`, no source/code/prompts/credentials/SSH topology, and enough proof refs for a consumer such as Avatar Home / HC Capital to ingest metadata fixtures without receiving proprietary implementation details.

(verify: `python3 -m pytest tests/test_work_assets.py::test_r4_export_bundle_redaction_boundary -q`)

## R5 Economic boundary

apatch WorkAsset surfaces MUST NOT implement or expose PI/GPI, Creator Bonus, clearing, escrow, marketplace, or price logic. HC Capital may compute economics from exported metadata later; apatch remains the evidence and portability layer.

(verify: `python3 -m pytest tests/test_work_assets.py::test_r5_economic_boundary -q`)