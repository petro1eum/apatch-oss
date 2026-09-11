# RFP-047-COWORK-SELECTIVE-DELIVERY — Cowork selective evidence delivery

> **apatch artifact:** `rfp:RFP-047-COWORK-SELECTIVE-DELIVERY`  
> **Status:** owner-directed implementation contract v1, 2026-09-10  
> **Consumer:** TrustChain Cowork `SPEC-COWORK-PROJECT-DELIVERY-1#R5`

## Objective

Keep APatch OSS useful and complete offline while making every transfer to TrustChain
Cowork an explicit, inspectable publication act. Building evidence is local. Previewing
publication performs no network request and creates no outbox entry. Publishing requires
the caller to confirm the exact immutable plan it just reviewed.

This contract extends the existing RFP-043 Change/binding/evidence transport. It does not
change the signed `apatch.work-evidence-bundle.v1`,
`apatch.timesheet-draft.v1`, Platform admission endpoint, Avatar schemas, or ownership.
The existing evidence admission requires the evidence bundle and its matching timesheet;
they are therefore one governed-work selection. Avatar export is never silently added to
that selection and continues through its separately consented owner surface.

## Terms

A **share plan** is a local, unsigned projection of already signed artifacts. It contains
only recipient and scope, artifact ids/hashes, connection generation, and a deterministic
plan hash. It is not evidence, authority, an outbox entry, or proof of delivery.

**Disconnected** means new preview remains available from local data, but new publication
and delivery of pending entries for that connection generation are fenced. It does not
delete local artifacts or claim erasure of acknowledged remote history.

## Acceptance

| ID | Level | Criterion |
|---|---|---|
| CSD-1 | MUST | `build_governed_evidence` is local-first: its default and explicit preview path create signed local evidence but no outbox entry, HTTP request, session, or Platform/Avatar authority. |
| CSD-2 | MUST | Preview returns an exact closed `apatch.cowork-share-plan.v1` containing Platform origin, client subject, tenant, ProjectGroup, scope `governed_work.evidence_admission`, evidence bundle id/hash, matching timesheet id/hash and claimed seconds, connection generation and deterministic plan hash. It contains no payload document or signature bytes. |
| CSD-3 | MUST | Publication accepts only a current locally reconstructed plan plus exact confirmation `publish:<plan_hash>`. Unknown fields, bool-as-int, altered recipient/scope/artifact/hash/generation, missing config, and confirmation mismatch fail before persistence or network. |
| CSD-4 | MUST | One accepted publication queues the existing canonical evidence-admission payload exactly once. Exact retry returns the same outbox identity; changed reuse or a second selection cannot overwrite it. Preview and publication never upload source, repository paths, diffs, prompts, credentials, transcripts, unrelated events, grants, economic conclusions or Avatar data. |
| CSD-5 | MUST | Offline sync preserves the exact queued bytes and retry identity. HTTP success without the existing signed matching Platform receipt is not delivery. An acknowledged entry remains immutable and visible as delivered. |
| CSD-6 | MUST | Disconnect is explicit, idempotent and generation-fenced. It blocks new publication and all not-yet-acknowledged delivery for the disconnected generation, while local build/preview/status and previously acknowledged history remain available. Reconnect requires an explicit configuration action that advances generation; it cannot silently reuse pending entries from an older generation. |
| CSD-7 | MUST | APatch exposes bounded MCP operations for preview, publish and disconnect. Safe results reveal ids, hashes, recipient, scope and state only; no secret, local path, payload/signature bytes or false deletion/acceptance claim is returned. Existing tools and wire documents remain compatible. |
| CSD-8 | MUST | A Cowork consumer acceptance test exercises local build → preview → exact publish → offline retry → receipt ACK → disconnect, and proves Avatar evidence remains a separate opt-in path and local artifacts survive throughout. |

## Non-goals

- Selecting individual fields inside the already signed evidence bundle or timesheet.
- Uploading a repository, source, patches, prompts, transcripts or credentials.
- Combining governed-work admission with Avatar export.
- Remote deletion, right-to-erasure claims, WorkRelease acceptance, timesheet acceptance,
  capability inference, pricing, ownership or settlement.
- Making a Platform account or subscription necessary for local APatch execution.
