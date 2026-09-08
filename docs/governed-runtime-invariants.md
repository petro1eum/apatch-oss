# Governed runtime reliability invariants

**Status:** implemented and regression-tested (2026-07-14)

This document is the no-regression contract for APatch maintainers and agents.
It consolidates the runtime guarantees introduced by transactional MCP sessions,
atomic apply chunks, canonical control-plane paths, and RFP-038. Read it before
changing session binding, apply chunking, TrustChain commits, verification
subprocesses, or requirement coverage.

## Invariants at a glance

| Area | Required behavior | Primary gate |
|---|---|---|
| Session ownership | Every write uses the exact session id and one-time token returned in `session_capability`. | `tests/test_transactional_sessions.py` |
| Capability rotation | A capability returned by `apatch_resume_session` supersedes the earlier start capability for every subsequent remote operation. | `tests/test_remote_mcp_routing.py` |
| Reconciliation | Reconcile performs at most one CAS write and is revision-idempotent when evidence has not changed. | `tests/test_session_recovery_mcp.py` |
| Apply chunks | All steps touching one file stay in one ordered atomic chunk, including interleaved A -> B -> A logs. | `tests/test_apply_session.py` |
| TrustChain writes | One successful apply chunk produces one signed mutation record for the files that survive verify/rollback. | `tests/test_ledger_hot_path.py` |
| Ledger cost | A normal commit validates only the appended object and HEAD; normal notarization verification uses the local index. | `tests/test_ledger_hot_path.py` |
| Full audit | Historical traversal remains available only through explicit rebuild/audit/recovery. | `tests/test_ledger_hot_path.py` |
| Relative paths | Leading control-plane dots are preserved: `.apatch/**` and `.github/**` must never lose their dot. | `tests/test_layout_paths.py`, `tests/test_spec_coverage.py` |
| Verify isolation | Child verification never inherits the MCP server-only `APATCH_MCP_TARGET_POLICY`. | `tests/test_tool_paths.py` |
| Diagnostics cost | A pytest failure with an exact file does not build a repository-wide symbol index. | `tests/test_contract_edges.py` |

## 1. Session capability and CAS

`apatch_session_start` returns the authoritative capability in:

```json
{
  "session_capability": {
    "session_id": "apatch_sess_...",
    "session_token": "<one-time-secret>"
  }
}
```

The convenience `session` projection can be stale during write-behind and must
not be used as the only source. Manual multi-call clients pass both values to
generate, apply, verify, attest, and session_end. Remote orchestration carries
the same capability internally. If `apatch_resume_session` returns a capability,
that capability is authoritative: never prefer the older id or token retained
from `apatch_session_start`.

The token itself is never persisted. Runtime state stores only its digest.
Reconciliation may legitimately advance the state revision before an operation;
therefore `MutationRuntime` re-captures its binding after state-machine checks.
Do not remove those re-captures or replace exact ownership with lane-recency
heuristics.

Read-only projections must remain lifecycle-neutral. A failed doctor/status/
inspection call cannot turn a verified mutation session into `blocked`.

## 2. Atomic apply and notarization

`chunk_max_files` is a packing target, not permission to split one file.
APatch computes first/last intervals for each target file, merges overlapping
intervals, preserves patch-log order, and then packs those atomic groups.

Notarization happens once after the chunk's verification decision. Its payload
contains only final `outcome=applied` paths. If signing, persisted-record
validation, or HEAD validation fails under enforcement, the whole chunk rolls
back.

Do not restore per-step TrustChain commits. They multiply crypto/filesystem work
and can sign intermediate file states that do not survive the chunk.

## 3. O(1) ledger write path

RFP-038 separates online commit proof from historical audit:

1. Capture chain length and HEAD before append.
2. Sign one mutation payload.
3. Read and validate only the appended object.
4. Verify signature, length transition, and persisted HEAD.
5. Update notarized-index v2 with file hashes and ledger metadata.

Normal apply and `verify_notarization` must not call
`iter_ledger_entries()`, `count_signed_blocks()`, or parse every object.
A full walk is still mandatory when the operator explicitly requests
`rebuild_index=true`, audit, or recovery.

Measured dogfood result: a 23-mutation apply dropped from about four minutes to
about 16 seconds including focused tests. The full regression suite after integration with the current master was
`1497 passed, 1 skipped` in 3:18; that runtime is the tests themselves, not
ledger overhead.

## 4. Canonical paths

Use `apatch.apatch_paths.normalize_rel` for repository-relative paths. Never
use `path.lstrip("./")`: it removes characters, not a prefix, and turns
`.apatch/conformance.json` into `apatch/conformance.json`. That silently
creates false staleness and broken notarization lookups.

## 5. Verification and diagnostics

`build_subprocess_env` removes `APATCH_MCP_TARGET_POLICY`. The variable
belongs to the long-lived MCP routing boundary; inheriting it makes isolated
pytest workspaces look like forbidden cross-workspace targets. The rule applies
to both synchronous verify and async workers after the MCP server reloads.

Pytest diagnostics already carry an exact node and file. Resolve that edge
directly. Repository-wide symbol indexing is reserved for diagnostics that
actually need symbol discovery.

## 6. Evidence and repository hygiene

The following project evidence and policy are source-controlled:

- `manifests/apatch-inclusion.jsonl` — reviewed portable inclusion snapshot for CI;
- `.apatch/reality.jsonl` — append-only observed-reality ledger;
- `.apatch/sandbox.json`;
- `.apatch/enforcement.json`.

Runtime `.apatch/inclusion.jsonl` is ignored; only its reviewed verified subset is
published to the portable manifest. Session patch logs, apply state, leases,
backups, `*.lock`, and ad-hoc root
JSONL are runtime artifacts and must not be committed. `session_end` removes a
registered session JSONL together with its companion `.lock`; `apatch_gc` owns
remaining cleanup.

## 7. Required verification before merge

```bash
python3 -m pytest -q \
  tests/test_transactional_sessions.py \
  tests/test_session_recovery_mcp.py \
  tests/test_apply_session.py \
  tests/test_ledger_hot_path.py \
  tests/test_layout_paths.py \
  tests/test_spec_coverage.py \
  tests/test_tool_paths.py \
  tests/test_contract_edges.py

python3 -m pytest -q
```

For governed changes, also require:

```text
apatch_verify_notarization(staged=true)
apatch_spec_status(spec="SPEC-LEDGER-HOT-PATH-1")
```

The executable SPEC must remain `5/5 attested`. Any intentional change to
these semantics starts with an RFP/SPEC update, not a local optimization.
