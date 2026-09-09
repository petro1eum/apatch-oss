# APatch documentation

Source version: **0.8.43**. See [PyPI](https://pypi.org/project/apatch/) for published package versions
and the [verification guide](oss-verification.md) for the evidence scope.

APatch turns specification-driven and test-driven agent work into an executable contract:
requirements, scoped authority, fixed verification and evidence belong to the same workflow.

## Start here

- [Candidate readiness and open issues](public-release-readiness.md): what is verified and what still blocks release.
- [OSS verification profiles](oss-verification.md): complete standalone/Avatar runs and honest scoped reports.
- [Getting started](getting-started.md): installation, local projects and the first governed workflow.
- [Product overview](../README.md): the contract-first model and its limits.
- [Executable specifications](executable-specs.md) and [SPEC authoring](spec-authoring.md).
- [MCP setup and CLI parity](mcp_setup.md), [agent onboarding](agent-onboarding.md) and [agent playbook](AGENTS.template.md).
- [Runtime identity and configured-child checks](mcp_setup.md#runtime-identity-and-health): installed environment, running MCP and host tool availability are separate observations.
- [Design Partner Playbook](design-partner-playbook.md): detailed setup and adoption checks.

The entry pages are in English. Many detailed engineering references below remain in Russian.
They are included in this source tree and source distribution; they do not require access to a private repository.

## Contracts and verification

| Subject | Read |
| --- | --- |
| Requirements and scoped ownership | [Ownership SPEC](specs/SPEC-SPEC-OWNERSHIP-GATE-1.md), [RFP authoring](rfp-authoring.md) |
| Frozen acceptance rules and task envelopes | [RFP-044](RFP-044-sdd-integrity-adoption.md), [SDD integrity SPEC](specs/SPEC-SDD-INTEGRITY-1.md) |
| Fixed-purpose verifier and falsification | [RFP-045](RFP-045-sdd-verifier-closure.md), [Probe](probe.md) |
| Continuous conformance | [Conformance](conformance.md), [Reality feedback](reality.md) |
| Runtime controls and limits | [Invariants](governed-runtime-invariants.md), [Sandbox](sandbox.md), [Security model](security-one-pager.md) |
| Full executable contract catalog | [SPEC index](specs/README.md) |
| Historical freeze portability | [Public-source amendment](public-source-amendment.md) |

Installing APatch does not enable every optional control. In particular, sandbox/enforcement
configuration is not the same as an owner-frozen SDD verification contract.
A successful test suite is not proof of external delivery, owner acceptance or payable work.

## Local work and optional connections

- [CLI cookbook](cookbook.md), [orchestration](orchestration.md), [transactional sessions](transactional-mcp-sessions.md).
- [Remote workspace setup](remote-onboarding.md) and [remote configuration example](examples/remote/remote.example.json).
- [WorkAssets](work-assets.md), [privacy and intellectual property](work-assets-privacy-ip.md), [consent and retention](work-assets-consent-retention.md).
- [Governed work and TrustChain](governed-work-trustchain.md), [Avatar architecture](AVATAR-ARCHITECTURE-CANON.md), [Avatar utility contract](AVATAR-UTILITY-CONTRACT.md).
- [Local extensions](extensions.md), [strip guide](strip_guide.md), [database and architecture workflows](orchestration.md).
- [Reports for leaders](for-leaders.md), [engineering evidence overview](engineering-truth-overview.md).
- [Studio and Cowork delivery boundary](apatch-studio.md), [product matrix](product-matrix.md).

Local history belongs to the user. Service connections and selected evidence exports are voluntary.
An external service retains its own identity, project, authority and business-acceptance decisions.

## Contributing and publishing

See [contributing](../CONTRIBUTING.md), [agent rules](../AGENTS.md),
[security reporting](../SECURITY.md), [release policy](release-versioning.md)
and [public source boundary](../SOURCE-EXPORT.md).

The public snapshot contains no private Git history, operational signing identity or historical
production inclusion proof. Portable policies and executable contracts remain included.
[Design archives](archive/README.md) are historical references, not current shipping promises.
