# SPEC-REMOTE-OPACITY-1 — The agent never learns where the server is

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-REMOTE-OPACITY-1`
> **Anchors:** [RFP-030](../RFP-030-opaque-ssh-broker.md)

## 0. Motivation

Remote work is already brokered: an agent names an opaque alias and apatch resolves the
host, root, jump host and credentials from local policy. A live probe confirms the
property holds today — neither host nor path reaches the agent, in the response, the
timeline, or inside a nested remote result.

Nothing froze it. RFP-030 states fourteen criteria and not one was traceable to a
requirement, so redaction could be deleted tomorrow and no requirement would go red.
Eight workspaces already run through this broker; the security property was resting on
tests that answered to no contract.

This specification binds existing behaviour to the ledger. It adds no capability.

## R0 RFP traceability gate

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| A30-A | R1 | covered |
| A30-B | R1 | covered |
| A30-C | R2 | covered |
| A30-D | R3 | covered |
| A30-E | R4 | covered |
| A30-F | R5 | covered |
| A30-G | R6 | covered |
| A30-H | R7 | covered |
| A30-I | R8 | covered |
| A30-J | R9 | covered |
| A30-K | R10 | covered |
| A30-L | R11 | covered |
| A30-M | R12 | covered |
| A30-N | R13 | covered |

(verify: python3 -c "from pathlib import Path; t=Path('docs/specs/SPEC-REMOTE-OPACITY-1.md').read_text(); ids=['A30-'+c for c in 'ABCDEFGHIJKLMN']; assert all(f'| {x} | R' in t for x in ids)")

## R1 An alias resolves to a full transport the agent never sees

Policy at `.apatch/remote.json` or `APATCH_REMOTE_POLICY` maps an opaque alias to host,
remote root, interpreter, ssh argv, timeout and display label. Resolution returns the
whole transport internally while the agent-facing view carries only alias, display,
workspace id and a redaction flag.

(verify: python3 -m pytest -q tests/test_remote_target.py::test_remote_policy_resolves_alias_without_leaking_host_path)

## R2 A broken or unknown alias fails closed

An unknown alias raises `REMOTE_ALIAS_NOT_FOUND` and malformed policy raises
`REMOTE_POLICY_INVALID`. Neither degrades to an unbrokered path.

(verify: python3 -m pytest -q tests/test_remote_target.py::test_remote_policy_rejects_unknown_alias tests/test_remote_opacity.py::test_malformed_policy_fails_closed)

## R3 Host and root allowlists decide before any transport exists

Policy allowlists are enforced during resolution, so a target outside them never reaches
transport construction.

(verify: python3 -m pytest -q tests/test_remote_target.py::test_remote_policy_enforces_host_and_root_allowlists)

## R4 Alias runs return no topology

Responses and timelines from an alias run carry no host, remote root, direct URI or
transport detail, including under the fake transport used for audit.

(verify: python3 -m pytest -q tests/test_remote_mcp_routing.py::test_remote_task_run_with_alias_redacts_timeline tests/test_remote_mcp_routing.py::test_mcp_remote_task_run_uses_alias_policy_and_redacts)

## R5 A leak inside a nested result or error is rewritten

A remote step that quotes its own host or root — in a result payload or in a failure
message — has those strings replaced before the response returns to the agent.

(verify: python3 -m pytest -q tests/test_remote_opacity.py::test_a_host_leaked_inside_a_nested_failure_is_rewritten)

## R6 An explicit target stays readable

`ssh://host/path` and `host:/path` remain visible for debugging and are not silently
redacted; only aliases are opaque.

(verify: python3 -m pytest -q tests/test_remote_opacity.py::test_an_explicit_target_stays_readable_for_debugging)

## R7 Policy can deny an internal operation

An operation outside the alias allowlist stops with `REMOTE_OPERATION_DENIED` before the
denied step runs.

(verify: python3 -m pytest -q tests/test_remote_mcp_routing.py::test_remote_task_run_policy_denies_operation)

## R8 A locked alias keeps its own transport

For an alias that does not allow overrides, policy interpreter and ssh argv replace any
caller-supplied value and the effective timeout is the smaller of policy and caller. A
caller may shorten a timeout; it can never widen one or inject an ssh argument.

(verify: python3 -m pytest -q tests/test_remote_opacity.py::test_a_locked_alias_keeps_its_own_transport)

## R9 One approval boundary

`apatch_remote_task_run` stays one user-facing call: alias resolution, policy
enforcement, redaction, transport selection and timeline production happen inside apatch.

(verify: python3 -m pytest -q tests/test_remote_mcp_routing.py::test_r7_remote_task_run_single_approval_boundary)

## R10 The gates need no SSH, network or secret

Every remote gate runs against a fake or monkeypatched transport, so CI proves the
property without a real connection.

(verify: python3 -m pytest -q tests/test_remote_opacity.py::test_remote_gates_never_open_a_real_connection)

## R11 The broker model is written down

Operator documentation explains that local MCP is the only visible tool boundary and SSH
is an internal capability.

(verify: python3 -m pytest -q tests/test_remote_opacity.py::test_the_broker_model_is_written_down)

## R12 A policy can be created and checked without hand-written JSON

`apatch remote init` builds an alias policy from minimal inputs and `apatch remote
validate` confirms it resolves.

(verify: python3 -m pytest -q tests/test_remote_onboarding.py::test_remote_init_cli_writes_policy_and_validate)

## R13 Source handoff reveals no topology

When a remote machine cannot fetch a repository because credentials are local, the
handoff plan and its CLI output disclose no host, remote path or credential location.

(verify: python3 -m pytest -q tests/test_remote_onboarding.py::test_source_handoff_plan_redacts_remote_and_local_paths tests/test_remote_onboarding.py::test_remote_handoff_cli_plans_redacted_handoff)

## Non-goals

- New remote capability: this specification freezes what already ships.
- Hiding topology from the operator, whose policy defines it.
