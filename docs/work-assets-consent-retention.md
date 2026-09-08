# WorkAsset Consent And Retention

## Consent Model

WorkAsset export is opt-in by action.

apatch may build a local index from the governed workspace, but a consumer only
receives a bundle when a user or governed process explicitly calls export.

The export contract records:

```json
{
  "consent_retention": {
    "export_requires_explicit_action": true,
    "external_training_allowed": false,
    "consumer_cache_owner": "consumer_workspace",
    "retention": "consumer_policy",
    "revocation": "remove_exports_and_consumer_caches_keep_local_audit_hashes"
  }
}
```

## Model Training

The default is simple:

> No model training from WorkAsset exports without separate consent.

The export bundle is a product data contract, not a training-data license.

## Retention Model

There are three separate stores:

| Store | Retention Owner | Deletion Behavior |
|-------|-----------------|-------------------|
| Local workspace evidence | Workspace owner | Repo/workspace policy |
| Local TrustChain audit hashes | Workspace owner / governed team | Keep audit hashes unless local policy removes ledger |
| Consumer export/cache | Consumer system | Remove on revocation or retention expiry |

Revocation removes exported bundles and consumer caches. It does not rewrite a
local audit ledger unless the workspace owner explicitly applies a local data
retention policy.

## Minimum Consumer Requirement

Any consumer that stores WorkAsset summaries must document:

- who can read summaries;
- how long summaries are retained;
- how a user revokes export/cached summaries;
- whether any downstream sharing is enabled;
- that external training remains disabled without separate consent.
