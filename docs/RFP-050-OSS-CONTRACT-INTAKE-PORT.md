# RFP-050-OSS-CONTRACT-INTAKE-PORT — Clean public transfer of qualified preparation

Status: proposal; owner freeze pending. This is a release-boundary amendment,
not a new product, authority grant, UI journey or certification.

## Before / after

The private source has a qualified fixed-ID preparation transaction. Its imports
reuse common functions located inside case-specific historical migrations. A
wholesale transfer would ship those migrations and overwrite later public signer
isolation. An installed public client currently has no generic intake API.

The public SDK must expose the same generic native transaction using only generic
helpers and the existing signer. Studio must add its qualified owner-facing HTTP
adapter to the current public application, preserving current Cowork delivery and
incremental journal loading. Private migration products are not exported.

## Exact source policy

Implementation follows an owner freeze of the test, fixture, pins and this SPEC.
The unchanged historical source judges remain historical evidence; they are not
silently renamed, filtered or claimed as installed-wheel acceptance. The new
20-case public-boundary suite is additive. The 23 frozen Studio intake API cases
must also run unchanged against the installed candidate pair.

Allowed Core runtime targets, and no others:

- CREATE `apatch/sdd_preparation_io.py`: move exactly eight pinned generic
  function bodies; keep the original capture limit and pending-state constants.
- CREATE `apatch/strict_existing_signer.py`: copy the qualified generic existing
  signer binding without enrollment or key creation.
- CREATE `apatch/sdd_contract_intake.py`: copy the qualified decision functions;
  replace only their imports of private migration helpers with the generic module.
- REPLACE `apatch/trustchain_helper.py`: add the required explicit existing-signer
  mode selectively. Preserve all later public target-key verification, captured
  identity, failed-sign refusal, fallback restrictions and Platform pin checks.
- REPLACE `apatch/sdd_integrity.py`: add only the intake-pending implementation
  fence. Do not import private preparation or migration APIs.

The existing public signer requirement remains the owner of its source file.
This release wrapper must not create a second conflicting SPEC owner or bypass
its native verification. Declaration changes needed for the three new generic
modules are limited to the approved portability scope; no wildcard is permitted.

Allowed Studio runtime targets, and no others:

- CREATE `apatch_studio/contract_intake_workflow.py`: qualified native adapter.
- CREATE `apatch_studio/authoring_workflow.py`: the source file required by the
  approved three-file candidate binding. Do not expose its historical routes or
  claim unsupported authoring operations are available in the installed SDK.
- REPLACE `apatch_studio/app.py`: install the intake routes before the static
  fallback. Preserve every existing public route, security guard and feature.

Version, changelog, English release docs, public source inventories, immutable
source-binding records and wheel/sdist evidence are updated only through their
existing release owners. Core 0.8.50 and Studio 0.1.5 are candidate versions, not
published facts. No private master/worktree/profile, identity, grants, existing
frozen inputs, Pro code, recovery feature or production service may be changed.

## Acceptance

| ID | Level | Criterion |
| --- | --- | --- |
| PORT-1 | MUST | Fixed IDs and exact contents survive read-only preparation and real native create/replace/approval/restart replay. Exactly two genuine verified receipts appear; no key/profile/frozen input changes or execution authority occur. |
| PORT-2 | MUST | Foreign/uncertified owner, changed review/snapshot/profile/frozen judge fail before any signature or installation, after a successful preparation control. |
| PORT-3 | MUST | Symlink, hardlink, traversal and runtime-source targets are refused without side effects; unknown pending transaction fences implementation without deleting evidence. |
| PORT-4 | MUST | The functional generic transaction works while every private migration module is absent and unloaded. Eight extracted generic function bodies match their prequalified byte fingerprints. |
| PORT-5 | MUST | Current Studio adds GET/POST review and POST approve inside the existing security boundary. Missing session yields exactly 401, missing origin exactly 403; both leave the workspace and ledger unchanged. The frozen original 23 API checks qualify successful owner approval and replay. |
| PORT-6 | MUST | Exact built Core 0.8.50 and Studio 0.1.5 wheels pass the entire suite in an isolated interpreter outside both checkouts, with package files only in that environment's site-packages. Existing publication, standalone/Cowork, signer-isolation and artifact gates stay mandatory. |

## Completion boundary

No placeholder source commit, source-only PYTHONPATH run, fixture-only success,
candidate record or prepared package establishes package publication. Capture
installed versions, file paths, archive hashes and actual test results. Build and
qualify first; publish source/tags/PyPI afterwards. Token acquisition is a separate
step only after qualification. No implementation for this new portability scope
has been performed yet.
