# WorkAsset Privacy And IP Boundary

## Product Promise

apatch does not export raw work. It exports a metadata-only proof summary that a
professional method was used, verified, and attested.

The raw workspace, source files, private prompts, credentials, SSH topology,
customer data, and raw logs stay where they were created unless a separate
human-approved process exports them.

## What The Export May Contain

- WorkAsset id, title, kind, lifecycle.
- Problem signature summary.
- Applicability and trigger signals.
- Outputs as metadata counts or names.
- Spec/RFP references.
- Proof references as ledger ids.
- Verification command references.
- Owner identity key metadata.
- Redaction and portability policy fields.

## What The Export Must Not Contain

- Source code.
- Private prompts.
- Credentials, tokens, keys, or secret paths.
- SSH hosts, jump hosts, usernames, or topology.
- Raw transcript logs.
- Customer records or private input data.
- Commercial calculation fields owned by Human Capital.

## IP Position

A WorkAsset is an evidence record, not an ownership transfer.

It says:

> This method was demonstrated and verified under these requirements.

It does not say:

> apatch owns the underlying employer work, source code, client data, or company
> method.

The export contract therefore includes `rights_claim =
evidence_record_not_ownership_transfer` and requires review when private context
or employment agreements may apply.

## Machine Check

The export bundle must include:

```json
{
  "privacy_boundary": {
    "raw_work_exported": false,
    "source_code_exported": false,
    "private_prompts_exported": false,
    "credentials_exported": false,
    "ssh_topology_exported": false,
    "raw_logs_exported": false,
    "customer_data_exported": false
  }
}
```

`validate_work_asset_export_bundle()` rejects a bundle when any of those flags is
not `false`.
