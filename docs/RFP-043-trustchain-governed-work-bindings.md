# RFP-043 -- TrustChain Governed Work Bindings

> **Status:** Draft v1
> **Owners:** APatch / TrustChain Platform
> **Date:** 2026-08-30
> **Executable contract:** `docs/specs/SPEC-GOVERNED-WORK-BINDINGS-1.md`
> **Platform contract:** `RFP-APATCH-TRUSTCHAIN-GOVERNED-WORK-BINDINGS-1`
> **Frozen Platform hashes:** RFP `sha256:6262e9ec610ec813247f7dec500bb07e306c00b1dfb76dc874623b43ed81c62f`; SPEC `sha256:793bc863cb06b71910e6da63d23e58d9bd8126c2942af6ce23900bd2a908e2f2`

## Objective

Connect an agreed TrustChain WorkProgram to APatch execution and return signed,
privacy-safe evidence without making either system a second source of truth.

TrustChain owns ProjectGroup authority, WorkProgram, WorkRelease, binding
admission, timesheet decisions and contribution/economic read models. APatch
owns Change, SPEC/Rk, governed sessions, verification, attestation, rollback,
ContributionEvent and the evidence bundle derived from them. The existing
ContributionEvent wire bytes are never changed by this RFP.

The v1 flow is:

```text
WorkProgram
  -> signed apatch.change.v1
  -> Platform-signed ProjectSourceBinding.v1
  -> APatch governed sessions and attestations
  -> signed apatch.timesheet-draft.v1
  -> signed apatch.work-evidence-bundle.v1
  -> durable evidence admission
  -> accepted WorkRelease
  -> Platform WorkspaceContributionBinding.v1
  -> separate Platform TimesheetDecision.v1
```

Platform availability is not a prerequisite for ordinary local APatch work.
An exact project policy may require a source binding before mutation; without
that policy, APatch queues work offline and reports it as unbound until sync.

## Canonical encoding and hashes

All APatch documents below are UTF-8 JSON with duplicate-key rejection, exact
top-level keys, NFC strings, sorted object keys, compact separators, no
NaN/Infinity and no implicit coercion. Booleans are never integers.

- `value_hash(value)` is `sha256:` plus lowercase SHA-256 of canonical JSON.
- `document_hash(document)` is `value_hash` after removing top-level
  `signature`.
- `envelope_hash(document)` is `value_hash` of the full signed document.
- Signature is exactly
  `{"algorithm":"Ed25519","key_id":string,"value":unpadded_base64url_64_bytes}`.
- APatch-owned Change, timesheet and evidence signatures cover canonical
  unsigned document bytes. Platform-owned binding and receipt signatures cover
  `b"TrustChain-Governed-Work\\x00v1\\x00" + purpose_ascii + b"\\x00" + canonical_unsigned_bytes`.
  The exact purposes used here are `governed_work.source.bind` and
  `governed_work.evidence.admit`; plain-Ed25519 verification is not accepted
  for Platform authority documents.
- `spec_hash` is full SHA-256 of APatch-normalized SPEC text. APatch strips
  trailing whitespace on every line and surrounding blank space before hashing.
- `requirement_hash` keeps the existing APatch `sha256:<16 lowercase hex>`
  requirement-block hash.

## Exact apatch.change.v1

```json
{
  "schema": "apatch.change.v1",
  "change_id": "apchg_<32 lowercase hex>",
  "execution_system": "apatch",
  "tenant_id": "string",
  "project_group_id": "tcpg_<32 lowercase hex>",
  "source_kind": "work_program",
  "work_program_id": "tcwp_<32 lowercase hex>",
  "work_program_hash": "sha256:<64 lowercase hex>",
  "context_release_id": "string|null",
  "context_release_manifest_hash": "sha256:<64 lowercase hex>|null",
  "spec_id": "SPEC-*",
  "spec_hash": "sha256:<64 lowercase hex>",
  "requirement_refs": [
    {
      "requirement_id": "Rk",
      "requirement_hash": "sha256:<16 lowercase hex>"
    }
  ],
  "purpose_hash": "sha256:<64 lowercase hex>",
  "actor_key_id": "string",
  "issued_at": "UTC RFC3339",
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "string",
    "value": "unpadded base64url"
  }
}
```

The optional ContextRelease fields are both null or both non-null.
`requirement_refs` is nonempty, sorted and duplicate-free. `purpose_hash`
commits to the normalized private intent while the intent text never leaves
the workspace. The deterministic `change_id` hashes the exact source, SPEC,
requirements, purpose and actor identity fields, excluding timestamps and
signature. Retrying the same change returns identical persisted bytes.

## ProjectSourceBinding consumption

APatch accepts only the exact Platform
`trustchain.project-source-binding.v1` frozen by the Platform contract.
Validation requires:

1. exact schema, keys, canonical hashes and sorted requirement refs;
2. a signature by a key in the purpose-separated Platform binding-authority
   trust store;
3. exact tenant, ProjectGroup, WorkProgram, ContextRelease, Change and SPEC
   hashes from the locally persisted Change;
4. exact active authority/revocation state when a Platform status snapshot is
   available.

A valid binding is stored immutably. The same binding id with different bytes
fails closed. APatch returns the typed session artifact
`project-source-binding:<binding_id>@<document_hash>`; governed evidence may
reference only sessions carrying that exact artifact.

## Exact apatch.timesheet-draft.v1

```json
{
  "schema": "apatch.timesheet-draft.v1",
  "timesheet_id": "apts_<32 lowercase hex>",
  "project_source_binding_id": "tcpsb_<32 lowercase hex>",
  "project_source_binding_hash": "sha256:<64 lowercase hex>",
  "subject_key_id": "string",
  "period_started_at": "UTC RFC3339",
  "period_ended_at": "UTC RFC3339",
  "claimed_active_seconds": 0,
  "session_refs": [
    {
      "governed_session_id": "string",
      "started_at": "UTC RFC3339",
      "ended_at": "UTC RFC3339"
    }
  ],
  "contribution_event_refs": [
    {
      "event_id": "string",
      "event_hash": "sha256:<64 lowercase hex>"
    }
  ],
  "issued_at": "UTC RFC3339",
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "string",
    "value": "unpadded base64url"
  }
}
```

The draft is a factual, verifiable claim. It contains no acceptance, rate,
price, salary, invoice, ownership or settlement fields. Platform may later
accept, reduce or reject seconds only through a separate TimesheetDecision.v1.

## Exact apatch.work-evidence-bundle.v1

```json
{
  "schema": "apatch.work-evidence-bundle.v1",
  "bundle_id": "apweb_<32 lowercase hex>",
  "change_id": "apchg_<32 lowercase hex>",
  "change_hash": "sha256:<64 lowercase hex>",
  "project_source_binding_id": "tcpsb_<32 lowercase hex>",
  "project_source_binding_hash": "sha256:<64 lowercase hex>",
  "spec_id": "SPEC-*",
  "spec_hash": "sha256:<64 lowercase hex>",
  "requirement_refs": [
    {
      "requirement_id": "Rk",
      "requirement_hash": "sha256:<16 lowercase hex>"
    }
  ],
  "attestation_refs": [
    {
      "spec_id": "SPEC-*",
      "requirement_id": "Rk",
      "attestation_id": "string",
      "attestation_hash": "sha256:<64 lowercase hex>",
      "outcome": "passed|rolled_back"
    }
  ],
  "contribution_event_refs": [
    {
      "event_id": "string",
      "event_hash": "sha256:<64 lowercase hex>"
    }
  ],
  "timesheet_ref": {
    "timesheet_id": "apts_<32 lowercase hex>",
    "timesheet_hash": "sha256:<64 lowercase hex>",
    "claimed_active_seconds": 0
  },
  "issued_at": "UTC RFC3339",
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "string",
    "value": "unpadded base64url"
  }
}
```

The bundle is derived from the signed APatch ledger and persisted
ContributionEvent envelopes. It contains only opaque IDs, hashes, outcomes,
timestamps and bounded counts. It never contains code, prompts, intents, diffs,
patch bytes, repo/host paths, credentials, grants, leases, session tokens,
private keys, rates, prices or professional-status conclusions. Evidence for a
WorkspaceContributionBinding must contain current `passed` attestations for
every exact source-binding requirement.

## Durable outbox and API

APatch persists signed source-binding requests and evidence admissions before
network delivery. Entries are immutable and idempotent by request hash. An ACK
is a separate atomic receipt; changing a payload under an existing
idempotency key fails locally before HTTP.

The governed-work outbox uses the frozen internal Platform routes on the
signed-service surface (`https://clients.trust-chain.ai` in production):

- `POST /api/internal/project-groups/{group_id}/governed-work/source-bindings`
- `POST /api/internal/project-groups/{group_id}/governed-work/evidence-admissions`
- `GET /api/internal/project-groups/{group_id}/governed-work/status/{work_program_id}`

A WorkProgram is a pre-execution Platform prerequisite and is never created by
the APatch outbox. When one is missing, client/bootstrap code uses the same
signed-service host and
`POST /api/internal/client/project-groups/{group_id}/work-programs`, signed with
the generic exact-raw-body service-request helper. The public
`https://keys.trust-chain.ai` host serves verification material and is not a
signed internal API endpoint. Endpoint changes use an explicit signed,
compare-and-swap, idempotent config transition; silent overwrite is forbidden.

The exact Platform request wire is:

- source POST body:
  `{subject: client_id, tenant_id, change, idempotency_key}`;
- evidence POST body:
  `{subject: client_id, tenant_id, evidence_bundle, timesheet_draft, idempotency_key}`;
- status GET query:
  `subject=client_id&tenant_id=<tenant_id>`;
- source response: the direct signed
  `trustchain.project-source-binding.v1` document;
- evidence response: the direct signed
  `trustchain.governed-work-admission-receipt.v1` document, never a
  `{"receipt": ...}` wrapper.

The outbox request hash is byte-exact with Platform:
`value_hash({"command":"create_source_binding","payload":change})` for source
binding and
`value_hash({"command":"admit_evidence","payload":{"evidence_bundle":bundle,"timesheet_draft":draft}})`
for evidence. It is persisted locally but not added as an extra POST field;
Platform recomputes it and returns it in the evidence receipt. `client_id`
must be a Platform subject alias authorized for the ProjectGroup.

Every internal Platform request is authenticated by the enrolled APatch
Ed25519 identity using the exact
`trustchain.project-group.service-request.v1` envelope. The signature binds the
HTTP method, URL path without query, SHA-256 of the exact canonical request
bytes, tenant, subject, issued/expiry epoch and a fresh single-use 32-hex nonce.
The domain is
`b"TrustChain-ProjectGroup-Service-Request\\x00v1\\x00"` and lifetime is 20
seconds. Platform stores only the public-key fingerprint mapping. The private
key stays in the APatch key provider; static service tokens and legacy fallback
are not part of the production contract. Request signatures, private material
and credentials are never written to the outbox, logs, MCP results or evidence.
Sync validates the returned Platform signature before ACK. Evidence admission
is acknowledged only by the exact signed
`trustchain.governed-work-admission-receipt.v1`:

```json
{
  "schema": "trustchain.governed-work-admission-receipt.v1",
  "receipt_id": "tcgwar_<32 lowercase hex>",
  "tenant_id": "string",
  "project_group_id": "tcpg_<32 lowercase hex>",
  "request_hash": "sha256:<64 lowercase hex>",
  "command": "evidence_admission",
  "resource_id": "apweb_<32 lowercase hex>",
  "resource_hash": "sha256:<64 lowercase hex>",
  "projection_cursor": 0,
  "accepted_at": "UTC RFC3339",
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "string",
    "value": "unpadded base64url"
  }
}
```

The receipt must match the queued tenant, ProjectGroup, request hash, bundle id
and APatch bundle document hash, and its signature must verify against the
purpose-separated Platform governed-work authority. Retry of the same accepted
request returns the receipt byte-identically. Missing, malformed, mismatched or
invalidly signed receipts are not ACKs and leave the immutable outbox entry
pending. Offline delivery is `queued_offline`, not success or failure of the
governed work itself. A permanently rejected request can leave the active queue
only through a signed APatch retirement marker after an exact same-command,
same-tenant and same-ProjectGroup replacement has a valid ACK. The immutable
original remains stored; arbitrary deletion and unacknowledged supersession fail
closed. Pending counts, sync and status apply the same ACK-or-retirement rule.

The signed receipt compatibility fixture is generated by Platform commit
`c956a3237177f294f0d8a86e50949d1f69681638` and pins canonical/service/router
SHA-256 values
`f2a237c4241c364be85733a22162d5193849abdf4738bf5263690506eedc797e`,
`efc0c013becef29700d74d336ca7b345d536b543f64daec38839260ed7026a6d`
and
`bb03badc12b0c1bca62d8e8ce536479e5cd3b0c5f964f121e44345d953be0561`.
Its purpose-separated receipt must verify byte-exactly in APatch. The separate
signed-request fixture pins production Platform release
`ce5402347aad06cbafa22878d7f0d82ceee58ba3`; APatch-generated headers must pass
that release's real `verify_signed_service_request` implementation.

## MCP surface

The professional full MCP catalog exposes first-class tools for configuring and
safely transitioning the signed-service endpoint, preparing a Change,
storing/inspecting a source binding, building evidence, syncing or safely
retiring superseded outbox requests, and reading local/Platform status. The catalog is not reduced merely to
lower tool count. Discovery, descriptions, packs and routing may be optimized,
but the full expert API remains available.

## Acceptance

| Id | Requirement | Level |
|---|---|---|
| GW-1 | APatch builds, signs, persists and verifies the exact deterministic `apatch.change.v1`, with full SPEC hash and existing short requirement hashes, while never emitting private intent text | MUST |
| GW-2 | APatch strictly validates and immutably stores the exact Platform `ProjectSourceBinding.v1` against the local Change and purpose-separated authority keys; mismatch, equivocation or revocation fails closed | MUST |
| GW-3 | APatch derives and signs the exact privacy-safe `apatch.work-evidence-bundle.v1` only from current exact source-bound attestations and unchanged ContributionEvent envelopes | MUST |
| GW-4 | APatch produces a separate signed `apatch.timesheet-draft.v1`; generated seconds never imply acceptance, value, payment, ownership or professional status | MUST |
| GW-5 | Source requests and evidence admissions use an atomic durable outbox, exact request hashes, purpose-separated signed-service authentication with replay protection, idempotent retries, signature-checked ACKs and monotonic reconciliation; Platform downtime does not lose local work | MUST |
| GW-6 | Privacy validation rejects code, prompt/intent text, paths, credentials, runtime authority, economics and unknown fields before persistence or delivery | MUST |
| GW-7 | The full professional MCP catalog exposes Change, binding, evidence, sync and status operations with actionable machine-readable states; no artificial tool-count reduction is introduced | MUST |
| GW-8 | Existing ContributionEvent bytes and ownership boundaries remain unchanged; Platform decisions are consumed as separate signed documents and no cross-system state is inferred | MUST |
| GW-9 | Tests prove deterministic replay, signature/hash tamper rejection, binding mismatch/revocation, stale attestation rejection, offline recovery, idempotent delivery and zero forbidden privacy fields in emitted envelopes | MUST |
| GW-10 | Work enters APatch only through a governed session opened in the local repository; no network path delivers something to execute. Every remote read is a decision or verification input about work already declared locally, validated against an exact key set, and a response that carries an instruction is rejected before it reaches storage | MUST |

## Non-goals

- Changing ContributionEvent, WorkProgram, WorkRelease or Platform authority.
- Accepting timesheets, valuing work or assigning rights inside APatch.
- Copying source code, prompts, patches, paths or secrets into Platform.
- Requiring Platform availability for local work unless an exact policy says so.
- Adding ProjectWorkItem.v1, cross-repo ChangeSet or economics in v1.
