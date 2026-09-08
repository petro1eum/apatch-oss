# SPEC-CONCEPT-CLI-1 — `apatch concept` CLI surface

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-CONCEPT-CLI-1`
> **Anchors:** [RFP-034 §6](../RFP-034-concept-graph.md) · SPEC-CONCEPT-COMPILE-1 / -COVERAGE-1 / -VERIFY-1 / -DEDUP-1 / -RENDER-1

## 0. Motivation

The Level 2 concept-graph library becomes a usable tool: `apatch concept` groups the
five operations as terminal commands (RFP-034 §6), each with `--json` for agents/CI.
No new behaviour — a command surface over the attested concept-graph specs.

`apatch/cli_concept.py` defines the group; `apatch/cli.py` registers it. Commands default
to compiling all `docs/concepts/*.md` (or a single doc via `--concepts`/argument).

## R1 `concept compile`

`apatch concept compile <doc> --json` compiles a doc's concept/claim fences and emits the
graph; a clean doc has no integrity errors.

(verify: python3 -m pytest tests/test_concept_cli.py::test_r1_compile -q)

## R2 `concept coverage`

`apatch concept coverage --json` reports guarded vs grey over `docs/concepts/`.

(verify: python3 -m pytest tests/test_concept_cli.py::test_r2_coverage -q)

## R3 `concept verify`

`apatch concept verify --json` runs the invariants and reports per-concept status;
exit code is non-zero on any `red`.

(verify: python3 -m pytest tests/test_concept_cli.py::test_r3_verify -q)

## R4 `concept graph` and `concept dedup`

`apatch concept graph` renders the Mermaid page to stdout; `apatch concept dedup --json`
returns the merge candidates.

(verify: python3 -m pytest tests/test_concept_cli.py::test_r4_graph_and_dedup -q)

## Non-goals

- Not new behaviour — a thin CLI over the existing library (RFP-034 §6 surface).
- Not the merge/clarify mutating commands (`concept merge`, `doc clarify`) — those write `same_as`/status and are human-confirmed follow-ups.
- Not MCP tools for these — CLI first; MCP wrappers later if needed.