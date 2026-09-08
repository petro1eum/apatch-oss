# Authoring an Executable Spec (RFP-007)

> **Канонический RFP:** [RFP-007-executable-specifications.md](./RFP-007-executable-specifications.md)  
> **Архитектура / MCP-цикл:** [executable-specs.md](./executable-specs.md)

A spec is "executable" when apatch can parse it, derive per-requirement status from the
ledger, and run each requirement's acceptance check. This standard is enforced by
`apatch spec lint` / `apatch_spec_lint` — run it before you start tracking a spec.

## The rules (what the linter checks)

1. **One H1, and it owns the id.** The first whitespace-delimited token of the H1 *is* the
   spec id. `# SPEC-HERMES-1 — Hermes` → id `SPEC-HERMES-1`.
2. **Declared artifact must match the H1 id.** If the body says `spec:HERMES-1` but the H1
   is `SPEC-HERMES-1`, every `spec:HERMES-1#R1` anchor your sessions emit will miss
   coverage. Use the *same* token in both places (`error: id_mismatch`).
3. **Each requirement is `## R<n> <title>`.** Use `## R1`, `## R2`, … (a trailing dot,
   `## R1.`, is fine). The id must end in a digit. The rest of the line is the title.
4. **Every requirement carries an acceptance check.** Add `(verify: <cmd>)` — on the heading
   or on its own line in the body. Without it, `attested` only means "code was touched here",
   not "this requirement is satisfied" (`warn: missing_verify`).
5. **`verify` is requirement-specific.** Don't reuse one global `npm run build` /
   `pytest` across every requirement — then any one passing "proves" all of them
   (`warn: generic_verify`). Point each requirement at the test that exercises *it*.
6. **`verify` proves the requirement, not the toolchain.** `npm run build` / `tsc` / `cmake`
   passing means it compiles, not that R5's dedup logic works (`info: weak_verify`). Prefer
   `pytest tests/test_hermes_dedup.py::test_monotonic_growth`.

## `verify` formats the parser accepts

All of these resolve to the command after `verify:`:

```
## R1 Title (verify: pytest tests/test_x.py)   # inline on the heading
(verify: pytest tests/test_x.py)               # own line, parenthesized
verify: pytest tests/test_x.py                 # bare
- verify: pytest tests/test_x.py               # bullet
**verify:** pytest tests/test_x.py             # bold
> verify: pytest tests/test_x.py               # blockquote
```

First match in the requirement body wins; the heading form takes precedence over the body.

## Template

```markdown
# SPEC-<ID> — <Human title>

> **Status:** Draft v1 · **Owner:** <team> · **Anchors:** <links to source/PRD>
> **apatch artifact:** `spec:SPEC-<ID>`   <!-- MUST equal the H1 token above -->

## 0. Motivation
<why this exists, in 1–2 paragraphs>

## R1 <short requirement title>
<one paragraph: what must be true when this is done>

(verify: <command that fails until R1 is satisfied, e.g. pytest tests/test_r1.py>)

## R2 <short requirement title>
<...>

(verify: <requirement-specific command>)

## Non-goals
- <explicitly out of scope>
```

## What lint does *not* check (RFP fidelity)

`apatch_spec_lint` validates **executable shape**, not **requirements completeness** against an RFP.

| Checked | Not checked |
|---------|-------------|
| H1 id, `spec:` artifact match | Every RFP acceptance row maps to a SPEC Rk |
| `(verify:)` per Rk | RFP deliverable moved to `## Non-goals` without explicit waiver |
| generic / weak verify warnings | RFP status vs attested SPEC status in doc indexes |

**Authoring rule (human + agent):** before `apatch_spec_run`, add `## RFP traceability` mapping each RFP Acceptance `Id` → `covered` Rk or explicit `waiver:`. Run coverage lint before spec run — **attested SPEC ≠ RFP 100% done** without it.

**Tools (RFP-023):** One gate — `apatch_spec_lint(spec='SPEC-<ID>')` embeds `rfp_lint` + `rfp_coverage` when SPEC has `## RFP traceability`. Standalone `apatch_rfp_lint` / `apatch_rfp_spec_coverage` remain for CLI and multi-spec aggregate (`specs='A,B,C'`). `apatch_spec_run` blocks when traceability exists and coverage fails.

See [agent-onboarding.md](./agent-onboarding.md) · [rfp-authoring.md](./rfp-authoring.md) · [apatch-transformation-matrix.md § Documentation product](./apatch-transformation-matrix.md).

## Workflow once the spec passes lint

**Fresh agent:** [agent-onboarding.md](./agent-onboarding.md) — read `apatch_doctor` once, then one `apatch_spec_lint` call returns format lint + RFP gates + `plan_scaffold`.

```text
apatch_doctor(target_dir=".")
apatch_spec_lint(spec="SPEC-<ID>")   # passed → plan_scaffold, needles_scaffold, agent_next
# Fill plan_scaffold.execution_plan.{Rk}.needles (agent cognition — not auto-generated)

# Whole spec (§3K — preferred for ≥2 Rk):
apatch_spec_run(spec="SPEC-<ID>", requirements={R1: {needles: [...]}, …})
# OR per-Rk inspection (§3I):
apatch_spec_next(spec="SPEC-<ID>") → apatch_session_start(requirement="SPEC-<ID>#R1")
# … mutate via apply_session → verify from (verify:) → apatch_attest()
apatch_spec_status(spec="SPEC-<ID>")
```

`attested` flips back to `stale` automatically if a requirement's `content_hash` changes
(you edited the requirement after attesting), so the spec stays honest over time.

## Shared file across requirements (file-drift rebind)

When several `Rk` append to the **same** file in one `apatch_spec_run` (common for
test files: R2 adds `test_foo`, R3 adds `test_bar`, …), earlier attestations often
become **`stale`** (`file_drift`) after later Rk mutate that file. That is expected.

**Rebind order (do not ping-pong):**

1. Let the batch run finish so the shared file reaches its **final** content.
2. Re-attest **last** the requirement that performed the **last mutation** on that
   file (highest R number with replace/append needles targeting it).
3. Re-attest remaining stale Rk with a **noop session**: marker fixture only
   (`tests/fixtures/<spec>_rN.txt`), **without** mutating the shared file again.

Dogfood reference: apatch `SPEC-LEDGER-ACTOR-1` (R2–R7 → `tests/test_ledger_actor.py`).
See [RFP-009 §9.1](./RFP-009-spec-run.md) and consumer [AGENTS.template.md §3K](./AGENTS.template.md).
