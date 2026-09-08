# RFP-042 -- Path-scoped Concurrent Writer Leases

> **Status:** Accepted for implementation (August 26, 2026)
> **Scope:** concurrent governed mutations, rollback isolation, and local ledger ordering
> **Executable contract:** `docs/specs/SPEC-PATH-LEASES-1.md`
> **Supersedes:** RFP-041 concurrent-write non-goal for disjoint write-sets

## Context

APatch 0.8.30 serializes every mutation in a workspace through one
`.apatch/write_lease.json`. The lease contains paths, but `acquire_lease` rejects a
different governed session before comparing those paths. In ProbStates this made an
Apple runtime edit block an independent `spec run-multi --execution-mode
shared_maintenance` over `o_lang/**`, even though planning proved that the two exact
write-sets were disjoint. Agents repeatedly had to negotiate five-minute windows.

The same global assumption leaks into recovery, watcher admission, rollback, and local
TrustChain/index updates. Increasing TTL, adding a wait queue, or teaching agents to
retry would preserve the bottleneck rather than remove it.

## Decision

APatch SHALL keep one atomic workspace lease registry containing multiple leases. Each
lease owns a canonical path set and one exact governed session. Admission runs as one
read/check/write transaction: canonicalize the complete requested write-set, prune only
expired/dead leases, compare against every foreign live lease, then add the whole set or
add nothing. It never waits and never holds per-path locks.

Canonical identity is based on the real workspace root, realpath-resolved existing
components, filesystem-aware case normalization, and segment-aware prefix comparison.
An existing directory or an explicitly requested prefix owns every descendant. Rename
owns source and target; delete owns the deleted path or subtree. Symlink aliases and
registered workspace aliases cannot create a second identity for the same physical path.

Planning and verification are read-only and acquire no writer lease. Every mutation
entry point passes its complete planned paths before its first source write. Inner apply
checks that the exact session lease covers every actual target, preventing a plan/apply
write-set expansion.

Physical backups and rollback remain session-scoped. A governed rollback resolves its
checkpoint only from the exact validated session capability, lane-owned apply cursor,
and backup ownership metadata; it never falls back to the workspace's latest checkpoint.
Missing or mismatched ownership fails before mutation. A rollback acquires only the
backed-up paths and cannot restore or delete a foreign session's files. Local TrustChain
append/checkpoint operations and notarized-index updates use short multiprocess critical
sections so parallel mutation records have one total order without lost updates. Under
the v2 lease registry rollback records a compensating operation; it does not move the
shared ledger HEAD behind another session's commit.

The legacy `.apatch/write_lease.json` is reconciled under the same admission
transaction. A live v1 owner keeps its original capability and remains
workspace-wide until release, death, or expiry; migration never replaces state
that the old process still needs to finish. While real v2 leases are active, a
bounded compatibility barrier makes v1 fail closed with
`APATCH_UPGRADE_REQUIRED`. Its expiry equals the latest live v2 lease, and MCP
startup sweeps every registered alias, so infrastructure state cannot become a
permanent workspace holder. New clients distinguish the barrier from governed
holders and use `.apatch/write_leases.json` as the normal hot path.

## Acceptance

| ID | Requirement | Level |
|---|---|---|
| PL-P0-1 | Two governed sessions atomically acquire and mutate disjoint canonical path sets in one workspace; overlapping exact or prefix paths fail before source mutation with exact owner/path diagnostics. | MUST |
| PL-P0-2 | Canonicalization covers real workspace aliases, existing and not-yet-created paths, filesystem case behavior, symlinks, rename source+target, delete, and file-vs-directory ownership. | MUST |
| PL-P0-3 | Multi-path admission is all-or-nothing and non-waiting; path ordering cannot deadlock or leave a partial lease. | MUST |
| PL-P0-4 | `spec_run`, `spec_run_multi`, `execute_next`, `apply_session`, shared maintenance, strip/compile mutation scopes, watcher, and sandbox use the same path-lease contract before first write. | MUST |
| PL-P0-5 | Rollback resolves checkpoint strictly from the supplied governed capability and owned lane/backup metadata, refuses missing or foreign ownership before mutation, and cannot change a concurrent disjoint session's files, checkpoint, lease, or TrustChain record. | MUST |
| PL-P0-6 | TrustChain append/checkpoint and notarized-index updates are multiprocess-safe; parallel commits are both retained and remain session-bound. | MUST |
| PL-P0-7 | Expiry, dead PID cleanup, finalization, and exact recovery release only owned leases; a stale lease never blocks unrelated paths. | MUST |
| PL-P0-8 | A live legacy owner remains operable and globally exclusive until completion; bounded v2 barriers fail old clients closed with upgrade guidance and self-clear after release/expiry across registered aliases. | MUST |
| PL-P0-9 | Read-only planning, lint, status, and verification acquire no writer lease. | MUST |
| PL-P0-10 | A multiprocess load test with at least 32 disjoint short sessions proves concurrent admission and completion without global serialization or lost registry/ledger updates. | MUST |
| PL-P0-11 | The IDE bootstrap replaces itself with the isolated interpreter and command from canonical `.apatch/mcp.json`; doctor exposes loaded runtime, catalog, and writer protocol v2. A stale writer fails with actionable reload guidance before it creates a draft session or reaches apply. Two disjoint SPEC writers succeed while the v2 compatibility guard is live. | MUST |

## Non-goals

- Concurrent mutation of the same file or overlapping directory trees.
- Waiting queues, automatic mutation windows, or lease stealing from a live owner.
- Distributed consensus across physically different workspaces or remote hosts.
- Concurrent writes to one non-transactional third-party service outside APatch's local
  workspace and TrustChain boundaries.
