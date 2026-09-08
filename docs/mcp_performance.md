# MCP performance — hot path, benchmarks, backlog

**Обновлено:** 2026-07-14 · **Контракт:** [RFP-038](./RFP-038-ledger-hot-path.md)

Документ фиксирует оптимизацию stdio MCP apatch (2026-06): что было медленно, что исправили, замеры до/после и что ещё можно ускорить без потери governed lifecycle.

---

## Симптом

MCP apatch **функционально работал**, но ощущался «тяжёлым»:

- Каждый `apatch_*` tool (даже `apatch_session_state`) мог занимать **десятки секунд** на consumer-репозиториях среднего размера.
- `apatch_doctor` на TrustChain_Agent — **~48 с** (до фикса).
- Агенты получали **полный doctor + warnings** на каждый ответ → лишний контекст в чате.

Корневая причина — не JSON-RPC и не tree-sitter, а **архитектурный overhead**: полная диагностика вызывалась на hot path каждого MCP-ответа.

---

## Что исправлено

### P0 — lightweight policy snapshot (`dd65a27`)

| Было | Стало |
|------|-------|
| `enrich_tool_response` → `build_session_view` → **`run_doctor()`** на каждый tool | `build_policy_snapshot()` — только trustchain / sandbox / enforcement |
| `_derive_next_action` в `idle` снова вызывал `build_session_view` (2× doctor) | Чтение `load_session_state()` напрямую |
| `build_verification_status` — doctor + session_view (двойной doctor) | Один `run_doctor`; `build_session_view` без doctor |

Новая функция: `apatch.doctor.build_policy_snapshot(workspace)` — те же три поля `policy`, что и в doctor, без toolchain/rglob/mcp_health.

**Не потеряно:** sandbox, TrustChain enforce, `state_update`, `invariant`, notarization, rollback.  
**Убрано с hot path:** warnings, grammars, mcp_health probes, recommended_verify — остаются в явном `apatch_doctor`.

### P1 — toolchain, кэши, lean mcp_health (`3d6e1dc`)

| Область | Изменение |
|---------|-----------|
| `detect_toolchain` | `rglob("*")` для elastic/cosmos → `list_workspace_file_paths()` (git index / pruned walk) |
| `detect_toolchain` | In-process кэш по mtime маркеров + `.git/index` |
| `PathIndex.build` | Кэш на workspace до смены git index |
| `apply_session` | Один `plan_from_logs` (lease paths из `layout.lease_paths`) |
| `build_mcp_health` | Кэш subprocess probe; без probe `apatch-mcp` binary когда current interpreter ok |
| `mcp_health.ok` | Для consumer с `.apatch/mcp.json` — ok по canonical config |

---

## Бенчмарки (2026-06-09)

Окружение: macOS, TrustChain_Agent (~973 git-tracked files), apatch `0.2.0`, MCP Python 3.14.

### TrustChain_Agent

| Операция | До | После |
|----------|-----|-------|
| `run_doctor` | ~48 с | **~1.2 с** |
| `detect_toolchain` (внутри doctor) | ~41 с | **~0.7 с** |
| `enrich_tool_response` × 1 | ~84 с | **~0.001 с** |
| `build_session_view` × 1 | ~48 с | **~0.001 с** |
| MCP `apatch_session_state` (stdio) | (не измерялось отдельно; ≈ enrich) | **~0.09 с** |
| MCP `apatch_doctor` (stdio) | ~48 с | **~1.2 с** |

### apatch (dogfood, ~309 git files)

| Операция | До | После |
|----------|-----|-------|
| `run_doctor` (cold / warm) | 3.9 с / 2.6 с | **~3 с / ~2 с** (малый репо — toolchain не доминировал) |
| `enrich_tool_response` × 50 | **255 с** (~5.1 с/вызов) | **~0.5 с** (~10 ms/вызов) |
| `build_mcp_health` | ~1.8 с | **~1.8 с** (без изменений на малых репо) |
| `PathIndex.build` (git) | ~58 ms | **~58 ms** (кэш → ~0 ms повторно) |

### Что по-прежнему медленно (ожидаемо)

| Операция | Почему не убираем |
|----------|-------------------|
| `verify=pytest` / `npm run build` | Реальная проверка стека |
| AST / tree-sitter match при drift | CPU-bound alignment |
| TrustChain checkpoint + SHA256 per chunk | Один ledger/Merkle commit на chunk |
| Первый `apatch_doctor` после cold start | Полная диагностика — **раз в сессию** |

### Slug ratify: первичная аттестация без N× lifecycle (2026-07-12)

Для полной slug-спеки из 16 Rk старый первый прогон создавал marker-файлы и выполнял
16 отдельных session/apply/verify/attest циклов. Теперь `apatch slug ratify <slug>`:

1. запускает каждый уникальный verify ровно один раз;
2. открывает одну governed-сессию с content-hashed артефактами всех зелёных open Rk;
3. делает один подписанный `noop_attest` с компактным shared-verify evidence;
4. строит conformance-вердикт из того же кэша.

Красные open Rk и open Rk без `(verify:)` не аттестуются. Устаревшая ссылка на
переименованный/удалённый тест в уже закрытом Rk (`exit >= 2`) остаётся advisory и не
блокирует batch-rebind зелёных open Rk. Поля `complete` и
`ratified_with_advisories` отделяют полную ратификацию от поступательного закрытия;
`primary_attested`, `reattested`, `attested` и `attestation` показывают фактический
результат и число реально открытых сессий/ledger commits.

---

## Ledger hot path (RFP-038, 2026-07-13)

До RFP-038 обычный apply дважды JSON-разбирал весь ledger. На 15 472
объектах это 1,211 с; для 160 212 объектов около 12,5 с. Теперь write path
проверяет только новую запись и HEAD, а полный scan выполняется явно через
`rebuild_index=true` для audit/recovery.

Dogfood apply из 23 мутаций сократился примерно с 4 минут до 16 секунд вместе
с целевыми тестами. После исправления смежных CAS/reconcile,
verify-environment и pytest-diagnostics регрессий полный прогон на актуальном
master завершился: 1497 passed, 1 skipped за 3:18.

Инварианты, которые нельзя откатывать локальной оптимизацией, и точная матрица
регрессионных тестов: [governed-runtime-invariants.md](./governed-runtime-invariants.md).

## Hot path после фикса

```text
MCP tool call
    │
    ├─ tool fn (plan / apply / …)
    │
    └─ enrich_tool_response
           ├─ classify_failure / state_update
           ├─ save session_state.json
           ├─ build_session_view
           │      └─ build_policy_snapshot  (~1–500 ms)
           ├─ emit_domain_event (append)
           └─ attach_artifact_guidance (статический playbook)

apatch_doctor (явный вызов)
    └─ run_doctor  (~1–3 s на consumer)
           ├─ build_policy_snapshot
           ├─ detect_toolchain (кэш / git paths)
           ├─ build_mcp_health (lean probes)
           ├─ tree_sitter_grammars
           └─ warnings, agent_protocol, …
```

---

## Rollback

```bash
# Только P1 (toolchain/cache)
git revert 3d6e1dc

# P0 + P1
git reset --hard b01bcb5

# После отката — restart MCP (Cursor Settings → MCP → user-apatch)
```

Editable install (`pip install -e`) после `.py`-only изменений **не обязателен** — нужен **restart MCP**, чтобы выгрузить старые модули из памяти.

---

## Рекомендации агенту (consumer)

1. **`apatch_doctor` — один раз в начале сессии**, не перед каждым chunk.
2. Не дёргать `apatch_session_state` / `apatch_verify_status` в tight loop — они лёгкие, но всё равно пишут `session_state.json`.
3. `apatch_simulate` → `apatch_apply_session` с `verify_deferred=true`; verify один раз в конце.
4. Warnings (mcp stale, trust anchor, inclusion gap) — **только из doctor**; не ожидать их на каждом tool.

---

## MCP lifecycle (2026-06-10, RFP-019 L1)

Ghost MCP после reload IDE и stale `write_lease.json` — отдельный класс проблем от hot-path perf.

| ID | Изменение | Файл |
|----|-----------|------|
| L1-1 | `atexit` + SIGTERM/SIGINT → release lease для текущего pid | `apatch/mcp/lifecycle.py` |
| L1-2 | `touch_workspace` → sweep stale lease (dead pid / expired) раз на workspace | `server.py` wrapper |
| L1-3 | Startup sweep cwd если есть `.apatch/` | `launcher.py` |
| L1-5 | `APATCH_MCP_GUIDANCE=doctor_only` — slim hot-path (`guidance_ref`) | `agent_guidance.py`, `mcp_health.py` |

Архитектура внешнего релиза и hosted MCP (уровни 2–3) — **[RFP-019](./RFP-019-mcp-scale-lifecycle.md)** (документировано, не в продакшене).

---

## Backlog (не реализовано)

Идеи для ускорения **без сокращения** sandbox / TrustChain / chunking:

| ID | Идея | Ожидаемый эффект | Риск |
|----|------|------------------|------|
| L1-6 | Session state write-behind | −disk I/O × N tools | Средний |
| L1-7 | `APATCH_MCP_PROFILE=core\|full` | Меньше tools/list | Средний |
| L1-8 | `apatch_mcp_hygiene` tool | Self-heal ghost PIDs | Низкий |
| ~~B9~~ | ~~`apatch_generate_batch` + `append`~~ | **Сделано (2026-06-09)** — замена `build_*_patches.py` | — |
| ~~B11~~ | ~~`generate_batch` mutation actions (create/delete/rename)~~ | **Сделано (2026-06-09)** — единый needles → jsonl без ручного Add File | — |
| B10 | `apatch_execute_next` (RFP-008) | Requirement execution — [SPEC-EXECUTOR-1](./specs/SPEC-EXECUTOR-1.md) | Средний |
| B1 | `apatch_doctor(full=False)` — только `recommended_verify` + `policy` + `mcp_health.ok` | Меньше JSON в doctor-ответе | Низкий |
| B2 | Warm-cache `recommended_verify_resolved` в MCP-процессе (TTL + mtime `package.json`) | Повторный doctor быстрее | Средний — кратковременный stale verify |
| B3 | Lazy `tree_sitter_grammars` в doctor (по запросу или `full=True`) | −10–50 ms на doctor | Низкий |
| B4 | `simulate` / `plan_batch`: переиспользовать один `PathIndex` + `MatchSession` в рамках одного MCP-вызова | 2–5× на больших JSONL | Низкий |
| B5 | Увеличить default `chunk_max_files` (10–15) в enforce-consumer profile | Меньше MCP round-trips | Средний — больше blast radius per chunk |
| ~~B6~~ | ~~Batch TrustChain notarization~~ | **Сделано (RFP-038):** O(1) receipt + один commit на chunk | Executable SPEC |
| B7 | SSE/HTTP MCP transport (вместо stdio) | Меньше serialize overhead | Инфраструктура Cursor, не apatch alone |
| B8 | Параллельный `plan_from_logs` по умолчанию на consumer с `workers=auto` | Уже есть; документировать в PROFILE | Низкий |

**Mutation generator (B11):** `apatch_generate_batch` — `action` create/delete/rename/replace → единый JSONL. См. [cookbook.md](./cookbook.md). **Multi-needle remainder:** `append` на `apatch_generate`.

Приоритет для следующего PR: **B1 + B3** (меньше payload doctor) или **B4** (plan/simulate на больших патчах).

---

## Как перезапустить замеры

```bash
# TrustChain_Agent (или другой consumer)
export TARGET=/path/to/consumer
python3 -c "
import time, sys
sys.path.insert(0, '/path/to/apatch')
from apatch.doctor import run_doctor
from apatch.session_state import enrich_tool_response

t0=time.perf_counter(); run_doctor('$TARGET'); print('doctor', round(time.perf_counter()-t0,3),'s')
t0=time.perf_counter()
for _ in range(10):
    enrich_tool_response('apatch_session_state', {'ok': True}, target_dir='$TARGET')
print('enrich_10x', round(time.perf_counter()-t0,3),'s')
"

# MCP stdio roundtrip (как Cursor)
python3 -m pytest tests/test_mcp_stdio_live.py::test_mcp_stdio_apatch_doctor_roundtrip -q

# Регрессия perf-тестов
python3 -m pytest tests/test_policy_snapshot.py tests/test_toolchain_perf.py -q
```

Тесты: `tests/test_policy_snapshot.py`, `tests/test_toolchain_perf.py`.

---

## Связанные документы

| Документ | Связь |
|----------|-------|
| [mcp_setup.md](./mcp_setup.md) | Установка, restart MCP, 17 compact / 124 full tools |
| [RFP-019-mcp-scale-lifecycle.md](./RFP-019-mcp-scale-lifecycle.md) | Scale: L1 lifecycle, L2 pip release, L3 hosted MCP |
| [AGENTS.template.md](./AGENTS.template.md) | `apatch_doctor` раз в сессию; не shell-retry pip |
| [adr/ADR-001_Enterprise_Scale_Gaps.md](./adr/ADR-001_Enterprise_Scale_Gaps.md) | PathIndex, parallel plan (масштаб apply) |
