# Continuous Conformance — the standing contract-gate

> RFP-035. apatch gives *governance* (every change is attested). Conformance adds
> *enforcement of state*: a standing gate that proves the **whole contract still
> holds** after every change, so specs can't be attested once and silently drift.

## The problem it solves

A spec is attested **once**. A later, legitimate change can quietly break it — and
its `verify` never runs again, so nothing catches it. Individual edits collectively
erode the contract. "Every session starts from scratch" is the symptom: the system
doesn't continuously prove alignment, so a forgetful session drifts.

Conformance turns *"we have specs"* into *"the contract holds, continuously"* —
independent of anyone's memory.

## The model — four buckets

The gate classifies **every gated spec** over the current tree:

| Bucket | Meaning | Blocks? |
|--------|---------|---------|
| `conformant` | attested **and** verify green now | no |
| `drifted` | verify **exit 1** — a test actually **failed** → a real regression | **yes** (default) |
| `broken` | verify **exit ≥2** — the verify can't run (renamed/missing test, collection error, env-skip) | no — advisory, fix the verify |
| `stale` | attested, attestation went stale, but verify still **green** | no — advisory, re-attest |
| `unproven` | in the contract but has no real `verify` (a visible hole) | no (configurable) |

Two distinctions, both learned by dogfooding apatch on itself, both about **not crying wolf**:

- **`stale` ≠ `drifted`** — a stale attestation with a green verify is bookkeeping, not a
  regression. Re-attest it.
- **`broken` ≠ `drifted`** — a verify that exits 4 (test renamed away) or 5 (no tests
  collected) means the *verify command* is broken, not that the code regressed. The gate
  flags it as advisory so a rotted verify reference doesn't masquerade as a failure.

Only a **live exit-1 failure** is a real, blocking regression. The gate blocks on
`drifted` only by default.

`attested` (true *then*) and `conformant` (true *now*) are orthogonal — a spec can be
`attested` and `drifted` at the same time.

## Enrollment is not qualification

Keep these layers separate in reports and automation:

1. **Domain declaration** — project metadata such as `used_for_spec` or
   `conformance: true`. apatch does not treat an arbitrary label as proof.
2. **Enrollment** — the exact SPEC id is present in
   `.apatch/conformance.json` (`contract.specs` or the selected glob).
   `--no-live` can confirm this bounded registration, but not current runtime
   correctness.
3. **Live conformance** — the enrolled SPEC's verify commands run against the
   current tree and produce one of the buckets above. Only `conformant` means
   the standing gate is green *now*.
4. **Domain evidence qualification** — stricter project-specific proof such as
   acceptance oracles for every semantic signal. This may be required by the
   consumer, but it is not silently implied by enrollment or by a YAML label.

A project that generates domain contracts should enforce a repository-wide
integrity invariant: every production declaration maps to an existing SPEC,
the intended SPEC set is enrolled without duplicates, and generated declaration
flags are derived from actual enrollment rather than from mere SPEC-file
existence. Report enrollment and evidence qualification as separate numbers.

Category consumers may configure `hooks.operational_status` in
`manifests/slug-intake.json`. With `apatch slug cockpit <slug> --live`, the hook
must emit a direct JSON object or a matching item in `results[]`/`rows[]` with
`work_status`, `runtime_work_complete`, `reopen_reasons`,
`runtime_defect_codes`, `evidence_debt_codes`, and `verification_key`. A valid
exit-1 payload is accepted because domain evidence debt may be red while runtime
work is complete. Only runtime reopen reasons/defects authorize runtime changes;
evidence-only debt remains visible but must not restart category implementation.
When runtime is explicitly complete, unavailable feedback replay or missing triage
keeps cockpit health yellow rather than falsely red. Explicit runtime defects and
high-severity fresh feedback remain red and authorize reopening.

## CLI

```bash
apatch conformance status                 # classify the contract (fast, from attestation state)
apatch conformance status --live          # re-run each spec's verify (truthful)
apatch conformance status --spec SPEC-X --no-live --json
apatch conformance gate                    # the gate (red only on a real drifted)
apatch conformance gate --live --base origin/main   # baseline-aware: re-run only changed specs
apatch conformance gate --live --ci-safe --blocking # CI: block on self-contained regressions
apatch conformance status --jobs 4 --timeout 30 --spec-budget-sec 45
```

- `--live` — run each spec's `verify` now (the truthful signal). Without it, state is
  derived from the attestation ledger (fast, but can't see a fresh regression).
- `--no-live` — force the fast attestation-derived view even when
  `.apatch/conformance.json` has `"live_verify": true`. Use it for static enrollment
  checks and for spec requirements that must prove "this spec is in conformance" without
  recursively invoking their own live `verify`.
- `--spec SPEC-X` — scope a run to one spec. Repeatable. This is the normal agent path
  for slug/category work: run the category's own query-first gates, then check
  `apatch conformance status --spec SPEC-X --no-live --json` for bounded enrollment.
- `--base REF` — **baseline-aware** (A35-H): only re-verify specs whose files changed
  since `REF`. Keeps the gate fast instead of running the whole contract every time.
- `--ci-safe` — block only on **self-contained pure-pytest** verifies; skip
  env-dependent ones (sibling repos, ledger calls, shell chains). Lets a fresh CI
  checkout block on real regressions **without the local ledger** and without false reds.
- `--blocking` — force a non-zero exit on a red contract even in advisory mode.
  Configured `mode: blocking` already exits non-zero without this flag. Text and
  `--json` output use the same exit status; a disabled gate remains a no-op.
- `--jobs N` — run up to `N` specs in parallel during live conformance.
- `--timeout SEC` — per-requirement live verify timeout. Timeout is `broken`, not
  `drifted`.
- `--spec-budget-sec SEC` — wall-clock budget for one spec's live verifies.
- `--requirement-jobs N` — run up to `N` requirement verifies inside one spec in
  parallel. Keep the default (`1`) for live/API-heavy tests unless measured; nested
  parallelism can overload the service and create false `timeout` noise.
- `--exhaustive` — run every requirement even after the first failure/broken verify.
  Default live mode is fail-fast: once a spec is already classified as `drifted` or
  `broken`, apatch stops scheduling more Rk for that spec.

## Configuration — every lever is the project's choice

Opt-in via `.apatch/conformance.json`. **Absent → conformance is inert** (no behavior
change). apatch ships the instrument; the project decides what to enforce.

```jsonc
{
  "enabled": true,
  "mode": "advisory",          // "advisory" (report) | "blocking" (gate merges)
  "live_verify": false,        // re-run verify vs trust attestation
  "jobs": 4,                   // parallel live spec workers
  "requirement_jobs": 1,       // parallel Rk workers inside a spec; measure before raising
  "verify_timeout_sec": 60,    // per-Rk live verify timeout
  "spec_budget_sec": null,     // optional per-spec live verify budget
  "fail_fast": true,           // stop a spec after first drifted/broken live Rk
  "ci_safe": false,            // block only on self-contained pure-pytest verifies
  "block_on": ["drifted"],     // which buckets fail the gate: drifted | broken | stale | unproven | in_progress
  "baseline": null,            // default base ref for baseline-aware live verify
  "contract": {}               // scope: {"specs": [...]} or {"spec_glob": "SPEC-PAY-*"}; default = all
}
```

| Lever | Choice |
|-------|--------|
| `enabled` | on / off |
| `mode` | `advisory` reports · `blocking` gates merges |
| `live_verify` | run verify live · trust the attestation ledger |
| `jobs` | parallelism across specs for live verify |
| `requirement_jobs` | parallelism inside one spec; default `1`, raise only after measuring service capacity |
| `verify_timeout_sec` | timeout for one requirement verify |
| `spec_budget_sec` | optional total live verify budget for one spec |
| `fail_fast` | stop scheduling more Rk once the spec is already `drifted`/`broken` |
| `ci_safe` | block only on self-contained pytest (safe in CI without the ledger) |
| `block_on` | which buckets are blockers (default: `drifted` only) |
| `contract` | which specs are under contract |

## CI

The gate's blocking signal (`drifted` = a live red verify) is **ledger-independent** —
it just runs the verify commands, which works in a fresh CI checkout. Use `--ci-safe`
so env-dependent verifies don't cry wolf:

```yaml
- name: Conformance gate
  run: apatch conformance gate --live --ci-safe --base "origin/${{ github.base_ref }}"
  # add --blocking to gate merges once every gated spec's verify is green & self-contained
```

## Keeping the contract fresh

A `stale` spec just needs re-attestation — its verify still passes. Use the MCP
tool **`apatch_rebind_stale(spec='SPEC-X')`** — it re-verifies and re-attests every
file-drift-stale requirement in one governed pass (skips requirements stale for
other reasons, e.g. the spec text changed).

## Agent playbook — category/slug conformance

For category work, do **not** make the final Rk run the global live conformance command
unbounded. If the project has `"live_verify": true`, this can recurse: the spec's
`verify` calls conformance, conformance runs the same spec's `verify`, and the agent
waits until a timeout.

Use a bounded final gate instead:

```bash
# 1. Run the slug's real query-first acceptance tests.
PYTHONPATH=. python3 -m pytest -q tests/live/test_<slug>_*.py tests/unit/test_<slug>_*.py

# 2. Prove the spec is enrolled in standing conformance without running itself again.
apatch conformance status --target-dir . --spec SPEC-<SLUG>-1 --no-live --json
```

Then no-op-attest the conformance/enrollment Rk only after both commands are green.
This keeps R15-style requirements honest: the original client strings still hit the
right products, and the conformance check is bounded and non-recursive.

## Timeout hygiene

Each live `verify` is run in its own process group. On timeout, apatch terminates the
whole group before classifying the requirement as `broken` (`Rk:timeout`). This matters
for remote/prod agents: a timed-out conformance run must not leave orphan `pytest` or
`apatch conformance status` processes consuming the machine after the MCP/tool call
returns.

For large live-search contracts, prefer a bounded interactive profile, for example:

```jsonc
{
  "live_verify": true,
  "jobs": 4,
  "requirement_jobs": 1,
  "verify_timeout_sec": 30,
  "spec_budget_sec": 45,
  "fail_fast": true
}
```

Do not assume `requirement_jobs > 1` is faster. It is useful for CPU/local pytest
suites, but live API suites may slow down or produce timeout noise under nested
parallelism. Measure the target service first; keep `--exhaustive` for a full
post-mortem run when a compact fail-fast status has already identified the red spec.

## Self-referential capstone — the gate under its own contract

Conformance is itself an **executable spec under the contract it enforces**:
[`SPEC-CONFORMANCE-GATE-1`](./specs/SPEC-CONFORMANCE-GATE-1.md). Its requirements
map to RFP-035 `A35-A..H` plus dogfood hardening lessons, each verified by a
self-contained test in
`tests/test_conformance.py`, registered and Ed25519-attested via `apatch_spec_run`
(current additions cover scoped runs, timeout cleanup, and bounded live execution). Run
through its **own** gate:

```text
$ apatch conformance status --live --ci-safe   # over SPEC-CONFORMANCE-GATE-1
SPEC-CONFORMANCE-GATE-1: conformant
buckets: conformant=1, drifted=0, stale=0, unproven=0   contract_holds: True
```

This is the RFP-035 "first run through its own gate": the feature that proves the
contract holds is itself part of the contract and passes its own check. Break
conformance and its own gate catches it.

## Phasing (RFP-035)

- **Done:** opt-in config, the four-bucket model, all-buckets-at-once reporting,
  blocking/advisory, baseline-aware live verify, `--ci-safe` ledger-free CI blocking,
  scoped `--spec` runs, explicit `--no-live`, process-group timeout cleanup,
  `block_on` policy choice, the `apatch conformance` CLI, and **tamper-evident
  attestation verification in CI via inclusion proofs (A35-E) — landed and live**:
  governed ops anchor to the `trust-chain.ai` transparency log and CI verifies
  inclusion against the public Merkle root, with no private dependency. See
  [transparency-anchor](./transparency-anchor.md).
- **Next:** contract source = the concept graph (RFP-034 §B.4) so coverage is
  complete by construction; merge-result awareness (A35-G).

See [RFP-035](./RFP-035-continuous-conformance.md) for the full design and acceptance criteria.
