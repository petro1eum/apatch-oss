# WorkAssets / Narabotki

WorkAsset is the content-safe unit that connects governed engineering evidence
to a reusable professional method. apatch produces and proves WorkAssets; Human
Capital and HC Capital consume the metadata and decide how to present or
monetize it.

## Boundary

apatch owns:

- deterministic candidate extraction from RFP/SPEC/session/attestation evidence;
- metadata-only list, show, search, schema, and export surfaces;
- TrustChain-backed proof refs and verification refs;
- the stable export contract `apatch.work_asset_export.v1`.

apatch does not own:

- PI, GPI, Creator Bonus, escrow, clearing, pricing, or marketplace logic;
- private source-code export;
- private prompts, credentials, SSH topology, or raw logs.

That economic layer belongs to Human Capital / HC Capital.

## Governance Documents

These documents are the product/security answer to "are you stealing
information?", split by audience and policy area:

- [Engineering Guide](./work-assets-engineering-guide.md) — producer/consumer
  contract, validation rules, integration and tests.
- [Director Brief](./work-assets-director-brief.md) — plain-language answer for
  executives, security, procurement, legal, and customers.
- [Privacy and IP Boundary](./work-assets-privacy-ip.md)
- [Data Access Matrix](./work-assets-data-access.md)
- [Portability Policy](./work-assets-portability-policy.md)
- [Consent and Retention](./work-assets-consent-retention.md)

## Producer Commands

From an apatch checkout or any governed workspace:

```bash
apatch work-assets list --target-dir . --json
apatch work-assets search "remote ssh" --target-dir . --json
apatch work-assets show <asset_id> --target-dir . --json
apatch work-assets schema --json
apatch work-assets export --target-dir . --json > .apatch/work_assets_export.json
```

MCP equivalents:

- `apatch_work_assets`
- `apatch_work_asset_show`
- `apatch_work_asset_search`
- `apatch_work_asset_export`
- `apatch_work_asset_export_schema`

If the running MCP server was started before these tools were installed, restart
the MCP server. CLI commands import the current source directly and do not need
an MCP reload.

## Export Contract

The stable producer-to-consumer bundle is:

```text
contract_id      apatch.work_asset_export.v1
contract_version 1
kind             work_asset_export
content_safe     true
```

Required safety flags are all false:

- `redaction.exports_code`
- `redaction.exports_private_prompts`
- `redaction.exports_credentials`
- `redaction.exports_ssh_topology`
- `redaction.raw_logs`

Consumers should validate the top-level contract fields before showing assets
or deriving any readiness signal.

The contract also carries machine-readable governance policy:

- `privacy_boundary` proves raw work, code, prompts, credentials, SSH topology,
  raw logs, and customer data are not exported;
- `data_access` states that raw workspace data is not exported and bundles are
  explicit-export only;
- `consent_retention` states that export requires an explicit action and external
  training is disabled without separate consent;
- each asset carries `portability_policy`, including `portability_class` and
  `rights_claim`.

## Human Capital Onboarding

Recommended integration path:

1. Produce an export bundle:

   ```bash
   cd /path/to/apatch
   apatch work-assets export --target-dir . --json > /path/to/work_assets_export.json
   ```

2. Configure the consumer backend to read that file:

   ```bash
   APATCH_WORK_ASSET_EXPORT_PATH=/path/to/work_assets_export.json
   ```

3. Keep a development fallback only for local checkouts:

   ```bash
   APATCH_REPO_PATH=/path/to/apatch
   ```

The stable file path is preferred because consumer systems can ingest it without
importing apatch internals or waiting for MCP reloads. The direct import fallback
is useful for local development and dogfood runs.

## Verification

Producer-side contract:

```bash
python3 -m pytest tests/test_work_asset_export_contract.py tests/test_work_assets.py -q
```

Consumer-side HC bridge:

```bash
cd /path/to/Human_Capital
cd HC_Platform/07_Professionals/HC_Tracker/backend
PYTHONPATH=../../.. python3 -m pytest tests/test_work_asset_bridge_api.py -q
```

Related specs:

- [SPEC-WORK-ASSET-INDEX-1](./specs/SPEC-WORK-ASSET-INDEX-1.md)
- [SPEC-WORK-ASSET-EXPORT-1](./specs/SPEC-WORK-ASSET-EXPORT-1.md)
- Human Capital: `HC_Platform/docs/specs/SPEC-AVATAR-WORK-ASSETS-1.md`
