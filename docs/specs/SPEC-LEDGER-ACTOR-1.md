# SPEC-LEDGER-ACTOR-1 — Named ledger actor (`signed_by`)

> **Status:** Attested v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-LEDGER-ACTOR-1`
> **Anchors:** RFP-005 (enrolled identity), RFP-006 (artifact traceability), RFP-007 (executable specs)
> **Dependency:** [SPEC-COVERAGE-1](./SPEC-COVERAGE-1.md) (traceability rows consumed by coverage)
> **Consumer alignment:** Human_Capital SPEC-TRUSTCHAIN-AUDIT-1 (external reference; not included in this OSS snapshot) (`trustchain_audit` v2)

## 0. Motivation

Engineering Truth and Human Capital attribution both answer **who signed**, not only **what
changed**. TrustChain stores the enrolled agent CN on the signed envelope as `key_id`
(RFP-005). Today agents and compliance tools see `op_id`, `intent`, and `artifacts`, but
often not the **named actor** on every chain step:

```text
Intent   → signed_by: apatch-edcher-prod
Plan     → signed_by: apatch-edcher-prod
Mutation → signed_by: apatch-edcher-prod
Attest   → signed_by: apatch-edcher-prod
```

**Principle:** `signed_by` is derived deterministically from the ledger (envelope +
payload fallbacks). It is never agent-writable. Display name / FIO mapping lives in
Platform AD — the ledger carries the enrolled **CN** (`key_id`).

Success criterion:

```text
apatch_trustchain_coverage(artifact='spec:SPEC-LEDGER-ACTOR-1')
# -> attestations[].signed_by == 'apatch-edcher-prod'

apatch_trustchain_history(artifact='spec:SPEC-LEDGER-ACTOR-1')
# -> entries[].signed_by present on every hit
```

Non-goals (this spec): multi-signer co-attestation inside one session; Platform UI for
FIO; ADR-002 contribution_vector math (HC economy layer — separate spec).

## R1 `ledger_signed_by` resolution order

Module `apatch.ledger_actor.ledger_signed_by(row)` resolves identity in order:
(1) normalized row `key_id` from the TrustChain envelope; (2) payload fields
`signed_by`, `agent_id`, `key_id`. Returns `None` when no enrolled/ephemeral id is
present (caller may omit the field).

(verify: python3 -m pytest tests/test_ledger_actor.py::test_ledger_signed_by_from_envelope_key_id tests/test_ledger_actor.py::test_ledger_signed_by_from_payload_fallback -q)

## R2 Payload stamp on governed commit

When `load_local_identity(workspace)` returns an enrolled agent, `commit_action` enriches
the payload via `enrich_payload_with_actor`: sets `signed_by`, `agent_id`, and `key_id`
(with `setdefault` — never overwrites explicit values). Ephemeral dev keys skip stamping.

(verify: python3 -m pytest tests/test_ledger_actor.py::test_enrich_payload_with_actor tests/test_ledger_actor.py::test_commit_enriches_actor_when_enrolled -q)

## R3 Ledger parse extracts envelope `key_id`

`TrustChainHelper._parse_ledger_object` copies `key_id` and `algorithm` from the object
JSON (top-level or inner `value`) into normalized ledger rows consumed by
`iter_ledger_entries`.

(verify: python3 -m pytest tests/test_ledger_actor.py::test_parse_ledger_object_key_id -q)

## R4 Traceability op summaries expose `signed_by`

`traceability._op_summary` includes `signed_by` and `key_id` on every op in
`artifact_coverage_report` / `resolve_op_id_artifacts` (`apatch_trustchain_coverage`).

(verify: python3 -m pytest tests/test_ledger_actor.py::test_traceability_op_summary_includes_signed_by -q)

## R5 Intent history exposes `signed_by`

`TrustChainHelper.list_intent_history` adds `signed_by` to each entry returned by
`apatch_trustchain_history`.

(verify: python3 -m pytest tests/test_ledger_actor.py::test_intent_history_signed_by -q)

## R6 Reverse lookup (`op_id`) carries actor

`resolve_op_id` / MCP `apatch_trustchain_coverage(op_id=…)` returns `signed_by` on the
matched op summary.

(verify: python3 -m pytest tests/test_ledger_actor.py::test_resolve_op_id_signed_by -q)

## R7 Human overview documents named identity

`docs/engineering-truth-overview.md` section «Кто подписал» states that every lifecycle
step carries enrolled CN; links to this spec. `apatch_doctor` → `trust_anchor.agent_id`
when enrolled.

(verify: python3 -m pytest tests/test_ledger_actor.py::test_doctor_surfaces_enrolled_agent_id -q)

## Non-goals

- Mapping `key_id` → human FIO in apatch (Platform / AD responsibility).
- Requiring `signed_by` on ephemeral dev ledgers (optional field).
- HC transfer settlement wiring (see HC `TRUSTCHAIN_ATTRIBUTION_ALIGNMENT.md`).

## Operational note (spec_run / file-drift)

R2–R7 share `tests/test_ledger_actor.py`. After a batch `apatch_spec_run`, R2–R6 may
show **`stale`** until rebind: attested **last** the Rk with the final append on that
file (R7), then re-attest R2–R6 via **noop** (marker fixtures only — do not mutate
`test_ledger_actor.py` again). See [RFP-009 §9.1](../RFP-009-spec-run.md).
