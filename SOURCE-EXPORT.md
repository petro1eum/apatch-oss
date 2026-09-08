# Public OSS source boundary

This is a clean **0.8.43 source candidate**, prepared on 2026-09-08 from the
already-developed APatch runtime. It has not been uploaded to PyPI and no
public GitHub destination has been assigned by this preparation step.

**Publication status: HOLD.** The source boundary is prepared, but clean-public
integration qualification still has open issues. The installed-resource and native
gate-exit fixes passed their targeted checks in this revision; the full public-only
suite still has 40 optional Avatar integration failures.
See [the readiness record](docs/public-release-readiness.md); do not describe this
candidate as a fully qualified release.

The current published package remains [APatch 0.8.42](https://pypi.org/project/apatch/0.8.42/).
The MIT license is unchanged and identical to TrustChain OSS.

## Included

- The APatch CLI/MCP runtime and compatibility package: 237 selected module/schema files;
  231 remain byte-identical to the baseline and six match the reviewed fixes recorded
  in the source manifest (ownership, consumer resources, and blocking CLI exits).
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

Repository creation/name selection, public remote URLs, private vulnerability
reporting and the final package upload are separate publication steps. Before
publishing, verify the chosen documentation URL anonymously and replace the
candidate wording with the exact released version and evidence.

The repository README uses offline-relative documentation links. The separate
`README.pypi.md` has no relative hyperlinks: copying a README to PyPI must never
turn `docs/...` into a nonexistent page beneath a PyPI project URL.
