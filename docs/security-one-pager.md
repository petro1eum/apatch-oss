# apatch — Security & Governance One-Pager

For security questionnaires and enterprise procurement.

**Audience:** apatch is operated by **developers and AI agents**; this page is for **governance review**. Plain-language overview (RU): [for-leaders.md](./for-leaders.md) · [engineering-truth-overview.md](./engineering-truth-overview.md).

## What apatch is

**Governed mutation runtime** for AI-assisted code changes: atomic apply, policy enforcement, cryptographic attestation. Not a VCS replacement — a governance layer above the working tree.

**Current scale:** 17 MCP tools by default / 129 full · 11 self-verified specifications · 9-layer engineering truth stack.

## Core Invariant

Every governed change must follow:

**Intent → Session → Mutation → Verification → Attestation | Rollback**

## Controls

| Risk | Control | Mechanism |
|------|---------|-----------|
| Unauthorized writes | Sandbox | MCP whitelist hooks + protected zone enforcement |
| Unbounded agent changes | Session budget | Chunks, mass-apply guard, checkpoint/rollback |
| Unverified merge | Multi-layer verify | `verify_run`, semantic verify, arch/db checks, ci-gate |
| Unattested changes | TrustChain | Ed25519 signatures; enforce mode blocks git commit |
| Cross-team conflicts | Interference detection | Pre-execution analysis of parallel specifications |
| Audit trail | Event ledger | `.apatch/events.jsonl` + `.trustchain/` with named identities |
| Plan accountability | Plan artifact | Registered decision + execution plan, version-tracked, signed |
| Stale approvals | Coverage drift | Auto-detect when files change after attestation |
| Theater gates (green but tests nothing) | Gate falsification | `apatch probe falsify` corrupts guarded code — the gate must go RED; `regress`/`ratify` cover no-new-failures / still-green |
| Unaddressed observed reality | Reality ledger | `apatch reality` — bugs/incidents/feedback are the source of truth; `uncovered` = requirements not yet discharged by a green gate (derived debt) |

## TrustChain modes

| Mode | Behavior |
|------|----------|
| `audit_pending` | Ledger created on first mutation |
| `audit` | Signed commits; soft failure |
| `enforce` | Rollback on failed notarization; commit blocked |

Check: `apatch attestation show --json` → `attestation.mode`

## Named identities

Every ledger operation carries `signed_by` / `key_id` — both **human engineers** and **AI agents** are enrolled as named identities with Ed25519 keypairs. No anonymous mutations. Agent identity can be issued and revoked by the organization (PKI: CA → leaf certificate → root anchor).

## WorkAssets and data boundary

WorkAssets are metadata-only professional-method evidence records. They are not
raw workspace exports and do not transfer ownership of source code, prompts,
credentials, customer data, or employer implementation details.

Security review entry points:

- [WorkAsset Director Brief](./work-assets-director-brief.md)
- [WorkAsset Engineering Guide](./work-assets-engineering-guide.md)
- [Privacy and IP Boundary](./work-assets-privacy-ip.md)
- [Data Access Matrix](./work-assets-data-access.md)
- [Portability Policy](./work-assets-portability-policy.md)
- [Consent and Retention](./work-assets-consent-retention.md)

Required export policy:

```text
raw_workspace     not_exported
export_bundle     explicit_export_only
external_training not_allowed_without_separate_consent
rights_claim      evidence_record_not_ownership_transfer
```

## Cross-team coordination

Multiple specifications from different teams (frontend, backend, infra) are analyzed **before execution** for file overlaps and patch-level conflicts. System determines safe execution order or flags irreconcilable specifications requiring human resolution.

## vs Git

| Git | apatch |
|-----|--------|
| Version history | Governed **execution** |
| Commit | Mutation with intent + plan |
| Branch | Session (scoped, budgeted) |
| Signed commit | Attestation (enrolled identity, full chain) |
| Merge conflict (post-facto) | Interference detection (pre-execution) |

## Demo commands

```bash
apatch doctor --json                 # system health + current state
apatch attestation show --json       # trust chain status
apatch spec status --spec SPEC-X     # requirement coverage
apatch console                       # visual dashboard
```

## Hardening

```bash
apatch init-consumer --with-enforcement --with-sandbox --with-ci
```

Full model: [domain.md](./domain.md) · Human overview: [engineering-truth-overview.md](./engineering-truth-overview.md)
