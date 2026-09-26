# RFP-048-AVATAR-SELECTIVE-PUBLICATION — Exact governed evidence publication to Avatar

> **apatch artifact:** `rfp:RFP-048-AVATAR-SELECTIVE-PUBLICATION`  
> **Status:** owner-directed implementation contract v1, 2026-09-24  
> **Depends on:** RFP-043 and RFP-047  
> **Consumers:** APatch Studio OSS; TrustChain Cowork `SPEC-COWORK-PROJECT-DELIVERY-1#R5`

## Observed failure

The production dogfood run selected one accepted governed-work result, but the generic
`apatch avatar sync` path enumerated the owner's complete local ContributionEvent
store. It attempted to reconcile and upload hundreds of unrelated historical events.
The selected event reached Avatar, but the operation violated the intended consent
boundary. A green Cowork test did not catch this because it verified Cowork evidence
admission, not Avatar publication.

This observed behavior is the red baseline. A replacement path is conformant only when
an unrelated valid event is present in the same store and no request, receipt, result or
reconciliation call names that event.

## Objective

Add a separate APatch OSS operation that lets the owner publish the exact signed
ContributionEvents referenced by one current `apatch.work-evidence-bundle.v1` to
their TrustChain Avatar. Local evidence remains private by default. Preview is read-only.
Delivery requires confirmation of the exact reviewed plan. The generic account-wide
Avatar sync remains available for an owner who intentionally wants it, but Studio and
Cowork must never invoke it for project-result publication.

Cowork evidence admission and Avatar publication are independent decisions:

```text
local signed evidence bundle
  ├─ explicit Cowork publish -> governed_work.evidence_admission
  └─ explicit Avatar preview -> exact event refs -> exact confirmation
                              -> selected ContributionEvents only
```

Neither branch implies WorkRelease acceptance, accepted time, price, ownership,
capability level or professional status.

## Exact selection

The caller selects one immutable local ProjectSourceBinding. APatch reconstructs the
single current signed evidence bundle and matching timesheet for that binding, then
uses only the bundle's `contribution_event_refs` as the Avatar selection.

Every selected event must:

1. exist exactly once in the chosen local ContributionEvent store;
2. have the exact `event_id` and APatch `document_hash` committed by the bundle;
3. retain a valid Ed25519 signature and the exact source-binding artifact;
4. belong to one subject/avatar identity for this publication;
5. be one of the bundle refs — store enumeration may resolve refs but may not expand them.

A missing, duplicate, hash-drifted, signature-invalid, identity-mixed or no-longer
source-bound event fails closed before preview or network. Unreferenced events are
irrelevant even when valid, pending or previously synced.

## Exact preview plan

Preview returns this closed unsigned projection:

```json
{
  "schema": "apatch.avatar-share-plan.v1",
  "avatar_origin": "https://trust-chain.ai",
  "avatar_id": "string",
  "subject_key_id": "string",
  "tenant_id": "string",
  "project_group_id": "tcpg_<32 lowercase hex>",
  "scope": "avatar.contribution_upload",
  "project_source_binding_ref": {
    "binding_id": "tcpsb_<32 lowercase hex>",
    "binding_hash": "sha256:<64 lowercase hex>"
  },
  "evidence_bundle_ref": {
    "bundle_id": "apweb_<32 lowercase hex>",
    "bundle_hash": "sha256:<64 lowercase hex>"
  },
  "contribution_event_refs": [
    {
      "event_id": "string",
      "event_hash": "sha256:<64 lowercase hex>"
    }
  ],
  "plan_hash": "sha256:<64 lowercase hex>"
}
```

`plan_hash` is `value_hash` of all other plan fields. Event refs are nonempty,
sorted and duplicate-free. The plan contains no event body, signature, source, diff,
prompt, transcript, repository/host path, credential, token, timesheet, claimed or
accepted seconds, rate, price, WorkRelease decision, capability inference or unrelated
event id.

The default and production recipient is the exact origin
`https://trust-chain.ai`. A different self-hosted origin is legal only when it is an
exact canonical HTTPS origin in the owner's local
`APATCH_AVATAR_ALLOWED_ORIGINS` allowlist. Paths, queries, fragments, userinfo, HTTP
and suffix/lookalike host matches are rejected. The bearer token is never sent to an
origin that did not pass this exact check.

## Confirmed delivery

Publish accepts the unchanged plan and exact confirmation
`publish:<plan_hash>`. It reconstructs current local documents and requires canonical
byte equality with the plan. It then calls the existing Avatar contribution endpoint
with exactly the selected event bodies and reconciles exactly the same selected ids.
It must not call account-wide export, CapabilityEvidence publication, Cowork admission
or timesheet delivery.

The owner-scoped Avatar token is read only at delivery time from the explicit argument
or `APATCH_AVATAR_TOKEN`. It is never persisted, echoed, hashed into the plan or
returned. Missing, empty, rejected, expired or owner-mismatched credentials create no
delivery receipt and leave all local artifacts unchanged.

Existing per-event Avatar receipts provide idempotency. Exact retry after a valid ACK
attempts no duplicate upload; retry after an offline or invalid response uses the same
selected set. A response is success only when its shape is valid and selected ids are
reconciled as present according to existing Avatar semantics. Results report counts and
selected refs only, never event bodies or credentials.

## Acceptance

| ID | Level | Criterion |
|---|---|---|
| AVP-1 | MUST | Selection starts from one current signed work-evidence bundle and resolves exactly its nonempty ContributionEvent refs. Missing, duplicate, hash-drifted, signature-invalid, identity-mixed or source-binding-mismatched events fail before a plan or request. |
| AVP-2 | MUST | Preview is byte-stable and read-only and returns the exact closed `apatch.avatar-share-plan.v1`; it emits refs and scope only and contains no event payload, signature, token, path, source, prompt, transcript, timesheet/economics, decision or unrelated event id. |
| AVP-3 | MUST | Recipient validation allows only an exact canonical HTTPS origin in the local allowlist, defaulting to `https://trust-chain.ai`. HTTP, userinfo, paths, queries, fragments, suffix tricks and unlisted origins fail before any credential or network use. |
| AVP-4 | MUST | Publish requires `publish:<plan_hash>`, reconstructs the plan from current signed local state and rejects unknown fields, bool coercion, altered recipient/scope/identity/ref/hash, stale state and confirmation mismatch before HTTP or receipt mutation. |
| AVP-5 | MUST | A valid publish uploads and reconciles exactly the bundle-referenced events. With additional valid unrelated events in the same store, no upload, reconciliation, receipt, result or helper invocation contains an unrelated id; Cowork admission, timesheet delivery, CapabilityEvidence and account-wide sync are not invoked. |
| AVP-6 | MUST | Delivery is idempotent and fail-closed: exact ACK/reconciliation produces per-event receipts; exact replay does not duplicate; offline, malformed/partial ACK, rejected/expired/wrong-owner token and remote identity refusal preserve the exact local selection without false success or receipt. The token is never persisted or returned. |
| AVP-7 | MUST | The full MCP profile exposes bounded preview and publish operations. Safe results contain only plan fields, counts, statuses and selected refs. Existing ContributionEvent, evidence bundle, Cowork admission, generic Avatar sync and public wire schemas remain byte-compatible. |
| AVP-8 | MUST | A cross-product acceptance test uses a production-shaped signed source binding, evidence bundle and at least two local events, selects one, proves the unrelated-event trap, proves preview has zero side effects, proves tamper/recipient/token failures make zero requests, and proves one confirmed idempotent Avatar round trip without changing Cowork, time, price, acceptance or capability state. |

## Non-goals

- Removing the deliberately broad owner-requested `apatch avatar sync` operation.
- Publishing CapabilityEvidence or assigning a capability level in this flow.
- Sending a timesheet, accepting time, accepting work, pricing work or settling payment.
- Mutating ContributionEvent, evidence-bundle, ProjectSourceBinding or Avatar wire schemas.
- Copying repositories, patches, prompts, transcripts, credentials or raw evidence.
- Claiming remote erasure of already acknowledged Avatar history.
