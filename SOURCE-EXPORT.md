# Public OSS source boundary

This is a clean **0.8.43 source candidate**, updated on 2026-09-09 from the
already-developed APatch runtime. The owner selected `petro1eum/apatch-oss` as
the new public destination; this candidate has not yet been pushed or uploaded.

**Release scope: standalone OSS, with separately qualified optional integrations.**
The full public-only suite retains40 absent-Avatar failures; a passing standalone
profile must not relabel those tests or the raw native contract as passing.
Use [the verification guide](docs/oss-verification.md) and exact source hashes to
produce fresh evidence. This inventory and [the historical readiness record](docs/public-release-readiness.md)
are not themselves an acceptance certificate or proof of external integration.

Published package versions are listed on [PyPI](https://pypi.org/project/apatch/).
The MIT license is unchanged and identical to TrustChain OSS.

## Included

- The APatch CLI/MCP runtime and compatibility package: 239 selected module/schema files;
  224 remain byte-identical to the baseline and 15 match committed fixes recorded
  in the source manifest (ownership, resources, blocking exits, runtime identity
  and file-bound reverification). No private commit objects are imported.
- Executable requirements, regression tests, frozen verification records and selected
  engineering guides, examples and tutorials.
- Local Avatar/HC adapters, factual time-accounting, WorkAssets and optional service
  integration. External peers and services remain separately installed and authorized.
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

Repository creation, public remote URLs, private vulnerability
reporting and the final package upload are separate publication steps. Before
publishing, verify the chosen documentation URL anonymously and record the
exact released version and evidence.

The repository README uses offline-relative documentation links. The separate
`README.pypi.md` has no relative hyperlinks: copying a README to PyPI must never
turn `docs/...` into a nonexistent page beneath a PyPI project URL.
