# apatch MCP server setup

## Install

**Consumer project** (PyPI — no developer paths):

```bash
python3 -m pip install 'apatch[mcp]'
apatch init-consumer --target-dir . --with-sandbox --with-enforcement --with-mcp
```

**apatch tool development** (editable, this repo):

```bash
python3 -m pip install -e ".[mcp,dev,yaml]"
```

`apatch doctor` → `mcp_health.recommended_install` picks the right command automatically
(PyPI for consumer workspaces; editable only when `pyproject.toml` + `apatch/` package present).

## IDE MCP config (any host)

### Quick setup (one command)

```bash
pip install 'apatch[mcp]'          # or: pip install -e ".[mcp,dev,yaml]" in apatch repo
cd /path/to/project
apatch mcp sync --target-dir .       # writes .apatch/mcp.json + updates known IDE configs
apatch mcp codex-approve --target-dir .  # Codex: approve apatch autopilot tools
apatch mcp codex-doctor --target-dir .   # Codex: diagnose repeated prompts
# Reload MCP in your IDE (toggle off/on)
apatch mcp check --target-dir . --json # configured-command bootstrap + stdio check
```

**Default:** projects get the **17-tool intent-level profile** (`APATCH_MCP_PROFILE=compact`). Use `core`, `spec`, or `full` only for work that needs those specialist surfaces.
**Opt-in expansion:** set `core` (28 tools), `spec` (38), or `full` (129) only when the task needs those specialist operations.

### Two-layer model

| Layer | File | Contents |
|-------|------|----------|
| **Canonical** | `{project}/.apatch/mcp.json` | Full block: `python -m apatch.mcp.launcher` + env (`PROFILE`, `GUIDANCE`, UTF-8, …) |
| **IDE stub** | `.cursor/mcp.json`, `~/.gemini/config/mcp_config.json`, … | Minimal: `python -m apatch.mcp.workspace_launcher` + `APATCH_WORKSPACE` |

**Why two layers?** IDEs start MCP with unpredictable `cwd` (often `$HOME`). The stub finds the project via `APATCH_WORKSPACE` (or `CURSOR_PROJECT_DIR`), then replaces itself with the exact interpreter, arguments, and env from `.apatch/mcp.json`. The canonical child starts Python in isolated mode and ignores inherited `PYTHONPATH`, so a stale source checkout cannot shadow the installed APatch wheel.

**Avatar runtime parity.** Isolated mode also means that an editable `avatar-contract`
visible in a normal shell may be invisible to MCP. For governed-work/Avatar releases,
install the APatch wheel and the matching immutable `avatar-contract` wheel into the
same canonical interpreter in one transaction, then restart MCP. Verify the actual
runtime with `apatch_doctor → avatar_contract`: `ok=true`, the expected
`installed_version`, and a non-editable `module_path` must all be visible before
delivery is considered operational. A stale installed contract is reported as
`AVATAR CONTRACT INCOMPATIBLE` with its exact interpreter, module path, and missing
symbols; queued evidence remains durable.

**IDE entry point** (same for Cursor, Antigravity, VS Code, …):

```bash
python -m apatch.mcp.workspace_launcher
```

Discovery order in `workspace_launcher`: `APATCH_WORKSPACE` → `CURSOR_PROJECT_DIR` → walk up from `cwd` → `.apatch/mcp.json`.

### Runtime identity and health

Use the exact venv executable, such as `/path/to/venv/bin/python`, in both
configuration layers. It may resolve to the same binary as base Python while
loading different packages. Sync retains this environment identity; Homebrew
normalization is limited to proven equivalent non-venv environments.

```bash
/path/to/venv/bin/python -I -m apatch.cli mcp sync --target-dir /path/to/project --force --no-auto-ide
/path/to/venv/bin/python -I -m apatch.cli mcp check --target-dir /path/to/project --json
# Explicit profile change (otherwise preserve the existing selection):
apatch mcp sync --target-dir . --force --profile full
```

Sync preserves user env, profile and unrelated config fields. Auto-detected IDE
configs already bound to another workspace are not rebound. A malformed config
is refused even with force; doctor does not rewrite configs as a side effect.
Existing isolated-mode arguments stay preserved.

CLI `mcp check` and the check after `mcp sync` inspect the selected child Python
in isolated mode and perform actual stdio initialize/tools/list through the
workspace bootstrap. They use a disposable copy of the selected config block,
not the user's ledger, keys, sessions or alias registry. The result's scope is
`configured_command_in_disposable_workspace`, not project/domain acceptance.
A failed check makes the command exit nonzero. Diagnostics distinguish the
selected executable, binary realpath, prefix/base_prefix, loaded APatch module
and distribution versions, canonical config source, and MCP handshake result.
The MCP SDK's server version is not the APatch package version.

Configured-command readiness and host tool availability are separate observations.
A running MCP doctor does not recursively start another server. Its
`configured_runtime.status=not_checked_in_stdio` means that independent check
was not performed; `mcp_health.ok=false` is not itself a failure of the running
writer. Read `writer_protocol.ready` for the current process. Neither a process
report nor a separate successful handshake proves that the host exposed tools to
this task: `host_tool_availability=not_observable`. Check both configuration
layers before asking for a reconnect; do not repeat installation blindly.

### Safe local workspace roaming

One MCP process may operate in explicitly authorized sibling repositories without
changing its global bound workspace. Registration is a human CLI action:

```bash
apatch workspace add agent /path/to/TrustChain_Agent
apatch workspace add crm /path/to/Sales_Pipeline
apatch workspace list
```

The registry is machine-local at `~/.config/apatch/workspaces.json` (override:
`APATCH_WORKSPACE_REGISTRY`). Each entry pins the repository identity and SHA-256 of
`AGENTS.md`. Contract or root drift blocks mutations until a human reviews and re-pins
with `--force`.

Agents use the read-only discovery tools first, then pass the alias on every call:

```text
apatch_workspace_list(target_dir=".")
apatch_workspace_inspect(alias="crm", include_contract=true, target_dir=".")
apatch_doctor(target_dir="@crm")
```

`workspace_launcher` enables `alias_only` policy: an off-bound raw absolute
`target_dir` is rejected with `WORKSPACE_ALIAS_REQUIRED`. Routing is stateless per
request, so parallel agents cannot change a shared global current workspace.

### Sync behavior

```bash
apatch mcp sync --target-dir .              # default: canonical + auto-detect existing IDE configs
apatch mcp sync --target-dir . --force      # overwrite stale apatch blocks
apatch mcp sync --target-dir . --ide-path ~/.gemini/config/mcp_config.json   # extra path
apatch mcp sync --target-dir . --no-auto-ide   # canonical only, skip IDE stubs
# or: apatch init-consumer --with-mcp   (calls sync internally)
```

**Auto-detect** (when `--no-auto-ide` is not set): writes stubs to `{project}/.cursor/mcp.json` (if `.cursor/` exists) and user-level files that already exist (`~/.cursor/mcp.json`, `~/.gemini/config/mcp_config.json`, `~/.gemini/antigravity-ide/mcp_config.json`). Does not create new IDE config paths.

### Agnostic install — machine paths are never committed

Both `.apatch/mcp.json` and the IDE stubs carry an **absolute, machine-specific
interpreter path** (e.g. `/opt/homebrew/bin/python3.14` — IDEs launch MCP without a
useful `PATH`, so the path can't be portable). They are therefore **generated per
machine, never committed** — `.apatch/mcp.json` and `.cursor/mcp.json` are gitignored.

Per-machine setup is two commands:

```bash
git config core.hooksPath scripts/hooks   # enable auto-refresh hooks (+ trustchain pre-commit)
apatch mcp sync                            # generate the local MCP config
```

The committed `scripts/hooks/{post-merge,post-checkout}` then run `apatch mcp sync`
after every pull/checkout. If an existing runtime pointer differs, review it and
use explicit sync with force. `apatch doctor` is diagnostic only and never repairs
`.apatch/mcp.json`. A fresh clone needs only those two
commands — no machine paths live in the repo. See `scripts/hooks/README.md`.

Each stub gets `APATCH_WORKSPACE=<target-dir>` so MCP works even when IDE `cwd` is wrong.

`apatch_doctor` → `mcp_health.canonical_mcp_path`, `recommended_ide_mcp_config`, `ide_setup_hint`.

Legacy `--user` / `--no-antigravity` CLI flags removed — use `--ide-path` for any host.

### Codex approval noise

Codex tool confirmations are separate from shell/filesystem sandbox prompts. First diagnose the class of prompt:

```bash
apatch mcp codex-doctor --target-dir . --json
```

If `mcp_tool_prompts_likely=true`, pre-approve the governed apatch MCP set:

```bash
apatch mcp codex-approve --target-dir .
```

This writes `approval_mode = "approve"` for the governed apatch tool set in
`~/.codex/config.toml`, including `apatch_remote_task_run`,
`apatch_remote_source_handoff`, and `apatch_remote_service_action`. Use
`--preset remote` for only remote tools, `--preset all` for every registered
`apatch_*` MCP tool, and `--dry-run --json` to preview.

If `filesystem_prompts_likely=true`, the requested target is outside the Codex
workspace/writable root. No-stop route: the agent must continue through the
pre-approved apatch broker (`apatch_generate_batch` → `apatch_apply_session`, or
remote broker tools) and must not use direct file-edit tools for that target.
Permanent cleanup is still to start Codex with that target as the workspace root
or add it as a writable root.

Reload/restart the Codex MCP server after changing approvals.

### Env vars (canonical `.apatch/mcp.json`)

**Default (all projects):**

```json
{
  "env": {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "APATCH_MCP_GUIDANCE": "doctor_only",
    "APATCH_MCP_PROFILE": "compact",
    "APATCH_LANE": "auto"
  }
}
```

**apatch source repo (dogfood)** — `mcp sync` also sets `APATCH_MCP_GUIDANCE=full` when `pyproject.toml` + `apatch/` package detected.

| Profile | Tools | When |
|---------|-------|------|
| **compact** | 15 | **Default** — complete intent-level governed workflow |
| `core` | 26 | Opt-in — compact + generation, chunked apply, extensions, sandbox and hygiene |
| `spec` | 37 | Opt-in — core + advanced multi-SPEC, coverage and slug operations |
| `full` | 123 | Opt-in — complete compatibility/product surface |

See [RFP-019 §3.3](./RFP-019-mcp-scale-lifecycle.md) for tier list.

**IDE stub example** (written by `mcp sync`):

```json
{
  "mcpServers": {
    "apatch": {
      "command": "/usr/local/bin/python3.12",
      "args": ["-m", "apatch.mcp.workspace_launcher"],
      "env": {
        "APATCH_WORKSPACE": "/path/to/project"
      }
    }
  }
}
```

Verify after install / IDE reload:

```bash
apatch doctor --json | jq '{version, profile: .mcp_profile, tools: .mcp_health.mcp_tool_catalog.count, writer: .writer_protocol}'
```

Expected after a successful restart: `writer.ready=true`,
`writer.writer_protocol_version=2`, `writer.reload_required=false`, and compact
`tools=17`. `writer.path_lease_api` lists the internal v2 admission operations; they
are intentionally not additional agent-callable MCP tools.

If a workspace has a live v2 compatibility guard and an old MCP is still loaded, the
next mutation returns `MCP_WRITER_PROTOCOL_MISMATCH` before creating a draft session.
Install the intended wheel, restart the APatch MCP server once in the client, run the
readback above, and retry. Do not remove `.apatch/write_lease.json` manually: v2 clients
recognize it as infrastructure state, and APatch clears it after the last live path
lease ends or expires.

`apatch doctor` / `apatch_doctor` return `mcp_health` with `doctor_interpreter`, `ide_configs`, `cursor_configs`, `candidates`, `stored_mcp_fingerprint`, `writer_protocol`, and `recommended_mcp_config`.

### Executable specs — what fresh agents must read

`mcp.json` only starts the server; **format rules are not in the config file**. They are embedded in MCP tool responses and **MCP Resources** (`apatch://playbook/*`):

| Source | Fields / tools |
|--------|----------------|
| **`apatch_doctor`** (first call) | `protocol_contract`, `agent_onboarding`, `runtime_hygiene`, `tool_usage`, `mcp_resources`, `spec_authoring`, `spec_execution`, `spec_run`, `sandbox_agent_protocol`, `hygiene`, `mcp_health.mcp_tool_catalog` |
| **MCP Resources** (optional; same JSON as doctor) | See table below — fetch `apatch://playbook/index` for read order |
| **`apatch_spec_lint`** | **Primary spec onboarding gate** — when `passed`: `plan_scaffold`, `needles_scaffold`, `agent_next` (RFP-024); with `## RFP traceability`: embedded `rfp_lint` + `rfp_coverage` (RFP-023). Always: `spec_authoring.rules` + lint errors |
| **`apatch_spec_next` / `apatch_spec_status`** | `spec_authoring` + `spec_execution` |
| **`apatch_spec_run` / `apatch_execute_next`** | `spec_authoring` + `spec_run` (needles, verify_deferred) |
| **`apatch_generate_batch`** (doctor_only) | `guidance_ref`, `logs_path_hint`, `if_spec_task` |

With `APATCH_MCP_GUIDANCE=doctor_only` (consumer default), hot-path tools return slim `guidance_ref` pointing to doctor or resources — not full playbooks on every call.

#### MCP Resources (`apatch://playbook/*`)

Registered in `apatch/mcp/resources.py` (RFP-019 L1-10). JSON, `application/json`.

| URI | Content |
|-----|---------|
| `apatch://playbook/index` | Catalog + recommended read order |
| `apatch://playbook/protocol_contract` | Task routing, invariant, never-do list |
| `apatch://playbook/runtime_hygiene` | EPHEMERAL JSONL → `.apatch/tmp/`, `session_end`, `apatch_gc` modes, inference sunset |
| `apatch://playbook/sandbox_protocol` | Sandbox enforce: no pip/shell writes; human-only MCP restart |
| `apatch://playbook/spec_authoring` | SPEC.md format (`apatch_spec_lint` rules) |
| `apatch://playbook/tool_usage` | Which `apatch_*` tool when — decision tree |
| `apatch://playbook/doc_outline` | Numbered markdown: `insert_section` / `shift_outline` workflow ([SPEC-DOC-OUTLINE-1](./specs/SPEC-DOC-OUTLINE-1.md)) |
| `apatch://playbook/spec_run` | RFP-009 whole-spec batch workflow (§3K) |
| `apatch://playbook/spec_execution` | RFP-007 per-requirement loop (§3I–§3J) |

Consumer scaffold (`init-consumer --with-mcp`): `docs/specs/SPEC-TEMPLATE.md`, `docs/specs/README.md`, `.cursor/rules/apatch-executable-specs.mdc` (opens on `docs/specs/**`). Playbook: [agent-onboarding.md](./agent-onboarding.md) · consumer `AGENTS.md` §3I–§3K.

#### IDE MCP tool count vs server catalog

Cursor normally shows the 17-tool compact profile. With `APATCH_MCP_PROFILE=full`, it may still show fewer tools than `apatch_doctor` → `mcp_health.mcp_tool_catalog.count` (**129**) because of client descriptor caching. Treat `apatch_doctor` as authoritative; do not ask the user to restart Cursor solely for tool count if the required tool works.

**Performance:** full diagnostics run only on explicit `apatch_doctor` (~1–3 s on typical consumers). Other MCP tools use a lightweight policy snapshot. Apply emits one Ed25519 notarization receipt per chunk and validates only the appended ledger object + HEAD; full history is explicit audit/recovery. Benchmarks: [mcp_performance.md](./mcp_performance.md). No-regression contract: [governed-runtime-invariants.md](./governed-runtime-invariants.md).

The launcher redirects **stderr to `.apatch/mcp_stderr.log`** so Rich output cannot corrupt JSON-RPC stdout.

## Tools (CLI parity)

**17 MCP tools by default; 129 in `full`** — бизнес-логика через `MutationRuntime` / `apatch.workflows`, как в CLI.
Актуальное число: `apatch_doctor` → `mcp_health.tool_count`.

Every MCP response includes **`state_update`** (R53), **`invariant`**, **`core_invariant`**, and formal **`error_type`** on failure (R56). State file: `.apatch/session_state.json`.

### Tool families (навигация)

| Family | Tools | Назначение |
|--------|-------|------------|
| **Session** | `apatch_session_start`, `apatch_session_end`, `apatch_session_state`, `apatch_recover`, `apatch_resume_session` | Governed Intent → Session; prefer exact one-call `recover(governed_session_id)`; low-level `resume_session` remains in specialist profiles |
| **Workspace** | `apatch_workspace_list`, `apatch_workspace_inspect` | Read-only local alias discovery and pinned `AGENTS.md` inspection; registration stays human CLI-only |
| **Extensions** | `apatch_extension_list`, `apatch_extension_inspect`, `apatch_extension_validate`, `apatch_extension_run` | Explicit digest-pinned local tools; static host surfaces, no dynamic in-process imports |
| **Mutation** | `apatch_plan`, `apatch_plan_batch`, `apatch_apply`, `apatch_apply_session`, `apatch_generate`, `apatch_generate_batch`, `apatch_strip`, `apatch_strip_dry_run`, `apatch_phase_run`, `apatch_rollback`, `apatch_replay` | Патчи и strip |
| **Verify** | `apatch_sdd_verify`, `apatch_verify_run`, `apatch_verify_status`, `apatch_verify_semantic`, `apatch_verify_notarization`, `apatch_arch_check`, `apatch_db_*`, `apatch_sandbox_ci_gate` | Frozen SDD judge/falsification and post-mutation checks |
| **Attestation** | `apatch_attest`, `apatch_commit_attested`, `apatch_noop_attest`, `apatch_attestation_show`, `apatch_attestation_export`, `apatch_events_tail`, `apatch_trustchain_history`, `apatch_trustchain_coverage` | TrustChain + audit log + artifact traceability; `noop_attest(covered_by=…)` attests an Rk covered by another (no marker file, RFP-027) |
| **Spec (RFP-007)** | `apatch_spec_lint`, `apatch_spec_status`, `apatch_spec_next`, `apatch_spec_coverage`, `apatch_spec_interference`, `apatch_spec_schedule`, `apatch_spec_cross_verify`, `apatch_spec_run_multi` | Executable specs; cross-spec L1/L2 + schedule + Level-3 cross-verify + multi-run (RFP-014) |
| **Spec executor (RFP-008)** | `apatch_execute_next` | Governed cycle на одно `Rk`; `needles` → `generate_batch` внутри; self-edit SPEC перечитывает verify и привязывает attest к свежему hash; pre-mutation failure автоматически закрывает новую сессию |
| **Spec run (RFP-009)** | `apatch_spec_run`, `apatch_spec_run_manifest_lint` | Вся спека: inline `requirements` → loop Rk; state `.apatch/spec_run.json`; optional `plan=` |
| **Plan artifact (RFP-011)** | `apatch_spec_plan_lint`, `apatch_spec_plan_register`, `apatch_spec_plan_diff`, `apatch_spec_plan_show` | Inline plan dict (schema v2: decision + execution) → signed `plan:SPEC-X@vN`; show/diff |
| **Plan adherence (RFP-012)** | `apatch_spec_adherence` | Plan-vs-fact deviation report per Rk (report-only) |
| **Trust anchor** | `apatch_trust_enroll`, `apatch_verify_anchor`, `apatch_verify_inclusion`, `apatch_policy_sign`, `apatch_policy_verify` | Enrollment + CI-гейт: подпись → leaf → root CA (§5.3); inclusion в внешний лог (§5.10); подписанная политика (§5.8) |
| **Orchestration** | `apatch_simulate`, `apatch_orchestrate`, `apatch_remote_task_run`, `apatch_remote_source_handoff`, `apatch_remote_service_action`, `apatch_plan_graph`, `apatch_execute_graph`, `apatch_pipeline_run`, `apatch_refactor_run`, `apatch_db_run` | Манифесты и графы |
| **Index / impact** | `apatch_index_build`, `apatch_index_query`, `apatch_impact`, `apatch_compile`, `apatch_suggest_until` | Символы, doc compile, strip hints |
| **Sandbox** | `apatch_sandbox_status`, `apatch_sandbox_audit` | Policy и lease |
| **Product views (RFP-020/RFP-033)** | `apatch_project_status`, `apatch_knowledge_graph`, `apatch_slug_cockpit` | Unified DTO, session graph, and slug diagnostics cockpit; CLI: `apatch status`, `apatch report`, `apatch spec list`, `apatch slug cockpit` |
| **Contribution (RFP-026)** | `apatch_timesheet` | Per-identity, cross-project timesheet over signed ContributionEvent receipts; `--by identity/project/spec/day`, `--verify`; CLI: `apatch timesheet` |
| **TrustChain governed work (RFP-043/RFP-047, full)** | `apatch_governed_work_configure`, `apatch_governed_work_transition_endpoint`, `apatch_governed_work_prepare_change`, `apatch_governed_work_store_binding`, `apatch_governed_work_build_evidence`, `apatch_governed_work_read_execution_proposal`, `apatch_governed_work_accept_execution_proposal`, `apatch_governed_work_preview_evidence`, `apatch_governed_work_publish_evidence`, `apatch_governed_work_sync`, `apatch_governed_work_disconnect`, `apatch_governed_work_retire_outbox`, `apatch_governed_work_status` | ProjectGroup WorkProgram → signed Change/binding/local evidence → explicit preview/confirm/publish → receipt; disconnect fences sharing without deleting history; [onboarding](./governed-work-trustchain.md) |
| **Consumer** | `apatch_doctor`, `apatch_init_consumer` | Диагностика и scaffold |
| **Scan / logs** | `apatch_scan`, `apatch_view` | Транскрипты IDE |

**`apatch_verify_run`:** без флагов — shell verify из `doctor.recommended_verify_resolved` (пути через `tool_paths.py`). Явно: `verify="npm run build"` или argv-списком `verify=["pytest", "-k", "not slow"]` (без shell-кавычек/экранирования), `semantic=True`, `notarization=True`, `pipeline_manifest=…`. **Baseline-aware verify** (pre-existing красное не блокирует): `baseline="capture"` до apply фиксирует падающие тесты в `.apatch/verify_baseline.json`; `baseline="compare"` после apply проходит, если нет **новых** падений; `allowed_failures=[node-id-или-подстрока]` — allow-list. **Async verify (AR-2):** `async_mode=True` — фоновый job, poll `apatch_verify_status(job_id=…)`; принудительный async при prior duration > `APATCH_VERIFY_SYNC_MAX_SEC` (default 45). Job исполняет отдельный stdio-isolated worker: он непрерывно вычитывает большой stdout/stderr, атомарно публикует terminal JSON после log и переживает restart MCP. Потерянный worker фиксируется как terminal `failed`, не вечный `running`. CLI: `apatch verify run --async`, `apatch verify status --job-id …`.

**Key backend (подпись журнала/политики, RFP-005 §5.9):** по умолчанию enrolled-PEM (`APATCH_AGENT_ID` + `APATCH_AGENT_KEY`). Для HSM/KMS — `APATCH_KEY_BACKEND=command` + `APATCH_AGENT_SIGN_CMD` (stdin=байты → stdout=base64(sig)) + `APATCH_AGENT_PUBKEY` (приватный seed не входит в процесс). `doctor` → `trust_anchor.backend`.

### Намеренные отличия

| CLI | MCP | Почему |
|-----|-----|--------|
| `apatch apply` (без `-y`) | — | Интерактивный TUI только для человека |
| `apatch apply -y` | `apatch_apply` | Малые задачи (≤15 кандидатов) |
| `apatch apply-session` | `apatch_apply_session` | **Массовый рефакторинг** — чанки + checkpoint |
| `apatch plan` (один файл) | `apatch_plan` + `apatch_plan_batch` | Один патч или весь JSONL |
| `strip --suggest-until` | `apatch_suggest_until` | Отдельный tool, тот же алгоритм |

Все остальные команды (оркестрация, index, trustchain, init-consumer) — **1:1** по параметрам.

### Tool table

| Tool | CLI equivalent | Notable parameters |
|------|----------------|-------------------|
| `apatch_plan` | single-patch evaluate | `target_dir`, old/new content; `old_content=""` → dry-run CREATE |
| `apatch_plan_batch` | `apatch plan --json` | `steps`, `workers`, filters |
| `apatch_apply` | `apatch apply -y` | Отклоняет >15 кандидатов без `budget`/`steps` |
| `apatch_apply_session` | `apatch apply-session` | `chunk_max_files`, `reset`, `abort`; один файл не делится между чанками; ответ `checkpoint`, `continue` |
| `apatch_simulate` | `apatch simulate` | preflight: `risk_map`, `rollback_probability`, `execution_graph` |
| `apatch_plan_graph` | `apatch graph plan` | dependency graph → `.apatch/execution_graph.json` |
| `apatch_execute_graph` | `apatch graph execute` | topo-order node execution |
| `apatch_orchestrate` | `apatch orchestrate` | simulate → plan graph → execute graph |
| `apatch_replay` | `apatch replay` | `apply_session` chunk timeline (`session_id`) |
| `apatch_sandbox_status` | `apatch sandbox status` | mode, lease, Cursor hooks |
| `apatch_sandbox_audit` | `apatch sandbox audit` | audit; `auto_revert` or `watcher:revert` restores files |
| `apatch_sandbox_ci_gate` | `apatch sandbox ci-gate [--base <ref>]` | PR gate: audit + notarization + policy drift; `base` (e.g. `origin/main`) проверяет diff PR (Ring-2 authority over shared history) |
| `apatch_policy_sign` | `apatch policy sign` | Подписать конфиг монитора enrolled-ключом → `.apatch/policy.lock.json` (tamper-evident, RFP-005 §0.3) |
| `apatch_policy_verify` | `apatch policy verify` | Проверить подпись политики + дрейф конфига (added/removed/changed); secure=root-anchored |
| `apatch_verify_inclusion` | `apatch trustchain verify-inclusion [--platform-url]` | Записанные op_id включены во внешний append-only лог (Merkle proof, RFP-005 §5.10); opt-in (`APATCH_PLATFORM_URL`) |
| `apatch_scan` | `apatch scan --json` | |
| `apatch_view` | `apatch view --json` | |
| `apatch_strip_dry_run` | `apatch strip -n --json` | `file`/`file_path`, `manifest`/`manifest_path`, `strict_overlap`; returns `boundary_warnings`, `boundary_assessment` (`boundary_source`: `ast` \| `heuristic`) |
| `apatch_suggest_until` | — | AST-first until candidates (`.tsx` → grammar `tsx`); returns `line`, `text`, `kind`, `score`, `confidence`, `source` (`ast` \| heuristic); top `root_return` ≈ 0.95 |
| `apatch_strip` | `apatch strip` | Через `MutationRuntime`; session gate по `governed_mode`; `verify`, `auto_wire`, `emit_barrel`, `to_module`, `out_dir` (required for codegen), `export_filename` |
| `apatch_phase_run` | `apatch phase run` | Через `MutationRuntime`; `profile`, `native_out_dir`, multi-file manifest (`files[]`) |
| `apatch_governed_phase_run` | — | Alias `phase_run` (тот же runtime-path; отличается `finish_tool` в ответе) |
| `apatch_rollback` | `apatch rollback` / `apatch rollback --preview` | `preview=true` lists checkpoint files without restore |
| `apatch_generate` | `apatch generate` | `match_mode`, `append`; single find/replace |
| `apatch_generate_batch` | `apatch generate-batch` | `needles[]` mutation generator: `replace` (default), `create`, `delete`, `rename`, `tool_calls` passthrough; `append`; `needles_path=` JSON-файл вместо inline needles (паритет с CLI `--needles`) |
| `apatch_execute_next` | `apatch spec execute` / `apatch execute-next` | `spec`, `requirement`, `needles`, `finalize`, `dry_run`; RFP-008 |
| `apatch_spec_run` | `apatch spec run` | `spec`, `requirements` (inline, compact specs), `manifest_path` (large/versioned), `dry_run`, `resume`, `reset`, `chunk_rk_per_call` (0=all); RFP-009 |
| `apatch_spec_run_manifest_lint` | — (MCP-only) | manifest vs `SPEC.md`; gaps → `MANIFEST_GAP` |
| `apatch_project_status` | — (MCP) | Unified project DTO; CLI mirror: `apatch status --json` |
| `apatch_knowledge_graph` | — (MCP) | Session knowledge graph `{nodes[], edges[]}`; defaults to active/latest diagnostics session |
| `apatch_slug_cockpit` | `apatch slug cockpit <slug>` | Read-only slug diagnostics cockpit: intake, feedback closure, vocabulary/graph diagnostics, and optional `operational_status` JSON hook; `runtime_work_complete` prevents evidence-only debt or unavailable feedback replay from masquerading as red runtime work |
| `apatch_slug_feedback_lint` | — | Feedback-status vocabulary lint: canonical statuses, alias normalization needles, unknown-status findings (read-only) |
| `apatch_doctor` | `apatch doctor --json` | `trustchain.mode` (audit_pending\|audit\|enforce), `trustchain.behaviors`, `toolchain`, `sandbox`, `enforcement` |
| `apatch_init_consumer` | `apatch init-consumer` | `profile`, `with_ci`, `with_arch_rules` |
| `apatch_natives_check` | `apatch natives-check` | |
| `apatch_compile` | `apatch compile` | |
| `apatch_remote_task_run` | — (MCP) | RFP-029: one user-approved intent boundary for remote task timeline; `dry_run=true` plans, `dry_run=false` executes via `SshRemoteTransport`; `plan.execute_next=true` routes one exact requirement through remote mutate+finalize; `plan.slug_ratify=true` routes verify-once, batch re-attestation, and conformance gating; `plan.rebind_stale=true` remotely verifies and rebinds shared-file stale Rk without fake source mutations; a generate/simulate failure before source mutation auto-closes the broker-owned generic session, so the correct SPEC-bound retry is never blocked by a stale capability |
| `apatch_remote_source_handoff` | `apatch remote handoff` | RFP-030 opaque source broker: local source archive handoff to a remote alias without exposing GitHub/SSH fallback details to the agent |
| `apatch_remote_service_action` | `apatch remote service` | RFP-030 lifecycle broker: plan or execute policy-approved `status`/`restart`/`logs`/`healthcheck` for configured remote services |

### Opaque SSH broker aliases (RFP-030)

For governed remote work, prefer a local alias over a direct SSH URI:

```json
{
  "allowed_hosts": ["yc-*"],
  "allowed_roots": ["/home/ubuntu/projects/*"],
  "targets": {
    "search-example": {
      "host": "example-search-host",
      "path": "/srv/example/search-workspace",
      "redact": true,
      "python": "/opt/remote/bin/python",
      "ssh_args": ["-J", "bastion"]
    }
  }
}
```

`apatch_remote_task_run(remote_target="search-example", target_dir=".")` resolves the alias from local `.apatch/remote.json`, enforces host/root/operation policy, keeps SSH details out of the MCP response, and then uses the internal SSH transport. Agents should not call raw `ssh` for governed work.

### Remote finalization

`plan={"fix_forward_current": true, "defer_finalize": true, "needles": [...]}` applies a correction but does not verify, attest, or close the active session. The flag must be a JSON boolean and is supported only by `execute_next` and `fix_forward_current`.

After a required service action, `plan={"finalize_current": true}` runs mandatory verify commands resolved from the active session's exact hash-bound SPEC requirements. A caller-supplied `verify` is supplementary, never a replacement; duplicate commands run once. Missing or changed requirements, unconfirmed older workers, and unfinished asynchronous checks block attestation. Dry-run, baseline allowances, and alternate verify modes cannot weaken finalization. Ordinary non-SPEC sessions retain explicit-command verification. Update both controller and worker before relying on this safety boundary.

Onboarding command: `apatch remote init --alias search-example --host example-search-host --path /srv/example/search-workspace --health-url http://127.0.0.1:8080/health --service api`. Full guide and copy/paste Codex prompt: [remote-onboarding.md](./remote-onboarding.md#what-to-tell-codex).

`--health-url` scaffolds only a read-only `healthcheck`. Remote restarts require
explicit service metadata such as `--service-kind systemd --service-unit <unit>`
or `--service-kind docker_compose --compose-service <name>`, then
`apatch remote service --alias search-example --service api --action restart --execute`.

If the remote machine cannot fetch the repository because GitHub credentials are
not available there, do not ask the agent to run `git clone`, `scp`, or an SSH
pipe. Enable source handoff in policy and call
`apatch_remote_source_handoff(alias="search-example", execute=true)` instead. The
agent sees only alias-level status; archive transport, SSH host/path, and
credential topology stay inside the local broker.

| `apatch_session_start` | `session start` | `intent` (required); `artifacts` — `kind:id@hash` or dicts (RFP-006); `requirement='SPEC-42#R3'` (RFP-007 shortcut) |
| `apatch_spec_lint` | `apatch spec lint` | **Agent onboarding gate** — format + RFP gates + `plan_scaffold` when `passed`; `spec`, `spec_path`. Prefer over standalone `apatch_rfp_*` / `apatch_spec_needles_scaffold` (may be absent in IDE tool list) |
| `apatch_spec_needles_scaffold` | `apatch spec needles-scaffold` | RFP-024 CLI/optional — same scaffold embedded in `apatch_spec_lint` when `passed` |
| `apatch_rfp_lint` | `apatch rfp lint` | RFP-023 CLI/optional — embedded in `apatch_spec_lint` when SPEC has traceability |
| `apatch_rfp_spec_coverage` | `apatch rfp coverage` | RFP-023 CLI/multi-spec — embedded in `apatch_spec_lint`; `specs` comma-list for aggregate |
| `apatch_spec_scaffold` | `apatch spec scaffold --from-contract` | RFP-023 **authoring side** — emit a SPEC.md skeleton from an RFP `## Acceptance` table (one Rk per row + traceability gate), contract-complete by construction; fill the `(verify:)` placeholders. `rfp`, `spec`, `out_path`, `mandatory_only` |
| `apatch_spec_status` | `apatch spec status` | Per-requirement coverage from ledger (`pending`…`stale`); file-drift via RFP-010 |
| `apatch_probe` | `apatch probe falsify\|regress\|ratify` | RFP-005 differential probe: `mode`, `verify` (any shell cmd), `files` (falsify), `baseline_failures`/`allowed_failures` (regress) → `real`/`false`/`stable`/`regressed`/`ratified`/`stale` |
| `apatch_reality` | `apatch reality add\|status` | RFP-005 observed-reality ledger: `action`, `summary`/`source`/`kind` (add), `spec` (status) → coverage with `uncovered` = derived debt |
| `apatch_slug_intake` | `apatch slug intake <slug>` | Slug/category cockpit: matching SPECs, reality records, conformance summary, query-first gate, data/dictionary evidence, and a SPEC template. Read-only; `live=false` never runs verify. |
| `apatch_slug_close` | `apatch slug close <slug>` | Slug feedback closure assistant: replays owner TSV rows through live API with `debug=true`, reads `decision_graph`, proposes `fixed`/`catalog_gap`/`other_slug`/`open_runtime_bug`, and returns governed mutation needles. Read-only. |
| `apatch_slug_ratify` | `apatch slug ratify <slug>` | Single-call slug ratification: resolve → lint → verify **once per unique command** → one governed session + one signed batch-attestation for every green open Rk → conformance from the same evidence. Broken verifies on open Rk block them; already-closed renamed/missing test-node references are advisory and do not block green open rebind. No marker files or N× lifecycle. `dry_run` previews. |
| — | `apatch status` | Rich CLI dashboard + `--json` (same DTO as `apatch_project_status`) |
| — | `apatch spec list` | Discover executable spec ids from `docs/specs/SPEC-*.md` |
| — | `apatch report --html` | Self-contained architect HTML report |
| — | `apatch report --format md` | Manager markdown summary (`--locale ru|en`) |
| `apatch_spec_coverage` | `apatch spec coverage` | Per-Rk matrix `{state, files[], drifted[], attested_at, session_id, op_ids[]}` + aggregate (RFP-010) |
| `apatch_spec_interference` | `apatch spec interference` | Cross-spec L1/L2 conflicts, `safe_order`, `risk_score` (RFP-014 Phase 1) |
| `apatch_spec_schedule` | `apatch spec schedule` | Enriched schedule view over interference; `risk_per_step` (RFP-014 Phase 1.5) |
| `apatch_spec_cross_verify` | `apatch spec cross-verify` | Level-3 sandbox: apply source needles → run victim verify → rollback (RFP-014 Phase 2) |
| `apatch_spec_run_multi` | `apatch spec run-multi` | **Phase 3:** N specs in `safe_order`; each explicit per-spec `requirements` map is a bounded work list (unrelated open Rk are skipped/reported); `cross_verify` gate; auto-stop + rollback. Prefer over N× manual `spec_run`. See AGENTS.template §3L |
| `apatch_spec_next` | `apatch spec next` | Next open requirement + its `verify` command |
| `apatch_spec_plan_lint` | — (MCP) | Lint inline `plan` dict vs `SPEC.md` requirements (RFP-011) |
| `apatch_spec_plan_register` | — (MCP) | Register signed plan → `.apatch/plans/` + ledger op `plan:SPEC-X@vN` |
| `apatch_spec_plan_diff` | — (MCP) | Diff registered plan versions per Rk (`from_version`, `to_version`) |
| `apatch_spec_plan_show` | — (MCP) | Full registered plan from `.apatch/plans/` including `decision_plan` (RFP-011 R6) |
| `apatch_spec_adherence` | — (MCP) | Plan-vs-fact adherence per Rk; aggregate `{adherent, deviated}` (RFP-012) |
| `apatch_session_end` | `session end` | closes governed session |
| `apatch_recover` | `session recover <session_id>` | exact one-call recovery: finish interrupted cleanup, release only owned stale state, close completed work, or rotate a resumable capability; never steals a foreign live lease |
| `apatch_session_state` | `session status` | lifecycle, invariant |
| `apatch_sdd_verify` | — (MCP) | Fixed-purpose execution of the exact session-bound frozen judge and reversible falsification; caller supplies no command or result |
| `apatch_verify_run` | `verify run` | default: shell `recommended_verify_resolved`; или `verify=` (строка или argv-список), `semantic`, `notarization`, `pipeline_manifest`; baseline: `baseline=capture\|compare`, `allowed_failures[]`; **async:** `async_mode=True`, CLI `--async` (AR-2) |
| `apatch_attestation_export` | `attestation export` | `out_path`, `event_limit` |
| `apatch_events_tail` | — | `limit`; read-only domain events |
| `apatch_attestation_show` | `attestation show` | mode, HEAD |
| `apatch_attest` | `attestation commit` | TrustChain commit |
| `apatch_commit_attested` | `commit-attested` | Exact `session_ids`, `message`, optional `push`/`remote`, `dry_run`; rejects drift and unrelated staged scope |
| `apatch_verify_status` | `verify status` | без `job_id`: policy + recommended verify; **с `job_id`:** poll async verify job |

Every enriched MCP response includes `invariant`, `core_invariant`, `state_update.next_action` (MCP tool names).

### Mutation needles (`apatch_generate_batch`)

Единый генератор JSONL — агент не собирает `*** Add File:` вручную:

| `action` | Поля | JSONL step |
|----------|------|------------|
| `replace` (default) | `find_text`, `replace_text`, `target_file?`, `match_mode?` | `replace_file_content` |
| `create` | `target_file`, `content` | `apply_patch` → Add File |
| `delete` | `target_file` | `apply_patch` → Delete File |
| `rename` | `source_file`, `target_file` | Add File + Delete File |
| **`shift_outline`** | `target_file`, `after`, `levels?` (default `[2,3]`), `delta?` (default `1`) | full-file `replace_file_content` |
| **`insert_before`** | `target_file`, `before`, `content` | full-file replace |
| **`insert_section`** | `target_file`, `before`, `content`, `shift_following?` `{levels, delta}` | full-file replace — типовой «новый §N» |
| passthrough | `tool_calls: [...]` | как в transcript |

**Numbered markdown** (`## 3.`, `### 3.1`): используй `insert_section` + `shift_following`, **не** N× `replace` по строкам заголовков ([SPEC-DOC-OUTLINE-1](./specs/SPEC-DOC-OUTLINE-1.md)). Якорь — substring в строке заголовка (`"## 3."`). Verify для `.md`: `grep`, не обязательно pytest.

Цикл: `generate_batch` → `simulate` → `apply_session` → `verify_run` → `attest`. Рецепт: [cookbook.md](./cookbook.md).  
`apatch_execute_next(needles=[…])` вызывает тот же `generate_batch` внутри (RFP-008). Если needles меняют саму активную SPEC, старая preflight verify-команда не запускается: после apply требование перечитывается, finalize использует текущую команду, а attestation получает свежий requirement hash — повторно «перезакрывать» Rk не нужно. Если generate/simulate падает до source mutation, открытая этим вызовом сессия закрывается автоматически. Для `apatch_apply_session(verify_deferred=true)` verify откладывается до последнего чанка всей сессии, чтобы код и его тесты могли находиться в разных чанках.

**Spec run (RFP-009):** `apatch_spec_run(requirements={Rk: {needles: [...]}})` — loop по pending `Rk`
(session → batch → apply → verify → attest per Rk). State: `.apatch/spec_run.json`. Рецепт: [cookbook.md § spec-run](./cookbook.md#spec-run), consumer §3K.

TrustChain is enabled by default for `apply`, `strip`, `phase_run`, and `compile` unless `no_trustchain=true`.

### `apatch_apply` response on verify failure

When `verify` fails in non-interactive mode, files are rolled back. Check:

- `ok: false`
- `verify_rollback: true`
- `rolled_back` ≥ 1
- `entries[].outcome` may be `rolled_back` with `reason: verify_failed`

For **multi-file** `apatch_phase_run`, verify failure also removes strip artifacts (`rollback.removed_artifacts` in the response).

`apatch_plan_batch` reports `strategy: json-semantic` for mapping files when structure matches but whitespace/key order differs.

### Orchestration tools (R41–R46 implemented)

Roadmap: [orchestration.md](./orchestration.md).

| Tool | CLI |
|------|-----|
| `apatch_arch_check` | `apatch arch check` |
| `apatch_impact` | `apatch impact` |
| `apatch_db_check` | `apatch db check` |
| `apatch_db_revision` | `apatch db revision` |
| `apatch_db_safety` | `apatch db safety` |
| `apatch_db_run` | `apatch db run` |

`apatch_apply` supports `budget` (`small` \| `medium` \| `large`), `max_files`, `max_insertions`, `max_deletions` (R49).

| `apatch_refactor_run` | `apatch refactor run` |
| `apatch_verify_semantic` | `apatch verify semantic` |
| `apatch_pipeline_run` | `apatch pipeline run` |
| `apatch_index_build` | `apatch index build` |
| `apatch_index_query` | `apatch index query` (symbols, routes, migrations, ADRs) |
| `apatch_trustchain_history` | `apatch trustchain history` (`query`, `artifact`) |
| `apatch_trustchain_coverage` | `apatch trustchain coverage` (`artifact`, `op_id`) |

### Understanding / WorkAssets / impact tools

| Tool | CLI equivalent | Notable |
|------|----------------|---------|
| `apatch_build_diagnose` | `apatch build diagnose` | Build/compiler error triage (RFP-018) |
| `apatch_rebind_stale` | `apatch spec rebind-stale` | RFP-027/041: re-verify + noop-attest `file_drift`-stale Rk; `requirement_ids[]` and `exclude_requirement_ids[]` constrain the exact set before any session opens |
| `apatch_scip` | `apatch scip index\|impact` | RFP-033: cross-file reference impact — native by default (no scip-python), `.scip` when present; advisory, never blocks |
| `apatch_work_assets` | `apatch work-assets list` | RFP-031: WorkAsset read model (list) |
| `apatch_work_asset_show` | `apatch work-assets show` | One WorkAsset (metadata-only) |
| `apatch_work_asset_search` | `apatch work-assets search` | Search WorkAssets |
| `apatch_work_asset_export` | `apatch work-assets export` | Stable `apatch.work_asset_export.v1` bundle (`--json`) |
| `apatch_work_asset_export_schema` | `apatch work-assets schema` | JSON Schema for the export bundle |
| `apatch_work_asset_suggest` | `apatch work-assets suggest` | Avatar Recall (AUC-1/A31-E): intent → proven recallable methods, explainable reasons |
| `apatch_work_asset_recall` | `apatch work-assets recall` | RecallBundle: money-free method distillate, integrity-checked against the signed pin |
| `apatch_work_asset_use` | `apatch work-assets use` | Signed use record after verify (A31-F) — reuse counters grow only from the ledger |
| `apatch_asset_summary` | `apatch asset summary` | Aggregate WorkAsset summary |
| `apatch_avatar_episodes` | `apatch avatar episodes` | WorkEpisode v2 facts and exclusion reasons |
| `apatch_avatar_capabilities` | `apatch avatar capabilities` | Evidence-linked estimates, intervals, attribution, uncertainty |
| `apatch_avatar_evidence_export` | `apatch avatar evidence-export` | Signed CapabilityEvidence v2 metadata distillate |
| `apatch_avatar_evidence_sync` | `apatch avatar sync` | Evidence-first durable delivery plus authenticated timeline batches |

### Artifact-anchored intent (RFP-006) — agent playbook

Typed engineering decision refs (`spec`, `adr`, `ticket`, `incident`, `compliance`, …) bind to **Session**, not to a separate runtime. One pipeline; `kind` is data, not a command.

```text
Artifact → Intent → Session → Mutation → Verification → Attestation
```

**Start session with anchors:**

```text
apatch_session_start(
  target_dir=".",
  intent="implement billing v2",
  artifacts=["spec:SPEC-42@sha256:…", {"kind": "adr", "id": "ADR-001"}]
)
```

**During apply:** ledger commits under an active governed session auto-stamp `governed_session_id`, `intent`, `artifacts[]` (no extra MCP call).

**After attest — traceability:**

```text
apatch_trustchain_coverage(target_dir=".", artifact="spec:SPEC-42")
# coverage.complete == true → mutation + attestation both linked

apatch_trustchain_coverage(target_dir=".", op_id="<ledger op_id>")
# reverse: which artifact(s) does this operation belong to?

apatch_trustchain_history(target_dir=".", artifact="spec:SPEC-42")
# prior intent/artifact checkpoints in ledger
```

**Read session:** `apatch_session_state` → `session.artifacts[]`.

**Artifact token format:** `kind:id` or `kind:id@content_hash` (hash optional but recommended for tamper-evident spec version).

**Not in MCP (RFP-006):** clause-level coverage below requirement (`Rk`); Jira/wiki body storage. See [RFP-006-artifact-anchored-intent.md](./RFP-006-artifact-anchored-intent.md).

### Executable specifications (RFP-007) — agent playbook

`SPEC.md` in `docs/specs/` becomes trackable requirements. Canonical RFP: [RFP-007-executable-specifications.md](./RFP-007-executable-specifications.md). Authoring standard: [spec-authoring.md](./spec-authoring.md).

```text
SPEC.md  →  spec:ID#Rk@hash  →  session(requirement)  →  mutate  →  verify  →  attest
```

**Before tracking:**

```text
apatch_spec_lint(target_dir=".", spec_path="docs/specs/SPEC-42.md")
```

**Execution loop:**

```text
apatch_spec_next(target_dir=".", spec="SPEC-42")
apatch_session_start(target_dir=".", requirement="SPEC-42#R1")
# … apply_session …
apatch_verify_run(target_dir=".", verify="<from spec_next>")
apatch_attest(target_dir=".")
apatch_spec_status(target_dir=".", spec="SPEC-42")
```

Status is **derived from the ledger**, not a checkbox in markdown. Details: [executable-specs.md](./executable-specs.md).

## Architecture

MCP tools do **not** reimplement commands. They call `apatch.workflows`, the same Python layer the CLI uses for business logic.

```
apatch.cli (human UI)  ──┐
apatch.mcp.server      ──┼──> apatch.workflows ──> matcher / strip_pipeline / tui / …
```

## AGENTS.md for consumer repos

```bash
apatch init-consumer --target-dir . --profile frontend
# or: sqlalchemy | prisma | django | elastic | cosmos
```

Copies **[AGENTS.template.md](./AGENTS.template.md)** → `AGENTS.md` (stack slice в `<!-- apatch:stack:* -->`, project backlog в `<!-- apatch:project:* -->`).  
Обновить без потери project-блока: `apatch init-consumer --refresh-agents`.

Domain guides: [docs/profiles/](./profiles/).

## Stdio transport (Cursor / IDE)

MCP uses **stdin/stdout** for JSON-RPC. Any Rich/emoji log line on **stdout or merged stderr** corrupts the channel (`invalid character 'ð'` / connection closed). Byte `0xf0` is the UTF-8 lead byte for emoji (🛡️, ⚡, …).

**Mitigations (built into `apatch-mcp`):**

1. `apatch.mcp.launcher` activates `stdio_guard` before other imports
2. File descriptor 2 → `.apatch/mcp_stderr.log` (warnings, Rich, `tc` stderr)
3. `PYTHONIOENCODING` / `LC_ALL` defaults when the host omits them
4. MCP apply paths force `quiet=True` on `InteractiveTUI`

If the IDE still fails, confirm `command` is `apatch-mcp` (not `python -m apatch.cli`) and add the `env` block above.

- `apatch_apply` runs the apply loop with **`quiet=True`** (no TUI output on stdout).
- All tool returns pass through **UTF-8 sanitization** and size caps (`apatch.mcp.sanitize`).
- Large applies: set `report_path`; MCP returns a summary (`entries_truncated` when needed).

For multi-minute monorepo scans, prefer CLI in a background terminal or split with `steps` / `budget` — MCP still blocks until the call returns.

## Write sandbox + MCP whitelist (consumer)

`apatch init-consumer --with-sandbox` copies `.apatch/sandbox.json` and Cursor hooks:

| Hook | CLI test | Effect |
|------|----------|--------|
| `preToolUse` | `apatch sandbox hook-pre-tool` | Block direct Write/StrReplace in protected zones |
| `beforeShellExecution` | `apatch sandbox hook-shell` | Block sed/tee/shell redirects |
| `beforeMCPExecution` | `apatch sandbox hook-pre-mcp` | Allow `apatch_*` + `mcp_extra_servers` (default includes `cursor-ide-browser` for read-only UI inspection) |

Protected default: `src/**`, `app/**`, `services/**`, `packages/**`. **Mutations:** only `apatch_*` MCP. **Inspection:** `cursor-ide-browser` allowed by default (navigate/snapshot — does not bypass write sandbox). More servers: `"mcp_extra_servers": ["my-server"]` in `sandbox.json` (unioned with defaults).

CI: `apatch sandbox ci-gate` or `scripts/ci/apatch-sandbox-gate.sh` (with `--with-ci`). Spec: [sandbox.md](./sandbox.md).

## TrustChain enforcement (consumer)

Check mode first: `apatch_doctor` → `trustchain.mode`:

- **audit_pending** / **audit** — soft ledger (see `trustchain.summary`)
- **enforce** — strict path after `init-consumer --with-enforcement`

`apatch init-consumer --with-enforcement` writes `.apatch/enforcement.json` (`governed_mode: auto_session` by default) and `scripts/hooks/pre-commit-trustchain.sh`.

### governed_mode

| Значение | MCP-мутации без `apatch_session_start` |
|----------|------------------------------------------|
| `off` | Разрешены (plain consumer) |
| `auto_session` | Авто-сессия + `SessionStarted(auto_started=true)` |
| `strict` | Блок с hint `apatch_session_start` |

`apatch_doctor` → `enforcement.governed_mode`. Override: `APATCH_GOVERNED_MODE`.

When **enforce** is active:

- `no_trustchain` is rejected on apply
- Each successful apply chunk is notarized once for all surviving files; failed `commit_action` rolls back the chunk
- The normal write path validates the appended object, Ed25519 signature, length transition, and HEAD in O(1)
- `.apatch/notarized_index.json` v2 tracks file sha256 plus ledger length/HEAD; full traversal requires explicit `rebuild_index=true` audit/recovery
- `apatch verify notarization --staged` / MCP `apatch_verify_notarization` blocks unproven changes
- Git hook blocks commit without proof

See [RFP-038](./RFP-038-ledger-hot-path.md) and the
[governed runtime invariants](./governed-runtime-invariants.md).

## Runtime hygiene (RFP-016)

`apatch_doctor` → `hygiene`:

| Field | Meaning |
|-------|---------|
| `orphan_count` | EPHEMERAL on disk with `gc_allowed` — run `apatch_gc(mode="safe")` |
| `inferred_count` | Classified on disk but not in registry — run `apatch_gc(mode="reconcile")` |
| `status` | `clean` \| `degraded` \| `critical` (inference sunset blocks governed apply when `inferred_count > 0` and governed ops ≥ 100) |
| `gc_recommendation` | Usually `apatch gc --dry-run` |

**Agent rules:**

1. `session_start` before `generate_batch` (JSONL → `.apatch/tmp/<session>/`, registered).
2. Always `session_end` after attest (tmp JSONL purged).
3. SPEC work → `apatch_spec_run` (closes sessions per Rk).
4. Never hand-create `patches-*.jsonl` in repo root.

```bash
apatch gc --dry-run    # report
apatch gc --reconcile  # register inferred paths (drops inferred_count; no delete)
apatch gc --safe       # delete EPHEMERAL + GARBAGE
apatch gc --rotate     # safe + prune HISTORY/DEBUG
```

MCP: `apatch_gc(mode="report"|"reconcile"|"safe"|"rotate")`, `apatch_mcp_hygiene` (ghost MCP / lease contention).

**Inference sunset:** after ≥100 governed ops, unregistered inferred artifacts block `apply_session`. Remediation: `apatch gc --reconcile`. During multi-chunk apply, apatch auto-reconciles inferred before blocking.

Consumer install adds `patches-*.jsonl` to `.gitignore` via `init-consumer`. Details: [RFP-016](./RFP-016-runtime-hygiene.md) · [AGENTS.template §1.2](./AGENTS.template.md).

## Tests

```bash
pytest tests/test_mcp.py tests/test_mcp_stdio_live.py tests/test_workflows.py -v
```

Live stdio smoke tests spawn `python -m apatch.mcp.server` (same path Cursor uses).
