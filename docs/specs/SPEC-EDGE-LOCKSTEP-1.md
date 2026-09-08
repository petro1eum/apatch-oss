# SPEC-EDGE-LOCKSTEP-1 — Edge invariant: ContributionEvent schema lockstep

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-EDGE-LOCKSTEP-1`
> **Anchors:** [RFP-034 §3.6/§3.9](../RFP-034-concept-graph.md) · [Avatar Architecture Canon §7, Rule 1](../AVATAR-ARCHITECTURE-CANON.md)

## 0. Motivation

First "full circle" of RFP-034 on a real edge. The canon Rule 1 demands ONE
`ContributionEvent` contract — "not two schemas that match by convention" — but the
contract lives in code three times: apatch's emitter (`apatch/contribution.py`), the
shared `avatar-contract` package, and HC_Tracker's SQLAlchemy mirror. Nothing guards
that these copies move together. This spec introduces the **edge invariant**
`SCHEMA-LOCKSTEP` (RFP-034's "new muscle": a check over a *relationship* between two
code anchors, not over one) for the apatch <-> avatar-contract pair; the Tracker copy
is a later layer.

The sentinel reuses the contract's own published vocabulary (`SCHEMA_VERSION` /
`KINDS` / `TRUST_LEVELS`) as the oracle — no new predicate language (RFP-034 non-goal).

> It fired on real drift on day one: the emitter was at `schema_version=2` but still
> emitted the v1 `kind="contribution"`. It fired again when the shared contract moved
> to v3 and required signed `created_at`. The emitter now derives the current contract
> version and R3 asserts the resulting lockstep. The sentinel surfaces drift (red); it
> never hides it.

## R1 Sentinel names the edge and both anchors

`apatch/edge_lockstep.py` exposes `contribution_lockstep(target_dir)` returning a dict
with `edge="SCHEMA-LOCKSTEP"`, `concept="cpt_contribution_event"`, both `realized_by`
anchors, and the `schema_version` of each side.

(verify: python3 -m pytest tests/test_edge_lockstep.py::test_r1_anchors_and_version -q)

## R2 Version dimension is in lockstep

The emitted event and shared contract agree on the current `schema_version`; the
version dimension reports no drift without duplicating the contract constant.

(verify: python3 -m pytest tests/test_edge_lockstep.py::test_r2_version_lockstep -q)

## R3 Live edge is in lockstep (green on the current shared schema)

The live edge is in lockstep: the sentinel is green with no violations, the emitter
uses the shared contract's current schema version, and it speaks the canonical kind.
The loop remains closed as the contract evolves. Detection itself — red on a drifted
pair — is covered by R4.

(verify: python3 -m pytest tests/test_edge_lockstep.py::test_r3_live_edge_in_lockstep -q)

## R4 Verdict discriminates (aligned pair is green)

`lockstep_verdict(...)` returns green for an aligned pair and red for any drifted
dimension — the sentinel is not always-red; it distinguishes lockstep from drift.

(verify: python3 -m pytest tests/test_edge_lockstep.py::test_r4_green_when_aligned -q)

## R5 Third anchor: HC_Tracker mirror checked statically

`tracker_lockstep(models_text)` checks the third `realized_by` copy — HC_Tracker's
SQLAlchemy persistence mirror — by **statically reading its column names** (no import of
the HC app, cross-repo). The mirror must carry the contract's core fields under the
contract's own names; a rename (`kind` -> `shape`) or a dropped field (`schema_version`)
is red with a named violation, an aligned mirror is green, a missing model is broken.

> Run against the live HC_Tracker mirror today, it is red: it persists `kind` as `shape`
> and has no `schema_version` column. Surfaced, not hidden.

(verify: python3 -m pytest tests/test_edge_lockstep.py::test_r5_tracker_third_anchor -q)

## Non-goals

- Not the generic edge-invariant engine (`apatch edge verify`) — one real edge, dogfooded end to end.
- Not the LIVE cross-repo wiring (pointing CI at a real HC checkout): the third-anchor *check* (R5) is in; running it against a live HC mirror in the pipeline is a later layer.
- Not fixing the drift — surfacing it is this spec; the fix is a separate governed change.