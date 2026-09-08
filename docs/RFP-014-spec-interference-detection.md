# RFP-014: Spec Interference Detection — кросс-спековый анализ конфликтов

* **Статус**: **Phase 1–3 implemented** · [SPEC-INTERFERENCE-3](./specs/SPEC-INTERFERENCE-3.md) attested
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-10
* **Зависимости**: RFP-007 (`spec.py`, `parse_spec`), RFP-009 (`spec_run`, inline `requirements`), RFP-010 ([SPEC-COVERAGE-1](./specs/SPEC-COVERAGE-1.md) — `requirement_file_sets`), `apatch_generate_batch`, `generate.py`

> **Нумерация:** RFP-010 занят requirement coverage & staleness. RFP-013 зарезервирован под
> Design artifact ([domain.md](./domain.md)). **Interference = RFP-014.**
>
> **Ключевая идея:** перенос *conflict serializability* из теории транзакций БД на мутации кода.
> Спецификации — транзакции, `find_text` — read set, `replace_text` — write set.
> Ациклический conflict graph ⟹ существует безопасный порядок исполнения.
> Цикл ⟹ спеки несовместимы без переработки needles.

---

## 1. Тезис

RFP-007–009 дали **governed execution** одной спеки за раз (temporal axis). Реальные проекты
имеют **параллельные спеки** от разных дисциплин:

```text
SPEC-UIUX-1   (frontend)     — Button.tsx, Layout.tsx, theme.css
SPEC-API-1    (backend)      — routes.ts, middleware.ts, Button.tsx  ← конфликт
SPEC-DEVOPS-1 (infra)        — Dockerfile, nginx.conf
SPEC-PERF-1   (performance)  — bundle.config.ts, Layout.tsx         ← конфликт
```

Сегодня межспековый конфликт обнаруживается **после факта** — когда `verify` одной спеки
ломается от мутаций другой. Хуже merge conflict в git, потому что:

1. **Поздно** — код уже написан, attestation могла пройти
2. **Непонятно** — «какая спека виновата?» — ручное расследование
3. **Не воспроизводимо** — зависит от порядка исполнения

RFP-014 вводит **Spec Interference Detection** — формальный анализ конфликтов между спеками
**до исполнения**, на данных из ledger, spec_run manifest и (опционально) JSONL.

**Intra-spec vs inter-spec:** RFP-009 + RFP-010 покрывают **одну** спеку (shared file →
`stale` на соседних Rk; noop re-attest). RFP-014 — **между** спеками (reorder или refactor
needles). См. [RFP-009 §9.1](./RFP-009-spec-run.md), [spec-authoring.md § Shared file](./spec-authoring.md).

---

## 2. Формальная модель: Conflict Serializability для мутаций кода

### 2.1. Маппинг на теорию БД

| Теория БД | apatch |
|-----------|--------|
| Transaction T_i | `spec_run` / governed session по SPEC-i |
| Write item w_i(x) | `{action: "replace", target_file: x, replace_text: ...}` |
| Read item r_i(x) | `{find_text: ...}` — контекст-якорь, который matcher **ожидает** найти |
| Write set WS(T_i) | `{(file, replace_text) ∀ needle ∈ SPEC-i}` |
| Read set RS(T_i) | `{(file, find_text) ∀ needle ∈ SPEC-i}` |
| Conflict (WR) | A пишет так, что исчезает или ломается якорь B (`find_text`) |
| Conflict (WW) | A и B мутируют пересекающуюся область одного файла |
| Serialization graph | Directed graph: ребро A → B, если A должна идти перед B |
| Schedule | Безопасный порядок исполнения спек |

### 2.2. Ключевой результат

> **Теорема (Conflict Serializability):**
> Если conflict graph **ациклический** — существует topological ordering спек, при котором
> literal `find_text` якоря остаются валидными (при `match_mode=literal`).
> Если есть **цикл** — спеки **несовместимы** при любом порядке и требуют рефакторинга needles.

### 2.3. Почему `find_text` — Read Set

В apatch `find_text` — **буквальное** содержимое для anchoring (literal mode). Если SPEC-A
выполнит `replace` так, что фрагмент B.find_text исчезнет из файла → `APPLY_FAILED` у SPEC-B.

Для **`match_mode=literal`** WR-detection — детерминированная строковая проверка
(post-replace simulation). Для `whitespace` / `regex` / `json` — точность ниже; отчёт
должен содержать `warning: non_literal_match_mode`.

---

## 3. Три уровня конфликтов

### Level 1: File-level (candidate set, O(S²·F))

```text
Источник:  spec_coverage → requirement_file_sets() (ledger, attested mutations)
Метод:     set intersection по target_file
Результат: "SPEC-A и SPEC-B обе трогают Button.tsx"
```

**Не всякое пересечение — конфликт.** Две спеки могут править разные функции в одном файле.
File overlap — **candidate set** для Level 2, не финальный вердикт.

### Level 2: Patch-level (структурный, O(N_a · N_b) per shared file)

```text
Источник:  needles из registry (§4.2) — planned + attested
Метод:     post-replace simulation + region overlap

Типы:
  WR (write-read):  после apply needle A в файле f якорь B.find_text отсутствует
  WW (write-write): пересекающиеся replace-регions в f (не merely общий prefix find_text)
  RW:               симметричный WR
```

**WR (рекомендуемая формализация v1):**

```python
def is_wr_conflict(needle_a: Needle, needle_b: Needle, *, file_content: str) -> bool:
    """A's write invalidates B's read anchor (literal mode)."""
    if needle_a.target_file != needle_b.target_file or not needle_b.find_text:
        return False
    if needle_b.find_text not in file_content:
        return False  # B already drifted — out of scope for predictive analysis
    # Simulate A on current content (single replace, literal)
    after_a = file_content.replace(needle_a.find_text, needle_a.replace_text, 1)
    if needle_b.find_text not in after_a:
        return True
    return False
```

**WW (консervative v1 — избегать ложных циклов):**

- **Не** считать WW только по `find_text_a in find_text_b` (общий import → false positive).
- WW, если **simulated replace regions** пересекаются (overlap char spans после match), **или**
  оба needle используют **один и тот же** `find_text` на том же файле.
- WW в conflict graph — **mutex** (annotation `refactor_needles`), **не** обязательно
  bidirectional ordering edge (иначе ложные циклы).

### Level 3: Semantic / Cross-verify (эмпирический, Phase 2)

```text
Источник:  verify commands из SPEC.md
Метод:     sandbox: apply SPEC-A → run verify(SPEC-B) → rollback

Ловит:     rename таблицы БД, удалённый CSS-класс, смена API schema, …
```

Cross-verify — единственный надёжный метод для semantic conflicts. Инфраструктура:
`sandbox.py`, `BackupManager`, `rollback_workspace` (RFP-004).

---

## 4. Источники данных (source priority)

> **Primary path RFP-009** — inline `requirements={Rk: {needles}}`; JSONL на диске **не**
> обязателен. Interference engine **не** должен полагаться только на grep `patches-*.jsonl`.

### 4.1. Приоритет (authoritative → fallback)

| Priority | Источник | Данные | Когда |
|----------|----------|--------|-------|
| **1** | `.trustchain/` + `spec_coverage` | Attested mutations per `spec:ID#Rk`; `requirement_file_sets()` | Post-mortem, L1 всегда; L2 для attested needles из ledger payload |
| **2** | `.apatch/spec_run.json` + inline manifest | Planned `requirements{Rk: {needles}}`, `manifest_sha256` | Pre-flight до apply |
| **3** | `.apatch/specs/<SPEC-ID>.json` | Registry: `source_path`, optional `last_needles_sha256`, `last_logs_path` | Stable spec discovery (HC: `HC_Platform/docs/specs/…`) |
| **4** | `patches-<SPEC-ID>[-*].jsonl` | Debug / CI artifact; `read_jsonl_patches()` + `normalize_mutation()` | Optional; **не** единственный ключ |

`generate_batch` / `spec_run` ** SHOULD** обновлять registry при записи needles:

```json
{
  "id": "SPEC-LEDGER-ACTOR-1",
  "source_path": "docs/specs/SPEC-LEDGER-ACTOR-1.md",
  "last_needles_sha256": "sha256:…",
  "last_logs_path": "patches-spec-run.jsonl"
}
```

**Convention (optional, human/CI):** `patches-<SPEC-ID>[-<run-id>].jsonl` — удобно для review,
не заменяет registry + ledger.

### 4.2. Существующий код (reuse, без новой логики ingest)

| Модуль | API | RFP-014 use |
|--------|-----|-------------|
| `spec_coverage.py` | `requirement_file_sets(entries, spec_id)` | Level 1 file sets |
| `generate.py` | `read_jsonl_patches`, `normalize_mutation` | Normalize JSONL → `Needle` |
| `spec.py` | `_discover_spec_path`, `parse_spec_file` | Spec list, verify cmds |
| `spec_run.py` | `.apatch/spec_run.json` | Planned needles per active/completed run |
| `trustchain_helper.py` | `iter_ledger_entries` | Attested mutation payloads |
| `simulate.py` | preflight risk (single JSONL) | Future: cross-jsonl (Phase 1.5) |
| `sandbox.py` | isolation, rollback | Level 3 cross-verify (Phase 2) |

### 4.3. Реализованные модули

| Модуль | Phase | Spec |
|--------|-------|------|
| `apatch/spec_interference.py` | 1, 1.5 | [SPEC-INTERFERENCE-1](./specs/SPEC-INTERFERENCE-1.md) |
| `apatch/spec_registry.py` | 1.5 | registry needles |
| `apatch/spec_cross_verify.py` | 2 | [SPEC-INTERFERENCE-2](./specs/SPEC-INTERFERENCE-2.md) |
| MVCC + structural_options + `spec_run_multi` | 3 | [SPEC-INTERFERENCE-3](./specs/SPEC-INTERFERENCE-3.md) ✅ |

Core types: `Needle`, `Conflict`, `ConflictType`, `InterferenceReport` — см. §15.

---

## 5. MCP / CLI surface (implemented)

### Phase 1 — passive detection only

| MCP | CLI | Назначение |
|-----|-----|------------|
| `apatch_spec_interference` | `apatch spec interference` | Cross-spec conflict analysis |

Ответ включает: `conflicts[]`, `conflict_graph`, `has_cycle`, `cycles[]`, **`safe_order`**,
`risk_score`, `summary`, `warnings[]` (missing data, non-literal match_mode).

**Tool count:** 66 → **67** (+1).

### Phase 1.5 — schedule (thin wrapper или gate)

| MCP | CLI | Назначение |
|-----|-----|------------|
| `apatch_spec_schedule` | `apatch spec schedule` | Alias / enriched view over `safe_order` + `risk_per_step` |

**Не в Phase 1:** `safe_order` уже в ответе `apatch_spec_interference`. Отдельный tool —
когда появится gate в `spec_run` (`blocked` при `has_cycle`) или partial dependency graph.

Phase 1.5 → **68 tools**.

### Параметры `apatch_spec_interference` (implemented)

| Параметр | Тип | Назначение |
|----------|-----|------------|
| `specs` | `List[str]` | ≥2 spec ids; `["*"]` = all registered / `docs/specs/*.md` |
| `target_dir` | `str` | Workspace root |
| `level` | `int` | Max depth: `1` file, `2` patch (default), `3` semantic (Phase 2) |
| `include_planned` | `bool` | Include needles from `.apatch/spec_run.json` (default: `true`) |
| `include_attested` | `bool` | Include ledger-derived needles (default: `true`) |
| `patches_glob` | `str` | Optional override for JSONL discovery (priority 4) |

### Пример ответа (Level 2)

```json
{
  "ok": true,
  "specs": ["SPEC-LEDGER-ACTOR-1", "SPEC-COVERAGE-1"],
  "analysis_level": 2,
  "data_sources": {
    "SPEC-LEDGER-ACTOR-1": ["ledger", "spec_run_manifest"],
    "SPEC-COVERAGE-1": ["ledger", "spec_run_manifest"]
  },
  "conflicts": [
    {
      "type": "file_overlap",
      "spec_a": "SPEC-LEDGER-ACTOR-1",
      "spec_b": "SPEC-COVERAGE-1",
      "file": "apatch/spec_coverage.py",
      "detail": "both specs attested mutations on shared module (candidate for L2 WR/WW)",
      "severity": "low"
    }
  ],
  "conflict_graph": { "SPEC-COVERAGE-1": ["SPEC-LEDGER-ACTOR-1"] },
  "has_cycle": false,
  "cycles": [],
  "safe_order": ["SPEC-COVERAGE-1", "SPEC-LEDGER-ACTOR-1"],
  "risk_score": 0.15,
  "summary": {
    "total_specs": 2,
    "total_conflicts": 1,
    "by_type": { "file_overlap": 1, "write_read": 0, "write_write": 0 },
    "irreconcilable": false
  },
  "warnings": []
}
```

---

## 6. Risk scoring (implemented)

```text
0.0       — no conflicts
0.1–0.3   — file overlaps only (L1 candidates)
0.4–0.6   — WR conflicts (order matters)
0.7–0.8   — WW / mutex regions (refactor needles)
0.9–1.0   — cycle in conflict graph (irreconcilable)
```

---

## 7. Scope boundaries (что это НЕ)

* **Не автоматический merge спек.** При конфликте — агент/человек переписывает needles.
* **Не оркестратор (Phase 1).** Interference **detect + report**; исполнение — N × `apatch_spec_run`.
* **Не AI intent analyzer.** Level 3 — cross-verify, не LLM embeddings (Phase 4 — research).
* **Не замена spec_run / spec_coverage.** RFP-014 — **между** spec_run запусками.
* **Не intra-spec stale fix.** Shared file в одной спеке → RFP-009 noop re-attest lesson.

---

## 8. Failure taxonomy (дополнения, planned)

| `error_type` | `recommended_action` | Когда |
|--------------|----------------------|-------|
| `SPEC_INTERFERENCE_WR` | `reorder` | WR conflict; изменить порядок spec_run |
| `SPEC_INTERFERENCE_WW` | `refactor_needles` | WW / mutex; переработать needles |
| `SPEC_INTERFERENCE_CYCLE` | `refactor_needles` | Цикл; спеки несовместимы |
| `SPEC_CROSS_VERIFY_FAILED` | `refactor_needles` | Level 3 semantic conflict (Phase 2) |
| `SPEC_INTERFERENCE_NO_DATA` | `reduce_scope` | Spec без needles в registry/ledger |
| `SPEC_INTERFERENCE_STALE` | `re_run_interference` | MVCC: needles изменились после report (Phase 3) |
| `SPEC_SCHEDULE_BLOCKED` | `resolve_conflicts` | `schedulable == false` — cycle или пустой order (Phase 3) |
| `SPEC_RUN_ORDER_BLOCKED` | `complete_predecessor_first` | Predecessor в `safe_order` не attested (Phase 3) |

---

## 9. Риски и митигации

| Риск | Митигация |
|------|-----------|
| JSONL не связан со spec | Priority 1–3 (ledger, spec_run, registry); JSONL optional |
| O(N²) specs | L2 только для L1 file-overlap pairs; lazy evaluation |
| False positives L1 | L1 = candidates; L2 уточняет |
| False positives WW (substring) | Span overlap / same find_text; WW → mutex not cycle edge |
| False cycles from WW | WW не добавляет bidirectional order edges в v1 |
| `match_mode != literal` | Warning in report; WR marked `confidence: medium` |
| Planned needles устарели | `manifest_sha256` drift → `MANIFEST_DRIFT` from spec_run |
| Spec без данных | `status: "no_data"` per spec; не блокирует остальные |

---

## 10. Phased delivery

### Phase 1: Passive detection (RFP-014 v1 goal)

| Компонент | Статус |
|-----------|--------|
| `spec_interference.py` — L1 + L2 + graph + risk | ✅ |
| MCP `apatch_spec_interference` | ✅ |
| CLI `apatch spec interference` | ✅ |
| Registry update in `spec_run` / `generate_batch` | ✅ `.apatch/specs/<ID>.json` |
| `tests/test_spec_interference.py` | ✅ |
| SPEC-INTERFERENCE-1 (dogfood) | ✅ attested 7/7 |
| Docs: §3L AGENTS.template, cookbook, mcp_setup | ✅ mcp_setup + §3L |

**Dogfood target (first):** apatch repo — SPEC-LEDGER-ACTOR-1 × SPEC-COVERAGE-1
(`apatch/spec_coverage.py` shared file). **Second:** Human_Capital (ROLE-WORK + DUTY-SHELL + TRUSTCHAIN-AUDIT).

### Phase 1.5: Schedule tool + spec_run gate ✅

| Компонент | Статус |
|-----------|--------|
| `spec_registry.py` — `.apatch/specs/<ID>.json` | ✅ |
| MCP `apatch_spec_schedule` | ✅ |
| CLI `apatch spec schedule` | ✅ |
| `spec_run(interference_check, peer_specs)` cycle gate | ✅ |
| `tests/test_spec_registry.py`, `tests/test_spec_schedule.py` | ✅ |

**Tool count:** 67 → **68** (+1).

### Phase 2: Cross-verify (Level 3) ✅

| Компонент | Статус |
|-----------|--------|
| `spec_cross_verify.py` — sandbox apply → verify → rollback | ✅ |
| MCP `apatch_spec_cross_verify` | ✅ |
| CLI `apatch spec cross-verify` | ✅ |
| `_remember_spec` merge (preserve registry needles) | ✅ |
| `tests/test_spec_cross_verify.py` + fixtures | ✅ |
| SPEC-INTERFERENCE-2 (dogfood) | ✅ attested 7/7 |

**Tool count:** 68 → **69** (+1).

### Phase 3: Coordination hardening ([SPEC-INTERFERENCE-3](./specs/SPEC-INTERFERENCE-3.md))

| Компонент | Статус |
|-----------|--------|
| MVCC: `computed_at`, `input_hashes`, `validity`, `data_domains` | ✅ |
| `structural_options[]` на L2 conflicts | ✅ |
| Enriched schedule: `risk_per_step`, `interference_hash`, `strategy` | ✅ |
| `spec_run` ordering gate (predecessor attested) | ✅ |
| Failure types: `STALE`, `SCHEDULE_BLOCKED`, `RUN_ORDER_BLOCKED` | ✅ |
| MCP `apatch_spec_run_multi` | ✅ |

**Tool count:** 69 → **70** (+1).

### Phase 4: Intent / domain tags

**Status:** Implemented (SPEC-UX-SPECIALIST-1 R7) — UX domain extension beyond `frontend`/`backend`/`infra`.

Tags are **L0 — pre-filter routing** over the formal interference stack. They answer: *should we run L1/L2/L3 for this spec pair at all?* They do **not** replace file overlap or patch conflict analysis; the graph **proves**, tags **filter**.

```text
L0  Tag heuristics      domain/layer/role/product rules          O(1) per pair
    ↓ (may skip expensive levels)
L1  File overlap        set intersection on touched paths        O(S²·F)
L2  Patch conflict      WR/WW string analysis on needles         O(N_a·N_b)
L3  Cross-verify        sandbox apply → verify                     expensive
```

| L0 signal | L1/L2/L3 action |
|-----------|-----------------|
| Same `domain:*` on overlapping scope | **Always** run L1+L2 (high conflict probability) |
| Different `domain:*`, no shared paths in registry | **Skip** L1/L2 (orthogonal; L0 short-circuit) |
| Same `layer:*` (even if different `domain:`) | **L1 minimum** (cognitive overlap ≠ file overlap, but worth checking) |
| Same `product:*` + different `layer:*` | Parallel OK at L0; still run L1 if paths overlap |

**Anti-pattern:** treating Phase 4 heuristics as “tags solve interference.” Tags optimize scheduling; L1/L2/L3 remain authoritative when invoked.

#### Tag vocabulary

| Prefix | Pattern | Example | Use |
|--------|---------|---------|-----|
| `domain:` | `domain:ux` | UX audit vs API specs | Inter-spec routing |
| `layer:` | `layer:L1` … `layer:L5` | Cognitive layer scope | Partial audit overlap |
| `role:` | `role:<code>` | `role:sales_director` | Persona-specific needles |
| `product:` | `product:<id>` | `product:jason` | Product instance dogfood |

Tags appear in:

- UX audit JSON (`manifests/ux-audit.schema.json` → `tags[]`)
- Spec needles and `apatch_spec_interference` registry entries
- RFP-015 artifact model

#### L0 interference heuristics (routing, not proof)

1. Same `product:*` + different `layer:*` → **parallel OK** at L0 (orthogonal layers); L1 if paths touch
2. Same `layer:*` + conflicting `role:*` → **review** → run L1 minimum
3. `domain:ux` + `domain:backend` on same path → **serial** via `safe_order` → run L1+L2

Placement of domain methodologies relative to core: [domain.md § Domain knowledge vs execution substrate](./domain.md).

#### Research (future)

- LLM intent embeddings for antagonistic needle detection (L0 supplement, not L1 replacement)
- Antagonistic intent heuristics beyond tag overlap

---

## 11. Consumer workflow (Phase 1, planned)

```text
# 0. Перед batch исполнением нескольких спек
apatch_spec_interference(
  specs=["SPEC-COVERAGE-1", "SPEC-LEDGER-ACTOR-1"],
  target_dir="."
)
# → conflicts[], safe_order, risk_score, warnings

# 1. risk_score < 0.4 — исполнять в safe_order
apatch_spec_run(spec="SPEC-COVERAGE-1", requirements={...})
apatch_spec_run(spec="SPEC-LEDGER-ACTOR-1", requirements={...})

# 2. risk_score >= 0.7 или has_cycle — refactor needles, re-run interference

# 3. После каждой спеки — обычный цикл RFP-009
apatch_spec_status(spec="SPEC-…")
```

---

## 12. Связь с RFP-серией

```text
RFP-004   Governed Runtime         ─── temporal axis (one session)
RFP-005   Reference Monitor        ─── enforcement invariant
RFP-006   Artifact-Anchored Intent ─── traceability
RFP-007   Executable Specs         ─── what to do
RFP-008   Spec Executor            ─── one Rk at a time
RFP-009   Spec Run                 ─── whole spec at a time (temporal)
RFP-010   Requirement Coverage     ─── intra-spec stale / file drift
RFP-011   Plan Artifact            ─── decision + execution plan
RFP-012   Plan Adherence           ─── plan vs fact
RFP-013   Design (planned)         ─── architectural topology
RFP-014   Spec Interference        ─── BETWEEN specs (parallel axis)  ← NEW
```

```text
         ┌──────────────────────┐
         │   Spec Registry      │
         │   (RFP-007)          │
         └─────────┬────────────┘
                   │
   ┌───────────────┴───────────────┐
   │                               │
┌──▼──────────────┐    ┌───────────▼──────────┐
│ Execution       │    │ Interference Engine   │
│ Ledger          │    │ (RFP-014)             │
│ (RFP-004/009)   │    │ parallel axis         │
│ temporal axis   │    │                       │
└──────┬──────────┘    └───────────┬──────────┘
       │                           │
       └───────────┬───────────────┘
                   │
          ┌────────▼────────┐
          │ Conflict Graph   │
          │ (serializability)│
          └──────────────────┘
```

> **Сдвиг:** RFP-004–012 + RFP-009 управляют **одним потоком** и **качеством доказательств**
> внутри спеки. RFP-014 добавляет **координацию между потоками** — constraint-aware
> scheduling **до** мутаций.

---

## 13. Документация

| Аудитория | Документ |
|-----------|----------|
| Архитектура | этот RFP |
| Реализация Phase 1 | [SPEC-INTERFERENCE-1](./specs/SPEC-INTERFERENCE-1.md) ✅ |
| Реализация Phase 2 | [SPEC-INTERFERENCE-2](./specs/SPEC-INTERFERENCE-2.md) ✅ |
| Реализация Phase 3 | [SPEC-INTERFERENCE-3](./specs/SPEC-INTERFERENCE-3.md) ✅ |
| Intra-spec shared file | [RFP-009 §9.1](./RFP-009-spec-run.md), [spec-authoring.md](./spec-authoring.md) |
| Coverage / stale | [RFP-010 / SPEC-COVERAGE-1](./specs/SPEC-COVERAGE-1.md) |
| Агент consumer | AGENTS.template.md §3L |
| MCP таблица | mcp_setup.md (+1 Phase 1; +1 Phase 1.5 schedule) |

---

## 14. Provenance

Черновик: Antigravity IDE brain artifact `RFP-010-spec-interference-detection.md` (2026-06-10).
Перенесён в apatch как **RFP-014** с правками: нумерация, source priority, WW/mutex semantics,
Phase 1 vs 1.5 schedule split, dogfood targets, intra/inter-spec cross-links.

**Detailed analysis (Antigravity):** `RFP-010-detailed-analysis.md` — формальная модель,
MVCC, `structural_options`, invariants, consumer workflow. Gap closure →
[SPEC-INTERFERENCE-3](./specs/SPEC-INTERFERENCE-3.md).

---

## 15. MVCC validity (Phase 3)

Interference report — **snapshot** over frozen needle state, не live view.

```python
@dataclass
class InterferenceReport:
    # ... conflicts, graph, safe_order ...
    computed_at: str                      # ISO timestamp
    input_hashes: Dict[str, str]          # {spec_id: sha256(needles)}
    validity: str                         # current | stale | planned_only
    data_domains: Dict[str, List[str]]    # {spec_id: [ledger, registry, ...]}
```

**Правила:**

- `apatch_spec_schedule` перевычисляет interference при изменении `input_hashes`
- `apatch_spec_run` с `peer_specs` проверяет `validity`; `stale` → warning +
  `SPEC_INTERFERENCE_STALE` / `re_run_interference` (не silent block в v1)
- **Planned** domain: registry / spec_run manifest — может измениться до apply
- **Observed** domain: ledger attested mutations — факт

`planned_only` когда ни одна спека не имеет attested needles в ledger.

---

## 16. Constraint explanation (`structural_options`)

Каждый L2 `Conflict` **должен** содержать actionable output (из detailed analysis §5):

```json
{
  "type": "write_read",
  "spec_a": "SPEC-UIUX-1",
  "spec_b": "SPEC-API-1",
  "requirement_a": "SPEC-UIUX-1#R1",
  "requirement_b": "SPEC-API-1#R3",
  "file": "src/components/Button.tsx",
  "detail": "R1 replace destroys R3 find_text anchor …",
  "structural_options": [
    {"option": "reorder", "feasible": true, "description": "Run API#R3 before UIUX#R1"},
    {"option": "rewrite_anchor", "feasible": true, "description": "Regenerate R3 find_text"},
    {"option": "merge", "feasible": "unknown", "description": "Requires semantic analysis"}
  ],
  "confidence": "structural"
}
```

**Ownership:** `reorder`, `split_region`, `rewrite_anchor` — computable graph/string ops.
`merge` и intent priority — **вне scope** (human / domain judgment).

---

## 17. Инварианты реализации (из detailed analysis §12)

1. **L1/L2 — pure functions.** Только чтение данных; никаких мутаций workspace.
2. **Graph = conservative safety envelope.** False positives допустимы; false negatives — нет
   (кроме semantic, где L3 обязателен).
3. **WW ≠ автоматический цикл.** WW — mutex + `refactor_needles`; рёбра ordering **только**
   из WR. Не добавлять bidirectional edges для каждого WW.
4. **Structural ∈ scope; semantic ∉ scope.** См. §16.
5. **MVCC обязательна (Phase 3).** Stale report → warning + explicit re-run.
6. **Reuse existing APIs.** `read_jsonl_patches`, `normalize_mutation`,
   `requirement_file_sets`, `parse_spec_file` — не дублировать ingest.

> **Исправление черновика Antigravity:** в §7 `build_conflict_graph` brain-doc ошибочно
> предлагал bidirectional edges для WW; канон — §3 Level 2 и invariant #3 выше.
