# OSS release verification

APatch turns agent instructions into an executable contract: requirements define
the permitted changes, verification supplies evidence, and signed records bind
that evidence to the work. Release verification must follow the same rule. A
scoped result is not permission to rewrite the underlying contract or its tests.

## Two explicit evidence scopes

| Profile | Required environment | What a passing profile means |
| --- | --- | --- |
| `standalone` | Public dependencies; no top-level `avatar_contract` module installed (the public-install case) | The complete local suite passes except the reviewed optional Java grammar skip, and the exhaustive native gate holds with only the canonical sibling-repository contract unproven. |
| `avatar` | A clean canonical `avatar-contract` checkout at the commit pinned in `apatch/_vendor/avatar_contract/UPSTREAM.json`; an installed top-level peer is neither required nor used | The bundled copy equals the canonical checkout, and the complete local suite, native gate, complete canonical suite and unchanged shared-contract requirements pass within their declared scope. |

Neither profile proves live HC, Tracker, Platform or production inclusion. Neither
uploads packages, changes repository visibility or signs owner approval.
Standalone operation requires no Platform account.

Since the owner decision of 2026-09-26, APatch bundles the canonical MIT
`avatar-contract` byte-for-byte as `apatch._vendor.avatar_contract` and never imports
a top-level `avatar_contract`. The inventory (`apatch.oss-verification-inventory.v2`)
therefore classifies no absent-peer test failures, no peer requirements and no Avatar
skips: any test failure, any drifted or broken requirement and any skip other than the
reviewed optional Java grammar skip fails qualification. The canonical
sibling-repository contract checks remain the single unproven external specification
in a standalone run. A changed input hash, unexplained skip, timeout or incomplete
report fails qualification. Do not automatically refresh this inventory to make a
changed run pass: review the change and its evidence first.

## Run from a reviewed public checkout

Use a clean POSIX qualification environment, not the running MCP environment.
Install a wheel built from this exact checkout with its public verification
dependencies (`dev`, `mcp`, `trustchain`, `yaml`, `platform`; `languages` is optional).
For the 0.8.50 intake qualification, install the exact built public Studio 0.1.5
wheel alongside the exact Core wheel. The full frozen intake suite verifies that
pair; Studio is a release-test prerequisite, not a base dependency of Core.
The v3 runner preserves committed public provenance and executes all tests
against installed SDK bytes. The historical v2 profile remains distinct.

Do not use an editable install: installed runtime and packaged consumer-resource
bytes must match the reviewed source. Python 3.10 needs the declared `tomli`
development dependency. Pin the resolved environment for repeatable release runs;
differences in pytest failure rendering are not silently treated as equivalent.

```bash
python scripts/qualify_oss.py --profile standalone --source . --output /tmp/apatch-standalone-fresh
```

The output directory must not exist and must be outside the public checkout.
The runner checks `PUBLIC-SOURCE-MANIFEST.json`, the source-bound inventory,
installed runtime bytes/version, console interpreter and actual dependencies.
It then creates separate fresh source snapshots for the full suite and native
gate, without private Git history, local credentials, workspaces or old ledgers.
It accepts no test selection, imported report or cached-pass argument. Native
enrollment, policy and frozen assertions remain unchanged.

For an independently provisioned, clean canonical checkout at the bundled pin:

```bash
python scripts/qualify_oss.py --profile avatar --source . --avatar-checkout /path/to/avatar-contract --output /tmp/apatch-avatar-fresh
```

The exact pin is the `commit` in `apatch/_vendor/avatar_contract/UPSTREAM.json`.
The prerequisite archives the checkout at that commit and requires every vendored
file, after the inverse import rewrite, to equal the canonical bytes and the recorded
hashes. A missing, dirty or differently pinned checkout, a drifted vendored file or a
changed record stops before the suite starts. The tool does not install or repair
anything; the canonical checks import the verified checkout copy, not an installed
package. Shared-source snapshots stay outside the public checkout; never upload those
directories as public CI artifacts.

## Read the report without overstating it

`qualification.json` keeps separate fields:

- `profile_passed`: the named scope passed; exit 0 means only this.
- `raw_suite_passed`: whether the complete unchanged suite actually passed.
- `raw_contract_holds`: the native standing-contract verdict under unchanged policy.
- `external_acceptance`: always `not_checked` by this local verifier.
- `release_authorized`: always `false`; publication remains a separate owner action.

Unmeasured raw verdicts are `null`, not success. On failure, inspect `error` and
the retained process metadata, stdout/stderr, full JUnit and observation files.
An interruption or prerequisite failure cannot produce a passing profile.
`complete/complete-run.json` binds full-run evidence to source hashes. Historical
provenance in the inventory describes its reviewed baseline, not a new test run.

## Public CI job

The public snapshot's release workflow uses this named job; do not replace the
classification step with `continue-on-error`, filtered tests or `|| true`.
The output upload is an explicit evidence allowlist, not the whole work directory.
Build in a disposable checkout (build caches are not release evidence).

```yaml
name: OSS standalone qualification
on: [push, pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  standalone:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.14'
      - name: Build the reviewed wheel
        run: |
          python -m pip install 'build>=1.2' 'setuptools>=77' wheel
          python -m build --wheel --outdir "${{ runner.temp }}/apatch-dist"
          python -m pip install "$(find "${{ runner.temp }}/apatch-dist" -name '*.whl')[dev,mcp,trustchain,yaml,platform]"
      - name: Qualify standalone (raw failures remain visible)
        run: python scripts/qualify_oss.py --profile standalone --source . --output "${{ runner.temp }}/apatch-qualification"
      - name: Retain verification evidence only
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: standalone-verification-evidence
          path: |
            ${{ runner.temp }}/apatch-qualification/qualification.json
            ${{ runner.temp }}/apatch-qualification/complete/complete-run.json
            ${{ runner.temp }}/apatch-qualification/complete/suite/*.json
            ${{ runner.temp }}/apatch-qualification/complete/suite/*.xml
            ${{ runner.temp }}/apatch-qualification/complete/suite/*.stdout
            ${{ runner.temp }}/apatch-qualification/complete/suite/*.stderr
            ${{ runner.temp }}/apatch-qualification/complete/contract/*.json
            ${{ runner.temp }}/apatch-qualification/complete/contract/*.stdout
            ${{ runner.temp }}/apatch-qualification/complete/contract/*.stderr
```

The example does not claim this GitHub job has run. Local release evidence and
hosted CI execution must be reported separately, especially when the hosted
runner is unavailable.
