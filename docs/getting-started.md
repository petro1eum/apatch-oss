# Getting started with APatch

APatch is a local-first runtime for executable contracts around AI-agent work.
The agent implements the change; the agreed requirements, scope and verifier
determine whether that work may be accepted as technically complete.

## Install

Python 3.10 or newer is required. macOS and Linux are supported.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "apatch[mcp,trustchain]"
apatch --version
```

That installs the published release. To evaluate this source candidate instead,
run `python -m pip install -e ".[dev,mcp,trustchain,yaml,platform]"` from this tree.
Neither command publishes a package or changes another project's configuration.

For this candidate, evaluate scaffold generation from the source checkout:
the installed wheel still lacks some editor/CI templates. Read the
[readiness record](public-release-readiness.md) and inspect generated files before
relying on enforcement; successful policy-file creation is not sufficient.

The base CLI also works with `python -m pip install apatch`. MCP and signed-ledger
support are optional extras. Avatar/HC peers are separate integrations, not
mandatory downloads and not a prerequisite for ordinary local work.

## Prepare your project deliberately

Inside a Git project you own:

```bash
apatch init-consumer --target-dir . --with-sandbox --with-enforcement
apatch doctor
apatch status
```

Initialization creates local governance configuration and guidance. Read the
diagnostics and enroll your own signing identity where required; no signing key,
production identity or private development ledger is supplied with this snapshot.
Review generated editor integration before enabling it. See [MCP setup](mcp_setup.md).

## Specify work before implementation

Write requirements and requirement-specific checks using the
[SPEC authoring guide](spec-authoring.md). Connect acceptance criteria through
[RFP authoring](rfp-authoring.md); do not use one unrelated green build to claim
every requirement.

Replace `SPEC-YOUR-1` below with an executable SPEC present in your project:

```bash
apatch spec lint --spec SPEC-YOUR-1
apatch spec run --spec SPEC-YOUR-1 --dry-run
```

The dry run checks the execution plan. It does not implement the work.
Through MCP, use `apatch_execute_next` for a requirement or `apatch_spec_run`
with the actual requirement mutations and let the governed workflow verify,
attest and close the session.

## Freeze the judge when using strict SDD

Sandbox/enforcement setup does not automatically freeze a test contract.
The strict SDD profile additionally needs an owner-frozen verification contract,
fixed judge assets/commands and an exact task envelope.

Read [the frozen-contract requirements](RFP-044-sdd-integrity-adoption.md)
and [the fixed-purpose verifier](RFP-045-sdd-verifier-closure.md).
Changing agreed acceptance rules requires an authority-approved amendment.
APatch-mediated controls do not seal off arbitrary independent shell access.

## Inspect evidence and keep checking

```bash
apatch status
apatch spec list
apatch report --html --out .apatch/report.html
apatch report --format md --out -
```

[Conformance](conformance.md) rechecks enrolled requirements; [probe](probe.md)
tests verifier quality; [reality](reality.md) connects observed defects to requirements.
Stale or missing evidence is not fresh proof.

Work history and factual time evidence stay local. Service connections and exports
are voluntary. An upload is not business acceptance and a timesheet draft is not
a payroll decision. See [governed work](governed-work-trustchain.md).

## Documentation without a service account

This tree and its source distribution contain `docs/`, examples, executable
SPECs and the [documentation index](README.md). No private Git history is needed.
Detailed references may still be in Russian; these English entry pages explain
the workflow and link to the existing engineering contracts.
