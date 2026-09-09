# APatch — executable contracts for AI agents

**Turn AI-assisted development from instructions into an executable contract.**

APatch connects what an AI agent is asked to build, what it is authorized to
change, how the result must be verified, and the evidence required to call the
work done. It brings specification-driven development and test-driven
development into one governed execution workflow.

The agent writes the implementation. It does not get to redefine success just
because its first attempt failed.

APatch is an open-source, local-first Python runtime with a CLI and an MCP
interface for AI coding agents. Its patch engine is the execution mechanism;
the product is the contract around the work.

## From instructions to a contract

Specifications and tests are essential. But attaching a specification to a
conversation does not bind an agent to it, and asking an agent not to change
tests does not protect the acceptance criteria.

APatch makes those expectations explicit and machine-checkable. With the
corresponding ownership, enforcement and strict SDD controls enabled:

| Instruction to the agent | APatch contract mechanism |
|---|---|
| "Follow this specification." | Bind the session to exact requirement revisions and their verification obligations. |
| "Stay within scope." | Admit only the declared write-set and task-envelope effects through APatch. |
| "Do not change the tests to make them pass." | Freeze judge assets and commands; separate implementation and verifier capabilities. |
| "Prove that it works." | Execute the bound checks and require meaningful results, including red/restore/green falsification for material obligations. |
| "Do not break what we already accepted." | Recheck enrolled requirements and distinguish live regressions from stale or missing evidence. |
| "Tell me what was actually done." | Derive status from recorded mutations, verification and signed attestations, not editable completion claims. |

This is an **engineering contract**, not a claim of legal enforceability or a
guarantee that a model can never make a mistake. APatch adds execution control
and evidence to SDD and TDD; it does not replace specification quality, test
design, human approval, Git or CI.

## How the contract works

1. **Define the obligation.** Map RFP acceptance criteria to executable SPEC
   requirements. Each requirement names its own verification, rather than
   borrowing one green build as proof of everything.
2. **Fix the rules before implementation.** The opt-in strict SDD profile binds
   a frozen contract revision, exact judge assets and command hashes, a baseline,
   verification perspectives and a bounded task envelope.
3. **Issue a scoped session.** The implementation capability is tied to the
   requirement and contract. Declared ownership and envelope admission govern
   APatch-mediated writes and other supported effects.
4. **Execute and verify.** Apply changes transactionally with checkpoints. The
   fixed-purpose SDD verifier loads the frozen judge; the implementation caller
   cannot supply a substitute command, a passing result or a verifier role.
5. **Record evidence, then keep checking.** Attest the exact work and its proof.
   Continuous conformance checks whether the enrolled contract still holds as
   the project changes. A changed requirement or file can make old evidence
   stale; missing evidence remains visible instead of becoming a fresh pass.

If the agreed scope or judge needs to change, the strict model provides an
authority-approved amendment path with new hashes and selective invalidation.
Rewriting history is not that path.

```text
Requirement + verification obligation
                 |
      Frozen contract + task envelope      [strict SDD profile]
                 |
       Scoped session -> admitted changes
                 |
      Bound verification + falsification
                 |
      Attestation / failure and recovery
                 |
        Continuous conformance
```

### Example: implementing a payment rule

The owner agrees that duplicate payment requests must not create duplicate
charges. The contract names the requirement, the allowed implementation files,
and the frozen positive, negative, boundary and regression checks.

The agent can implement the rule within that scope. It cannot use its
implementation capability to weaken the frozen duplicate-charge test or replace
the judge with a shorter passing command. If the rule itself needs to change,
that is an amendment for the authority, not an implementation shortcut.

A passing test is only one part of the result: the evidence must refer to the
same contract, session and changed artifacts. Later changes can trigger a new
conformance check. Technical attestation is still distinct from the owner's
business acceptance.

## Start with executable specs

Requires **Python 3.10+**. macOS and Linux are the supported platforms.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "apatch[mcp,trustchain]"

# Run inside the project you want to govern.
apatch init-consumer --target-dir . --with-sandbox --with-enforcement
apatch doctor

# Replace SPEC-YOUR-1 with an executable SPEC in your project.
apatch spec lint --spec SPEC-YOUR-1
apatch spec run --spec SPEC-YOUR-1 --dry-run

# Inspect the same project facts as the agent.
apatch status
apatch spec list
apatch report --html --out .apatch/report.html
apatch report --format md --out -
```

The dry run inspects readiness; it does not implement the specification. Through
MCP, use `apatch_execute_next` for one requirement or `apatch_spec_run` with
the actual requirement mutations or a versioned run manifest. The workflow
handles the governed session, application, verification and attestation.

**Sandbox/enforcement setup does not automatically freeze a test contract.**
The strict SDD profile additionally needs the owner-frozen verification contract
and exact task envelope. Configure that profile deliberately; do not infer its
protection from the presence of a SPEC file or from a successful installation.

The developer's `apatch status`, architect's HTML report and manager's Markdown
report share `project_status_workspace` / `apatch_project_status`: one set of
facts, three views ([RFP-020](https://github.com/petro1eum/apatch-oss/blob/main/docs/RFP-020-three-views.md)).

### Installation options

```bash
python -m pip install apatch                    # Local CLI and patch engine
python -m pip install "apatch[mcp]"             # MCP interface for AI agents
python -m pip install "apatch[mcp,trustchain]"  # MCP and signed-ledger integration
```

Additional extras: `yaml` for YAML manifests, `languages` for Java/C#/Ruby/PHP
grammars, `platform` for the optional Platform transport, and `dev` for source
development and packaging checks. C++, Python, JavaScript, TypeScript, Rust and
Go grammars are included in the base package.

## What is included

- **Executable specifications and plans.** RFP-to-SPEC traceability, contract
  scaffolding, requirement-specific checks, registered plans and plan adherence.
- **Scoped execution.** Governed sessions, exact requirement ownership,
  capabilities, sandbox hooks, path leases and bounded apply chunks.
- **Frozen verification.** The opt-in SDD profile protects judge assets,
  separates roles and runs the exact contract-bound verifier. False-green result
  shapes such as zero-collected or all-skipped checks cannot satisfy it.
- **Verification quality.** `probe falsify`, `probe regress` and `probe ratify`
  test whether a gate detects relevant breakage, introduces no new regressions
  against a baseline, and still passes when rechecked.
- **Continuous conformance.** Inspect the enrolled contract across the current
  tree, including `conformant`, `drifted`, `broken`, `stale` and `unproven` states.
  Enrollment, live verification and domain acceptance are distinct facts.
- **Reality feedback.** Record observed bugs, incidents and feedback, and connect
  them to requirements that discharge the resulting evidence debt.
- **Transactional changes and recovery.** Exact, whitespace-, AST- and
  Markdown-aware matching; previews; multi-file checkpoints; controlled rollback.
- **Coordination and analysis.** Cross-spec interference, safe execution order,
  symbol anchors, cross-file impact, architecture/database/semantic checks and
  engineering pipelines.
- **Signed evidence and Git handoff.** TrustChain integration for attestations,
  attribution and exact-file `commit-attested`; optional certificate enrollment
  and external transparency inclusion for separately verifiable provenance.
- **Local professional history.** Signed work facts, factual timesheet drafts,
  metadata-only WorkAssets, Avatar/HC adapters and durable offline delivery.
- **Local and remote operation.** MCP workspace aliases and policy-controlled
  remote brokers keep host topology and credentials outside agent-facing plans.

## Local first; services are optional

Local work does not require a hosted account or a Pro subscription. Specifications,
plans, checkpoints and work evidence accumulate in the user's workspace.

Connecting to TrustChain or Human Capital is an explicit integration choice.
Supported governed-work exports use bounded metadata and signed references, not
an automatic upload of source code, private prompts, credentials or raw logs.
Queued evidence survives an unavailable service and can be reconciled later.

Avatar/HC adapters remain in OSS. Their external `avatar-contract` peer is not
bundled and is installed separately where authorized; there is no public
`apatch[avatar]` extra. A timesheet draft records work facts, not accepted hours,
payroll, pricing or settlement. An evidence upload is not business acceptance.

Work starts through a locally opened governed session. Network responses do not
become a second instruction channel: the governed-work interface accepts bounded
verification inputs or signed decisions about work already declared locally.

## Trust and enforcement boundaries

APatch reports the protection that is actually enabled:

| Layer | Boundary |
|---|---|
| Base CLI | Local tooling; installation alone does not activate strict governance. |
| Audit | Checkpoints and available ledger integration; not fail-closed enforcement. |
| Enforce | Configured notarization requirements and rollback on notarization failure. |
| Strict SDD | Frozen judge and task-envelope admission for APatch-mediated effects. |
| Continuous conformance | Opt-in contract enrollment, live checks and project-selected blocking policy. |

The default conformance policy blocks live regressions; broken checks, stale
evidence and unproven requirements remain visible and can be made blocking by
the project owner. A green gate is not proof of requirements that were never
enrolled or of external integrations that were not exercised.

An agent with unrestricted shell, filesystem or network access outside APatch
still has an out-of-band channel. Ordinary local use is **mediated-only**, not
sealed containment. Stronger isolation needs an appropriate runner and access
policy. Do not describe hooks or signed records as universal containment.

TrustChain is a cryptographic signing and audit substrate, not a blockchain.
Local bootstrap/checkpoint support is not the same as an enrolled Ed25519
identity or a publicly anchored transparency proof. Those require the matching
dependencies and configuration. Signing proves provenance and integrity; it
does not turn an inadequate test into proof of correctness.

Verification commands execute code. Only authorize trusted commands and judge
assets. Failure handling depends on the workflow and policy: APatch supports
fix-forward and rollback, rather than promising that every red test always
triggers the same recovery action.

## Replay IDE transcripts (secondary workflow)

For existing patch logs outside an executable-spec workflow:

```bash
apatch scan
apatch view --logs transcript.jsonl
apatch plan --logs transcript.jsonl --target-dir . --diff
apatch apply --logs transcript.jsonl --target-dir .
```

APatch can ingest supported agent transcripts and patch envelopes, preview
matching confidence, and apply selected changes. Transcript replay is an
additional execution/recovery tool, not a substitute for governed acceptance.
Existing SPEC ownership and workspace policies still apply.

## Documentation

The complete selected engineering documentation is included under `docs/` in
this source tree and its source distribution. Start with the
[English documentation index](https://github.com/petro1eum/apatch-oss/blob/main/docs/README.md) or [getting started](https://github.com/petro1eum/apatch-oss/blob/main/docs/getting-started.md).
Source version: **0.8.43**. The [OSS verification guide](https://github.com/petro1eum/apatch-oss/blob/main/docs/oss-verification.md)
explains standalone scope, raw contract results and separate integration acceptance.
See [PyPI](https://pypi.org/project/apatch/) for published package versions.

| Topic | Reference |
|---|---|
| Documentation index and CLI recipes | [Index](https://github.com/petro1eum/apatch-oss/blob/main/docs/README.md), [cookbook](https://github.com/petro1eum/apatch-oss/blob/main/docs/cookbook.md) |
| Agent onboarding and MCP setup | [Onboarding](https://github.com/petro1eum/apatch-oss/blob/main/docs/agent-onboarding.md), [MCP](https://github.com/petro1eum/apatch-oss/blob/main/docs/mcp_setup.md), [playbook](https://github.com/petro1eum/apatch-oss/blob/main/docs/AGENTS.template.md) |
| Executable requirements and ownership | [Spec authoring](https://github.com/petro1eum/apatch-oss/blob/main/docs/spec-authoring.md), [ownership contract](https://github.com/petro1eum/apatch-oss/blob/main/docs/specs/SPEC-SPEC-OWNERSHIP-GATE-1.md) |
| Frozen contracts, roles and task envelopes | [RFP-044](https://github.com/petro1eum/apatch-oss/blob/main/docs/RFP-044-sdd-integrity-adoption.md), [integrity SPEC](https://github.com/petro1eum/apatch-oss/blob/main/docs/specs/SPEC-SDD-INTEGRITY-1.md) |
| Fixed-purpose verifier and falsification | [RFP-045](https://github.com/petro1eum/apatch-oss/blob/main/docs/RFP-045-sdd-verifier-closure.md), [probe](https://github.com/petro1eum/apatch-oss/blob/main/docs/probe.md) |
| Ongoing proof and observed failures | [Conformance](https://github.com/petro1eum/apatch-oss/blob/main/docs/conformance.md), [reality](https://github.com/petro1eum/apatch-oss/blob/main/docs/reality.md) |
| Runtime and security boundaries | [Runtime invariants](https://github.com/petro1eum/apatch-oss/blob/main/docs/governed-runtime-invariants.md), [sandbox](https://github.com/petro1eum/apatch-oss/blob/main/docs/sandbox.md), [security](https://github.com/petro1eum/apatch-oss/blob/main/docs/security-one-pager.md) |
| Architecture, database work and extraction | [Orchestration](https://github.com/petro1eum/apatch-oss/blob/main/docs/orchestration.md), [strip guide](https://github.com/petro1eum/apatch-oss/blob/main/docs/strip_guide.md) |
| Work history, Avatar and service integration | [Governed work](https://github.com/petro1eum/apatch-oss/blob/main/docs/governed-work-trustchain.md), [Avatar canon](https://github.com/petro1eum/apatch-oss/blob/main/docs/AVATAR-ARCHITECTURE-CANON.md), [WorkAsset privacy/IP](https://github.com/petro1eum/apatch-oss/blob/main/docs/work-assets-privacy-ip.md) |

Many detailed engineering documents are currently in Russian. This English
README is the primary product description; translation of the complete
documentation set is separate work.

## Development and verification

From a source checkout or extracted source distribution:

```bash
python -m pip install -e ".[mcp,trustchain,dev,yaml]"
python -m pytest tests/ -q
```

Follow the repository's `AGENTS.md` and declared ownership when contributing.
Report the tested commit, environment and actual results. A test count or a
checked documentation box is not a substitute for requirement-level evidence.

## License and product boundary

**APatch OSS is available under the MIT license**, copyright 2026 Ed Cherednik,
with the same license text as TrustChain OSS. The license is included in both
the wheel and source distribution.

This package contains the APatch OSS CLI/MCP runtime. It does not bundle Pro
implementations, managed services or the external Avatar peer. APatch Studio OSS
and the APatch Studio Pro / TrustChain Cowork assembly have their own delivery
and acceptance lifecycle; publishing this runtime does not declare them released.

Third-party assets and user extensions retain their respective licenses.
Publishing or exporting work evidence does not transfer ownership of source,
private data or professional work, and does not grant external training rights.
