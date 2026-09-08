# RFP-029 — Local MCP control plane for SSH remote workspaces

> **Status:** Draft v1 · **Date:** 2026-06-20 · **Owner:** apatch core
> **Package context:** 0.7.x — make local Codex/Cursor MCP operate remote repos over SSH without losing apatch governance
> **Depends on:** [RFP-006](./RFP-006-artifact-anchored-intent.md) (artifact sessions) · [RFP-007](./RFP-007-executable-specifications.md) (specs) · [RFP-016](./RFP-016-runtime-hygiene.md) (registry/GC) · [RFP-019](./RFP-019-mcp-scale-lifecycle.md) (MCP lifecycle) · [RFP-027](./RFP-027-agent-ux-recovery.md) (agent recovery)
> **Origin:** Field evidence — an agent had local `mcp__apatch` on macOS, but the target repo lived at `/srv/example/search-workspace` behind SSH. The agent mixed local MCP state with ad-hoc remote shell commands and hit the wrong workflow.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A29-A | Local MCP remains the user-facing control plane: Codex/Cursor connects to a local `apatch` MCP server, while `ssh://host/path` targets execute on the remote filesystem | MUST |
| A29-B | `ssh://<host>/<abs-path>` and `host:/abs/path` targets parse into a canonical `RemoteTarget` with host alias, absolute path, display id, and stable workspace identity | MUST |
| A29-C | `apatch_remote_doctor` / `apatch_doctor(target='ssh://…')` verifies SSH reachability, remote Python, git root, apatch availability, remote `.apatch` health, and version/fingerprint compatibility | MUST |
| A29-D | Remote bootstrap prepares a remote worker without ad-hoc shell editing: it checks/install-instructions only when apatch is missing, creates no repo mutations except remote `.apatch` runtime state, and returns typed remediation steps | MUST |
| A29-E | Local MCP talks to the remote worker through a JSON protocol over SSH stdin/stdout; user input is data, not shell syntax, so command injection through target path, verify command, or needles is blocked | MUST |
| A29-F | All governed runtime state for a remote repo lives on the remote repo: `.apatch/session_state.json`, registry, leases, backups, apply/spec_run state, and TrustChain notarization context | MUST |
| A29-G | Mutating MCP tools route remote targets through the worker (`session_start`, `generate_batch`, `simulate`, `apply_session`, `rollback`, `verify_run`, `attest`, `session_end`) and reject mixed local/remote state | MUST |
| A29-H | Remote verify runs on the remote host, supports argv form, baseline capture/compare, async/polling, structured diagnostics, and bounded log streaming back to local MCP | MUST |
| A29-I | SSH interruption is recoverable: a subsequent call re-reads remote session state and can resume, verify, rollback, or session_end without local ghost state | MUST |
| A29-J | Security policy is explicit: host allowlist, remote root allowlist, environment redaction, no secret echoing, no implicit `pip install`, and clear audit of local controller + remote executor identity | MUST |
| A29-K | Trust/audit payloads include remote execution metadata (`remote_host`, `remote_root`, remote git HEAD, local controller id) without treating the local Mac path as the workspace | SHOULD |
| A29-L | UX errors are typed and actionable: `REMOTE_UNREACHABLE`, `REMOTE_APATCH_MISSING`, `REMOTE_VERSION_SKEW`, `REMOTE_WORKSPACE_NOT_GIT`, `REMOTE_ROOT_DENIED`, `REMOTE_PROTOCOL_ERROR`, `REMOTE_VERIFY_FAILED` | MUST |
| A29-M | Tests use a fake SSH transport / local subprocess harness; CI does not require a real SSH server or network access | MUST |
| A29-N | Documentation includes the Codex setup pattern: local MCP config stays local, remote work is selected by target URI, not by changing Codex working directory | SHOULD |
| A29-O | Agent autopilot approval minimization: a normal governed remote task has one user-facing approval intent, then an apatch orchestrator performs internal doctor/lint/plan/simulate/apply/verify/attest/session_end steps without re-prompting for every sub-tool; it stops only on risk escalation, policy boundary, or typed failure | MUST |

Canonical ids: this section.

---

## 1. Problem

A local MCP server is excellent for IDE integration: one local process, stable tools, no per-chat server setup, and no need to expose a remote service to the network. But apatch is filesystem-governed. It does not merely edit text; it owns session state, registry, leases, backups, verification, and attestations.

When a repo lives behind SSH, a naive agent tends to do this:

```text
local Codex → local mcp__apatch    (sees Mac filesystem)
local Codex → ssh host "commands"  (sees remote filesystem)
```

That split breaks apatch's invariants:

- `target_dir=/home/ubuntu/projects/X` is meaningful on the remote host, not on macOS.
- local `.apatch` state would describe a different filesystem than the files being changed.
- verify runs in the wrong environment unless manually tunneled.
- rollback/backups/leases are not co-located with the mutation.
- TrustChain evidence cannot honestly describe where execution happened.
- approval prompts multiply because the agent falls back to many shell operations.
- even when the agent switches to apatch, a workflow made of many tiny MCP calls creates
  approval fatigue and violates the product promise of a code autopilot.

The desired product shape is different:

```text
Codex / Cursor
  → local apatch MCP (control plane)
    → SSH transport
      → remote apatch worker (execution plane, co-located with repo)
        → remote files + remote .apatch + remote verify
```

The local MCP remains the only MCP the IDE knows about, but every governed operation executes where the repo actually lives.

---

## 2. Product goal

Make remote repos feel first-class to a local agent without weakening governance:

```text
apatch_doctor(target='ssh://example-search-host/srv/example/search-workspace')
apatch_session_start(target='ssh://…', intent='fix filter')
apatch_generate_batch(target='ssh://…', needles=[…])
apatch_apply_session(target='ssh://…')
apatch_verify_run(target='ssh://…', verify=['pytest', 'tests/...'])
apatch_attest(target='ssh://…')
apatch_session_end(target='ssh://…')
```

The agent should not need to know whether the workspace is local or remote except for choosing the target URI. The response contract, state machine, diagnostics, and recovery semantics remain apatch-native.

The human approval model must be intent-level, not tool-call-level. A user approving
"fix this remote repo under apatch governance" should not be asked again for every
internal `rfp_lint`, `spec_lint`, `simulate`, or `session_state` call. apatch should expose
a high-level orchestrator that converts one approved intent into a bounded governed run,
with explicit stop points only when risk or policy changes.

---

## 3. Architecture

### 3.1 Control plane vs execution plane

| Layer | Runs where | Responsibility |
|-------|------------|----------------|
| Local MCP server | user's Mac / IDE host | MCP tool descriptors, approval boundary, target parsing, SSH transport, response normalization |
| SSH transport | local → remote | authenticated process invocation, JSON stdin/stdout, timeout/log limits |
| Remote worker | remote host | imports remote apatch, resolves remote workspace, performs governed operations, returns JSON |
| Remote workspace | remote repo path | files, `.git`, `.apatch`, backups, registry, verify commands, TrustChain context |

### 3.2 Worker protocol

Local MCP sends a JSON envelope over stdin to a remote Python module:

```json
{
  "protocol_version": 1,
  "operation": "apatch_apply_session",
  "target_dir": "/srv/example/search-workspace",
  "kwargs": {"logs_path": ".apatch/tmp/.../patches.jsonl"},
  "controller": {"host": "edcher-mac", "apatch_version": "0.7.0"}
}
```

The remote worker returns exactly one JSON object on stdout. Rich/progress/stderr is captured separately and truncated into diagnostics. Any non-JSON stdout is `REMOTE_PROTOCOL_ERROR`.

### 3.3 Bootstrap

Bootstrap does not silently mutate user source code. It may:

- validate SSH reachability;
- check remote Python and git;
- check whether remote `python -m apatch` imports;
- create remote `.apatch/remote_worker.json` state if needed;
- return human steps when installation is missing or version skewed.

It must not run unapproved package installs from the agent. If install is needed, return commands for the human or an explicitly approved admin flow.

### 3.4 Security posture

Remote mode increases blast radius, so the defaults are conservative:

- host allowlist in `.apatch/remote.json` or user config;
- remote root allowlist such as `/home/ubuntu/projects/*`;
- no shell interpolation of paths/needles/verify strings;
- argv verify preferred, shell verify marked as higher risk;
- secrets redacted from response payloads;
- remote identity and git HEAD included in attestation metadata.

---

## 4. User experience

### Happy path

```text
apatch remote doctor ssh://example-search-host/srv/example/search-workspace
# ok: true, remote apatch version compatible, git root found, verify tools found

apatch_doctor(target_dir='ssh://example-search-host/srv/example/search-workspace')
# same protocol_contract as local, plus remote block
```

Agents then use the normal governed workflow. No ad-hoc `ssh host sed ...`, no local fake workspace, no manual path translation.

### Failure examples

| Condition | Error | Next action |
|-----------|-------|-------------|
| SSH alias cannot connect | `REMOTE_UNREACHABLE` | fix SSH config or host alias |
| remote path exists but is not git | `REMOTE_WORKSPACE_NOT_GIT` | choose repo root |
| host/path not allowed | `REMOTE_ROOT_DENIED` | update allowlist |
| apatch missing remotely | `REMOTE_APATCH_MISSING` | install apatch on remote or run bootstrap admin flow |
| local/remote versions differ | `REMOTE_VERSION_SKEW` | update remote apatch or restart MCP after local update |
| remote worker printed non-JSON | `REMOTE_PROTOCOL_ERROR` | inspect captured stderr/log |

### Autopilot approval boundary

For agent use, the preferred call is a high-level remote governed run:

```text
apatch_remote_task_run(
  target='ssh://example-search-host/srv/example/search-workspace',
  intent='fix engineering-system filter',
  plan={...},
  verify=[...]
)
```

The tool may internally run doctor, lint, plan, simulate, session_start, generate,
apply_session, verify, attest, and session_end. It returns one timeline and one audit
bundle. It must not ask the human to approve each internal step. It must stop and return a
typed response when it crosses a new boundary: install/admin action, disallowed root,
destructive rollback, version skew, policy violation, or verify failure requiring new
agent cognition.

---

## 5. Phase plan

1. **Remote target core** — parse/canonicalize remote targets, detect local-vs-remote mismatch, add typed errors and config allowlists.
2. **SSH worker protocol** — local transport + remote worker envelope, fake transport tests, version handshake.
3. **Remote governed routing** — route doctor/session/generate/simulate/apply/rollback/verify/attest/session_end to the worker; keep `.apatch` remote. Recovery `reset=true` is one-shot: resumed chunks must reuse persisted progress without resetting the session again.
4. **Remote verify/recovery** — robust logs, async/poll, baseline compare, SSH disconnect resume.
5. **Autopilot orchestrator** — one intent-level tool for remote governed tasks; internal sub-steps are not user-facing approvals.
6. **Docs and Codex UX** — `apatch remote doctor`, Codex setup docs, troubleshooting guide.

---

## 6. Non-goals

- Hosting a public MCP server on the remote machine.
- Building a full SFTP filesystem abstraction for apatch internals.
- Running arbitrary user shell through apatch as a generic SSH client.
- Cross-host distributed transactions between local and remote `.apatch` state.
- Auto-installing packages on remote hosts without explicit human/admin approval.
- Replacing CI/CD or remote deployment tooling.

---

## 7. SPEC projection

| RFP id | SPEC | Disposition |
|--------|------|-------------|
| A29-A | SPEC-REMOTE-SSH-CORE-1 | covered |
| A29-B | SPEC-REMOTE-SSH-CORE-1 | covered |
| A29-C | SPEC-REMOTE-SSH-WORKER-1 | covered |
| A29-D | SPEC-REMOTE-SSH-WORKER-1 | covered |
| A29-E | SPEC-REMOTE-SSH-WORKER-1 | covered |
| A29-F | SPEC-REMOTE-SSH-MCP-1 | covered |
| A29-G | SPEC-REMOTE-SSH-MCP-1 | covered |
| A29-H | SPEC-REMOTE-SSH-VERIFY-1 | covered |
| A29-I | SPEC-REMOTE-SSH-VERIFY-1 | covered |
| A29-J | SPEC-REMOTE-SSH-CORE-1 + SPEC-REMOTE-SSH-WORKER-1 | covered |
| A29-K | SPEC-REMOTE-SSH-MCP-1 | covered |
| A29-L | all remote specs | covered |
| A29-M | all remote specs | covered |
| A29-N | SPEC-REMOTE-SSH-MCP-1 | covered |
| A29-O | SPEC-REMOTE-SSH-MCP-1 | covered |

---

## 8. Open questions

1. Should remote bootstrap support a fully managed venv under `~/.cache/apatch/remote-worker/`, or only validate and instruct?
2. Should `target_dir` accept `ssh://…` directly, or should MCP tools add a separate `target` field for remote URIs while keeping `target_dir` local-only?
3. How strict should version compatibility be: exact package version, MCP tool fingerprint, or protocol version + feature flags?
4. Should TrustChain signing happen with remote identity only, or include a dual signature from local controller and remote worker?
5. What is the first design-partner remote repo for end-to-end dogfood?
6. Should `apatch_remote_task_run` be remote-only, or should the same orchestrator become the default local governed autopilot as well?

---

**Next:** implement [RFP-029-IMPLEMENTATION](./specs/RFP-029-IMPLEMENTATION.md) in order: CORE → WORKER → MCP → VERIFY.
