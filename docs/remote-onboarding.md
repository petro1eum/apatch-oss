# Remote Onboarding

This is the human-friendly path for RFP-029/RFP-030 remote work. A new user should not
write `.apatch/remote.json` by hand.

## Minimum Inputs

Ask for three values:

| Field | Example | Meaning |
|---|---|---|
| alias | `search-example` | Opaque name the agent may use |
| host | `example-search-host` | Local SSH config host alias |
| path | `/srv/example/search-workspace` | Absolute remote git workspace |

Optional but useful:

| Field | Example |
|---|---|
| health URL | `http://127.0.0.1:8080/health` |
| service alias | `api` |
| service kind | `http`, `systemd`, `docker_compose`, `command` |
| systemd unit | `search-api.service` |
| compose service | `api` |
| source handoff | `--source-handoff` |
| apatch runtime path | `--runtime-path /home/ubuntu/.local/src/apatch_runtime` |
| remote Python | `/opt/remote/bin/python` |
| SSH args | `-J bastion` |

## One-command Setup

```bash
apatch remote init \
  --alias search-example \
  --host example-search-host \
  --path /srv/example/search-workspace \
  --health-url http://127.0.0.1:8080/health \
  --service api \
  --target-dir .
```

Then validate:

```bash
apatch remote validate --alias search-example --target-dir .
```

If the remote machine cannot fetch GitHub because credentials live only on the
local machine, enable opaque source handoff:

```bash
apatch remote init \
  --alias search-example \
  --host example-search-host \
  --path /srv/example/search-workspace \
  --source-handoff \
  --target-dir .
```

Then plan or execute through the broker:

```bash
apatch remote handoff --alias search-example --source . --target-dir . --json
apatch remote handoff --alias search-example --source . --target-dir . --execute
```

Agents should not fall back to `git clone`, `scp`, or `tar | ssh`; use
`apatch_remote_source_handoff` so transport and credentials remain local policy.

If apatch itself is not installed on the remote host, bootstrap the apatch source
to an opaque runtime directory and configure `--runtime-path`. The transport will
prepend that path to `PYTHONPATH` before running `python -m apatch.remote.worker`;
agents must not invent raw `scp` or `tar | ssh` fallback commands.

```bash
apatch remote init --alias search-example --host example-search-host --path /srv/example/search-workspace --runtime-path /home/ubuntu/.local/src/apatch_runtime --target-dir . --force
```

The agent can now use:

```python
apatch_remote_task_run(
    remote_target="search-example",
    intent="Fix engineering system filter visibility",
    plan={"needles": [...]},
    verify="npm test -- MatchingTool",
)
```

## Writing to the remote — and the dry-run trap

`apatch_remote_task_run` **defaults to `dry_run=true`**. A dry-run simulates the whole
`doctor → session → generate → simulate → apply → verify → attest → session_end`
timeline against a **fake transport** — it returns `attest` and `phase=complete` but
makes **no SSH connection and writes nothing**. The response says so: `applied: false`,
`transport: "fake"`. A simulated attest is **not** a real apply.

To actually write on the remote, two things are required:

```jsonc
apatch_remote_task_run(
  remote_target = "<alias>",
  intent        = "...",
  plan          = { "needles": [ /* the real edit as mutation dicts */ ] },  // (1)
  verify        = "<remote test>",
  dry_run       = false                                                       // (2)
)
```

1. **The edit must be in `plan.needles`** (or `patches`/`logs_path`). With no patch in
   the plan, the apply step is **skipped** — the governance ceremony runs but the file is
   never touched (response: `applied: false` + a "put the edit in plan.needles" hint).
2. **`dry_run=false`** — otherwise it's the fake simulation above.

**Confirm the write:** check `result["applied"] is true` (a real `apatch_apply_session`
step ran in the timeline). The exec channel (`apatch_remote_service_action ... exec`) is
**read-only by design** and cannot write — use `apatch_remote_task_run` for mutations.

The write path itself is real: `dry_run=false` uses the SSH transport, which runs
`python -m apatch.remote.worker` on the remote, whose `apatch_apply_session` writes the
files (governed + attested in the remote ledger). The alias's `allowed_operations` must
permit the apply steps.

### Finalize a recovered remote session

If `apatch_remote_task_run` applied the patch but the verify step failed for an
external/transient reason (for example, the service restart was slower than the verify
healthcheck), do **not** open a new mutation session and do **not** hand-edit
`.apatch/session_state.json`.

After the service is healthy and the same acceptance gate is expected to pass, close the
active failed/verifying session with:

```jsonc
apatch_remote_task_run(
  remote_target = "<alias>",
  intent        = "finalize after recovered verify",
  plan          = { "finalize_current": true },
  verify        = "<the same remote acceptance gate>",
  dry_run       = false
)
```

This executes only:

```text
doctor → resume_session → verify_run → attest → session_end
```

It deliberately runs **no** `session_start`, `generate_batch`, `simulate`, or
`apply_session`. `verify` is required; apatch refuses `finalize_current` without a fresh
green gate.

## What To Tell Codex

Do not tell Codex to connect to SSH. Tell Codex to use the apatch remote broker.

Copy/paste prompt:

```text
Work through apatch MCP remote broker only.

Remote target alias: search-example.

Do not run raw ssh, scp, rsync, git clone over SSH, or tar pipes.
Do not ask me for SSH host, username, key path, jump host, remote path, or credentials.
Do not inspect .ssh, SSH config, or remote policy internals.

For remote code work, call:
apatch_remote_task_run(remote_target="search-example", intent=..., plan=..., verify=..., dry_run=false)

If the remote needs local source because it cannot fetch the repo itself, call:
apatch_remote_source_handoff(alias="search-example", execute=true)

For backend lifecycle operations, use only configured service broker actions:
apatch_remote_service_action(alias="search-example", service="api", action="healthcheck|status|logs|restart")

If a capability is denied or missing, report the denied operation/capability id.
Do not fall back to raw SSH.
```

The agent still sees the alias (`search-example`) and the task intent. It should not
see the SSH host, key, user, jump host, raw remote path, archive transport, or
credential topology. If those details appear in tool output, that is a broker
redaction bug.

## What Not To Say

Do not say:

```text
ssh example-search-host and fix the project in /srv/example/search-workspace
```

Do say:

```text
Use apatch remote alias search-example through MCP only. Do not use raw SSH.
```

## Generated Policy

`apatch remote init` writes `.apatch/remote.json`:

```json
{
  "allowed_hosts": ["example-search-host"],
  "allowed_roots": ["/srv/example/search-workspace"],
  "targets": {
    "search-example": {
      "host": "example-search-host",
      "path": "/srv/example/search-workspace",
      "display": "search-example",
      "redact": true,
      "timeout_sec": 600,
      "allowed_operations": [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end"
      ],
      "services": {
        "api": {
          "kind": "http",
          "allowed": ["healthcheck"],
          "healthcheck": {
            "url": "http://127.0.0.1:8080/health",
            "timeout_sec": 60
          }
        }
      },
      "source_handoff": {
        "enabled": true,
        "allowed_modes": ["workspace_overlay"],
        "local_roots": ["."]
      }
    }
  }
}
```

## Enabling Restarts

A health URL alone creates a read-only `healthcheck` service. It does not grant
`restart`, `status`, or `logs`.

For lifecycle actions, bind the alias to an explicit service manager:

```bash
apatch remote init \
  --alias search-example \
  --host example-search-host \
  --path /srv/example/search-workspace \
  --health-url http://127.0.0.1:8080/health \
  --service api \
  --service-kind systemd \
  --service-unit search-api.service \
  --target-dir .
```

Plan first:

```bash
apatch remote service \
  --alias search-example \
  --service api \
  --action restart \
  --target-dir . \
  --json
```

Execute only after the plan is expected:

```bash
apatch remote service \
  --alias search-example \
  --service api \
  --action restart \
  --target-dir . \
  --execute
```

For docker compose, use `--service-kind docker_compose`, `--compose-service`,
and optionally `--compose-file`.

## Custom-managed services (`kind: command`)

Some runtimes are managed neither by systemd nor docker compose — for example a
process started with `nohup` and stopped by `kill`. For these, use
`kind: command` and define an explicit shell command per action directly in
`.apatch/remote.json`. apatch runs each command on the remote as
`bash -lc "<command>"`, so pipes, `kill`, `nohup`, and redirects all work. The
SSH host, path, and key stay hidden from the agent; the returned plan is redacted
to the alias.

```json
"services": {
  "search": {
    "kind": "command",
    "allowed": ["status", "restart", "logs", "healthcheck"],
    "healthcheck": { "url": "http://127.0.0.1:8004/health", "timeout_sec": 10 },
    "commands": {
      "status": "lsof -t -iTCP:8004 -sTCP:LISTEN && echo UP || echo DOWN",
      "restart": "PID=$(lsof -t -iTCP:8004 -sTCP:LISTEN); [ -n \"$PID\" ] && kill \"$PID\"; cd /srv/app && nohup python3 -m uvicorn app:app --port 8004 >> app.log 2>&1 & echo RESTARTED",
      "logs": "tail -n {lines} /srv/app/app.log"
    }
  }
}
```

Notes:

- `commands.<action>` is a literal shell string. `{lines}` is substituted with
  the `lines` argument for the `logs` action.
- `allowed` still gates which actions the agent may invoke; an allowed action
  with no matching `commands` entry is reported as unsupported.
- `healthcheck` is orthogonal to the kind — keep `healthcheck.url` to also expose
  a read-only `healthcheck` action alongside the command actions.
- The command text is policy-authored (by a human), not generated by the agent.
  This is the one place full shell freedom is allowed; the agent only chooses the
  verb (`status` / `restart` / `logs`).

`kind: command` is authored directly in `.apatch/remote.json` (there is no
`apatch remote init --service-kind command` flag yet).

## Product Rule

Discovery proposes, policy approves. The agent may suggest a service or health endpoint,
but it must not silently broaden `allowed_operations`, change SSH host/path, or enable raw
SSH. `apatch_remote_task_run` should remain the visible tool boundary.
