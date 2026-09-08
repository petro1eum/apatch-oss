# SPEC-CONCEPT-STATUS-1 — Live verify status on the graph

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-STATUS-1`
> **Anchors:** [RFP-034 §B.7 / §3.6](../RFP-034-concept-graph.md) · [SPEC-CONCEPT-VERIFY-1](./SPEC-CONCEPT-VERIFY-1.md) · [SPEC-CONCEPT-RENDER-1](./SPEC-CONCEPT-RENDER-1.md)

## 0. Motivation

Coverage colours the graph by "is there a checkable invariant" (guarded/grey). This adds
the **live** view: colour each node by the result of actually running its invariants
(green / red / broken / grey) — `red` and `broken` are visible, not silent (§3.6).

`render_concept_graph_md(graph, statuses=...)` gains an optional `statuses` map; the CLI
`apatch concept graph --verify` runs the verifier and feeds it in. Without statuses the
render is unchanged (coverage colouring) — back-compatible.

## R1 Status colouring

Given a `statuses` map, the render emits `classDef green/red/broken` and colours each node
accordingly, plus a Verify summary line.

(verify: python3 -m pytest tests/test_concept_status.py::test_r1_status_colours -q)

## R2 Coverage render unchanged without statuses

Called without `statuses`, the render keeps the coverage line and guarded/grey colouring —
no regression for the static graph page.

(verify: python3 -m pytest tests/test_concept_status.py::test_r2_default_coverage_unchanged -q)

## R3 `concept graph --verify`

`apatch concept graph --verify` runs the verifier and colours the live graph; the
ContributionEvent node is green because its invariants pass.

(verify: python3 -m pytest tests/test_concept_status.py::test_r3_cli_graph_verify -q)

## Non-goals

- Not regenerating the committed `docs/concepts/index.md` with live status — that page stays the static coverage view; live status is the `--verify` render / a future gen-files hook.
- Not gating CI on red — reporting/colouring first.