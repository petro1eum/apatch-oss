# OSS release qualification and historical evidence

This document records source-boundary checks and historical observations, not an
automatic approval of every artifact built from this tree. Fresh qualification
must name its profile and exact source hashes. The existing private repository
must not be made public as a shortcut.

## Current assembly — 2026-09-12

The owner approved the new destination `petro1eum/apatch-oss`; the old repository
and its history stay private. This revision adds the reviewed Cowork execution
proposal, selective evidence delivery and canonical Work Item acceptance path,
plus runtime-identity and file-bound reverification fixes and explicit
[OSS verification profiles](oss-verification.md). The complete public source suite
passed **2240 tests with 1 optional skip** locally; this is not a hosted CI run or
proof of live Avatar, HC, Platform or Cowork acceptance.

The public workflow now invokes `standalone` and retains an explicit allowlist of
raw evidence. It does not waive failed checks, remove enrollment or certify live
Avatar/HC/Platform acceptance. A passing scoped report and a red raw standing gate
must be reported separately, as must local evidence, hosted CI execution and publication.

## Verified boundaries

- Of 241 runtime/compatibility files, 221 match the original baseline; 20 match
  the committed fixes recorded in the source manifest. No private
  commit history was imported.
- MIT is unchanged and matches TrustChain OSS.
- The selected source tree has no imported private Git history, Pro implementation,
  production signing identity, operational ledger or unrelated third-party dataset.
- Source distribution includes the documentation, executable specifications,
  tests, portable policies and exact historical RFP fixture.
- The English PyPI description contains no relative documentation hyperlinks.
- The owner-approved frozen-test amendment changes only how historical bytes are
  read; original assertions and historical freeze records remain preserved.

These checks do not certify absence of every possible secret or prove acceptance
by an external service.

## Historical qualification — preceding 237-file revision

This revision includes the reviewed ownership fix and the installed-resource /
native conformance-exit fixes. The dev change passed 82 targeted checks, including
wheel execution outside the checkout, user-file preservation, and missing-resource
failure before writes. Wheel and sdist contain the same 20 canonical resource inputs.
Fresh qualification of this exported revision on Python 3.14 completed with
**1921 passed, 40 failed, 6 skipped**. The failed test identities are exactly the
same 40 optional Avatar integration tests as in the preceding revision; no new
failures were introduced. A task-local workspace registry isolated test MCP
processes from the operator's real projects. An earlier misconfigured run had
nine additional permission-related startup failures; that run is not the baseline
used for this result. No access guard was disabled.

All 22 focused checks from the unpacked source distribution passed, including
source boundaries, documentation links, frozen-test portability and consumer
initialization using code extracted from the wheel outside the source checkout.
This wheel execution check is not a claim of a new package installation into the
operator's environment. Archive inventory, license, metadata and resource-byte
parity checks passed. Other Python versions and a remote CI run were not remeasured.

## Remaining qualification and historical observations

### PUB-QA-1: optional Avatar integration is not a public-only test profile

The preceding revision completed with **1882 passed, 40 failed, 6 skipped** in a
public-only environment. The current revision retains those same 40 failures.
All 40 failures exercise
Avatar/HC integration paths requiring the separately distributed `avatar-contract`.
The affected files are `test_avatar_delivery.py`, `test_avatar_evidence.py`,
`test_avatar_runtime_config.py`, `test_episode.py`,
`test_governed_work_compatibility.py`, `test_outcome_delivery.py` and
`test_taxonomy_delivery.py` under `tests/`.

Five skip entries also concern that absent peer (including two module-level
collection skips); the remaining skip requires the optional Java grammar.
Tests and assertions were not disabled or rewritten to manufacture a green run.
The public CI workflow therefore remains a qualification gate, not a claim that
the whole suite already passes with public-only inputs.

Fresh live conformance checked all 91 enrolled specifications: 85 conformant,
3 drifted, 2 broken, 1 unproven. The semantic verdict remains `gate: failed` and
`contract_holds: false`. The corrected native CLI returned **exit 1** in configured
blocking mode without an extra `--blocking` flag. The previous revision incorrectly
returned zero for that verdict. Public CI retains the additional JSON check in
`scripts/check_public_conformance.py`.

The non-green specifications remain Avatar evidence, episodes, governed-work
compatibility, contribution timesheets, edge lockstep and the shared Avatar
contract. Absent peer tests and verification commands targeting a sibling checkout
are not standalone OSS acceptance. No enrollment was removed to change this result.

Before release, explicitly define and test the standalone OSS and optional-peer
verification profiles, retaining separate integration acceptance. Alternatively,
qualify an owner-approved public distribution of the peer. Do not bundle private
peer code, invent a replacement contract, or restore a direct URL dependency.

### PUB-QA-2: installed-wheel scaffold template defect closed locally

In the preceding revision, a wheel installation was checked outside the source tree. The command
`init-consumer --with-ci --with-sandbox --with-enforcement --no-with-mcp`
returned success and created policy files, but did not create the CI workflow,
consumer `AGENTS.md` or Cursor hook configuration.

The preceding runtime read those templates from repository-relative `docs/` and
`scripts/` paths that are absent from the installed wheel. The source distribution
contains them; that does not fix wheel installation. Policy JSON alone does not
establish the missing editor or CI enforcement.

The current revision builds canonical templates into package resources and loads
them from the installed wheel. It fails before consumer writes when a required
resource is missing. Regression checks cover CI, AGENTS, editor and commit hooks,
devcontainer, profile docs, manifests, user-file preservation, and corrupted inputs.
The exported wheel and source archive passed the targeted regression checks above.
This closes the missing-resource defect, not PUB-QA-1 or external release acceptance.

## Publication steps after qualification

Create only the approved `petro1eum/apatch-oss` destination; retain the old
repository privately. Establish the actual private vulnerability-reporting route.
Verify anonymous documentation access, update candidate wording, rerun artifact
and contract checks, and publish only the exact newly qualified artifacts.

Historical private attestations and passing local checks cannot replace those steps.
