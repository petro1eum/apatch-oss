# RFP-036 -- Transactional Multi-Agent Session Safety

> **Status:** Proposed (July 12, 2026)
> **Scope:** governed MCP session identity, lane routing, state CAS, writer isolation, ephemeral ownership, and deterministic recovery
> **Executable contract:** `docs/specs/SPEC-APATCH-TRANSACTIONAL-SESSIONS-1.md`

## Context

APatch currently protects intent, mutations, verification, and attestation, but
manual MCP workflows resolve several operations through the currently active
lane. Independent MCP processes can therefore switch the lane between
`generate`, `apply`, `verify`, `attest`, and `session_end`. Observed
failures included cross-spec mutation, attestation on the wrong requirement,
overwritten patch logs, orphan leases after rebind, and cleanup of a different
session than the one reported.

The patch engine is not the product boundary. The required boundary is an
atomic governed transaction: every state-changing operation must prove which
session owns it, which immutable patch artifact it consumes, and which workspace
writer lease protects it.

## Decision

A manual governed session SHALL return an opaque session capability consisting
of a globally unique `session_id` and a secret `session_token`. The plaintext
token is returned once and never persisted. MCP hot-path operations SHALL bind
to that capability. Internal one-call orchestrators such as `spec_run` and
`execute_next` may carry the capability in process memory.

Session state SHALL use atomic replace and monotonic revision. Every bound
operation SHALL verify the expected session before execution and before writing
its resulting phase. A mismatch is a recoverable `SESSION_MISMATCH` or
`SESSION_TOKEN_MISMATCH`, never a mutation of whichever session happens to be
current.

Lane routing SHALL resolve an explicit session to exactly one registry row.
Multiple active lanes without an explicit binding are ambiguous and SHALL fail
closed; newest-lane selection is forbidden.

The original implementation made the writer capability workspace-global.
[RFP-042](./RFP-042-path-scoped-writer-leases.md) supersedes that admission
rule: different sessions may mutate disjoint canonical path sets concurrently,
while exact and directory-prefix overlap still fails before mutation. A live
lease is never merged across sessions or stolen.

Generated patch JSONL SHALL be session-owned and content-bound. Generation may
not overwrite an artifact owned by another session, and apply SHALL reject a
log whose owner or content digest differs from its registry contract.

Lifecycle helpers SHALL close only the session they successfully opened.
`rebind-stale` SHALL check `open_session`, skip foreign sessions, and execute
verify, attest, and close against one captured capability. Read-only status and
lint calls SHALL not advance or overwrite governed lifecycle state.

## Compatibility

CLI and one-call orchestrators remain source compatible. Manual MCP calls may
omit credentials only when no governed session is active. Once a tokenized
session exists, missing credentials fail with an actionable response containing
the expected `session_id`, never the token. The operational manual is
`docs/transactional-mcp-sessions.md`.

## Acceptance

| ID | Requirement | Level |
|---|---|---|
| TS-A | Session start creates collision-resistant identity, returns a one-time secret token, and persists only its digest. | MUST |
| TS-B | Explicit session binding resolves the owning lane; ambiguous newest-lane fallback is removed. | MUST |
| TS-C | Manual MCP generate/apply/verify/attest/end calls reject missing, mismatched, or invalid capabilities without changing foreign state. | MUST |
| TS-D | Session phase writes use atomic replace, monotonic revision, and pre/post session CAS. | MUST |
| TS-E | Writer leases isolate live mutators across lanes: overlapping canonical paths serialize, while disjoint path sets may proceed concurrently; leases cannot be merged across sessions. | MUST |
| TS-F | Patch JSONL is session-owned and content-bound; foreign ownership or digest drift fails before apply. | MUST |
| TS-G | session_end unregisters and cleans only its expected session. | MUST |
| TS-H | rebind-stale verifies open success and never verifies, attests, or closes a foreign session. | MUST |
| TS-I | Read-only doctor/status/lint calls do not mutate lifecycle state. | MUST |
| TS-J | Multiprocess regressions, MCP schema parity, documentation, and existing orchestration tests pass. | MUST |

## Non-goals

- No distributed consensus across different workspaces.
- No concurrent writes to one checkout.
- No automatic takeover of a live session.
- No weakening of TrustChain, sandbox, or executable-spec verification.
