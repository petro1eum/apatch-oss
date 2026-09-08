# RFP-044 -- Adopt the Frozen APatch Studio SDD Integrity Contract in Core

> **Status:** Owner-approved Core adoption profile; verification contract must freeze before source mutation
> **Owner:** APatch Core
> **Date:** 2026-09-01
> **Source authority:** APatch Studio RFP-012 at commit `cb18e4a13e29ea4cad49ac67fa5e1101c35712a3`
> **Source SHA-256:** `cdfad4871f81abb6c26d762ab23cf4350aa07eed7d3d69f7a0a230538913ea1d`
> **Studio test-contract commit:** `12c1f5d937f8cfc8d26a81212b16fa578154b7f1`
> **Implemented baseline:** APatch 0.8.38 source line at `35ba9dd5da84b01853945780540ff5cd1a778798`

## Decision

APatch already provides RFP/SPEC traceability, governed sessions, transactional
mutation and rollback, sandbox and path leases, plan adherence, verification,
probe falsify/regress/ratify, attestation, continuous conformance, remote
execution and a signed-event timesheet. Those mechanisms remain canonical.

This RFP adopts only the Core-owned deltas of frozen Studio RFP-012. It does not
create another runtime, document store, ledger, verifier, session model or
timesheet. Studio remains a consumer and human-facing command surface.

## Integrity model

The existing invariant is extended, not replaced:

```text
Intent -> frozen contract revision -> exact task envelope
       -> governed session -> admitted effects -> verification capability
       -> falsification/regression/ratification -> attestation | rollback
```

A source mutation capability is not valid until a freeze record pins the brief,
RFP, SPEC, plan, baseline, verification obligations, test and fixture bytes,
schemas and command hashes. A contract amendment creates new hashes and
invalidates affected capabilities; it never edits historical proof.

The envelope is an allowlist for effects mediated by APatch. It covers paths,
symbols, APatch tools, commands, network destinations, remote targets, service
actions and budgets. It does not claim to control a separate unrestricted shell
or network channel. Sealed-runner containment is explicit; ordinary local use is
reported as mediated-only containment.

## Roles

- `authority` freezes or amends a contract.
- `implementation` may use only the issued task envelope.
- `verifier` is non-mutating and evaluates the frozen judge.
- `reviewer` may issue a technical decision but cannot rewrite evidence.

A solo owner may hold authority and reviewer roles at different times. The
implementation capability still cannot approve its own amendment or proof.

## Verification quality

Behavioral obligations require positive, negative, boundary and regression
perspectives. Risk claims add their named perspectives. A material gate must
observe a relevant red state or a reversible targeted falsification, restore
green and ratify. Zero-collected, all-skipped, drifted, tautological or
perspective-incomplete gates are rejected.

The verifier records collected, executed, passed, failed and skipped counts,
perspectives, judge-asset hashes, falsification and environment. A shell exit
code alone is not sufficient proof.

## Effect admission

Every APatch-mediated effect uses one decision vocabulary:

- `allowed`: exact contract and envelope authorize the effect;
- `denied`: fail closed before the first external effect;
- `amendment_required`: the request may be proposed to authority;
- `unsupported_channel`: APatch cannot claim containment for this channel.

Path admission composes with the existing sandbox and path lease. Remote and
service admission composes with existing alias policy. Plan mismatch is blocking
in strict mode and cannot be converted into a warning.

## Evidence and compatibility

Attestation binds contract and envelope hashes, actor, mutation set, plan
adherence, verification summary, falsification, checkpoints and ledger refs.
Timesheet derivation remains the existing signed-event projection and remains
distinct from accepted time or payroll.

Existing sessions and workspaces without an SDD freeze continue under their
current policy. A project opts into the new strict profile explicitly. OSS and
Pro use the same integrity floor; Pro may add authority and policy but cannot
weaken or monopolize correctness.

## Delivery order

1. Freeze RFP-044, SPEC-SDD-INTEGRITY-1, exact tests and observed red baseline.
2. Implement canonical models and validation without wiring effect surfaces.
3. Bind the freeze and task envelope to governed-session capabilities.
4. Enforce path, tool, remote and service admission at existing central points.
5. Add non-mutating verifier quality and exact attestation evidence.
6. Add amendment invalidation and compatibility/readback.
7. Run adversarial parity, rollback and falsification proof.

## Acceptance

| Id | Requirement | Level |
|---|---|---|
| CORE-SDD-1 | Existing APatch 0.8.38 session, mutation, rollback, sandbox, lease, probe, attestation, conformance, remote and timesheet mechanisms remain canonical and compatible | MUST |
| CORE-SDD-2 | Versioned brief, verification obligation, contract freeze, task envelope and amendment documents have canonical hashes, strict validation and no source-content requirement | MUST |
| CORE-SDD-3 | Freeze is refused unless RFP-to-SPEC coverage is complete and every behavioral obligation has stable tests, independent oracle, required perspectives, exact asset and command hashes, baseline, falsification plan and named authority before mutation | MUST |
| CORE-SDD-4 | Implementation and verifier capabilities are role-separated; implementation cannot mutate frozen judge assets, and verifier cannot mutate source or contract | MUST |
| CORE-SDD-5 | The task envelope expresses exact requirement and contract hashes, reads, writes, symbols, tools, commands, network, remote/service effects, budgets, checks, rollback ownership and honest containment level | MUST |
| CORE-SDD-6 | Existing apply, sandbox/lease, MCP/local runner, network, remote and service admission points return the same fail-closed decision before their first supported external effect | MUST |
| CORE-SDD-7 | Strict plan mismatch and out-of-envelope attempts block attestation, are attributed to the exact session/actor and do not change unrelated paths | MUST |
| CORE-SDD-8 | Verifier quality rejects zero-collected, all-skipped, tautological, drifted and perspective-incomplete gates and requires observed red falsification for material obligations | MUST |
| CORE-SDD-9 | Attestation binds exact contract/envelope hashes, actor, mutations, adherence, test counts/results, falsification, environment, checkpoints and ledger refs | MUST |
| CORE-SDD-10 | Signed amendment preserves the superseded contract and reason and invalidates exactly affected capabilities, requirements and attestations through existing stale/conformance mechanisms | MUST |
| CORE-SDD-11 | OSS and Pro share the same integrity floor, existing non-opted-in workspaces remain compatible, and direct channels outside a sealed APatch runner are never represented as contained | MUST |

## Non-goals

No generic task runner, document database, payroll, business acceptance,
professional-status inference, cloud source copy, arbitrary remote shell or
second evidence system is introduced.

This RFP does not require a particular UI. It defines Core facts and commands
that APatch Studio projects in human language.
