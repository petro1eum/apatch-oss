# RFP-045 -- Close the Frozen SDD Verifier Loop

> **Status:** Owner-approved implementation contract
> **Owner:** APatch Core
> **Date:** 2026-09-01
> **Depends on:** RFP-044 / SPEC-SDD-INTEGRITY-1
> **Baseline:** APatch 0.8.40 at `c7bdbe571188773f84aab5812b03a682e99a2683`

## Problem

APatch already freezes SDD verification contracts, binds task envelopes to governed
sessions, rejects false-green result shapes and embeds accepted evidence in
attestation. The normal agent workflow still has a closure gap: it can run a generic
verify command, but no fixed-purpose Core operation loads and executes the frozen
judge, performs reversible falsification and records the result itself. Consequently
an implementation actor cannot complete a strict SDD attestation without an
out-of-band/manual result-recording path.

This RFP closes that gap. It does not create another test runner or evidence store.
It makes the frozen contract executable through the existing runtime.

## Decision

Add one fixed-purpose verifier operation owned by Core. It receives only workspace
and exact governed-session capability. It never accepts a command, result, counts,
perspectives, asset hashes or verifier role from the implementation caller.

The operation loads the exact contract and task envelope bound to the active session,
selects only obligations named by `envelope.checks`, verifies command and judge-asset
hashes, executes argv without a shell, performs the frozen reversible falsification
for material obligations, restores bytes and executable mode, and atomically records
the accepted or rejected verifier/falsification evidence on that same session.

`execute_next(finalize=true)` uses this operation automatically for SDD-bound
sessions before attestation. Legacy sessions continue to use the existing generic
verify path.

## Security and integrity

- Judge asset mappings are workspace-relative `{path, sha256}` records and must
  exactly account for `asset_hashes`.
- `command_hash` is the canonical hash of the exact argv list.
- Commands run with `shell=False`; the caller cannot inject an alternate command.
- Material gates name an exact workspace-relative falsification target. APatch takes
  a path lease, snapshots bytes and mode, applies a built-in mutant, requires red,
  restores the target, reruns the judge and requires green.
- Source or judge drift before execution fails closed. Any source mutation caused by
  a judge command is detected and reported; it cannot be described as non-mutating.
- Persisted evidence contains ids, hashes, counts, outcomes and bounded output hashes,
  not raw source, command output, prompts, secrets or absolute paths.
- The fixed verifier role is internal. MCP exposes no caller-selectable role or
  caller-supplied result payload.

## Compatibility

Existing RFP-044 v1 documents remain readable. The fixed verifier requires the
additional executable fields only when that operation is invoked. Workspaces and
sessions without an SDD binding retain the existing verify behavior. OSS and Pro use
the same correctness floor.

## Acceptance

| Id | Requirement | Level |
|---|---|---|
| SDD-CLOSE-1 | The bound session retains the exact frozen contract required to execute its named checks, without exposing it in public evidence | MUST |
| SDD-CLOSE-2 | The fixed verifier accepts no caller command/result/role and resolves one exact active governed session capability | MUST |
| SDD-CLOSE-3 | Every selected obligation has an exact canonical argv hash and complete workspace-relative judge-asset mapping; drift blocks before command execution | MUST |
| SDD-CLOSE-4 | Frozen commands execute as argv without a shell and produce bounded content-safe hashes/counts rather than raw output | MUST |
| SDD-CLOSE-5 | Every material obligation observes red under its frozen target perturbation, restores exact bytes and mode, and restores green | MUST |
| SDD-CLOSE-6 | Verification plus falsification are recorded atomically on the exact session and rejected evidence cannot satisfy attestation | MUST |
| SDD-CLOSE-7 | `execute_next(finalize=true)` automatically uses the fixed verifier for SDD sessions and the existing verifier for legacy sessions | MUST |
| SDD-CLOSE-8 | Full MCP exposes one fixed-purpose `apatch_sdd_verify` tool with exact session capability only; no generic verifier authority is exposed | MUST |
| SDD-CLOSE-9 | Focused adversarial, integration and compatibility tests are frozen and green before release | MUST |

## Non-goals

No business acceptance, accepted time, payroll, cloud source upload, arbitrary command
relay, independent test authoring or retroactive proof fabrication is introduced.
