# RFP-029 — Remote SSH workspaces implementation chain

> **Parent:** [RFP-029-remote-ssh-workspaces.md](../RFP-029-remote-ssh-workspaces.md)  
> **Authoring:** [spec-authoring.md](../spec-authoring.md) · lint each SPEC with `apatch_spec_lint` before implementation.

Implement in order. The first two specs are pure local/fake-transport work and do not require real SSH in CI.

```text
SPEC-REMOTE-SSH-CORE-1
        ↓
SPEC-REMOTE-SSH-WORKER-1
        ↓
SPEC-REMOTE-SSH-MCP-1
        ↓
SPEC-REMOTE-SSH-VERIFY-1
```

| Order | Spec | Delivers | Depends on |
|-------|------|----------|------------|
| 1 | [SPEC-REMOTE-SSH-CORE-1](./SPEC-REMOTE-SSH-CORE-1.md) | target URI parser, remote config, allowlists, local-vs-remote guard, typed remote errors | RFP-029 |
| 2 | [SPEC-REMOTE-SSH-WORKER-1](./SPEC-REMOTE-SSH-WORKER-1.md) | SSH transport interface, JSON worker protocol, bootstrap/doctor handshake, fake transport tests | CORE |
| 3 | [SPEC-REMOTE-SSH-MCP-1](./SPEC-REMOTE-SSH-MCP-1.md) | MCP/workflow routing for remote governed operations, remote `.apatch` state, audit metadata, intent-level autopilot approval boundary | WORKER |
| 4 | [SPEC-REMOTE-SSH-VERIFY-1](./SPEC-REMOTE-SSH-VERIFY-1.md) | remote verify, logs, diagnostics, async/poll, disconnect resume | MCP |

## Suggested module layout

| Module | Spec | Role |
|--------|------|------|
| `apatch/remote/target.py` | CORE | `RemoteTarget`, URI parser, canonical identity |
| `apatch/remote/config.py` | CORE | host/root allowlists, config loading |
| `apatch/remote/errors.py` | CORE | typed remote errors and response helpers |
| `apatch/remote/transport.py` | WORKER | SSH/fake transport interface, JSON round-trip |
| `apatch/remote/worker.py` | WORKER | remote Python entrypoint, operation dispatch |
| `apatch/remote/bootstrap.py` | WORKER | remote doctor/bootstrap checks |
| `apatch/remote/runtime.py` | MCP | local facade routing existing workflows to remote worker |
| `apatch/remote/orchestrator.py` | MCP | high-level remote task runner that executes internal governed steps under one approval intent |
| `apatch/remote/verify.py` | VERIFY | log bounds, diagnostics normalization, async/poll helpers |
| `apatch/mcp/server.py` | MCP/VERIFY | accepts remote targets and returns same MCP response shape |
| `apatch/cli.py` | CORE/WORKER | `apatch remote doctor/bootstrap` CLI |

## Test plan

| Test file | Covers |
|-----------|--------|
| `tests/test_remote_target.py` | URI parser, canonical identity, allowlists, typed local/remote guards |
| `tests/test_remote_worker_protocol.py` | JSON envelope, fake transport, non-JSON stdout, version skew |
| `tests/test_remote_mcp_routing.py` | session/generate/apply/attest route to fake remote worker, never touch local `.apatch` as workspace, and expose one intent-level autopilot run |
| `tests/test_remote_verify.py` | remote verify argv, bounded logs, baseline compare, disconnect resume |

## Dogfood path

1. Implement CORE and WORKER with fake transport only.
2. Add a manual `example-search-host` smoke script under docs or a skipped test marker; do not require real SSH in CI.
3. After MCP routing works with fake transport, dogfood against one remote repo and capture the transcript in RFP-029 follow-up notes.

## Non-goals for this chain

- Hosted remote MCP.
- Generic SSH shell tool.
- Auto-installing remote dependencies.
- Remote CI replacement.
