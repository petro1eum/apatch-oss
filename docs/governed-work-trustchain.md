# TrustChain governed work

This guide onboards one APatch workspace into a TrustChain ProjectGroup without
copying source code, prompts, repository paths or credentials to Platform.

Canonical contracts:

- [RFP-043](./RFP-043-trustchain-governed-work-bindings.md)
- [SPEC-GOVERNED-WORK-BINDINGS-1](./specs/SPEC-GOVERNED-WORK-BINDINGS-1.md)
- [apatch.change.v1](./contracts/apatch.change.v1.schema.json)
- [apatch.timesheet-draft.v1](./contracts/apatch.timesheet-draft.v1.schema.json)
- [apatch.work-evidence-bundle.v1](./contracts/apatch.work-evidence-bundle.v1.schema.json)
- [TrustChain admission receipt](./contracts/trustchain.governed-work-admission-receipt.v1.schema.json)

## What the user receives

The TrustChain Project page or an administrator must provide these exact public
values; a new user is not expected to invent them:

1. Canonical signed-service URL (`https://clients.trust-chain.ai` in
   production) for ProjectGroup, WorkProgram and governed-work requests.
2. Canonical public verification URL (`https://keys.trust-chain.ai` in
   production). It serves public trust material and is never `platform_url`.
3. Exact Platform subject alias authorized for the ProjectGroup.
4. Governed-work authority key id and Ed25519 public key used to verify Platform
   bindings and admission receipts.
5. Tenant id, ProjectGroup id, WorkProgram id/hash and optional ContextRelease
   id/manifest hash.

`platform_url` below is always the signed-service URL. Public verification and
signed internal API surfaces are intentionally different.

APatch must already have an enrolled Ed25519 identity. Configuration derives
both Platform registration mappings from that identity and returns public keys
only. The private key remains inside the configured PEM/command/HSM provider;
there is no service token, token environment variable or legacy fallback.

## MCP profile

This integration is a professional full-profile workflow. Configure the MCP
process with:

```text
APATCH_MCP_PROFILE=full
```

The full API remains available by design. Compact is useful for generic
onboarding, but it does not expose governed-work specialist tools. After an
APatch package upgrade or MCP catalog change, the human restarts the MCP host
once; ordinary work, retries and offline recovery require no restart.

## One-time workspace configuration

Call:

```text
apatch_governed_work_configure(
  target_dir="@workspace",
  platform_url="https://clients.trust-chain.ai",
  client_id="<authorized-platform-subject>",
  binding_authority_keys={"<platform-authority-key-id>": "<unpadded-base64url-public-key>"}
)
```

The result contains `public_registration` with two operator-safe values:

- `governed_work_public_keys`: purpose -> document key id -> public key, for
  `TC_APATCH_GOVERNED_WORK_PUBLIC_KEYS_JSON`;
- `service_request_public_keys`: full `sha256:<64hex>` key id -> public key, for
  `TC_PROJECT_GROUP_SERVICE_PUBLIC_KEYS_JSON`.

Only these public mappings are installed on Platform. `client_id` is the exact
Platform `subject` alias used by internal governed-work requests and must
resolve to an authorized ProjectGroup actor.

Configuration is immutable by content. A different authority or endpoint is a
new explicit configuration decision, not silent overwrite. Move an existing
workspace with the CAS/idempotent operation instead of editing JSON:

```text
apatch_governed_work_transition_endpoint(
  target_dir="@workspace",
  expected_platform_url="https://keys.trust-chain.ai",
  platform_url="https://clients.trust-chain.ai",
  idempotency_key="governed-work-clients-transition-20260831"
)
```

The operation preserves subject, service-request identity and authority keys,
persists a signed transition intent, atomically replaces only `platform_url`
and safely completes the same transition after a process crash. A changed
current URL or reused idempotency key with different inputs fails closed.

## Bootstrap a missing WorkProgram

A WorkProgram is Platform-owned and must exist before APatch can prepare a
source binding. If the ProjectGroup has none, create it through the published
Project Work client surface, never by direct database access:

```text
POST https://clients.trust-chain.ai/api/internal/client/project-groups/{group_id}/work-programs
```

Sign the exact canonical request bytes with
`apatch.governed_work_transport.signed_service_headers`; this generic helper
uses the same enrolled APatch identity as governed-work delivery. The body is
`{subject, tenant_id, title, objective_ref, idempotency_key}`, where
`objective_ref` is `sha256:<64hex>` and the idempotency key is stable. A
successful response is `trustchain.collective.work-program.v1` and supplies the
immutable `program_id` and `program_hash` used below. Keep APatch
`platform_url=https://clients.trust-chain.ai`: source binding, status and
evidence admission use this same signed-service surface. The `keys` host is
public verification only.

## Production smoke inputs

A real source-binding smoke needs only non-secret authority inputs:

- `tenant_id`;
- an existing `project_group_id` where `client_id` is an authorized member;
- immutable `work_program_id` and exact `work_program_hash`;
- local `spec_id`, selected requirement ids and private purpose text (only its
  hash leaves APatch);
- the Platform governed-work authority public key mapping used to verify the
  returned binding and receipt.

Run `apatch_governed_work_prepare_change(...)`, then
`apatch_governed_work_sync(...)`. Expected success is a locally stored direct
signed `trustchain.project-source-binding.v1`; after source-bound execution and
evidence build, the second sync succeeds only when the direct signed
`trustchain.governed-work-admission-receipt.v1` matches the queued request hash,
bundle id/hash, tenant and ProjectGroup. An HTTP 2xx without that valid receipt
is not an ACK.

## Governed workflow

1. `apatch_governed_work_prepare_change` signs `apatch.change.v1` and queues
   the source-binding request before network delivery.
2. `apatch_governed_work_sync` retries the outbox. Offline Platform means
   queued work, not lost or failed local work. A successful source request
   receives and stores the direct Platform-signed ProjectSourceBinding.
3. `apatch_governed_work_store_binding` is the explicit import path for an
   externally obtained Platform-signed ProjectSourceBinding and accepts it only
   when it matches the exact local Change.
4. Add the returned `project-source-binding:...@sha256:...` artifact to the
   governed APatch session and execute the SPEC requirements normally.
5. `apatch_governed_work_build_evidence` derives the signed timesheet draft and
   evidence bundle from current source-bound attestations and unchanged
   ContributionEvents, then queues evidence admission.
6. `apatch_governed_work_sync` marks evidence delivered only after the exact
   direct signed `trustchain.governed-work-admission-receipt.v1` is verified.
   The Platform response is the receipt document itself, not a wrapper.
7. `apatch_governed_work_status(refresh_platform=true, ...)` shows four
   independent states: collective acceptance, source verification,
   contribution binding and timesheet acceptance.

A WorkRelease may be accepted while source verification or timesheet acceptance
is still pending. APatch never infers one decision from another.

## Recovery

- Missing config: result is `config_required`.
- Missing enrolled identity or public-key mismatch: `credential_unavailable`;
  the outbox remains pending.
- Platform unavailable: retry `apatch_governed_work_sync` with the same bytes.
- Invalid or mismatched receipt: no ACK is written; correct Platform authority
  or response and retry.
- Repeated accepted request: the same request hash replays without a duplicate.
- Permanently rejected request: after an exact same-command, same-tenant and
  same-ProjectGroup replacement has a valid ACK, call
  `apatch_governed_work_retire_outbox`. APatch preserves the original request,
  writes a signed retirement marker and excludes it from pending/sync/status.
- Revoked binding: source-bound evidence fails closed until Platform issues an
  authorized replacement under its revocation policy.

Do not delete or hand-edit `.apatch/governed_work/` to recover. Its immutable
documents and ACK files are the local reconciliation record.

## Data boundary

APatch sends only canonical IDs, hashes, outcomes, bounded counts and
timestamps. It never sends source, diffs, patches, prompts, private intent,
repository/host paths, credentials, grants, leases, session capabilities,
rates, prices or ownership conclusions. TrustChain owns collective decisions
and economics; APatch owns execution facts and cryptographic evidence.
