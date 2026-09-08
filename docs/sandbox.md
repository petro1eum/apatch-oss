# Write sandbox v1 — спецификация

> **Статус:** P0–P1.5 реализованы (lease, hooks, audit, auto-revert).

Цель v1: **minimal enforcement kernel for write control**, не полноценный filesystem monitor.

```
Prevent (Cursor hooks)  →  Runtime (write lease)  →  Detect (TrustChain + git hook)
```

---

## 1. Protected surface

По умолчанию защищается **production + verify + executable-spec boundary**:

```json
{
  "protected_globs": [
    "src/**",
    "app/**",
    "services/**",
    "packages/**",
    "e2e/**",
    "server/**",
    "server.js",
    "scripts/**",
    "docs/specs/**",
    "playwright.config.ts",
    "vitest.config.ts"
  ],
  "allow_globs": [
    "manifests/**",
    "patches/**",
    "tests/**",
    "docs/*.md",
    "*.md",
    ".apatch/**",
    ".cursor/**",
    ".trustchain/**",
    ".git/**",
    "patches.jsonl"
  ],
  "watcher": "revert"
}
```

`allow_globs` перекрывает `protected_globs` при совпадении.  
`docs/specs/**` — только через apatch (`apatch_execute_next` / `apatch_spec_run`); narrative docs остаются в `docs/*.md`.

---

## 2. Режимы

| `mode` | Hooks | Lease на apply | Watcher |
|--------|-------|----------------|---------|
| `off` | no-op | нет | `off` |
| `audit` | log | нет | `audit` (log only) |
| `enforce` | deny | обязателен | `audit` или `revert` |

Конфиг: `.apatch/sandbox.json`  
Включение: `apatch init-consumer --with-sandbox` (+ рекомендуется `--with-enforcement`)

### governed_mode (session gate)

Ключ в `.apatch/enforcement.json` (независим от `mode` TrustChain):

| `governed_mode` | Поведение |
|-----------------|-----------|
| `off` | Мутации без сессии (дефолт plain-консьюмера) |
| `auto_session` | **Дефолт `--with-enforcement`**: авто-создание сессии с intent `auto: <op> via MCP`, событие `SessionStarted(auto_started=true)` |
| `strict` | Блок мутации без явного `apatch_session_start` |

Remote finalization (`fix_forward_current` / `finalize_current`) additionally requires completion of the session's hash-bound SPEC checks. `defer_finalize=true` leaves the session open after apply; it does not waive verification. See [MCP finalization](./mcp_setup.md#remote-finalization).

Override: env `APATCH_GOVERNED_MODE`. CLI: `apatch init-consumer --with-enforcement --governed-mode strict`.

---

## 3. File creation policy

| Действие | Разрешено |
|----------|-----------|
| `apatch_generate` → JSONL | да |
| `apatch_apply` / `apatch_apply_session` в protected | да (под lease) |
| IDE `Write` / `StrReplace` в protected | **нет** (enforce) |
| `sed` / `tee` / heredoc в protected | **нет** (shell hook) |
| Прямое создание `.py` в `tests/**` | да (не в protected) |
| Новые `.py` в `src/**` | только через apply_session |

---

## 4. Write lease — capability token

Lease = **одноразовый capability** одной governed session на запись в
канонический набор путей. Несколько непересекающихся lease могут жить одновременно.

```json
// .apatch/write_leases.json
{
  "version": 2,
  "revision": 41,
  "workspace_root": "/real/workspace",
  "leases": {
    "lease_8f3a1b2c": {
      "governed_session_id": "apatch_sess_...",
      "pid": 12345,
      "tool": "apatch_apply_session",
      "paths": ["app/models/user.py"],
      "canonical_paths": [{"path": "app/models/user.py", "canonical_path": "/real/workspace/app/models/user.py"}],
      "issued_at": "2026-08-26T12:00:00Z",
      "expires_at": "2026-08-26T12:05:00Z"
    }
  }
}
```

### Семантика (syscall model)

| Операция | Кто вызывает | Условие |
|----------|--------------|---------|
| `acquire set` | apatch runtime | перед mutate: весь planned write-set атомарно или отказ без частичного lease |
| `commit` | TrustChain success | notarize → `release` |
| `abort` | rollback / verify fail | `release(aborted=true)` |

Агент **не** вызывает `acquire` — только apatch internals.

### Проверка пути

```text
write(path):
  if sandbox.mode != enforce: ALLOW
  if path in allow_globs: ALLOW
  if path not in protected_globs: ALLOW
  if exact_session_lease.covers(canonical(path)) and lease.valid(): ALLOW
  else: DENY (DIRECT_WRITE_BLOCKED)
```

### Cross-workspace alias boundary

При запуске через `workspace_launcher` target policy равен `alias_only`. Перед sandbox
resolution допускаются только bound `target_dir="."` и человеко-зарегистрированный
`target_dir="@alias"`. Реестр закрепляет identity корня и SHA-256 `AGENTS.md`; drift
возвращает `WORKSPACE_IDENTITY_DRIFT` или `WORKSPACE_CONTRACT_DRIFT`. После resolution
lease, protected globs, enforcement и TrustChain загружаются из effective workspace.

### Параллельные агенты (lanes)

Lane разделяет состояние и артефакты сессий, а workspace-wide lock table
`.apatch/write_leases.json` координирует их фактические write-set. Lane выбирается
так: явный `APATCH_LANE` → активный spec → git-ветка → `default`.

Сессии на `apple/**` и `o_lang/**` могут писать одновременно. `src/a.py` конфликтует
с `src/a.py` и `src/`, включая symlink, alias workspace, rename source/target и
регистр на case-insensitive filesystem. Захват не ждёт: при конфликте возвращаются
точные requested/held paths, session, lease, pid и tool.

Чтобы гонять несколько агентов параллельно — дай каждому свой lane:

- **отдельная git-ветка** на агента (самое простое: ветка = lane), или
- **git worktree** — `apatch lane new <id>` одной командой создаёт изолированный
  worktree (своя рабочая копия + ветка + `.apatch`-состояние), `apatch lane rm <id>`
  убирает. (Для spec-исполнения apatch роутит в worktree автоматически по
  `manifests/worktrees.yaml`.) Или
- явный `APATCH_LANE=<id>` в окружении агента.

APatch **никогда не отбирает** чужой активный пересекающийся lease. Мёртвые и
истёкшие записи удаляются независимо: устаревший `apple/**` lease не блокирует
`o_lang/**`. Живой владелец старого `.apatch/write_lease.json` сохраняет исходный
capability и может штатно завершиться; для v2 он остаётся глобальным до release,
смерти или expiry. Когда активны реальные v2 writers, старый клиент видит ограниченный
их TTL barrier `APATCH_UPGRADE_REQUIRED`. После release/expiry barrier исчезает сам,
а MCP startup очищает stale state во всех зарегистрированных aliases.

---

## 5. Cursor hooks (P0)

Шаблоны: `scripts/cursor-hooks/` → копируются в `.cursor/hooks/`.

| Hook | Matcher | Действие |
|------|---------|----------|
| `preToolUse` | `Write\|StrReplace\|…` | deny protected без lease |
| `beforeShellExecution` | `sed\|awk\|tee\|>` | deny **файловых** мутаций shell |
| `beforeMCPExecution` | (все MCP) | whitelist `apatch_*` + `mcp_extra_servers` (default: `cursor-ide-browser`) |

Проверка: `apatch sandbox hook-pre-tool` / `hook-shell` / `hook-pre-mcp` (stdin JSON → stdout permission JSON).

**Что блокирует `beforeShellExecution` (quote-aware, `detect_shell_mutation`):**

- Блокирует только **shell-level** мутации файлов вне кавычек: `cmd > file`, `cmd >> file`, `sed`, `awk`, `tee`, `perl -pi`.
- **НЕ** блокирует read-only команды: `>`/`<` и `sed`/`tee` внутри кавычек (`python -c "assert a < b"`, `echo '2>&1 sed'`), fd-редиректы (`2>&1`, `>&2`), сбросы в `/dev/null` (`2>/dev/null`, `>/dev/null`, `&>/dev/null`), пайпы (`| tail`), `pytest`, build, lint, `git`.
- Matcher в `hooks.json` намеренно широкий (включает `>`), чтобы хук *проинспектировал* команду; финальное решение принимает `detect_shell_mutation`, поэтому quoted/fd-случаи возвращают `allow`.

**Важно:** хуки энфорсят только при committed `.apatch/sandbox.json` (`is_sandbox_enabled`). Нет конфига → `allow` (согласовано с `apatch_doctor.sandbox.enabled`). Чтобы включить — `apatch init-consumer --with-sandbox`.

**MCP whitelist (enforce):** по умолчанию разрешены `apatch_*` и **`cursor-ide-browser`** (навигация/snapshot для визуального осмотра — read-only, не пишет в protected). Дополнительные серверы — в `sandbox.json`:

```json
"mcp_extra_servers": ["cursor-ide-browser", "my-readonly-mcp"]
```

Даже если в committed `sandbox.json` массив пустой, `effective_mcp_extra_servers` всегда включает встроенный browser (см. `DEFAULT_MCP_EXTRA_SERVERS` в `apatch/sandbox.py`).

`doctor` → `sandbox.cursor_hooks_installed`.

---

## 6. Watcher (P1 audit + P1.5 revert)

| `watcher` | Поведение |
|-----------|-----------|
| `off` | выкл |
| `audit` | лог в `.apatch/sandbox_violations.jsonl` |
| `revert` | лог + **auto-revert** каждый проход (**default** в `init-consumer`) |

```json
{
  "watcher": "revert",
  "revert_untracked": true
}
```

**Auto-revert:**
- tracked → `git checkout HEAD -- <path>`
- untracked в protected → удаление файла (`revert_untracked: true`)

```bash
apatch sandbox audit --json                    # audit only (unless watcher=revert)
apatch sandbox audit --auto-revert --json      # one-shot revert
apatch sandbox watch --interval 2              # loop (watcher: audit|revert)
```

MCP: `apatch_sandbox_audit(auto_revert=false)` — revert также при `watcher: revert`.

Не делать watcher обязательным — избегаем «двойной правды» hook vs watcher.

---

## 7. CI gate (P2)

```bash
apatch sandbox ci-gate --target-dir . --json
# или scripts/ci/apatch-sandbox-gate.sh .

# На PR — проверять diff против merge-base (Ring-2 authority над общей историей):
# ловит изменения, обошедшие локальный хук (git commit --no-verify).
apatch sandbox ci-gate --base origin/main --json
```

Транзакционное поведение локальной команды:
- нет `.apatch/sandbox.json` и нет enforcement → **skip** (exit 0)
- `--base <ref>` → точный набор `ref..HEAD` + staged index; unrelated unstaged/untracked work не входит в PR-гейт
- без `--base` → рабочее дерево, включая untracked protected paths
- sandbox on → запись допустима по live exact path lease либо по exact committed proof
- committed proof = SHA-256 подписанной мутации + более поздняя signed attestation той же governed session
- signed, но ещё не attested mutation не считается завершённой и блокируется
- enforcement on → notarization проверяется ровно для candidate paths (fail → `rejection_prompt`)

`.apatch/notarized_index.json` v3 разделяет `files` (последняя подписанная мутация,
включая pending) и `committed_files` (последнее полностью attested состояние).
Поэтому failed attempt/rollback не уничтожает более ранний валидный proof, а
`session_end` не превращает корректные staged bytes в ложное нарушение из-за
освобождённого lease.

Важно: lease, локальный ledger и notarized index намеренно ephemeral/ignored. Поэтому fresh CI checkout не может честно доказать их наличие и не должен запускать lease-based `ci-gate` как provenance-гейт: легитимные governed-коммиты будут ложноположительно красными. Workflow от `apatch init-consumer --with-ci` проверяет переносимый committed control plane (`sandbox=enforce`, `enforcement=strict`, `block_commit_without_proof`) и, если `manifests/apatch-inclusion.jsonl` намеренно tracked, требует публичный Merkle proof. Без tracked manifest job явно сообщает, что provenance в CI не доказан.

Consumer: `apatch init-consumer --with-ci --with-sandbox --with-enforcement` копирует этот workflow; `scripts/ci/apatch-sandbox-gate.sh` остаётся локальной/сохранённой-state диагностикой.

---

## 8. Devcontainer (P2+)

```bash
apatch init-consumer --with-devcontainer --with-sandbox --with-enforcement
```

Копирует `.devcontainer/devcontainer.json` (Python 3.12, apatch preinstall, `apatch doctor` on create).  
Изолированная среда для агентов: меньше риска случайных правок вне sandbox hooks на хосте.

---

## 9. Surface

| MCP | CLI |
|-----|-----|
| `apatch_sandbox_status` | `apatch sandbox status --json` |
| — | `apatch sandbox check-path --path app/x.py` |
| — | `apatch sandbox hook-pre-tool` (для `.cursor/hooks`) |

---

## 10. Failure taxonomy

| `error_type` | Когда |
|--------------|-------|
| `DIRECT_WRITE_BLOCKED` | hook deny / apply без lease |
| `LEASE_EXPIRED` | lease истёк mid-apply |
| `LEASE_CONFLICT` | другой pid держит lease |

---

## 11. Roadmap

| PR | Содержание |
|----|------------|
| **P0** | `sandbox.py`, lease, hooks, apply_session wire, status |
| **P1** | `sandbox audit` / `watch`, `.apatch/sandbox_violations.jsonl` |
| **P1.5** | `watcher: revert`, `--auto-revert`, untracked removal |
| **P2** | `apatch sandbox ci-gate` + GitHub Action job `sandbox-gate` |
| **P2+** | `.devcontainer/` via `init-consumer --with-devcontainer` |
