# SPEC-REMOTE-SSH-CORE-1 — Remote target model and safety gates

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-REMOTE-SSH-CORE-1`  
> **Anchors:** [RFP-029](../RFP-029-remote-ssh-workspaces.md)

## 0. Motivation

Before apatch can execute a remote governed workflow, it needs a precise target model.
A string like `/srv/example/search-workspace` must not be interpreted as local when it is meant
for SSH. A string like `ssh://example-search-host/srv/example/search-workspace` must parse into a stable
identity that can be checked against host/root allowlists and carried into error/audit
payloads.

## R0 RFP traceability gate (meta)

RFP-029 Acceptance rows mapped in this spec:

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A29-A | R1 | covered |
| A29-B | R1 | covered |
| A29-C | — | waiver: SPEC-REMOTE-SSH-WORKER-1 |
| A29-D | — | waiver: SPEC-REMOTE-SSH-WORKER-1 |
| A29-E | R4 | covered |
| A29-F | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-G | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-H | — | waiver: SPEC-REMOTE-SSH-VERIFY-1 |
| A29-I | — | waiver: SPEC-REMOTE-SSH-VERIFY-1 |
| A29-J | R2 | covered (allowlist; R3/R4 exercise guard and data boundary) |
| A29-K | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-L | R5 | covered |
| A29-O | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-M | R0 | covered by fake/local tests |
| A29-N | — | waiver: SPEC-REMOTE-SSH-MCP-1 docs |

(verify: python3 -m pytest tests/test_remote_target.py::test_r0_remote_core_rfp_coverage -q)

## R1 Remote target parser

`parse_remote_target(value)` accepts `ssh://host/abs/path` and `host:/abs/path`, rejects
relative remote paths, and returns `RemoteTarget(host, path, uri, display_id, workspace_id)`.
Local paths continue to resolve through existing local workspace logic and are not silently
converted to remote.

(verify: python3 -m pytest tests/test_remote_target.py::test_r1_parse_remote_targets -q)

## R2 Host and root allowlists

`load_remote_policy(root)` reads `.apatch/remote.json` or user config with allowed hosts
and allowed remote root globs. `RemoteTarget` validation returns `REMOTE_ROOT_DENIED` when
the host or path is outside policy. Default policy is deny-by-default for mutating tools;
read-only doctor may return diagnostics with `allowed=false`.

(verify: python3 -m pytest tests/test_remote_target.py::test_r2_remote_allowlist_policy -q)

## R3 Local-vs-remote guard

Mutating workflows reject a remote-looking POSIX path (`/home/ubuntu/...`) when it does
not exist locally and no remote URI/host alias is supplied. The error is
`WORKSPACE_NOT_LOCAL` with next actions: use `ssh://host/path`, mount/sync locally, or run
MCP on the remote host.

(verify: python3 -m pytest tests/test_remote_target.py::test_r3_workspace_not_local_guard -q)

## R4 No shell interpolation boundary

Remote core exposes only structured target and argv data to the transport layer. Paths,
needles, and verify argv are never concatenated into a shell command by core helpers.
Suspicious values are preserved as JSON data and tested against injection payloads.

(verify: python3 -m pytest tests/test_remote_target.py::test_r4_no_shell_interpolation_helpers -q)

## R5 Typed remote errors

Remote errors normalize to apatch failure taxonomy fields: `ok=false`, `error_type`,
`recoverable`, `recommended_action`, and `state_update`. Required types:
`REMOTE_UNREACHABLE`, `REMOTE_APATCH_MISSING`, `REMOTE_VERSION_SKEW`,
`REMOTE_WORKSPACE_NOT_GIT`, `REMOTE_ROOT_DENIED`, `REMOTE_PROTOCOL_ERROR`,
`WORKSPACE_NOT_LOCAL`.

(verify: python3 -m pytest tests/test_remote_target.py::test_r5_typed_remote_errors -q)

## Non-goals

- Opening SSH connections (SPEC-REMOTE-SSH-WORKER-1).
- Routing MCP tools (SPEC-REMOTE-SSH-MCP-1).
- Running remote verify (SPEC-REMOTE-SSH-VERIFY-1).