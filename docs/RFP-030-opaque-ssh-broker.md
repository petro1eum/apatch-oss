# RFP-030 — Opaque SSH broker for local MCP remote work

> **Status:** Draft v1 · **Date:** 2026-06-20 · **Owner:** apatch core
> **Package context:** 0.7.x — make local MCP safely operate SSH workspaces without exposing SSH details to the agent
> **Depends on:** [RFP-029](./RFP-029-remote-ssh-workspaces.md) (local MCP control plane for SSH workspaces) · [RFP-027](./RFP-027-agent-ux-recovery.md) (agent recovery) · [RFP-016](./RFP-016-runtime-hygiene.md) (runtime hygiene)
> **Origin:** Product hardening discussion — the desired UX is that an agent asks apatch to operate a remote workspace alias, while SSH host/user/key/path details remain hidden in local policy.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A30-A | Agents can address a remote workspace by opaque alias such as `search-example`; the alias resolves through local `.apatch/remote.json` or `APATCH_REMOTE_POLICY` | MUST |
| A30-B | The alias policy stores SSH host, remote root, optional Python executable, SSH argv, timeout, display label, redaction mode, and allowed operations | MUST |
| A30-C | Unknown aliases fail closed with `REMOTE_ALIAS_NOT_FOUND`; malformed policy fails closed with `REMOTE_POLICY_INVALID` | MUST |
| A30-D | Policy enforces host and remote-root allowlists before any SSH transport is created | MUST |
| A30-E | Alias targets redact host, remote root, direct URI, and transport details from MCP responses, timelines, and fake-transport audit calls by default | MUST |
| A30-F | Redaction replaces leaked host/path strings inside nested remote results and errors before the response returns to the agent | MUST |
| A30-G | Direct explicit targets (`ssh://host/path`, `host:/path`) remain supported for debugging and are not automatically redacted | SHOULD |
| A30-H | Alias policy can deny individual internal operations with `REMOTE_OPERATION_DENIED`, stopping before the denied step | MUST |
| A30-I | For locked aliases, policy transport defaults override agent-supplied `python`, `ssh_args`, and `timeout_sec` | MUST |
| A30-J | `apatch_remote_task_run` remains one user-facing MCP call; alias resolution, policy enforcement, redaction, transport selection, and timeline production happen inside apatch | MUST |
| A30-K | Tests use fake transport and monkeypatched SSH transport; CI does not require real SSH, network, or secrets | MUST |
| A30-L | Documentation explains the broker model: local MCP is the only visible tool boundary, SSH is an internal capability, and agents should not call raw SSH for governed work | SHOULD |
| A30-M | New users can create and validate a policy through `apatch remote init/validate` from minimal inputs (`alias`, `host`, `path`, optional health URL), without hand-writing JSON | MUST |
| A30-N | When a remote machine cannot fetch a repository because credentials live locally, agents can use `apatch_remote_source_handoff` / `apatch remote handoff`; responses do not reveal GitHub credential topology, SSH pipe details, host, or remote path | MUST |

Canonical ids: this section.

---

## 1. Problem

RFP-029 makes remote SSH workspaces possible through local MCP, but a direct target such as
`ssh://example-search-host/srv/example/search-workspace` still exposes too much to the agent:

- host aliases and usernames;
- remote directory layout;
- jump-host flags;
- runtime Python path;
- operational details that encourage agents to fall back to raw `ssh` commands.

That is the wrong security boundary. The agent should not “know SSH”; it should know an
apatch-governed workspace capability.

The desired call shape is:

```python
apatch_remote_task_run(
    remote_target="search-example",
    intent="Fix engineering system filter visibility",
    plan={"needles": [...]},
    verify="npm test -- MatchingTool",
)
```

Only local apatch policy knows what `search-example` means.

---

## 2. Product goal

Turn apatch into an SSH safety broker:

```text
agent intent
  -> local apatch MCP tool
    -> local alias policy
      -> internal SSH transport
        -> remote apatch worker
```

The agent gets a governed response shape and timeline, not SSH primitives. The human can
approve one high-level intent, and apatch keeps the sensitive transport details inside its
local policy boundary.

---

## 3. Policy file

Local control workspace:

```json
{
  "allowed_hosts": ["yc-*"],
  "allowed_roots": ["/home/ubuntu/projects/*"],
  "targets": {
    "search-example": {
      "host": "example-search-host",
      "path": "/srv/example/search-workspace",
      "display": "Search main",
      "python": "/opt/remote/bin/python",
      "ssh_args": ["-J", "bastion"],
      "timeout_sec": 600,
      "redact": true,
      "allowed_operations": [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end"
      ]
    }
  }
}
```

`APATCH_REMOTE_POLICY=/path/to/remote.json` may override discovery for machine-local secrets
or user-level policy.

---

## 4. Security model

### 4.1 Agent-visible data

Allowed:

- alias (`search-example`);
- display label;
- stable workspace id;
- governed operation names;
- sanitized result/timeline;
- typed errors and recommended actions.

Hidden by default:

- SSH host/user/port/jump host;
- remote root path;
- direct `ssh://...` URI;
- remote Python path;
- SSH argv;
- secrets in stdout/stderr.

### 4.2 Fail-closed behavior

- Missing alias: `REMOTE_ALIAS_NOT_FOUND`.
- Bad JSON/schema: `REMOTE_POLICY_INVALID`.
- Host outside allowlist: `REMOTE_HOST_DENIED`.
- Root outside allowlist: `REMOTE_ROOT_DENIED`.
- Internal step not allowed: `REMOTE_OPERATION_DENIED`.

### 4.3 Transport override policy

Alias entries are locked by default: agent-supplied `python`, `ssh_args`, and `timeout_sec`
do not override policy. Direct explicit targets may use those arguments for debugging.

---

## 5. MVP implementation

RFP-030 MVP adds:

- `apatch.remote.policy.load_remote_policy`;
- `apatch.remote.policy.resolve_remote_target`;
- `RemoteTarget` redaction metadata and transport defaults;
- `remote_task_run(..., policy_root=...)`;
- redaction of target metadata and nested timeline/result strings;
- MCP `apatch_remote_task_run(target_dir=...)` policy root;
- tests for alias resolution, allowlists, redaction, operation denial, and policy transport defaults.

---

## 6. Non-goals

- Managing SSH keys.
- Storing secrets in repo-local `.apatch/remote.json`; use user-level policy/env override when needed.
- Replacing enterprise bastion/SSH certificate policy.
- Allowing arbitrary SSH commands through MCP.
- Teaching agents to bypass the broker with `git clone`, `scp`, or `tar | ssh`.
