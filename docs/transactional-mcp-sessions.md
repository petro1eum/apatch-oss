# Transactional MCP sessions

Manual multi-call workflows use an opaque capability so every mutation, verify,
attestation, and cleanup operation stays bound to the session that started it.

## Start once

Call:

```text
apatch_session_start(
  intent="implement the change",
  artifacts=["spec:SPEC-X#R1@sha256:..."]
)
```

The authoritative response capability is:

```json
{
  "session_capability": {
    "session_id": "apatch_sess_...",
    "session_token": "<one-time secret>"
  }
}
```

The top-level `session_token` and `session` view are compatibility projections.
During state write-behind, `session` can still show the prior projection, so
clients and remote orchestration must prefer `session_capability`. Store both
capability values only in caller memory. APatch persists the token's SHA-256
digest, never the secret. Do not write it to manifests, patch logs,
documentation, or source control.

## Bind every hot-path call

Pass both `governed_session_id` and `session_token` to:

- `apatch_generate` and `apatch_generate_batch`
- `apatch_apply` and `apatch_apply_session`
- `apatch_verify_run`
- `apatch_attest`
- `apatch_session_end`

A missing capability on a tokenized active session fails with
`SESSION_BINDING_REQUIRED`. A wrong token, replacement session, or stale state
revision fails without updating the active session.

## Writer and patch ownership

Planning and read-only inspection may run in parallel. Project mutation uses one
workspace-global write lease. The lease owner is the governed session, not the
lane name or process id, so two sessions cannot merge their write scopes.

Generated JSONL is routed to `.apatch/tmp/<session_id>/`. Its artifact registry
record contains the owner session and SHA-256 digest. Apply rejects an
unregistered, foreign, or modified log before touching project files.


## Atomic chunks and verification

All patch steps for one target file remain in one ordered chunk, even when the
log is interleaved (`A -> B -> A`). `chunk_max_files` is therefore a packing
target and an atomic group may exceed it.

A successful chunk creates one TrustChain mutation record after verification,
not one record per textual patch. Normal signing validates the new object and
HEAD in O(1); full ledger traversal is explicit audit/recovery only.

Verification children do not inherit `APATCH_MCP_TARGET_POLICY=alias_only`.
That policy belongs to the MCP server boundary and would otherwise reject
pytest temporary workspaces. Restart the MCP server after changing this runtime
code so synchronous calls load the new function definitions.

The complete no-regression contract is
[governed-runtime-invariants.md](./governed-runtime-invariants.md).
## End exactly what was started

Use the same capability for `apatch_session_end`. Cleanup unregisters the exact
lane row, releases only that session's artifact leases, and deletes only its
eligible ephemeral files. It cannot close a replacement session.

## One-call workflows

`apatch_execute_next` and `apatch_spec_run` remain the preferred executable
SPEC interfaces. They create and carry the capability internally, so callers do
not add manual token plumbing. The explicit fields are for manual multi-call MCP
workflows and integrations that intentionally split the lifecycle.

For calls that can outlive a client timeout, generate one stable `request_id`
(UUID is sufficient) and reuse that exact value only when retrying the same
operation and payload. APatch journals the request and payload hashes before
execution. A completed retry returns `idempotent_replay=true`; an active session
also receives a newly rotated `session_capability`. Reusing a request id for a
different payload fails with `REQUEST_ID_CONFLICT`. The journal never stores the
plaintext request id or capability token.

After an MCP process crash, the retry checks the original owner PID and the
request hash embedded in atomic session state. An exact surviving session is
recovered, including the narrow state-written/registry-not-yet-written window.
If the old PID is dead and no session was created, the new process claims and
executes the request. A live owner remains `REQUEST_IN_PROGRESS`.

`apatch_execute_next` also accepts an explicit `governed_session_id` and
`session_token` pair when an integration intentionally splits mutate and
finalize calls. Supplying only one field fails before verify or mutation.

File-drift-only stale requirements can be re-anchored selectively:
`apatch_rebind_stale(requirement_ids=['R1','R3'],
exclude_requirement_ids=['R3'])`. Filters are validated before a session opens;
exclusion wins and every unselected stale requirement remains unchanged.

## Remote task orchestration

`apatch_remote_task_run` carries the session capability internally across
generate, apply, verify, attest, and cleanup. Each task uses
`.apatch/tmp/<session_id>/remote-patches.jsonl` and a task-scoped apply-state
file, so a crashed task cannot reuse or block another task's staging state.

All apply chunks execute inside one remote-worker process. This is required
because the sandbox lease validates both the governed-session owner and the
live worker PID; client-side process-per-chunk execution would invalidate the
lease between chunks. Capability tokens are always redacted from the returned
timeline.

`apatch_resume_session` rotates the capability. With more than one active lane,
call `apatch_resume_session(governed_session_id="apatch_sess_...")`; the exact id
selects only its registered lane, while omission fails with `SESSION_AMBIGUOUS`.
Once its response is present, that fresh `session_capability` supersedes every
id/token projection returned by the earlier `apatch_session_start`, including
when both calls occur in one remote noop-reattest timeline. Verify, noop-attest,
attest, and session-end must all use the rotated token; preferring the start token
deterministically produces `SESSION_TOKEN_MISMATCH`. Invalid or ended ids fail
without rotating any lane.

## Read-only calls

Doctor, workspace inspection, index queries, session and SPEC status, lint,
coverage, and other query surfaces are projections. Success or failure on these
surfaces does not advance or overwrite the governed lifecycle state.

Atomic `.lock` sidecars are synchronization primitives, not managed artifacts.
GC never inventories or deletes them. The reality ledger and local conformance
or remote policy files use canonical non-ephemeral classes, so safe GC cannot
remove control-plane truth or configuration.

## Recovery

- `SESSION_AMBIGUOUS`: pass the exact `governed_session_id` to
  `apatch_resume_session`; use the newly returned capability for every later hot-path
  call instead of relying on lane recency.
- `SESSION_REVISION_MISMATCH`: another operation changed the same session;
  inspect current status and retry intentionally.
- `LEASE_CONFLICT`: wait for the owning session to finish or use a separate
  worktree; never steal the lease.
- `PATCH_LOG_DIGEST_MISMATCH`: regenerate the patch log in the owning session.
- `SESSION_TOKEN_MISMATCH` after resume: discard the pre-resume projection and
  use the exact `session_capability` returned by `apatch_resume_session`; do not
  close or reopen the governed session merely to obtain another token.
