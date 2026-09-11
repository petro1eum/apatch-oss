# Changelog

Формат основан на [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Версия **пакета** (`pip`, `apatch --version`) — semver в `pyproject.toml` ([политика](./docs/release-versioning.md)).  
**RFP 0.2 / 0.3** в [RFP-005](docs/RFP-005-reference-monitor.md) — зрелость контрольного монитора, не semver пакета.

## [Unreleased]

## [0.8.44] — 2026-09-12

### Added

- **Canonical Cowork acceptance:** an explicitly accepted signed execution proposal is delivered to the Platform Work Item acceptance endpoint and returns an immutable, hash-linked local binding. Tampered proposal, acceptance, authority, actor or receipt data fail closed.
- **TrustChain by default:** every APatch installation now requires `trustchain>=3.3.0`; the historical `trustchain` extra remains as an empty compatibility alias for existing install commands.

### Public source preparation

- Prepare a clean, independently rooted OSS source snapshot with selected engineering documentation and offline source-package access.
- Preserve the runtime modules, MIT license, enforcement policies and frozen acceptance assertions; exclude private history, operational identity/evidence, Pro implementation and unrelated third-party reference data.
- Make the owner-frozen RFP check portable through an explicitly approved byte-identical fixture; preserve its original SHA-256 and historical freeze records.
- Keep both the repository README and PyPI description on absolute public links so package documentation never resolves against a nonexistent PyPI-relative path.

### Documentation

- **English, contract-first product description:** explain how APatch binds specifications, permitted changes, frozen verification and signed evidence into an executable engineering contract. Retain the governed-spec quickstart and secondary replay workflow; correct obsolete private/unreleased, installation and license copy. Distinguish opt-in strict SDD, mediated-only containment and optional service integration from base installation. The published 0.8.42 archives remain unchanged.

## [0.8.43] — 2026-09-09

### Distribution

- Previous immutable public maintenance archive. It predates the mandatory TrustChain dependency and canonical Cowork Work Item acceptance delivered in 0.8.44.

## [0.8.42] — 2026-09-08

### Distribution

- **First public OSS package:** MIT license identical to TrustChain OSS; source and wheel retain the license. Only the APatch OSS packages are distributed: no Pro implementation, private avatar-contract source, or direct-URL dependencies.
- **Optional peers:** TrustChain remains an optional named dependency. Avatar/HC integration code stays in APatch; the separately licensed avatar-contract peer is installed explicitly, not bundled or silently fetched.

### Fixed

- **Evidence integrity:** a mutation-free rebind cannot erase recorded file references or turn incomplete evidence into a fresh attestation. Missing or partial signed file hashes remain visible as incomplete/stale; unrelated requirements cannot supply their mutations.
- **Honest tool contracts:** unknown MCP arguments are rejected, compact MCP exposes the read-only impact and build diagnostics, and the remote-opacity and single-intake contracts have executable regression checks.

### Known limitations

- Mutation-free reverify still needs a complete newly signed file basis; this release preserves and reports the incomplete evidence instead of claiming that rebind repaired it.
- This is the APatch CLI/MCP OSS release, not a release qualification of APatch Studio, Cowork assembly, or the pending contract-authority work. External Avatar acceptance is not certified by these package checks.

## [0.8.41] — 2026-09-01

### Added

- **Executable frozen SDD judge:** full MCP now exposes `apatch_sdd_verify`, which loads the exact session-bound verification contract, validates command and judge-asset hashes, executes argv without a shell, performs reversible falsification, restores bytes/mode, and records only content-safe evidence.
- **Automatic strict finalization:** SDD-bound `execute_next(finalize=true)` uses the frozen judge before attestation; legacy sessions retain the existing verify path.

### Fixed

- **Deterministic concurrent admission:** both an observed live session and a CAS-loss race now return `SESSION_CONFLICT`.
- **Release verification:** the full suite is the executable R9 gate, MCP documentation tracks all 124 full-profile tools, and MIT distribution assertions follow PEP 639 SPDX metadata.


## [0.8.38] — 2026-09-01

### Fixed

- **Transactional sandbox admission:** protected bytes from an ended governed session remain valid when their exact mutation hash has a later signed session attestation; a signed but unattested mutation still fails closed.
- **Rollback proof preservation:** pending mutation hashes are stored separately from the last attested file state, so a failed attempt and rollback cannot erase earlier valid proof.
- **Exact Ring-2 scope:** `sandbox ci-gate --base` checks `base..HEAD` plus the staged index and no longer absorbs unrelated unstaged or untracked owner work.

## [0.8.37] — 2026-08-31

### Added

- **Attested Git handoff:** `apatch commit-attested` / `apatch_commit_attested` commits and optionally pushes only the current hashes from explicitly named governed sessions with a later signed attestation. It fails closed on drift, pre-staged files, unsafe paths, missing proof, or mixed scope and leaves unrelated dirty files untouched.
- **Remote exact commit:** `apatch_remote_task_run` accepts `plan={"commit_attested": true, "session_ids": [...], "push": true}` and routes it as a standalone operation without opening a mutation session.

### Security

- Commit scope is derived from signed TrustChain mutation payloads rather than `git status`; the staged set must equal the proven set and pass notarization again immediately before commit.

## [0.8.36] — 2026-08-31

### Fixed

- **RFP scaffold path contract:** `apatch_spec_scaffold` now accepts the absolute and workspace-relative RFP paths advertised by its MCP schema, while preserving ID discovery.
- **Rollback-free authoring diagnostics:** a scaffold request rejected before any write remains an idle, low-risk preflight error instead of becoming `APPLY_FAILED` with a rollback instruction.
- **One-call recovery lifecycle:** MCP enrichment preserves the `verify` or `reapply` phase returned by `apatch_recover`, so the returned capability is immediately usable without a second `resume_session`.
- **Avatar runtime preflight:** `apatch_doctor` reports the exact optional `avatar-contract` version, module path, isolated interpreter, required symbols, and restart requirement; stale editable installs are actionable before evidence delivery while queued evidence remains durable.

## [0.8.35] — 2026-08-30

### Added

- **TrustChain governed work:** exact signed `apatch.change.v1`, Platform `ProjectSourceBinding.v1` admission, source-bound evidence bundles, factual timesheet drafts, and durable offline delivery with the purpose-separated signed `trustchain.governed-work-admission-receipt.v1`.
- **Professional MCP surface:** eight full-profile operations configure, safely transition the signed-service endpoint, prepare Change, store binding, build evidence, sync the outbox, retire superseded rejected requests, and read four independent Platform states; the full catalog is 122 tools while compact remains 15.
- **Portable contracts and onboarding:** strict JSON Schemas, RFP-043/SPEC traceability, privacy boundary, recovery playbook, and an end-to-end tamper/idempotency test.

### Security

- Signed service requests use the enrolled APatch Ed25519 identity; no static service credential enters MCP arguments, persisted config, outbox records or evidence. Endpoint transitions are signed, CAS-bound and crash-replay-safe. Missing, mismatched or invalidly signed Platform receipts never acknowledge evidence. A rejected outbox request can be retired only by a signed marker after an exact same-scope replacement is acknowledged.

### Compatibility

- Existing ContributionEvent wire bytes are unchanged and pinned by a golden hash; governed evidence references the event by id and document hash instead of extending its schema.
- Legacy 0.8.34 ContributionEvents without an embedded public key are accepted only when their key id matches the current enrolled local identity and the original signature verifies; generated timesheet timestamps are normalized to canonical UTC `Z` without changing event bytes.

## [0.8.34] — 2026-08-26

### Fixed

- **Canonical MCP runtime:** `workspace_launcher` now replaces itself with the exact interpreter and command from `.apatch/mcp.json` in isolated Python mode, removing inherited source-path shadowing and binding the selected workspace explicitly.
- **Writer protocol fail-fast:** doctor and the persisted MCP fingerprint expose path-lease protocol v2, runtime provenance, registry state, and reload readiness; incompatible writers return actionable `MCP_WRITER_PROTOCOL_MISMATCH` before creating a draft session or reaching apply.
- **Mixed-version concurrency readback:** v2 writers explicitly recognize the bounded legacy compatibility guard as infrastructure, and an end-to-end two-SPEC regression proves disjoint mutations apply and finalize concurrently while the guard is live.

## [0.8.33] — 2026-08-26

### Fixed

- **Cross-session rollback isolation:** governed rollback now resolves one checkpoint strictly from the supplied session capability, its exact lane cursor, and governed backup metadata; absent or foreign ownership fails before mutation instead of selecting the newest workspace backup.
- **Rollback path admission:** physical restore and TrustChain compensation use the same governed session identity and acquire only that checkpoint's canonical backup paths, preserving concurrent disjoint sessions and rejecting overlap.
- **Fresh session state:** opening a new governed session clears any checkpoint retained by a previously ended session in the reused lane.

## [0.8.32] — 2026-08-26

### Fixed

- **Mixed-version lease migration:** a live 0.8.30 writer keeps its original legacy capability and can extend or release it; 0.8.31+ treats that lease as workspace-wide until the old operation finishes instead of replacing the old owner's state with a protocol sentinel.
- **Bounded protocol barrier:** the v2 compatibility guard exists only while real path leases are live, expires with their latest TTL, identifies itself as `APATCH_UPGRADE_REQUIRED`, and is removed automatically after release or expiry.
- **Alias-wide crash recovery:** MCP startup sweeps stale lease registries for every human-registered workspace, so an expired guard in `@probstates` or another alias cannot survive indefinitely merely because that alias was not the launcher's bound workspace.

## [0.8.31] — 2026-08-26

### Added

- **Path-scoped concurrent writer leases:** governed sessions atomically acquire complete canonical write-sets in `.apatch/write_leases.json`; disjoint sessions mutate one workspace concurrently while exact, prefix, symlink, alias, rename, delete, and filesystem-case overlap fail before mutation with exact diagnostics.
- **Concurrent durability:** rollback restores only its session-owned paths; local TrustChain appends, notarized-index merges, and inclusion updates use short multiprocess transactions so parallel commits remain ordered and lossless.

### Changed

- **Safe v1 migration:** a live legacy workspace lease remains globally exclusive until release/expiry, while a compatibility sentinel prevents old APatch processes from bypassing the v2 registry.
- **Read/write separation:** planning and verification remain lease-free; every apply-backed mutation path now admits its planned paths independently of sandbox mode and rejects write-set expansion inside apply.

## [0.8.30] — 2026-08-25

### Added

- **Timeout- and crash-safe orchestration retries:** `session_start`, `execute_next`, and `spec_run` accept a client-generated `request_id`; retrying the same operation replays one journaled outcome and rotates the exact active capability instead of creating duplicate work. A restarted MCP correlates an interrupted request to its exact session, repairs the narrow pre-registry crash window, or safely re-executes when the dead process created no session. Only request/payload hashes are persisted, and plaintext session tokens are recursively redacted.
- **Selective stale rebind:** `apatch_rebind_stale` and `apatch spec rebind-stale` accept include/exclude requirement ids so shared-file drift can be re-anchored without touching intentionally stale requirements.

### Fixed

- **Stale finalize bootstrap:** finalizing a file-drift-stale requirement with no active session now performs one exact selective verify/rebind cycle; other no-session finalize calls fail before running the expensive verify and return one executable session-start action.
- **Exact execute-next ownership:** `apatch_execute_next` now exposes and enforces `governed_session_id` plus `session_token` when a caller intentionally continues an existing session.
- **No phantom lanes:** lint, dependency, and manifest failures that occur before session creation remain request diagnostics and no longer persist blocked lane state.
- **Profile-aware guidance:** compact doctor/playbooks only recommend tools available in the 15-tool compact surface and explicitly route non-SPEC refactors to the core profile.

## [0.8.29] — 2026-08-21

### Added

- **Exact one-call recovery:** `apatch_recover(governed_session_id=...)` and `apatch session recover <session_id>` reconcile one session, finish interrupted cleanup, rotate a resumable capability, or return a non-destructive foreign-lease blocker.
- **Collision-proof runtime artifacts:** governed temporary state is physically scoped by lane, spec hash, requirement, and session; lineage also records a workspace identity.
- **Automatic differential verification:** apply captures a command- and session-scoped pre-mutation failure baseline and rolls back only newly introduced parsed test failures.
- **Compact MCP profile:** the 15-tool intent-level profile is now the default; `core`, `spec`, and the complete 110-tool `full` surface remain opt-in.

### Fixed

- **Idempotent session finalization:** session end journals and converges ended state, registry cleanup, owned ephemeral deletion, writer-lease release, history rotation, and lane unregister; interrupted or physically drifted cleanup can be retried safely.
- **Exact stateful ownership:** resume, verify, attest, rollback, end, and recovery expose explicit governed session identity/capability contracts instead of selecting the latest active session.
- **Baseline-red rollback:** pre-existing parsed pytest failures no longer cause an unrelated successful mutation to be restored.
- **Authoritative chunk resume:** `execute_next` persists one apply cursor, remains in `applying` between chunks, and deterministically resumes the remaining work without requiring an agent to recover or pass the original JSONL path.
- **Executable fix-forward recovery:** a verify failure that already rolled back resumes the exact governed session in an apply-capable state; corrected patch logs retain exact session ownership and can be applied immediately.
- **Mutation primitive safety:** overlapping needles fail before source/log mutation with deterministic safe retry batches, while atomic replacements preserve executable mode unless an explicit chmod changes it.
- **Bounded workspace evidence:** machine-local `.cursor/mcp.json` no longer makes working-tree notarization red, and doctor/GC compact repeated history, inferred-artifact, and orphan-ephemeral debt into bounded counts plus executable repair actions.

## [0.8.28] — 2026-08-05

### Added

- **Restart-aware exact-Rk execution:** remote `execute_next` can defer live verification/finalization until the governed service has restarted, then resume through `finalize_current`.

### Fixed

- **Fix-forward reset ordering and lane binding:** `apply_session(reset=true)` validates and binds the governed capability before reopening a frozen verifying/committed lifecycle, so corrective needles no longer resolve another lane or fail with `RUNTIME_TRANSITION`.
- **Truthful remote timeout recovery:** `remote_task_run` reconciles `apatch_session_state` after `REMOTE_TIMEOUT`, accepts only identity-matched completed work, and otherwise preserves a typed `reconcile_remote_state` outcome without auto-closing or recommending rollback.

## [0.8.27] — 2026-08-01

### Fixed

- **Truthful attested maintenance:** explicit non-empty SPEC needles reopen an attested requirement by default; `skip_if_attested: true` is the only explicit skip, and conservatively proven already-satisfied postconditions return `applied=0` without a retry loop.
- **Truthful remote mutation reporting:** generic apply, execute-next, and single-/multi-SPEC workflows derive and accumulate physical apply counts across chunks/resume ticks; mixed outcomes remain `APPLIED`, while pure already-satisfied noops, explicit skips, and slug re-attestation are reported separately.
- **Slug-contract shared maintenance:** partitioned cross-SPEC maintenance accepts canonical, direct lowercase `docs/specs/slug_contracts/*.yaml` targets while continuing to reject executable SPEC markdown, path aliases, traversal, and hidden contract files.

## [0.8.26] — 2026-08-01

### Fixed

- **Shared-maintenance rollback state:** failed acceptance verification now reports canonical `VERIFY_FAILED` with diagnostics and completed rollback metadata; once that governed session has ended, response enrichment leaves governance idle instead of persisting `UNKNOWN`/blocked state.

## [0.8.25] — 2026-08-01

### Added

- **Partitioned shared maintenance:** `apatch_spec_run_multi(execution_mode="shared_maintenance")` applies independent cross-SPEC repairs once, runs the original Rk acceptance commands in parallel, and produces one governed attestation.
- **Signed file ownership:** the TrustChain payload records an exact `SPEC#Rk → files` partition; uncovered, duplicated, or shared targets fail closed before mutation or attestation.

## [0.8.24] — 2026-07-29

### Fixed

- **Pre-apply cross-spec verification:** `apatch_spec_run_multi(cross_verify=true)` now tests the exact inline/registry source plan against every later spec before any governed mutation, eliminating false failures caused by reapplying consumed anchors.
- **Atomic multi-run recovery:** successful spec runs expose every chunk checkpoint; multi-run rolls them back in reverse order and never reports a restored workspace when a mutating run omitted rollback evidence.
- **Checkpoint/backup identity:** physical backups now share the TrustChain checkpoint id, and nanosecond process-scoped ids prevent adjacent chunks from colliding.

## [0.8.23] — 2026-07-29

### Fixed

- **Remote session isolation:** `remote_task_run` now fails closed with `REMOTE_SESSION_BUSY` and read-only active-session context instead of auto-closing a foreign live governed session.
- **Bounded remote timelines:** large mutation needles are represented by a deterministic count/targets/size/hash summary instead of being echoed three times in the response.

## [0.8.22] — 2026-07-28

### Fixed

- **Bounded remote execution:** alias policy continues to pin the approved Python and SSH arguments, while a caller may shorten—but never raise—the transport timeout; all remote task calls are capped below the outer MCP deadline.
- **Service-action liveness:** health checks and policy-approved commands now carry operation-specific timeouts, command subprocesses return a typed timeout error, and the outer SSH transport retains a bounded grace period instead of hanging for the alias-wide 600 seconds.

## [0.8.21] — 2026-07-27

### Fixed

- **Contribution contract lockstep:** the apatch emitter now follows the installed shared `ContributionEvent` schema version, emits the required UTC `created_at` field for v3, and compares the actual emitted event against the contract.
- **Sandbox-complete execution:** `apatch_strip` keeps its default output inside the selected workspace, while plan evaluation falls back to deterministic sequential execution when process pools are unavailable.
- **Artifact lifecycle hygiene:** apply-session state inherits the governed lane session id; GC reconciles stale leases from already-ended lanes; the project index is registered as a canonical replay-critical artifact.

### Changed

- **P0 evidence closure:** all executable specifications are attested (`402/402`, no stale requirements), including WorkAsset lifecycle/recall and the continuous conformance gate; the spec index now reflects ledger-derived status.

## [0.8.20] — 2026-07-26

### Fixed

- **Runtime-complete cockpit health:** unavailable feedback replay or missing triage now remains yellow when the operational hook proves runtime complete. Explicit runtime defects and high-severity fresh feedback remain red.

## [0.8.19] — 2026-07-26

### Added

- **Operational slug completion:** `apatch slug cockpit <slug> --live` can consume a project `hooks.operational_status` JSON report and exposes `runtime_work_complete`, scoped reopen reasons, evidence debt, and a deterministic verification key. A valid evidence-red exit no longer tells agents to rebuild already verified runtime work.

### Changed

- **Agent guidance:** generated consumer contracts explicitly require preserving completed category runtime when only evidence debt remains.
- **Fix-forward recovery:** `resume_session` now returns rolled-back verify failures to the apply phase and tells agents to reapply corrected needles with `reset=true`, instead of recommending an operation that the lifecycle immediately rejects.

## [0.8.18] — 2026-07-25

### Fixed

- **Remote rebind policy parity:** `apatch_rebind_stale` is now part of `DEFAULT_REMOTE_TASK_OPERATIONS`, so newly onboarded aliases can use `plan={"rebind_stale": true, "spec": "SPEC-X"}` without a manual policy edit.

## [0.8.17] — 2026-07-25

### Fixed

- **One-call stale rebind on remote workspaces:** `apatch_remote_task_run(plan={"rebind_stale": true, "spec": "SPEC-X"})` now verifies and re-attests shared-file drift directly, without fake marker mutations or one session per requirement.
- **Chunk capability continuity:** when `execute_next` opens a governed session and a multi-chunk apply must continue, its response now includes the fresh session capability required by `apatch_apply_session`.
- **Consumer guidance:** generated AGENTS and MCP setup documentation describe the remote rebind route and keep it inside the brokered lifecycle.

## [0.8.16] — 2026-07-25

### Fixed

- **Fresh self-edit evidence:** `execute_next` now rebinds the active SPEC artifact to the post-mutation requirement hash before attestation, so a successful self-edit does not become stale immediately.
- **Pre-mutation cleanup:** generation/simulation failures automatically close sessions opened by `execute_next`; the remote broker also cleans up targeted failures from older runtimes before any apply.
- **No phantom rollback:** a missing anchor before source mutation is reported as a closed pre-mutation failure instead of leaving a session blocked on a nonexistent checkpoint.

## [0.8.15] — 2026-07-25

### Fixed

- **Session-wide deferred verification:** multi-chunk apply sessions now run a deferred repository verify only after the final chunk, avoiding false rollback when implementation and tests are split across chunks.
- **Fresh self-edit verification:** when `execute_next` mutates its own SPEC, apatch skips the stale pre-mutation verify command, reparses the requirement after apply, and finalizes with the current command.

## [0.8.14] — 2026-07-25

### Fixed

- **Bounded cross-spec batches:** explicit per-spec requirement maps in `spec_run_multi` are now treated as the requested work lists, so unrelated pending or stale Rk are skipped and reported instead of blocking the batch with `MANIFEST_GAP`; registry-driven runs remain strict.

## [0.8.13] — 2026-07-25

### Fixed

- **Remote pre-mutation cleanup:** when a broker-owned generic task is rejected during generate or simulate, apatch now closes the mutation-free session automatically instead of leaving the correct SPEC-bound retry blocked or demanding an impossible rollback.

## [0.8.12] — 2026-07-25

### Fixed

- **Forward-progress-safe slug ratification:** broken verify references belonging only to already-closed requirements are now reported as advisories instead of blocking green open requirements; real test regressions and broken open requirements still block.

### Changed

- **Conformance and recovery documentation:** agent and maintainer guides now separate declaration, enrollment, live proof, and domain evidence, and document the advisory treatment of obsolete closed-requirement verify references.

## [0.8.11] — 2026-07-25

### Fixed

- **Remote resume capability precedence:** after `apatch_resume_session` rotates a governed capability, remote verify/noop-attest/session-end now use the fresh token instead of the obsolete token returned by `apatch_session_start`.

## [0.8.9] — 2026-07-23

### Fixed

- **Malformed SPEC-contract recovery:** the exact active remote fix-forward session may repair its own malformed slug-contract YAML without weakening ownership checks for category code or ordinary mutation channels.

## [0.8.6] — 2026-07-22

### Fixed

- **Active rollback retention:** GC now follows active apply-session checkpoints, including brokered remote state, so fix-forward and rollback cannot lose their backups mid-lifecycle.
- **Remote SPEC fix-forward:** corrective needles in the exact active SPEC session use an authorized fix-forward channel instead of being rejected as a generic remote mutation.

## [0.8.5] — 2026-07-22

### Fixed

- **Remote single-SPEC execution:** `apatch_remote_task_run` now routes one SPEC directly to remote `apatch_spec_run`; dummy peer specs are no longer required.
- **Failure fidelity:** nested `VERIFY_FAILED` evidence and fix-forward guidance survive `spec_run_multi` and remote orchestration without a destructive second rollback.
- **SPEC bootstrap correctness:** creating a new SPEC uses a non-attesting `spec-bootstrap:` authorization artifact, so bootstrap cannot falsely mark R0 complete.
- **Multi-chunk regression gate:** seven-file apply sessions are proven to consume every chunk before completion.

## [0.8.4] — 2026-07-18

### Fixed

- **Spec-run verify overrides:** `verify_override` now reaches the initial mutation apply, continuation chunks, retry finalization, and normal finalization instead of silently falling back to the SPEC command.

## [0.8.3] — 2026-07-18

### Fixed

- **Spec-run forced attested reruns:** an explicit `skip_if_attested: false` now bypasses the completed-spec shortcut, appears in the execution plan, and executes its needles instead of returning a false green no-op.

## [0.8.2] — 2026-07-17

### Fixed

- **Remote governed fix-forward:** capability rotation now remains bound to the authoritative remote session, allowing verification failures to resume and complete without reopening or losing applied mutations.

## [0.8.1] — 2026-07-17

### Fixed

- **Python 3.10 source version detection:** source checkouts now fall back to installed `tomli` when stdlib `tomllib` is unavailable, so editable/prod overlays report the `pyproject.toml` release instead of stale package metadata.

## [0.8.0] — 2026-07-17

### Added

- **capability_evidence export & surfaces — RFP-037 Phase 4 (`apatch/avatar_evidence.py`, [SPEC-AVATAR-EVIDENCE-1](./docs/specs/SPEC-AVATAR-EVIDENCE-1.md)):** the avatar's export to HC Capital as an *individual update to the market prior* — qualified episodes + honest `unqualified {count, reasons}` histogram + capabilities + `generated_at`, Ed25519-signed best-effort; an economic leak raises `EvidenceBarrierError` instead of exporting. `verify_evidence_bundle` re-derives episodes AND recomputes capabilities on the bundle's own clock — `capability_drift` / `not_derivable_from_ledger` / `missing_from_bundle` are explicit flags. New CLI group **`apatch avatar`** (`episodes` / `capabilities` / `evidence-export --verify`), and `apatch_project_status` now embeds `capabilities_summary` (top classes with reliability/recency/uncertainty — what the avatar provably does, never how much data was collected).
- **Capability compiler — RFP-037 Phase 2 (`apatch/capability.py`, [SPEC-CAPABILITY-1](./docs/specs/SPEC-CAPABILITY-1.md)):** rule-derived ability claims over qualified episodes — no LLM scoring anywhere in the core. Deterministic `task_class` taxonomy (explicit `task-class:` tag > `spec_family/stack` > `unclassified/stack`); a capability cannot exist without `evidence` episode_ids; `claimed`-trust and unqualified episodes never feed it. Signals: weighted `reliability` (gate quality sets the weight: probe-`falsified` 1.0, `unprobed` 0.5, `false_gate` **0.0**; `verified` trust ×1.2; rejected/red outcomes stay in the denominator at full trust weight — a bad result can't be discounted by a weak gate), `recency` with decay (fresh/aging/stale — the human twin of `attested → stale`), `growth` trend, `transferability` (≥2 projects), `company_dependency` (v1 project-concentration proxy), `ai_reliance` (attribution shares). `uncertainty {level, reasons}` is first-class (`n<5`, `no_falsified_gates`, `single_project`, `stale_evidence`). Anti-gaming under test: volume-blind, negative evidence immutable, unprobed-gate farming buys uncertainty instead of confidence.
- **WorkEpisode read model — RFP-037 Phase 1 (`apatch/episode.py`, [SPEC-EPISODE-1](./docs/specs/SPEC-EPISODE-1.md)):** the missing semantic layer between signed events and capability derivation ([RFP-037](./docs/RFP-037-avatar-semantic-engine.md): события → эпизоды → способности → экономические сигналы). `episodes_from_rows` / `build_episodes` fold a governed session's already-signed ledger rows (mutations, attest, `plan_register`) + RFP-026 receipts into a deterministic, money-free, content-safe episode (task / context / decisions / result / role / proof_ref) — discovery, not upload; no new instrumentation. `qualify_episode` implements the RFP-037 §5 gate (Q1–Q7) with explicit `disqualify_reasons`; the gate is **volume-blind** (scaling ops/insertions never changes the verdict — the semantic twin of the economic barrier, under test). `verify_episodes` re-derives from the raw ledger and flags drift (`not_derivable_from_ledger`, tampered `qualified` / `proof_ref.head`).
- **`apatch attestation noop` CLI (RFP-027):** CLI surface for noop-attest (attest a requirement covered by another Rk's mutation, no marker file), mirroring the `apatch_noop_attest` MCP tool — `--covered-by R1,R4` parses to a list and calls `MutationRuntime.noop_attest`. Closes the MCP-only gap one of the contribution-timesheet overhead levers flagged ([SPEC-NOOP-ATTEST-CLI-1](./docs/specs/SPEC-NOOP-ATTEST-CLI-1.md)).
- **Scaffold a SPEC from the contract — `apatch spec scaffold --from-contract` / `apatch_spec_scaffold` (RFP-023):** the authoring inverse of `rfp_spec_coverage` — emit a SPEC.md skeleton from an RFP `## Acceptance` table: one `## Rk` stub per acceptance row + a pre-filled `## R0 RFP traceability gate`, **contract-complete by construction** (round-trips to 0 coverage gaps). The author fills the `(verify:)` placeholders instead of hand-inventing the whole checklist + traceability — directly cuts the spec-authoring overhead the ledger timesheet flagged as the biggest governed-time bucket. `scaffold_spec_from_rfp(+_workspace)` in `apatch/rfp_coverage.py`; `--mandatory-only` for MUST rows.
- **Unified differential probe — `apatch probe` (RFP-005):** falsify/regress/ratify are now one primitive (`apatch/probe.py` `differential_probe`): apply a perturbation, re-measure a signal, judge the delta against a polarity (`must_diverge` | `must_hold`). `probe falsify` corrupts the files a requirement guards — the gate MUST go RED, else it is a FALSE gate; `probe regress` allows no NEW failures vs a recorded corpus (baseline-aware); `probe ratify` requires an attested gate to STILL be green (red now = STALE attestation). `conformance.falsify_requirement` is a back-compat shim delegating to the driver. Domain-agnostic (`--verify` is any shell command). Doc: [probe.md](./docs/probe.md).
- **Reality ledger — `apatch reality` (RFP-005):** inverts the source of truth — an append-only ledger of observed facts the project must close (`.apatch/reality.jsonl`); spec requirements `discharge` records (`## Rk … (discharges: REC-…)`). `apatch reality add` / `apatch reality status` derive coverage as "observed reality closed by a green gate", with `uncovered` as derived debt (pure `reality_coverage` core). `source`/`kind` are free-form — domain-agnostic. Doc: [reality.md](./docs/reality.md).
- **Symbol-granular attestation anchoring (RFP-032, SPEC-SYMBOL-ANCHOR-1 ✅ 6/6):** `apatch/symbol_anchor.py` anchors drift to tree-sitter symbol boundaries (functions/classes), not whole-file sha256, so editing one symbol no longer drifts every requirement that touched a shared file. `extract_file_symbols`, `symbols_for_line_range`, `symbol_drift`, `requirement_stale_with_symbols` (file-hash fallback). Go/no-go: editing `foo` leaves a requirement anchored to `bar` green.
- **SCIP cross-file reference impact (RFP-033, SPEC-SCIP-IMPACT-1 ✅ 6/6):** `apatch/scip_ingest.py` ingests a `.scip` index (Apache-2.0) via a zero-dependency in-process protobuf-wire decoder (no `protobuf` runtime, no generated stub) and surfaces, for a changed symbol, the attested requirements whose anchored symbol references it across files. Advisory only — a warning, never an automatic staleness trigger; closes the refactoring blind spot left by symbol anchors.
- **SCIP Phase 3 — native cross-file resolver, works without scip-python (RFP-033, SPEC-SCIP-IMPACT-3 ✅ R1-R3):** Phase 2 produced the index via `scip-python`, but nobody installs it, so impact stayed dormant. `apatch/native_refs.py` builds the cross-file reference graph from the Python AST **in process, import-resolved** (`from a import foo; foo()` and `import a; a.foo()` both resolve to `a.py`'s `foo`) — so `apatch scip impact` / `apatch_scip(action='impact')` work out of the box on any repo (`model_source: native`). A real `.scip` still wins when present (`model_source: scip`); `verify_run` passes `allow_native=False` to stay scip-only on the hot path. Proven live on apatch itself (10 cross-file impacts on attested requirements).
- **SCIP Phase 2 — producer + surfaced advisory (RFP-033 A33-G, SPEC-SCIP-IMPACT-2 ✅ R1-R3):** the Phase-1 ingest was a tested library nobody called; Phase 2 wires it end to end. `apatch/scip_producer.py`: `produce_scip_index` runs `scip-python` out of band → `.apatch/scip/index.scip` (graceful no-op when scip-python absent — no hard runtime dep); `changed_symbols_since` reads changed symbols from a `git diff`; `scip_impact_workspace` reports which ATTESTED requirements reference them cross-file. Surfaced via `apatch scip index|impact` (CLI), `apatch_scip(action=…)` (MCP, now 95 tools), and an advisory `scip_impact` field on `apatch_verify_run` when an index is present. Stays advisory — never marks stale or blocks; `index_present: False` with no warnings otherwise.
- **`apatch_rebind_stale` MCP tool (RFP-027):** one call re-verifies and re-attests every `file_drift`-stale Rk of a spec (noop-attests the green ones), clearing the shared-file cascade after a batch run.
- **Mutation-safety + UX fixes (dogfood):** `apatch_generate_batch` refuses overlapping same-file needles (silent-corruption guard); the MCP wrapper forwards `target_dir` only to tools that accept it (fixes `apatch_scan`/`apatch_view`/`apatch_work_asset_export_schema`/`apatch_verify_anchor`/`apatch_trust_enroll`/`apatch_natives_check` TypeErrors); `apatch_project_status(view='compact')` is the MCP default (~242k -> ~21k chars); `apatch_gc(mode='reconcile')` now persists inferred artifacts.
- **Markdown outline mutations:** `shift_outline`, `insert_before`, `insert_section` needles in `apatch_generate_batch` — renumber `## 3.` / `### 3.1` cascades without N× replace ([SPEC-DOC-OUTLINE-1](./docs/specs/SPEC-DOC-OUTLINE-1.md)).
- **Agent discoverability:** `apatch_generate_batch` MCP docstring, `PARAM_DOCS`, `doctor.spec_run.lessons`, `mcp_setup.md`, `AGENTS.template.md` §3B — routing for numbered markdown.
- **MCP resource `apatch://playbook/doc_outline`:** self-contained workflow for unfamiliar agents editing numbered markdown (`doc_outline_playbook` in `apatch/agent_playbooks.py`); indexed in `mcp_playbook_index` and `tool_usage.by_intent.numbered_markdown_outline`.
- **Docs:** [apatch-transformation-matrix.md](./docs/apatch-transformation-matrix.md) — build/compiler metaphor, readiness matrix; [RFP-022](./docs/RFP-022-unified-diagnostics-knowledge-graph.md) draft — Unified Diagnostics & Knowledge Graph.
- **SPEC-DIAGNOSTIC-GRAPH-1 attested:** `apatch/diagnostics/` — schema v1, clang/pytest/spec/trust adapters, `collect_diagnostics`, session artifacts, `diagnose_playbook` + MCP resource `apatch://playbook/diagnose`.
- **SPEC-CONTRACT-EDGES-1 attested:** `apatch/diagnostics/edges.py` — `resolve_symbol_edges`, C++ missing_member + Python symbol + `@sid` edges, `impact_ref`, merged in `collect_diagnostics`.
- **SPEC-KNOWLEDGE-GRAPH-1 attested:** `apatch/knowledge_graph.py`, `diagnostics_summary` in project status, MCP `apatch_knowledge_graph`, manager report **Verification & diagnostics** section.
- **UD-7 dogfood smoke PASS:** design-partner-playbook §11; follow-up fix — diagnostics artifact binds governed `session_id` from `session_state` (not `unknown.json`).
- **Contribution timesheet (RFP-026, SPEC-CONTRIB-TIMESHEET-1 ✅ 10/10):** signed `ContributionEvent` receipt per attested session keyed to the TrustChain certificate (`apatch/contribution.py`); `apatch_timesheet` / `apatch timesheet` aggregate per-identity, cross-project (identity/project/spec/day) with `--verify` re-derivation.
- **Agent UX & recovery hardening (RFP-027, SPEC-AGENT-UX-1 ✅ 6/6 + SPEC-AGENT-UX-2 ✅ 4/4):** `apatch_resume_session` recovers a `failed`/`verifying` session; `apatch_noop_attest(covered_by=…)` attests an Rk covered by another (no marker file); gc no longer deletes active-session backups; typed `BACKUPS_PRUNED` rollback; `apply_session` returns explicit `SESSION_ALREADY_COMPLETE` (no silent no-op); per-session playbook payload dedupe; self-edit `restart_required` signal.
- **WorkAssets / Narabotki (RFP-031):** deterministic metadata-only WorkAsset read model, CLI/MCP list/show/search/export surfaces, JSON Schema command, and stable `apatch.work_asset_export.v1` bundle for Human Capital consumers. Docs: [work-assets.md](./docs/work-assets.md).
- **Data-integrity fixes:** atomic file write in `tui._commit_write` (tempfile + fsync + `os.replace`); `BackupManager.create_backup` raises `BackupError` instead of silently returning `False`.
- **MCP tools: 105** (+27 since 0.7.0's 78: remote orchestration, diagnostics/knowledge graph, reality/probe, contribution/work-asset flows, recovery and spec authoring tools).

### Fixed

- **Remote worker JSON framing:** governed/Rich progress output is redirected to stderr, while the SSH transport also recovers the final JSON line from older noisy workers instead of returning a false `REMOTE_JSON_INVALID`.
- **Spec-run verification diagnostics:** outer `spec_run` failures retain pytest/compiler output, parsed diagnostics, artifact paths, checkpoint details, and the typed `VERIFY_FAILED` cause.
- **Post-rollback recovery:** an apply chunk that was already auto-restored now reports `rollback_performed=true` and `fix_forward`, so agents resume the same governed session instead of attempting a destructive second rollback.
- **Governed runtime reliability contract:** added [governed-runtime-invariants.md](./docs/governed-runtime-invariants.md), covering transactional capability binding, CAS-idempotent reconcile, atomic same-file chunks, canonical dot-paths, verify environment isolation, and RFP-038 no-regression gates.
- **TrustChain ledger hot path (RFP-038):** normal commit validation is O(1) over the appended object + HEAD, notarized-index v2 carries ledger metadata, normal verify is cached, and one apply chunk produces one signed mutation record. Explicit rebuild/audit still performs the full walk.
- **Transactional recovery:** runtime re-captures the session capability after reconcile changes the CAS revision; remote orchestration accepts the authoritative `session_capability` projection; read-only and diagnostics paths no longer poison or stall the lifecycle.
- **Apply and path correctness:** all steps touching one file remain in one ordered atomic chunk, and canonical `normalize_rel` preserves leading dots for `.apatch/**` and `.github/**`.
- **Ephemeral cleanup:** `session_end` now removes the atomic companion `.lock` together with each released patch JSONL, including explicitly routed root logs.

- **Lane resolution is deterministic — no more apply/attest session_state split (#3, `REC-b21fbd7ef7f2`).** With >1 lane active and no `spec=` bound, `resolve_lane` routed to a dead lane literally named `ambiguous`, so when the active-lane count flipped between apply (1 active → real lane) and attest (2 active → `ambiguous`) the two read different `session_state.json` files — the non-deterministic `applying`/`draft` flap. `resolve_active_lane` now returns the most-recently-registered active lane (explicit `spec=` still wins); apply and attest resolve to the same lane. Gate: `tests/test_lane_resolve.py` ([SPEC-LANE-RESOLVE-1](./docs/specs/SPEC-LANE-RESOLVE-1.md)).
- **Reality-loop self-audit — 3 apatch bugs found, gated, and discharged (RFP-005 dogfood).** The first live use of the reality ledger on apatch itself surfaced three bugs; each is now `covered` by an attested requirement with a real gate (red before, green after): (1) `apatch_reality(status)` returned `ok:false` for "debt exists" (a valid result), which `enrich_tool_response` misread as a tool failure — now reports operational `ok:true` + a `clean` field ([SPEC-REALITY-STATUS-1](./docs/specs/SPEC-REALITY-STATUS-1.md), `REC-7855`); (2) `apatch_apply_session` silently DUPLICATED on re-apply when a replacement kept its anchor (`new ⊇ old`) — `ASTMatcher.evaluate` now returns an `already_applied` skip ([SPEC-APPLY-IDEMPOTENT-1](./docs/specs/SPEC-APPLY-IDEMPOTENT-1.md), `REC-6a1d`); (3) `apatch_resume_session` reported `verifying` but the global tool wrapper's enrich re-derived `idle`, so the session flapped to `draft` and could not be attested — `_derive_phase` now returns `verify` for a successful resume, gated on both the flap and the `reset=true` trigger ([SPEC-RESUME-LIFECYCLE-1](./docs/specs/SPEC-RESUME-LIFECYCLE-1.md) R1+R2, `REC-390b`).
- **Guard rejections no longer poison the session lifecycle.** A `RUNTIME_TRANSITION` (a valid op called in the wrong phase — e.g. `attest` while still `applying`) was reclassified by `classify_failure` as `ERROR_APPLY_FAILED` and persisted as a session `failure`, so `derive_lifecycle` flipped the session to `failed` and advised `rollback` (which would discard already-applied work); `resume → verify → attest` then never converged. `enrich_tool_response` now treats `RUNTIME_TRANSITION` as a no-op on `session_state` (no `failure`, no phase change, keeps the `resume_session` advice). Regression: `tests/test_runtime_transition_noop.py`.
- **Recursive globs now match root-level files.** `iter_matching_files` (`apatch/generate.py`) matched a `**/…` pattern with `fnmatch`, which requires a literal `/` — so a root-level file (rel path == basename) silently failed `**/*` / `**/*.md`. Effect: `replace` needles, whose default glob is `**/*`, silently skipped root files (e.g. a find/replace on `README.md` returned "find_text not found"). The same root cause was masked in two security-sensitive matchers, `_glob_match` (`apatch/artifact_governance.py`) and `_glob_matches` (`apatch/sandbox.py`) — a recursive allow-glob like `**/*.md` excluded root files, kept hidden only because the default sandbox config also lists `*.md`. All three now match the de-prefixed pattern (parity with `git_util.path_matches`); protected `…/**` suffix globs are unchanged. Regression: `tests/test_iter_matching_root.py`, `tests/test_glob_root_match.py`.

## [0.7.0] — 2026-06-12

Minor: **RFP-021 Design Partner Ready (L1)** — agent reliability for unsupervised MCP cycles. **74 MCP tools** (unchanged since 0.6.0).

### Added

- **Session recovery hardening (AR-1):** post-attest / pre-`session_end` reconcile, async verify job facts on reconnect (`latest_verify_job_for_session`), R6 auto-heal retry in `assert_operation` with `reconcile_applied` / `effective_lifecycle` in `RUNTIME_TRANSITION` errors ([SPEC-SESSION-RECOVERY-1](./docs/specs/SPEC-SESSION-RECOVERY-1.md) §4.3 six-scenario matrix).
- **Async verify (AR-2):** background verify jobs, `apatch_verify_run(async_mode=True)`, `apatch_verify_status(job_id=…)`, forced async via `APATCH_VERIFY_SYNC_MAX_SEC` ([SPEC-VERIFY-ASYNC-1](./docs/specs/SPEC-VERIFY-ASYNC-1.md)).
- **Failure taxonomy v2 (AR-3):** `fix_forward` default for innocent verify failures; `resume_session` for lifecycle desync ([SPEC-FAILURE-TAXONOMY-2](./docs/specs/SPEC-FAILURE-TAXONOMY-2.md)).
- **Baseline-aware verify (AR-4):** `baseline=capture|compare`, `allowed_failures` — pre-existing red must not block attest (`apatch/verify_baseline.py`).
- **Supervisor visibility (AR-6):** `apatch status --json` → `session.blocker`, baseline summary ([SPEC-STATUS-BLOCKED-1](./docs/specs/SPEC-STATUS-BLOCKED-1.md)).
- **Design partner playbook (AR-7):** [docs/design-partner-playbook.md](./docs/design-partner-playbook.md) — G1–G7 checklist, escalation table, async verify recipe; copied by `init-consumer` ([SPEC-DESIGN-PARTNER-1](./docs/specs/SPEC-DESIGN-PARTNER-1.md)).
- **Agent contract (AR-5):** `needles_path` on `apatch_generate_batch`; verify as argv list; sandbox allows safe fd redirects (`2>/dev/null`).

### Changed

- **[RFP-021](./docs/RFP-021-agent-reliability-design-partner.md):** L1 maturity model; AR-1…AR-7 implementation chain attested.
- **`assert_operation`:** reconcile on every governed entry; stale `failure` cleared when ledger/apply/verify job proves success.

### Fixed

- MCP disconnect mid-cycle: `phase=verify` no longer stuck in `lifecycle=draft` after reconnect.
- Verify failures on correct patches no longer recommend rollback when chunk verify did not roll back.


Minor: **RFP-020 productization** — one data layer (`project_status_workspace`), three views (developer CLI, architect HTML, manager MD). **74 MCP tools** (+`apatch_project_status` since 0.5.0).

### Added

- **Unified project status (RFP-020 Stage 1):** `apatch/project_status.py`, MCP `apatch_project_status`, `plan_adherence` reads real mutation payloads from ledger ([SPEC-PROJECT-STATUS-1](./docs/specs/SPEC-PROJECT-STATUS-1.md)).
- **Developer CLI (Stage 2a):** `apatch status` (`--json`), `apatch spec list`, richer `apatch doctor` / `apply-session` human output ([SPEC-CLI-STATUS-1](./docs/specs/SPEC-CLI-STATUS-1.md)).
- **Reports (Stage 2b):** `apatch report --html`, `apatch report --format md` (`--locale ru|en`) via `apatch/report_render.py` ([SPEC-REPORT-1](./docs/specs/SPEC-REPORT-1.md)).
- **Onboarding (Stage 3):** README executable-spec quickstart, [RFP-020-three-views.md](./docs/RFP-020-three-views.md), docs index + CLI↔MCP parity ([SPEC-ONBOARDING-1](./docs/specs/SPEC-ONBOARDING-1.md)).
- **`apatch.mcp.workspace_launcher`** — resolves project via `APATCH_WORKSPACE` / `CURSOR_PROJECT_DIR` / cwd walk; loads `.apatch/mcp.json`; starts MCP. Entry point `apatch-mcp-ws`.
- CLI `apatch mcp sync --no-auto-ide` to skip IDE stub auto-detect.

### Changed

- **MCP config canonical location:** project config in `.apatch/mcp.json` (not `.cursor/mcp.json`). IDE hosts use `python -m apatch.mcp.workspace_launcher` stub with `APATCH_WORKSPACE`.
- **`apatch mcp sync` auto-detect:** updates existing IDE config files (`.cursor/`, `~/.gemini/…`) and pins `APATCH_WORKSPACE` to `--target-dir`.
- **MCP profile default:** `APATCH_MCP_PROFILE=full` (**74 tools**); `core`/`spec` opt-in for specialty bundles.
- **Product stabilization:** ephemeral JSONL test fixes, inference-sunset reconcile semantics, staging debris removed ([SPEC-PRODUCT-STAB-1](./docs/specs/SPEC-PRODUCT-STAB-1.md)).
- **`docs/AGENTS.template.md`:** §8 tool count 74 + Product views (RFP-020) section.

### Fixed

- MCP `Connection closed` when IDE starts with `cwd=$HOME` — stub now carries `APATCH_WORKSPACE`.

## [0.5.0] — 2026-06-11

Minor: RFP-018 build diagnose + RFP-019 MCP guidance/resources + RFP-016 hygiene reconcile. **73 MCP tools** (+`apatch_build_diagnose`, +`apatch_mcp_hygiene` since 0.4.0).

### Added

- **MCP guidance slim mode (RFP-019 L1-5):** `APATCH_MCP_GUIDANCE=doctor_only` — hot-path tools return `guidance_ref` instead of full playbooks; default in `recommended_mcp_server_block()` / consumer `mcp.json`; apatch source dogfood stays `full` ([RFP-019](./docs/RFP-019-mcp-scale-lifecycle.md)).
- **Build-aware diagnostics (RFP-018 MVP):** `apatch/build_diagnose.py`, `apatch build-diagnose`, MCP `apatch_build_diagnose` — parse clang/gcc errors, C++ member context, advisory fix suggestions; artifacts `.apatch/build_log.json` + `diagnostics.json` ([SPEC-BUILD-DIAGNOSE-1](./docs/specs/SPEC-BUILD-DIAGNOSE-1.md)).
- **MCP Resources (RFP-019 L1-10):** eight `apatch://playbook/*` resources (`index`, `protocol_contract`, `runtime_hygiene`, `sandbox_protocol`, `spec_authoring`, `tool_usage`, `spec_run`, `spec_execution`); `apatch/agent_playbooks.py`; mirrored in `apatch_doctor` (`runtime_hygiene`, `tool_usage`, `mcp_resources`).

### Changed

- **`apatch_gc`:** `mode=reconcile` registers inferred artifacts (inference sunset remediation); CLI `--reconcile`; expanded MCP docstring ([RFP-016](./docs/RFP-016-runtime-hygiene.md)).
- **Ephemeral patch JSONL:** `generate_batch` / `spec_run` / `execute_next` route logs to `.apatch/tmp/<session_id>/`; agents must use response `out_path` for simulate/apply ([RFP-016](./docs/RFP-016-runtime-hygiene.md)).
- **MCP docstrings:** `apatch_doctor`, `apatch_session_end`, `apatch_gc`, `apatch_generate_batch` — session lifecycle, hygiene, and `out_path` routing spelled out for agents.
- **Docs:** [mcp_setup.md](./docs/mcp_setup.md) resource table; [RFP-019](./docs/RFP-019-mcp-scale-lifecycle.md) L1-10 detail.

### Fixed

- **`spec_run` / `spec_executor`:** use `generate_batch` response `out_path` in apply loop (not logical repo-root path).
- **`artifact_governance`:** auto-reconcile inferred artifacts before inference-sunset block; extended inference rules.

## [0.4.0] — 2026-06-10

Minor: RFP-016 artifact lifecycle MVP (`apatch_gc`, governance registry) + RFP-019 MCP lifecycle (Level 1). **71 MCP tools** (+`apatch_gc`).

### Fixed

- **MCP lifecycle (RFP-019 L1):** `apatch/mcp/lifecycle.py` — `atexit` + SIGTERM/SIGINT release sandbox leases on MCP shutdown; stale `write_lease.json` sweep on startup and first `target_dir` touch per process ([RFP-019](./docs/RFP-019-mcp-scale-lifecycle.md)).
- **Ghost MCP / lease conflicts:** `acquire_lease` reclaims orphan `apatch_apply_session` lease when a new stdio MCP pid starts (`sandbox.py`).
- **MCP health latency:** `build_mcp_health` reuses in-process interpreter in stdio mode — no extra subprocess probe on every `apatch_doctor` (~−1 s).

### Added

- **Artifact lifecycle MVP (RFP-016 draft):** `artifact_governance` registry, `apatch_gc` / `apatch gc`, doctor `hygiene` block, RUN_STATE leases on `apply_session` / `spec_run`, session-end registry cleanup; specs [HYGIENE-CORE](./docs/specs/SPEC-HYGIENE-CORE.md) … [SESSION-LIFECYCLE-1](./docs/specs/SPEC-SESSION-LIFECYCLE-1.md).
- **Docs:** [RFP-019](./docs/RFP-019-mcp-scale-lifecycle.md) (L2 pip + L3 hosted MCP — architecture only, no rollout); [RFP-018](./docs/RFP-018-build-diagnose.md) draft.

### Changed

- `spec_run` / `apply_session`: artifact registration + `gc_allowed` guards on write-path (RFP-016).

## [0.3.0] — 2026-06-10

Волна Engineering Truth + executable specs + cross-spec coordination. **70 MCP tools** (было 53 в 0.2.0).

### Added

- **Release policy:** [docs/release-versioning.md](./docs/release-versioning.md) — semver cadence, checklist; `__version__` из `pyproject.toml` ([SPEC-VERSION-1](./docs/specs/SPEC-VERSION-1.md)).
- **Engineering Truth overview (docs):** [engineering-truth-overview.md](./docs/engineering-truth-overview.md).
- **Executable Specifications (RFP-007 ✅):** `apatch/spec.py`, `apatch_spec_lint|status|next`, ledger-derived requirement state, `session_start(requirement=…)`; [spec-authoring.md](./docs/spec-authoring.md).
- **Spec executor (RFP-008 ✅):** `apatch_execute_next`; [SPEC-EXECUTOR-1](./docs/specs/SPEC-EXECUTOR-1.md).
- **Spec run (RFP-009 ✅):** `apatch_spec_run`, inline `requirements={Rk: {needles}}`, resume; [SPEC-RUN-1](./docs/specs/SPEC-RUN-1.md).
- **Requirement coverage (RFP-010 ✅):** `apatch_spec_coverage`, file-drift `stale`; [SPEC-COVERAGE-1](./docs/specs/SPEC-COVERAGE-1.md).
- **Plan artifact v2 (RFP-011 ✅):** `decision_plan` + `execution_plan`, `apatch_spec_plan_show`; [SPEC-PLAN-ARTIFACT-1](./docs/specs/SPEC-PLAN-ARTIFACT-1.md).
- **Plan adherence (RFP-012 ✅):** `apatch_spec_adherence`; [SPEC-ADHERENCE-1](./docs/specs/SPEC-ADHERENCE-1.md).
- **Cross-spec interference (RFP-014 ✅):** L1/L2 detection, schedule, Level-3 cross-verify, MVCC, `spec_run_multi`, L0 domain-tag routing; [SPEC-INTERFERENCE-1…5](./docs/specs/SPEC-INTERFERENCE-1.md).
- **UX specialist (RFP-015 ✅):** five-layer audit schema/validator, methodology; [SPEC-UX-SPECIALIST-1](./docs/specs/SPEC-UX-SPECIALIST-1.md).
- **Mutation generator (B11):** `apatch_generate_batch` — `create` | `delete` | `rename` | `replace`; `apatch_plan` dry-run CREATE.
- **Domain model v2:** [domain.md](./docs/domain.md) — Engineering Truth stack, circuit breaker, L0 tags.

### Changed

- `apatch_doctor.sandbox_agent_protocol`: уточнён `never_via_shell` и `shell_allowed`.
- MCP `strategy` parameter moved from `apatch_spec_interference` to `apatch_spec_schedule` ([SPEC-INTERFERENCE-5](./docs/specs/SPEC-INTERFERENCE-5.md)).

### Fixed

- **Shell hook false positives:** `detect_shell_mutation` игнорирует `>`/`<`/`sed`/`tee` внутри кавычек; fd-редиректы и пайпы не считаются мутацией.
- **Hook consistency:** хуки уважают `is_sandbox_enabled`.
- **MCP interference:** `strategy=` больше не ломает `apatch_spec_interference`.

## [0.2.0] — 2026-06-07

Первый осмысленный semver-релиз после `0.1.0`: governed runtime, контрольный монитор (У2/скелет У3), artifact-anchored intent.

### Added

- **Governed Mutation Runtime (RFP-004):** `MutationRuntime`, session lifecycle `Intent → Session → Mutation → Verification → Attestation | Rollback`, `governed_mode` (`off` | `auto_session` | `strict`), `state_update.next_action` на всех MCP-ответах.
- **MCP lifecycle:** `apatch_session_start/end`, `apatch_verify_run`, `apatch_attest`, `apatch_attestation_export`, `apatch_events_tail` — **53 tools** (см. `apatch_doctor` → `mcp_health.tool_count`).
- **Control monitor (RFP-005):** sandbox enforce, Cursor hooks (`preToolUse`, `beforeShellExecution`, `beforeMCPExecution`), leases, `apatch sandbox ci-gate`, control-plane lock, trust anchor (enroll + `verify-anchor`), env ratchet, signed policy (`policy sign|verify`), KMS/HSM `CommandKeyProvider`, external inclusion proofs (`verify-inclusion`), dogfood `apatch/**` protected.
- **Artifact-anchored intent (RFP-006):** `Artifact{kind,id,ref?,content_hash?}`, `session start --artifact`, `artifacts[]` в session/attest, `trustchain history --artifact`, `trustchain coverage`, op_id → artifact reverse map, auto-stamp на ledger commit.
- **Agent self-service:** `artifact_anchored_intent` и `sandbox_agent_protocol` в `apatch_doctor`; `tooling_refresh` после правок в apatch-репо; расширенные MCP docstrings и `PARAM_DOCS`.
- **Orchestration:** `apatch_orchestrate`, `apatch_pipeline_run`, `apatch_db_run`, `apatch_refactor_run`, engineering-pipeline manifest с `artifacts[]`.
- **MCP health:** canonical Cursor config, `apatch mcp sync`, interpreter drift repair.

### Changed

- Strip / phase / apply / rollback маршрутизируются через `MutationRuntime` при `governed_mode`.
- `trustchain_intent` в pipeline пишет `artifacts[]` (legacy `adr` нормализуется).
- Документация: `AGENTS.template.md` как deployed runtime contract; RFP-005/006 активные; tool count 52→53.

### Fixed

- TrustChain `apatch_attest` больше не блокируется enforcement policy hook.
- MCP stdio transport hardening (stderr/encoding).
- Многочисленные strip/hook/auto_wire/codegen fixes из MCP dogfood на consumer monorepos.
- `test_warn_filters` на Python 3.14; все MCP params имеют schema descriptions.

### Release criteria (mapping)

| Документ | Статус в 0.2.0 |
|----------|----------------|
| RFP-004 Mutation Runtime | ✅ реализован |
| RFP-005 monitor **0.2** | ✅ код; org: branch protection required check |
| RFP-005 monitor **0.3** | ✅ скелет (policy, KMS, inclusion); TCB — отдельно |
| RFP-006 **6.1–6.2** | ✅ artifact + coverage; CI-gate «вне артефакта» — опц. |

## [0.1.0] — 2026-05

Начальный публичный пакет: AST-fuzzy apply, strip, compile, TrustChain bootstrap, базовый MCP.
