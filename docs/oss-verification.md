# OSS release verification

APatch turns agent instructions into an executable contract: requirements define
the permitted changes, verification supplies evidence, and signed records bind
that evidence to the work. Release verification must follow the same rule. A
scoped result is not permission to rewrite the underlying contract or its tests.

## Two explicit evidence scopes

| Profile | Required environment | What a passing profile means |
| --- | --- | --- |
| `standalone` | Public dependencies; no `avatar_contract` module | The complete local suite and exhaustive native gate contain only the exact reviewed absent-peer observations. |
| `avatar` | Installed canonical peer plus a clean checkout at the source-declared Git pin | The complete local suite, native gate, complete canonical peer suite and unchanged shared-contract requirements pass within their declared scope. |

Neither profile proves live HC, Tracker, Platform or production inclusion. Neither
uploads packages, changes repository visibility, signs owner approval or publishes
the private Avatar contract. Standalone operation requires no Platform account.

The standalone inventory currently classifies 40 exact test failures, 22 exact
requirement failures and the existing dependency-specific optional skips. These
are **raw failures, not passing tests**. The raw standing contract remains red.
Mixed modules still run in full, including all local-only tests. A new failure,
changed message, changed input hash, unexplained skip, timeout or incomplete
report fails qualification. Do not automatically refresh this inventory to make
a changed run pass: review the change and its evidence first.

## Run from a reviewed public checkout

Use a clean POSIX qualification environment, not the running MCP environment.
Install a wheel built from this exact checkout with its public verification
dependencies (`dev`, `mcp`, `trustchain`, `yaml`, `platform`; `languages` is optional).
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

For an independently provisioned, clean, pinned canonical peer:

```bash
python scripts/qualify_oss.py --profile avatar --source . --avatar-checkout /path/to/avatar-contract --output /tmp/apatch-avatar-fresh
```

The exact pin is the explicit installation comment in `pyproject.toml`. Missing,
dirty, differently pinned or substituted source/installations stop before the
suite starts. The tool does not install or repair the peer. Private shared-source
snapshots stay outside the public checkout; never upload those directories as
public CI artifacts.

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
