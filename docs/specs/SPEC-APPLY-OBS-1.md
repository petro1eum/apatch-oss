# SPEC-APPLY-OBS-1 - apply_session resume integrity and verify observability

> **apatch artifact:** `spec:SPEC-APPLY-OBS-1`
> **Status:** attested (2026-06-10)
> **Origin:** SPEC-MONITOR-TAIL-1 dogfood post-mortem (2026-06-10)

Two infrastructure defects in the governed apply pipeline made spec_run failures
undebuggable and resumes unsafe. Both were found while executing
SPEC-MONITOR-TAIL-1: the R2 patch was correct, but the pipeline reported a
misleading verify failure and then silently no-op'ed on resume.

## R1 No silent no-op resume after verify rollback

When a chunk verify fails and files are rolled back, `run_apply_session` must NOT
advance `chunk_index`. The failed session state is cleared so the next call
re-applies the chunk, instead of resuming a "completed" session that applies
nothing while reporting ok=true and emitting a bogus MutationApplied event.

(verify: python3 -m pytest tests/test_apply_resume.py -q)

## R2 Verify failure output must propagate to the caller

`InteractiveTUI._run_verification` must run through `tool_paths.run_shell_verify`
(materialized command plus augmented PATH - same resolution as apatch_verify_run)
and store the failed verify stdout/stderr in `last_verify_output`.
`apply_from_logs` and `run_apply_session` must surface it as `verify_output` so
MCP callers see WHY verify failed: in MCP stdio mode the TUI console writes to
/dev/null and the output was previously lost.

(verify: python3 -m pytest tests/test_verify_output.py -q)

## R3 Deferred verify must hold for the last chunk

`run_apply_session` forced `verify_deferred=False` on the last chunk, demoting a
requested deferred verification to step-level verify. Any chunk with
interdependent steps (e.g. create a module plus its test) could then never
pass: verify ran after each step against a half-applied chunk and rolled it
back. The deferred flag must propagate to `apply_from_logs` unchanged; the TUI
already runs exactly one deferred verification at the end of the chunk and
rolls the whole chunk back on failure.

(verify: python3 -m pytest tests/test_apply_deferred_last_chunk.py -q)