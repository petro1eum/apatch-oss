# Public OSS source boundary

This is the **0.8.49 public maintenance candidate**, prepared on 2026-10-03 from
public commit `9b20e82e7ea343bdfb8784bdb20fda2b908f841b` (0.8.48).
The historical 0.8.48 export published the complete APatch line since 0.8.45, with
all 266 runtime files byte-identical to its recorded private release commit and
Apatch Pro excluded. That historical inventory is preserved verbatim in
`PUBLIC-SOURCE-MANIFEST.json`; no private commit objects are imported.
The current candidate changes five runtime files for workspace signer isolation
and spec-owned Move destination admission. The remaining 261 runtime files are
unchanged. `petro1eum/apatch-oss` remains the public source destination.
Fresh source and built-artifact qualification is required before 0.8.49 publication;
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

- The APatch CLI/MCP runtime and compatibility package: 266 module/schema files,
  five reviewed maintenance changes and 261 unchanged files relative to public
  0.8.48. The exact changed paths and before/after hashes are recorded in the
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
0.8.49 delta, including current verification-inventory pins. Existing profile
provenance remains historical; refreshing source hashes does not claim a new pass.
It excludes itself, Git metadata and generated build/test/runtime artifacts.
It is an inventory, not a cryptographic owner attestation or a security certificate.

The sole frozen-test portability amendment is documented in
[the amendment record](docs/public-source-amendment.md). Historical freeze records
and all original assertions are retained; the frozen RFP bytes keep their exact
SHA-256. The other frozen judge assets remain unchanged.

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
