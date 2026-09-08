# SPEC-WORK-ASSET-EXPORT-1 — WorkAsset export contract

> **apatch artifact:** `spec:SPEC-WORK-ASSET-EXPORT-1`

Locks the producer-side `apatch -> consumer` WorkAsset contract so Avatar Home / HC Capital can ingest a stable metadata bundle without importing apatch internals or waiting for MCP reloads.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A31-A | — | waiver: covered in SPEC-WORK-ASSET-INDEX-1 |
| A31-B | — | waiver: covered in SPEC-WORK-ASSET-INDEX-1 |
| A31-C | — | waiver: deferred to SPEC-WORK-ASSET-LIFECYCLE-1 |
| A31-D | R1 | covered |
| A31-E | — | waiver: deferred to SPEC-WORK-ASSET-SUGGEST-1 |
| A31-F | — | waiver: deferred to SPEC-WORK-ASSET-LIFECYCLE-1 |
| A31-G | R1 | covered |
| A31-H | R2 | covered |
| A31-I | R3, R4 | covered |
| A31-J | — | waiver: deferred to later integration-status slice |
| A31-K | R1 | covered |
| A31-L | R2, R4 | covered |

## R1 Export contract identifiers and schema

`apatch.work_assets` MUST publish an explicit export contract with stable top-level fields:

- `contract_id == apatch.work_asset_export.v1`;
- `contract_version == 1`;
- `kind == work_asset_export`;
- `content_safe == true`;
- redaction flags proving no code, private prompts, credentials, SSH topology, or raw logs are exported.

The contract MUST have a machine-readable JSON Schema and CLI/MCP surfaces to read it without inspecting implementation code.

(verify: `python3 -m pytest tests/test_work_asset_export_contract.py::test_r1_export_contract_schema -q`)

## R2 Content-safe consumer fixture

apatch MUST ship a small fixture bundle under `docs/examples/` using the same contract id/version. The fixture MUST be safe to commit, copy into consumer tests, and parse without TrustChain or apatch runtime access.

(verify: `python3 -m pytest tests/test_work_asset_export_contract.py::test_r2_export_fixture_is_content_safe -q`)

## R3 Economic boundary remains outside apatch

The export contract MUST NOT add PI/GPI, Creator Bonus, clearing, escrow, marketplace, or price logic. HC Capital may compute economics from the metadata later; apatch only publishes proof-backed WorkAsset evidence.

(verify: `python3 -m pytest tests/test_work_asset_export_contract.py::test_r3_export_contract_keeps_economics_out -q`)

## R4 Privacy, access, portability, consent, and retention policy

The export contract MUST include machine-readable policy fields:

- `privacy_boundary`: raw work, source code, private prompts, credentials, SSH topology, raw logs, and customer data are not exported;
- `data_access`: raw workspace is not exported, export bundle is explicit-action only, external training requires separate consent;
- `consent_retention`: export requires an explicit action, external training is disabled by default, consumer caches are governed by consumer retention policy;
- per-asset `portability_policy`: content-safe, no code/prompt/credential/SSH/raw-log export, portability class, rights claim, and employer review requirement.

(verify: `python3 -m pytest tests/test_work_asset_export_contract.py::test_r4_export_policy_rejects_raw_work_and_training -q`)
