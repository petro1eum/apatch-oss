# SPEC-MONITOR-TAIL-1 - Monitor & spec_run hardening (audit tail)

> **Status:** Draft v1 - **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-MONITOR-TAIL-1`
> **Anchors:** RFP-005 (reference monitor), RFP-009 (spec run)

## 0. Motivation

Full-suite audit (2026-06-10) found 4 red tests - all pre-existing (they also fail on
commit f8a3cfb). Three root causes:

1. **Ring-1 bypass:** `_normalize_rel` uses `lstrip` with a character set, so the path
   `.apatch/sandbox.json` becomes `apatch/sandbox.json`. The `evaluate_pre_tool_use`
   hook receives the already-normalized path, `is_control_path` no longer matches it,
   and a direct StrReplace into monitor control-plane files is ALLOWED in consumer
   repos (protected globs there are `src/**` etc., not `apatch/**`).
2. **CI gate self-heal:** `run_sandbox_ci_gate` passes `auto_revert=False`, but
   `run_sandbox_watch_once` re-enables revert from config (`watcher: revert` is now the
   default): the gate reverts the violation and returns `gate: passed` instead of
   `failed`. The Ring-2 boundary silently heals a bypass instead of failing it.
3. **spec_run resume:** after `SPEC_RUN_BLOCKED` and a manual fix (verify already green,
   the needle effect already present in the file) resume fails with `find_text not
   found` - the verify precheck skips mutations only when needles are empty, ignoring
   the resume-after-failure case.

## R1 Dotfile-preserving path normalization in the agent hook channel

`_normalize_rel` preserves a leading dot in path components (`.apatch/`, `.trustchain/`,
`.cursor/`): only the `./` prefix (possibly repeated) and leading `/` are stripped.
Paths from `_extract_paths_from_hook_input` reach `evaluate_write_policy` with the dot
intact, `is_control_path` matches, and `evaluate_pre_tool_use` returns deny for
control-plane mutations under enforce.

(verify: python3 -m pytest tests/test_sandbox.py -q)

## R2 CI gate is report-only - violations fail the gate, never self-heal

`run_sandbox_watch_once` accepts `report_only: bool = False`; when True, auto-revert is
never performed regardless of the config watcher. `run_sandbox_ci_gate` calls the audit
with `report_only=True`: unleased protected mutations yield `ok: false`, `gate: failed`
and the violation list (including base mode - diff against the merge base). Local
watcher/audit (`apatch_sandbox_audit`, daemon) keeps the existing config-driven revert
behavior.

(verify: python3 -m pytest tests/test_sandbox_ci_gate.py -q)

## R3 spec_run resume after manual fix - verify precheck for previously failed Rk

`_run_one_requirement` receives `previously_failed` (derived from `per_rk` state). If
the requirement verify is already green AND the Rk previously failed, mutations are
skipped (`verify_precheck_passed`) and the requirement is finalized (attested) within
the same governed session. Fresh-run semantics are unchanged: with needles set and a
non-failed Rk, mutations remain mandatory even when verify is green.

(verify: python3 -m pytest tests/test_spec_run.py -q)

## Non-goals

- GitHub Actions startup_failure (billing issue, not code).
- Changing `apatch_sandbox_audit` / watcher daemon behavior (config-driven revert stays).
- Policy signing and external inclusion anchoring - operator steps, not code.