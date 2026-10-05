# Public OSS source boundary

This is the **0.8.50 public contract-intake candidate**, prepared on 2026-10-06 from
public commit `9b20e82e7ea343bdfb8784bdb20fda2b908f841b` (0.8.48).
The historical 0.8.48 export published the complete APatch line since 0.8.45, with
all 266 runtime files byte-identical to its recorded private release commit and
Apatch Pro excluded. That historical inventory is preserved verbatim in
`PUBLIC-SOURCE-MANIFEST.json`; no private commit objects are imported.
The current candidate preserves those reviewed public signer/ownership changes
and adds portable owner contract preparation. Relative to public 0.8.48, 260
runtime files are unchanged, six are modified and three generic modules are
added: 269 runtime files in total. `petro1eum/apatch-oss` remains the public source destination.
Legacy artifact-enrichment and first-party attest-policy tests use genuine
disposable target-pinned Ed25519 receipts. An enforced workspace without a
configured identity is rejected before a ledger append. These fixture corrections
retain the runtime signer isolation and receipt protections.
Fresh source and built-artifact qualification is required before 0.8.50 publication;
the historical 0.8.48 evidence does not qualify this candidate.

**Release scope: standalone OSS, with separately qualified optional integrations.**
The canonical MIT Avatar contract is bundled as `apatch._vendor.avatar_contract`
(RFP-049), so the public-only suite no longer has absent-Avatar failures. A passing
profile still does not prove live Avatar, HC, Platform or Cowork acceptance.
Use [the verification guide](docs/oss-verification.md) and exact source hashes to
produce fresh evidence. This inventory and [the historical readiness record](docs/public-release-readiness.md)
are not themselves an acceptance certificate or proof of external integration.

Published package versions are listed on [PyPI](https://pypi.org/project/apatch/).
The MIT license is unchanged and identical to TrustChain OSS.

## Included

- The APatch CLI/MCP runtime and compatibility package: 269 module/schema files,
  six reviewed modifications, three generic additions and 260 unchanged files
  relative to public 0.8.48. The exact changed paths and before/after hashes are recorded in the
  source manifest. No private commit objects are imported.
- The canonical MIT `avatar-contract` 0.7.2, bundled byte-for-byte under
  `apatch/_vendor/avatar_contract` with only its own imports rewritten;
  `UPSTREAM.json` pins its commit, license and hashes.
- Executable requirements, regression tests, frozen verification records and selected
  engineering guides, examples and tutorials.
- Local Avatar/HC adapters, factual time-accounting, WorkAssets and optional service
  integration. External peers and services remain separately installed and authorized.
- TrustChain `>=3.3.0` is a base dependency because signed contract evidence is an
  APatch capability; connecting to a hosted service remains an explicit choice.
- Portable enforcement, sandbox and conformance policies; no private signing identity.
- Generic fixed-ID contract preparation and owner confirmation, using an already
  enrolled signer. The eight extracted helper bodies and the 20-case portability
  judge retain their approved fingerprints. The exact public runtime transfer is
  commit `77a5e19572df0fc14526881b8f9ecf6fdb6905a7`; it must be a real ancestor
  of the release and its five bound runtime blobs must match the current files.
  The existing signer helper retains its public R15 owner. Preparation is not
  execution authority. The Studio HTTP adapter ships in its separate package.

## Not included

No private Git objects, old refs/tags/PRs, CI logs, Pro implementation, unrelated
third-party reference dataset, personal MCP configuration, production identity,
private operational ledger or historical production inclusion proof is exported.
Some engineering documents retain historical product/contract references; those
records are not evidence that a new public candidate has been independently accepted.

An excluded external reference is labelled as such, not replaced by a fabricated
document or a link that appears public but requires a private repository.

## Integrity and verification

`PUBLIC-SOURCE-MANIFEST.json` records the selected source files and hashes.
It records both the immutable historical 0.8.48 inventory and the exact reviewed
0.8.50 delta, including current verification-inventory pins and the exact public
contract-intake source binding. Existing profile
provenance remains historical; refreshing source hashes does not claim a new pass.
It excludes itself, Git metadata and generated build/test/runtime artifacts.
It is an inventory, not a cryptographic owner attestation or a security certificate.

The historical frozen-test portability amendment is documented in
[the amendment record](docs/public-source-amendment.md). Historical freeze records
and all original assertions are retained; the frozen RFP bytes keep their exact
SHA-256. The other frozen functional judge assets remain unchanged. The approved
versioned source-inventory extension is declared in
[SPEC-SPEC-OWNERSHIP-GATE-1 R11](docs/specs/SPEC-SPEC-OWNERSHIP-GATE-1.md#r11-attested-git-handoff).
Its 0.8.49 validator and historical test functions remain intact; 27 additive
cases cover the exact 0.8.50 inventory and public provenance. Those source
fixtures do not establish functional, installed-pair or publication acceptance.

No private commit history is required to run the public tests. No old signing key
or production ledger may be copied in to make a check appear verified.
Fresh external transparency inclusion, when required by a deployment, must be
established for that deployment and cannot be inferred from local tests.

## Public/private contribution routing

Develop public runtime changes in this clean history. Bring future private work
across as separately reviewed patches with a declared file set and verification;
do not merge, mirror-push or import the private history or its release tags.
Pro code remains in its separately authorized development and distribution path.

Public source, private vulnerability reporting and the final package upload are
separate publication steps. Before uploading, verify the chosen documentation
URL anonymously and record the exact released version and evidence.

Both `README.md` and `README.pypi.md` use absolute public documentation links:
publishing either description must never turn `docs/...` into a nonexistent page
beneath a package-index URL.
