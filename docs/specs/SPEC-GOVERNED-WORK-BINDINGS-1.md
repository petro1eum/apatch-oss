# SPEC-GOVERNED-WORK-BINDINGS-1 -- TrustChain Governed Work Bindings

> **apatch artifact:** `spec:SPEC-GOVERNED-WORK-BINDINGS-1`
> **Anchors:** RFP-043
> **Platform contract hashes:** RFP `sha256:6262e9ec610ec813247f7dec500bb07e306c00b1dfb76dc874623b43ed81c62f`; SPEC `sha256:793bc863cb06b71910e6da63d23e58d9bd8126c2942af6ce23900bd2a908e2f2`
> **Ownership:** APatch implements local Change/evidence/outbox. TrustChain Platform remains the authority for ProjectSourceBinding, WorkRelease, WorkspaceContributionBinding and TimesheetDecision.

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| GW-1 | R1 | covered |
| GW-2 | R2 | covered |
| GW-3 | R3 | covered |
| GW-4 | R4 | covered |
| GW-5 | R5 | covered |
| GW-6 | R6 | covered |
| GW-7 | R7 | covered |
| GW-8 | R8 | covered |
| GW-9 | R9 | covered |
| GW-10 | R10 | covered |

Every RFP acceptance row maps one-to-one to an executable requirement. The
joint Platform hashes above are immutable inputs to this implementation.

(verify: python3 -m apatch.cli rfp coverage --rfp RFP-043 --spec SPEC-GOVERNED-WORK-BINDINGS-1 --target-dir . --json)

## R1 Deterministic signed Change

Implement strict canonical JSON, full normalized SPEC hashing, deterministic
change identity, exact requirement refs, Ed25519 signing and immutable replay.
Only a purpose hash crosses the boundary; private intent text never appears in
the signed envelope or persisted outbox.

(verify: python3 -m pytest tests/test_governed_work_change.py -q)

## R2 Platform source-binding validation

Validate the frozen exact ProjectSourceBinding.v1 against the local Change and
a purpose-separated Platform authority key registry. Store valid bindings
immutably, reject unknown/duplicate keys, hash mismatch, equivocation and
revoked status, and return the exact session artifact token.

(verify: python3 -m pytest tests/test_governed_work_binding.py -q)

## R3 Current source-bound evidence

Derive the exact signed work-evidence bundle from signed ledger attestations
and unchanged ContributionEvent envelopes. Require every selected session to
carry the exact source-binding artifact and reject stale requirement hashes,
missing requirements, rolled-back qualification and event drift.

(verify: python3 -m pytest tests/test_governed_work_evidence.py -q -k "bundle or stale or source_bound")

## R4 Separate timesheet draft

Build a deterministic signed timesheet draft from the same source-bound session
and ContributionEvent facts. Claimed seconds are nonnegative factual input only;
the schema cannot express acceptance, rates, value, ownership or settlement.

(verify: python3 -m pytest tests/test_governed_work_evidence.py -q -k "timesheet")

## R5 Durable offline delivery and reconciliation

Persist source-binding requests and evidence admissions before HTTP, bind each
idempotency key to one request hash, authenticate every exact HTTP request with
the enrolled APatch Ed25519 identity and a single-use purpose-separated nonce,
ACK only signature-validated Platform responses, repair missing ACKs by
reconciliation and preserve queued work while Platform is offline. Static
service tokens are not part of the production transport contract.

(verify: python3 -m pytest tests/test_governed_work_delivery.py tests/test_governed_work_transport.py -q)

## R6 Privacy barrier

Reject unknown or forbidden fields before local persistence and before network
delivery. Emitted Change, timesheet and evidence documents contain no code,
prompt/intent text, diffs, patches, repo or host paths, credentials, grants,
leases, session tokens, economics or professional-status conclusions.

(verify: python3 -m pytest tests/test_governed_work_evidence.py -q -k "privacy")

## R7 Full professional MCP surface

Register first-class full-profile MCP operations for Change preparation,
source-binding storage/inspection, evidence construction, outbox sync and
governed-work status. Tool results are bounded and machine-readable. Existing
expert tools remain available; no catalog reduction is introduced.

(verify: python3 -m pytest tests/test_governed_work_mcp.py -q)

## R8 Preserve truth ownership and wire compatibility

Do not mutate ContributionEvent schema or bytes. Do not infer source
verification, contribution admission or timesheet acceptance from APatch
activity or WorkRelease state. Platform decisions remain separate signed
documents consumed through exact refs and hashes.

(verify: python3 -m pytest tests/test_governed_work_compatibility.py -q)

## R9 End-to-end tamper and recovery proof

Prove deterministic replay, signature and hash tamper rejection, exact binding
mismatch/revocation, stale attestation rejection, offline queue recovery,
idempotent HTTP retry, no secret persistence and successful reconstruction of
the complete Change -> binding -> evidence chain.

(verify: python3 -m pytest tests/test_governed_work_*.py -q)

## R10 Single task-intake point and explicit Cowork proposal acceptance

Work still starts through one door: a governed session opened in this repository
for a locally selected SPEC and explicit local purpose. APatch may read one exact,
short-lived, Platform-signed `trustchain.apatch-studio.execution-intent.v1` as a
**proposal**. Reading is transient: it writes no file, opens no session, applies no
mutation and returns `work_started=false`. Signature, canonical bytes, expiry,
tenant, ProjectGroup, ProjectWorkItem, authority version and WorkProgram pins all
fail closed.

Acceptance is a separate explicit local operation. The caller repeats the exact
`work_item_id:authority_version` confirmation and supplies the local SPEC and
private purpose. APatch then creates its ordinary signed Change and an immutable,
content-free acceptance reference containing proposal/change hashes and authority
pins; proposal objective and criteria are not persisted. Acceptance still opens
no governed session. Duplicate acceptance is byte-identical. A changed, expired,
tampered, cancelled or access-revoked proposal cannot expand authority.

When delivery is requested, APatch durably queues the exact acceptance and Change
for the canonical ProjectWorkItem acceptance endpoint. It acknowledges delivery
only after the returned Platform-signed Work Item execution binding verifies and
matches every tenant, group, item, authority, intent, WorkProgram, acceptance and
Change hash. Invalid, replay-conflicting or tampered responses remain pending.
The older generic source-binding route is not a substitute for this transition.

All other remote projections retain exact key sets and reject smuggled task,
command, instruction or intent fields before storage. The frozen inventory of
network-capable modules remains enforced, and no such module may call a
work-starting primitive.

(verify: python3 -m pytest tests/test_single_intake.py tests/test_execution_proposal.py tests/test_work_item_acceptance_delivery.py -q)
