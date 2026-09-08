# WorkAsset Engineering Guide

Audience: engineers, tech leads, AI-agent integrators, and backend owners who
need to produce, validate, or consume WorkAsset exports.

## What Engineers Must Know

WorkAsset is a metadata-only evidence record. It is not a source-code archive,
not a prompt dump, not an SSH handoff, and not a license transfer.

The stable producer-to-consumer contract is:

```text
contract_id      apatch.work_asset_export.v1
contract_version 1
kind             work_asset_export
content_safe     true
```

Producer implementation:

- `apatch/work_assets.py`
- `tests/test_work_asset_export_contract.py`
- `docs/specs/SPEC-WORK-ASSET-EXPORT-1.md`

Consumer example:

- Human Capital `HC_Platform/07_Professionals/HC_Tracker/backend/app/services/work_asset_bridge.py`
- Human Capital `HC_Platform/docs/WORK_ASSETS.md`

## Producer Flow

From a governed workspace:

```bash
apatch work-assets export --target-dir . --json > .apatch/work_assets_export.json
```

Or through MCP:

```text
apatch_work_asset_export
apatch_work_asset_export_schema
```

The export must include:

- `redaction`
- `privacy_boundary`
- `data_access`
- `consent_retention`
- per-asset `portability_policy`

## Consumer Rules

A consumer must fail closed when any required boundary is missing or unsafe.

Required false flags:

```text
redaction.exports_code
redaction.exports_private_prompts
redaction.exports_credentials
redaction.exports_ssh_topology
redaction.raw_logs

privacy_boundary.raw_work_exported
privacy_boundary.source_code_exported
privacy_boundary.private_prompts_exported
privacy_boundary.credentials_exported
privacy_boundary.ssh_topology_exported
privacy_boundary.raw_logs_exported
privacy_boundary.customer_data_exported
```

Required policy values:

```text
data_access.raw_workspace      not_exported
data_access.export_bundle      explicit_export_only
data_access.external_training  not_allowed_without_separate_consent

consent_retention.export_requires_explicit_action  true
consent_retention.external_training_allowed        false
```

Required per-asset policy:

```text
portability_policy.content_safe      true
portability_policy.exports_code      false
portability_policy.exports_private_prompts false
portability_policy.exports_credentials      false
portability_policy.exports_ssh_topology     false
portability_policy.raw_logs          false
portability_policy.rights_claim      evidence_record_not_ownership_transfer
```

If a consumer cannot validate these fields, it should return an unavailable
state and keep the UI usable without WorkAssets.

## Integration Pattern

Preferred production pattern:

```text
apatch workspace
  -> explicit export bundle
  -> consumer backend validation
  -> sanitized Avatar / WorkAsset DTO
  -> UI cards and counts
```

Avoid importing apatch internals in production consumers. Direct import is only
for local dogfood and development fallback.

## What Not To Build

Do not add these fields to apatch exports:

- PI or GPI;
- Creator Bonus;
- price;
- marketplace;
- escrow;
- clearing;
- raw code;
- raw prompts;
- credentials;
- SSH host topology;
- raw logs.

Human Capital / HC Capital may build an opt-in economic lens later. apatch owns
proof and export boundaries, not market pricing or transaction clearing.

## Verification

Producer:

```bash
python3 -m pytest tests/test_work_asset_export_contract.py tests/test_work_assets.py -q
```

Consumer example:

```bash
cd /path/to/Human_Capital/HC_Platform/07_Professionals/HC_Tracker/backend
PYTHONPATH=../../.. python3 -m pytest tests/test_work_asset_bridge_api.py -q
```

## Related Documents

- [WorkAssets overview](./work-assets.md)
- [Privacy and IP Boundary](./work-assets-privacy-ip.md)
- [Data Access Matrix](./work-assets-data-access.md)
- [Portability Policy](./work-assets-portability-policy.md)
- [Consent and Retention](./work-assets-consent-retention.md)
- [Director brief](./work-assets-director-brief.md)
