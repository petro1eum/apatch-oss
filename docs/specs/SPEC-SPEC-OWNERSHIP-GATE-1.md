# SPEC-SPEC-OWNERSHIP-GATE-1 -- Enforced SPEC ownership

> **Status:** Proposed
> **Owner:** APatch
> **apatch artifact:** `spec:SPEC-SPEC-OWNERSHIP-GATE-1`
> **ownership mode:** strict
> **RFP:** `RFP-039`

## 0. Motivation

A signed mutation is unauthorized when its exact executable requirement did not
declare the target. Editable prose is never completion authority.

## RFP traceability

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| SO-A | R1 | covered |
| SO-B | R2 | covered |
| SO-C | R3 | covered |
| SO-D | R4 | covered |
| SO-E | R5 | covered |
| SO-F | R6 | covered |
| SO-G | R7 | covered |
| SO-H | R8 | covered |
| SO-I | R9 | covered |
| SO-J | R10 | covered |
| SO-K | R12 | covered |
| extension | R11 | attested Git handoff |

## R1 Exact slug ownership

owns: `apatch/spec_ownership.py`, `tests/test_spec_owned_mutation_gate.py`

Resolve an existing slug/category contract to one exact SPEC and enumerate all
owned target paths without fuzzy matching.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_exact_slug_ownership_resolution -q)

## R2 Generic mutation rejection

owns: `apatch/spec_ownership.py`, `apatch/workflows.py`, `tests/test_spec_owned_mutation_gate.py`

Reject unbound generic mutations of owned contract, category, schema, and SPEC
files before patch JSONL is written.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_unbound_owned_mutation_is_rejected_before_generation -q)

## R3 Requirement-bound authorization

owns: `apatch/spec_ownership.py`, `tests/test_spec_owned_mutation_gate.py`

Allow a legacy owned mutation only when the active governed session contains the
exact owning SPEC requirement; reject a requirement from another SPEC.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_exact_requirement_binding_allows_and_wrong_spec_rejects -q)

## R4 Remote worker parity

owns: `apatch/remote/worker.py`, `apatch/remote/orchestrator.py`, `tests/test_spec_owned_mutation_gate.py`

Remote execution surfaces `SPEC_WORKFLOW_REQUIRED` unchanged and creates no
patch log when ownership authorization fails.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_remote_worker_propagates_spec_workflow_required -q)

## R5 Agent-visible contract

owns: `apatch/agent_guidance.py`, `apatch/agent_playbooks.py`, `docs/AGENTS.template.md`, `docs/RFP-039-spec-owned-mutation-gate.md`, `docs/specs/SPEC-SPEC-OWNERSHIP-GATE-1.md`, `tests/test_mcp_profiles.py`, `tests/test_spec_owned_mutation_gate.py`

Doctor and agent guidance state that SPEC-owned files require
`spec_run`/`execute_next`, not a generic signed session. The playbooks an agent
reads are part of that contract, so they move with it rather than drifting under
a generic session.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_agent_guidance_exposes_hard_spec_gate -q)

## R6 Strict requirement ownership declarations

owns: `apatch/spec.py`, `apatch/spec_ownership.py`, `tests/test_spec_owned_mutation_gate.py`, `tests/test_spec.py`

Parse and resolve repository-relative exact paths and bounded `/**` prefixes
from strict requirements. A path MAY be declared by several requirements of the
same SPEC: a module that implements six requirements is owned by all six and any
of them authorizes a write to it, so shared implementation surface does not force
a file into one artificial owner. Reject a malformed entry, a repeat inside one
`owns:` line, and a path two different SPECs both claim.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_declared_requirement_ownership_resolves_exact_paths_and_prefixes -q)

## R7 Exact declared write-set enforcement

owns: `apatch/spec_ownership.py`, `apatch/workflows.py`, `tests/test_spec_owned_mutation_gate.py`

A session bound to a strict requirement may mutate every and only target paths
declared by that requirement. Reject undeclared new files before generation.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py::test_strict_requirement_rejects_undeclared_target_before_generation -q)

## R8 Ledger-derived status consistency

owns: `apatch/spec.py`, `tests/test_spec.py`

Reject top-level and requirement-table implementation claims when the current
TrustChain ledger does not derive the corresponding attested state.

(verify: python3 -m pytest tests/test_spec.py::test_spec_lint_rejects_unattested_completion_claims -q)

## R9 Ownership-driven plan scaffold

owns: `apatch/spec_needles_scaffold.py`, `tests/test_spec_needles_scaffold.py`

For strict requirements, declared ownership is the authoritative plan target
list. Verify commands and narrative prose must not silently enlarge it.

(verify: python3 -m pytest tests/test_spec_needles_scaffold.py::test_declared_ownership_is_authoritative_for_plan_targets -q)

## R10 OLang consumer enforcement profile

owns: `apatch/consumer_profiles.py`, `apatch/doctor.py`, `apatch/sandbox.py`, `apatch/cli.py`, `tests/test_doctor.py`, `docs/AGENTS.template.md`

Provide an OLang consumer profile selectable through the public `init-consumer`
command whose sandbox protects `o_lang/**`,
`docs/RFP-*.md`, `docs/specs/**`, and `AGENTS.md`, without an allow rule
silently overriding those protected paths.

(verify: python3 -m pytest tests/test_doctor.py::test_init_consumer_olang_profile_protects_full_language_surface -q)


## R12 Local SPEC bootstrap channel

owns: `apatch/spec_ownership.py`, `tests/test_spec_owned_mutation_gate.py`

A governed session that declares `spec-bootstrap:<SPEC>#Rk` may create that
not-yet-existing SPEC file through the local generate/apply channel, exactly as
the remote requirement channel does. The authorization covers only the absent
SPEC file itself: an existing SPEC, a foreign SPEC, or a plain `spec:` binding
still requires `spec_run`/`execute_next`.

(verify: python3 -m pytest tests/test_spec_owned_mutation_gate.py -q -k bootstrap)

## R13 Remote finalization preserves mandatory verification

owns: `apatch/remote/orchestrator.py`, `apatch/remote/worker.py`, `apatch/mcp/server.py`, `tests/test_remote_mcp_routing.py`, `tests/test_remote_finalization.py`, `apatch/consumer_profiles.py`, `docs/AGENTS.template.md`, `docs/README.md`, `docs/orchestration.md`, `docs/sandbox.md`, `docs/mcp_setup.md`

`fix_forward_current` with `defer_finalize=true` stops after apply and preserves the
active session; it does not run final verify, attest, or session_end. The same flag
continues to work for `execute_next`. Invalid or unsupported defer options fail
before transport.

Both fix-forward finalization and `finalize_current` resolve mandatory verify commands
from the session's exact hash-bound SPEC requirements. A caller-supplied shorter command
is supplementary, never a replacement. Missing/drifted requirements, pending asynchronous
checks, and unconfirmed older workers stop before attestation. Non-SPEC sessions retain
explicit verify. Finalization disallows dry-run, baseline allowances, and alternate modes.
MCP responses distinguish an applied but deferred session from an attested closed session.

(verify: /opt/homebrew/bin/python3.14 -m pytest -q tests/test_remote_finalization.py tests/test_remote_mcp_routing.py tests/test_remote_worker_protocol.py tests/test_remote_ssh_transport.py)

## R11 Attested Git handoff

owns: `apatch/git_commit.py`, `apatch/workflows.py`, `apatch/cli.py`, `apatch/mcp/server.py`, `apatch/remote/policy.py`, `apatch/remote/worker.py`, `apatch/remote/orchestrator.py`, `apatch/consumer_profiles.py`, `tests/test_commit_attested.py`, `tests/test_cli_commit_attested.py`, `tests/test_mcp.py`, `tests/test_remote_worker_protocol.py`, `tests/test_remote_mcp_routing.py`, `pyproject.toml`, `CHANGELOG.md`, `README.md`, `docs/README.md`, `docs/mcp_setup.md`, `docs/AGENTS.template.md`, `docs/cookbook.md`, `docs/orchestration.md`, `docs/apatch-studio.md`, `docs/security-one-pager.md`, `docs/mcp_performance.md`, `docs/RFP-008-spec-executor.md`, `docs/RFP-009-spec-run.md`, `docs/agent-onboarding.md`, `docs/RFP-007-executable-specifications.md`, `docs/for-leaders.md`, `docs/RFP-019-mcp-scale-lifecycle.md`

Commit and optionally push only the exact current file hashes from explicitly
named governed sessions when each session has a later signed attestation.
Fail closed on drift, pre-staged files, unsafe paths, missing proof, or mixed
scope; preserve unrelated dirty files. Remote orchestration routes the operation
without opening a new mutation session.

(verify: python3 -m pytest tests/test_commit_attested.py tests/test_cli_commit_attested.py tests/test_remote_worker_protocol.py tests/test_remote_mcp_routing.py tests/test_mcp.py -q)
## Non-goals

- Guessing the authorizing requirement.
- Treating a bare SPEC artifact or editable status as completion.
- Inferring implementation ownership from prose or filenames.
