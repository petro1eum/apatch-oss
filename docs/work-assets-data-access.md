# WorkAsset Data Access Matrix

This matrix is the plain-language answer to "who can see what?"

| Layer | Contains | Default Access | Exported By apatch |
|-------|----------|----------------|--------------------|
| Raw workspace | Source files, local configs, unfiltered context | Workspace owner / repo ACL | No |
| Raw agent transcript | Chat/tool transcript, prompts, command details | Local machine / IDE owner | No |
| TrustChain ledger detail | Signed operation ids, hashes, attestations | Workspace owner / governed team | No, except references |
| WorkAsset export bundle | Metadata-only proof summaries | Explicit export recipient | Yes, explicit action only |
| Avatar Home summary | Sanitized cards and counts | HC Tracker user/backend policy | Produced by consumer |
| Commercial lens | Consumer-owned business interpretation | HC policy and consent | No |

## Export Access Rule

apatch exports WorkAssets only when a user or governed process calls an export
surface:

```bash
apatch work-assets export --target-dir . --json
```

or the equivalent MCP tool:

```text
apatch_work_asset_export
```

The export contract records this as:

```json
{
  "data_access": {
    "raw_workspace": "not_exported",
    "ledger_detail": "workspace_owner",
    "export_bundle": "explicit_export_only",
    "avatar_summary": "consumer_content_safe",
    "commercial_lens": "consumer_policy_only",
    "external_training": "not_allowed_without_separate_consent"
  }
}
```

## Agent Access Rule

An agent may read the safe export bundle and derived summaries. It should not
need raw SSH topology, credentials, remote source handoff internals, or private
workspace content to use a WorkAsset.
