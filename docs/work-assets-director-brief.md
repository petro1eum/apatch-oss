# WorkAsset Director Brief

Audience: executives, security reviewers, procurement, legal, and customer
stakeholders who need to understand what apatch WorkAssets store, expose, and
protect.

## Plain Answer

WorkAssets do not copy the employee's work into a marketplace.

apatch creates a proof-backed summary that says:

```text
This person demonstrated this reusable professional method,
under these requirements,
with these verification references.
```

It does not export the underlying source code, private prompts, credentials,
SSH topology, raw logs, customer records, or employer-owned implementation
details.

## What Is Stored

| Store | What it contains | Who controls it |
|-------|------------------|-----------------|
| Local workspace | Source files, configs, private project context | Workspace owner / repo owner |
| apatch evidence | Operation ids, hashes, verification refs, attestations | Workspace owner / governed team |
| WorkAsset export | Metadata-only professional-method summaries | Explicit export recipient |
| Avatar / HC summary | Sanitized cards and counts | HC backend policy |

## What Is Not Exported

- Source code.
- Private prompts.
- Credentials, tokens, keys, secret paths.
- SSH hosts, usernames, jump hosts, or topology.
- Raw agent transcripts.
- Raw logs.
- Customer data.
- Employer implementation details.
- Pricing, marketplace, clearing, escrow, PI, GPI, or Creator Bonus fields.

## Ownership And IP Position

A WorkAsset is an evidence record, not an ownership transfer.

It proves that a professional method exists and was demonstrated. It does not
claim that apatch owns the employer's code, customer data, proprietary
architecture, or internal process.

The machine contract carries this explicitly:

```text
rights_claim = evidence_record_not_ownership_transfer
```

Assets that depend on private employer or customer context require review before
external reuse.

## Consent Model

WorkAsset export is explicit-action only. A user or governed process must call
the export surface.

Default policy:

```text
external_training_allowed = false
export_requires_explicit_action = true
```

No model training rights are granted by a WorkAsset export. Training would
require a separate future consent flow and a separate policy.

## Access Model

The raw workspace stays local. The consumer receives only a sanitized export
bundle.

```text
raw_workspace     not_exported
export_bundle     explicit_export_only
external_training not_allowed_without_separate_consent
```

This is enforced as a machine-readable contract and validated by consumers such
as HC Tracker before displaying WorkAssets.

## Retention And Revocation

There are separate retention scopes:

| Scope | Default |
|-------|---------|
| Local workspace evidence | Governed by repo/workspace policy |
| Audit hashes | Kept for local audit unless local policy removes them |
| Consumer export/cache | Removed by consumer retention or revocation policy |

Revocation removes exported bundles and consumer caches. It does not silently
rewrite local audit history unless the workspace owner applies a local retention
policy.

## What To Tell A Customer Or Employer

Use this short answer:

> apatch does not export raw work. It exports metadata-only evidence that a
> method was used and verified. The export contract explicitly forbids code,
> prompts, credentials, SSH topology, raw logs, customer data, and ownership
> transfer. Any economic or external sharing layer is opt-in and belongs to the
> consumer product, not the apatch core tool.

## Review Checklist

- Is export explicit, not automatic?
- Does the export contain `privacy_boundary`?
- Are all raw-data export flags false?
- Does every asset say `rights_claim =
  evidence_record_not_ownership_transfer`?
- Is external training disabled?
- Does the consumer fail closed if policy fields are missing?
- Is retention/revocation documented by the consumer?

## Related Documents

- [WorkAssets overview](./work-assets.md)
- [Engineering guide](./work-assets-engineering-guide.md)
- [Privacy and IP Boundary](./work-assets-privacy-ip.md)
- [Data Access Matrix](./work-assets-data-access.md)
- [Portability Policy](./work-assets-portability-policy.md)
- [Consent and Retention](./work-assets-consent-retention.md)
- [Security one-pager](./security-one-pager.md)
