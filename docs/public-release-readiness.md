# Public source candidate: publication hold

This is a prepared source snapshot, **not an approved 0.8.43 release**.
The existing private repository must not be made public as a shortcut.
No remote repository or package was published by this preparation.

## Verified boundaries

- OSS runtime and compatibility modules are byte-identical to the reviewed baseline.
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

## Open qualification issues

### PUB-QA-1: optional Avatar integration is not a public-only test profile

A full run in a fresh Python 3.14 environment using only public dependencies
completed with **1882 passed, 40 failed, 6 skipped**. All 40 failures exercise
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

Fresh live conformance checked 91 enrolled specifications: 85 conformant,
3 drifted, 2 broken, 1 unproven. The semantic verdict was `gate: failed` and
`contract_holds: false`, despite the CLI process returning zero. Public CI
explicitly validates the JSON verdict using `scripts/check_public_conformance.py`;
it must not infer success from that exit status. The baseline CLI itself is unchanged.

Before release, explicitly define and test the standalone OSS and optional-peer
verification profiles, retaining separate integration acceptance. Alternatively,
qualify an owner-approved public distribution of the peer. Do not bundle private
peer code, invent a replacement contract, or restore a direct URL dependency.

### PUB-QA-2: installed-wheel scaffold templates are incomplete

An actual wheel installation was checked outside the source tree. The command
`init-consumer --with-ci --with-sandbox --with-enforcement --no-with-mcp`
returned success and created policy files, but did not create the CI workflow,
consumer `AGENTS.md` or Cursor hook configuration.

The current runtime reads those templates from repository-relative `docs/` and
`scripts/` paths that are absent from the installed wheel. The source distribution
contains them; that does not fix wheel installation. Policy JSON alone does not
establish the missing editor or CI enforcement.

Before release, bundle the required runtime templates as package resources,
use an installation-safe resource loader, and add wheel-installed end-to-end
checks. Do not silently accept a missing requested template. This source snapshot
preserves runtime bytes and does not claim that this functional fix is implemented.

## Publication steps after qualification

Choose and authorize the new public repository destination; retain the old
repository privately. Establish the actual private vulnerability-reporting route.
Verify anonymous documentation access, update candidate wording, rerun artifact
and contract checks, and publish only the exact newly qualified artifacts.

Historical private attestations and passing local checks cannot replace those steps.
