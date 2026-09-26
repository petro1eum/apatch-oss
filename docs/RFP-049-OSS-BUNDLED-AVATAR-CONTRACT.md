# RFP-049 — Bundled canonical Avatar contract in APatch OSS

> **apatch artifact:** `rfp:RFP-049`  
> **Status:** owner decision 2026-09-26 — "include avatar_contract in APatch OSS"; implementation contract v1  
> **Owner:** APatch core  
> **Executable contract:** `docs/specs/SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1.md`

## Observed failure

Public APatch imported the shared `avatar_contract` package lazily, but that package
was neither a declared dependency nor available from a package index. Every public
install therefore degraded the same way:

- `apatch_attest` returned `contribution_receipt: {status: unavailable, code:
  AVATAR_CONTRACT_UNAVAILABLE}` and wrote no ContributionEvent;
- `apatch.governed_work.build_workspace_evidence` could not build evidence, so an
  independent developer using APatch OSS and APatch Studio could not deliver a
  Cowork result at all;
- the OSS qualification inventory had to classify 40 absent-peer test failures,
  22 failing or broken peer requirements and five Avatar skips;
- an interpreter with a stale separately installed `avatar-contract` (for example
  0.6.0) loaded that stale copy instead, which aborted test collection and made
  runtime behaviour depend on whatever happened to be installed.

With the contract available, one more defect kept the claimed time at zero: attestation
emits the event before `session_end`, so `duration_sec` was 0 and `active_sec` empty.
A 20-second governed session in APatch Studio produced an event with no claimable time.

## Owner decision

The repository owner decided on 2026-09-26: **include avatar_contract in APatch OSS.**
The ONE shared definition rule (Avatar Architecture Canon §7, Rule 1) stays intact:
APatch does not fork or re-implement the contract. It bundles the canonical MIT
`avatar-contract` package at an exact commit, byte-for-byte except for a mechanical,
reversible import rewrite, under a private namespace.

## Design

- **Source:** `https://github.com/petro1eum/avatar-contract` at commit
  `44c8f9ada8a50fb8b7c94346a4103c09a19f15c2` (version 0.7.2, MIT), read from Git
  objects (`git archive`), never from a working tree.
- **Location:** `apatch/_vendor/avatar_contract/**` (all 17 package files: modules,
  `schema/*.json`, `transport/*.json`), the upstream `LICENSE`, and `UPSTREAM.json`
  with repository, commit, version, license, the rewrite rule and the SHA-256 of every
  upstream file.
- **Only transformation:** lines matching `^(\s*)from avatar_contract(\.| )` become
  `\1from apatch._vendor.avatar_contract\2` (a bare `import avatar_contract` line would
  become `from apatch._vendor import avatar_contract`). JSON documents are untouched.
- **Why a private namespace:** a top-level `avatar_contract` inside the APatch wheel
  would collide with a separately installed `avatar-contract` distribution (HC_Platform
  uses one). With the namespaced copy both can coexist, and APatch behaviour cannot
  change with the external version.
- **Proof of identity:** `scripts/vendor_avatar_contract.py --check` applies the inverse
  rewrite to every vendored file and compares it with `UPSTREAM.json`; with
  `--checkout` it also proves `UPSTREAM.json` equals the canonical Git objects at the
  recorded commit. `--checkout PATH --commit SHA` regenerates the copy.
- **Runtime:** every APatch import of the contract uses `apatch._vendor.avatar_contract`.
  `avatar_runtime_compatibility()` reports `source: bundled`, the upstream version and
  commit and the vendored module path; a separately installed distribution is only
  named (`external_installed_version`) and never used.
- **Distribution:** package data ships the vendored JSON, `LICENSE` and `UPSTREAM.json`.
  There is no `avatar` extra and no direct-URL requirement.

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| BAC-1 | The vendored tree equals the canonical package at the pinned commit after the inverse import rewrite; `UPSTREAM.json` records repository, commit, version 0.7.2, MIT license, rewrite rule and upstream hashes; the MIT `LICENSE` ships; the regeneration/check script fails closed on any drift, extra file or non-mechanical edit. | MUST |
| BAC-2 | No APatch runtime module imports a top-level `avatar_contract`; with that name unimportable or bound to a stale module, contribution building, the lockstep edge and every Avatar surface work from the bundled contract. | MUST |
| BAC-3 | The runtime compatibility report and doctor describe the bundled contract (ok, ready, upstream version, vendored module path, bundled marker); an external install of any version is ignored; a damaged bundled copy fails closed before any partial contract use. | MUST |
| BAC-4 | Contract resources (transport and schema JSON) load from the bundled package through `importlib.resources`; tests formerly gated on an installed peer run unconditionally. | MUST |
| BAC-5 | An end-to-end governed attestation in a temporary workspace, with the top-level name blocked, emits a signed, schema-valid ContributionEvent and no `AVATAR_CONTRACT_UNAVAILABLE`. | MUST |
| BAC-6 | Wheel and sdist contain every vendored file byte-identical to the checkout, no top-level `avatar_contract` package, no `avatar` extra and no direct-URL requirement. | MUST |
| BAC-7 | The OSS qualification inventory declares no Avatar absent-peer failures, peer requirements or Avatar skips; the Avatar profile prerequisite proves the vendored copy equals the canonical checkout at the pinned commit; the canonical sibling-repository contract checks remain the single unproven external specification. | MUST |
| BAC-8 | README, CHANGELOG, MCP setup notes and the RFP-028 reconciliation record the bundled contract instead of an external install (the Avatar canon already names one shared versioned package and stays unchanged); a docs contract test pins the wording. | MUST |
| BAC-9 | Newly emitted ContributionEvents carry `session.active_sec`: the measured span from session start through every session ledger operation, excluding gaps longer than 30 minutes, so governed evidence claims real work time in the canonical verify → attest → end order; `duration_sec`, timesheet hours and the bytes of stored events are unchanged. | MUST |

## Non-goals

No change to the Avatar contract itself, its schemas, wire format, signatures or
version. No new public API beyond the compatibility report fields. No publication,
upload, visibility change or release approval. The canonical sibling-repository
contract checks stay where they are; this RFP does not claim them as proven by APatch.
