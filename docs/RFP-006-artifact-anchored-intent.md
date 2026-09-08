# RFP-006: Artifact-anchored intent — apatch как память об инженерных решениях

* **Статус**: **6.1–6.2 реализованы** (пакет **0.2.0**) — binding, history, coverage, op_id reverse map; CI-gate (опц.) в очереди
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-07
* **Зависимости**: RFP-004 (Mutation Runtime — реализован), RFP-005 (контрольный монитор — 0.2/0.3 закрыты кодом)

> Документ назван по предметной области (`artifact-anchored intent`), а не по команде. Тезис переживёт конкретные CLI/MCP-обёртки.

---

## 1. Тезис

apatch уже хранит не результат изменения, а его **жизненный цикл**:

```text
Intent → Session → Mutation → Verification → Attestation | Rollback
```

Git отвечает на вопрос «*что* изменилось» (`state A → state B`). apatch отвечает на «*почему*, *кем* и *с какой проверкой*». Это другой слой памяти.

Единственное, чего модели не хватает для универсальности, — **типизированный якорь намерения**. Сегодня `Intent` это свободная строка, а единственный «артефакт» (`adr`) захардкожен в манифесте рядом с `intent`. Это вырожденный, ad-hoc случай того, что хочет стать первоклассной сущностью.

**Вводим `Artifact`** — типизированную ссылку на инженерное решение (spec, ADR, ticket, incident, compliance-требование), привязанную к `Session`. Тогда цепочка становится:

```text
Artifact → Intent → Session → Mutation → Verification → Attestation
```

И TrustChain начинает доказуемо связывать **инженерное решение** с **кодом**, который его реализовал.

### Позиционирование

> **Git proves what changed. apatch proves why, by whom, and that it was verified.**

Отличие от issue-трекеров (Jira/Linear): они тоже «трекают intent», но связь решения с кодом держится на честном слове разработчика. apatch связывает `Artifact → Mutation → Attestation` **одной криптографической цепочкой** (TrustChain Ed25519). Это то, чего нет ни у Git, ни у трекеров.

---

## 2. Что это НЕ

Явные анти-паттерны (зафиксированы, чтобы не расползлось):

* **Не новый runtime и не новый глагол.** `orchestrate` / `pipeline_run` остаются. `apatch spec run`, `engineering_run`, `spec_run` — **не появляются**. «Spec-driven», «ADR-driven», «bugfix-driven», «compliance-driven» — это не команды и не режимы, а значения `Artifact.kind`. Один путь исполнения, разные типы артефакта.
* **Не редактор спецификаций.** apatch не пишет PRD/ADR и не парсит их семантику. Он *ссылается* на артефакт, фиксирует его идентичность (hash) и доказывает связь с изменением.
* **Не замена Jira.** Артефакт — ссылка (`kind:id@hash`), а не хранилище тела тикета. Тело живёт в источнике (репо, трекер, wiki); apatch хранит провенанс связи.
* **Не «spec→code».** Artifact биндится к `Session`, а не к `Mutation` — потому что исходом решения может быть не только diff (см. §4).

---

## 3. Что уже есть (кирпичи)

| Нужда | Уже в apatch |
|------|--------------|
| Намерение перед изменением | `session start --intent`, `set_session_intent` (`runtime/session.py`) |
| Машиночитаемый план-решение | `engineering-pipeline.json` (`intent`, `adr`, `phases`) |
| Вырожденный артефакт | поле `adr` в манифесте + `--query` в `trustchain history` |
| Подписанные метаданные | канал `metadata` в `platform_client.push_step` / `_maybe_push_to_platform` |
| История по намерению | `trustchain_intent_history_workspace(query=...)` |
| Доказуемость | TrustChain Ed25519 + `attestation commit` |

Модель сама просится: `intent` + `adr` рядом в схеме — это сигнал, что нужен **обобщённый типизированный якорь**, а не второе спец-поле.

---

## 4. Модель

### 4.1. Artifact

```json
{
  "kind": "spec",
  "id": "SPEC-42",
  "ref": "docs/specs/SPEC-42.md#billing-v2",
  "content_hash": "sha256:…"
}
```

* `kind` — **открытое множество** (не enum): `spec` | `adr` | `ticket` | `incident` | `compliance` | … Расширяется без изменения схемы.
* `id` — стабильный идентификатор в своём пространстве.
* `ref` *(опц.)* — указатель на источник (путь в репо, URL трекера, anchor).
* `content_hash` *(опц., но рекомендуется)* — хеш тела источника на момент привязки. Без него «реализовали SPEC-42» недоказуемо: спека могла измениться задним числом.

Три свойства, отличающие first-class Artifact от строки с двоеточием:

| Свойство | Что значит | Без него |
|----------|------------|----------|
| **Identity** | `(kind, id)` + `content_hash` источника | нет доказательства «какую именно версию решения реализовали» |
| **Linkage** | Artifact ↔ все mutations/attestations сессии в TrustChain | нет трассировки решение → код |
| **Queryability** | «все изменения под SPEC-42»; «какие артефакты имеют attested-мутацию» | нет coverage / traceability matrix |

### 4.2. Биндинг к Session (а не к Mutation)

`Session` получает `artifacts: Artifact[]` (множественное: одно изменение может закрывать ticket *и* ADR).

Почему к Session, а не к Mutation: исходом решения может быть **не код** — принятый ADR без diff, подпись политики (`policy sign`), миграция, compliance-attestation. Биндинг к сессии делает модель универсальной, а не «spec→code». Изменение кода — лишь один из возможных исходов.

```text
Session
 ├─ Artifact[]        ← НОВОЕ
 ├─ Intent
 ├─ Mutation[]
 ├─ Checkpoint[]
 ├─ Verification[]
 └─ Attestation
```

### 4.3. Запись в TrustChain

Артефакты кладутся в **подписанные** метаданные attestation/ledger-записи (канал `metadata` уже существует). Так связь `Artifact → код` становится tamper-evident на уровне RFP-005: подделать привязку требует root-anchored ключ, а не доступ к репо.

---

## 5. Поверхность (минимальная, без новых глаголов)

| Точка | Изменение |
|-------|-----------|
| `session start` | `--artifact spec:SPEC-42@<hash>` (повторяемый флаг для нескольких) |
| `set_session_intent` | принимает `artifacts[]` |
| `session_state.json` | поле `artifacts: []` (аддитивно, обратносовместимо) |
| манифест pipeline | `artifacts: [{kind,id,ref}]` рядом с `intent`; legacy `adr` → нормализуется в `{kind:"adr"}` |
| `attest` | артефакты сессии → в подписанные metadata |
| `trustchain history` | `--artifact spec:SPEC-42` (обобщение `--query`; `adr`/`intent` остаются работать) |
| MCP | `apatch_session_start(artifacts=[...])`, `apatch_trustchain_history(artifact=...)`, `apatch_trustchain_coverage` — см. [mcp_setup.md § Artifact](./mcp_setup.md) |

Реализация: тонкий слой идентичности поверх `Intent`. Не трогает sandbox, apply-loop, signer, KMS, inclusion.

---

## 6. Coverage / traceability (фаза 2)

Побочный продукт, которого нет у Git и трекеров: **traceability matrix как криптографический артефакт**.

* `apatch trustchain coverage --artifact spec:SPEC-42` → какие пункты спеки имеют attested-мутацию, какие висят без реализации.
* Обратная связь: по `op_id` мутации → к какому артефакту она относится.
* CI-применение: gate «нет attested-изменений вне зарегистрированного артефакта» (опционально, для regulated).

Не «мы написали тесты по спеке», а цепочка: `SPEC-42 §3 → mutation abc123 → verify pass → attestation HEAD`.

---

## 7. Критерии готовности

### 6.1 — Artifact как сущность
* [x] Schema `Artifact{kind,id,ref?,content_hash?}`; `kind` открыт (`apatch/artifact.py`).
* [x] `artifacts[]` в `session_state.json` (аддитивно); legacy `adr` нормализуется.
* [x] `session start --artifact` / `set_session_intent(artifacts=...)` / MCP-параметр.
* [x] Артефакты в подписанных metadata attestation (tamper-evident по RFP-005).
* [x] `trustchain history --artifact <kind:id>` (обобщение `--query`, обратносовместимо).
* [x] Тесты: биндинг, нормализация `adr`, query, round-trip metadata (`tests/test_artifact.py`).

### 6.2 — Coverage / traceability
* [x] `trustchain coverage --artifact` (artifact → mutations → attestation; `apatch/traceability.py`).
* [x] Обратный маппинг op_id → artifact (`--op-id` / `apatch_trustchain_coverage(op_id=...)`).
* [x] Авто-штамп `artifacts`/`governed_session_id` на ledger commit при активной сессии.
* [ ] (опц.) CI-gate «нет attested-изменений вне артефакта».

**Определение готовности 6.1:** любое изменение можно доказуемо привязать к типизированному инженерному решению; `git`-история и трекер перестают быть единственной (необязывающей) связью.

---

## 8. Связь с RFP-005

RFP-005 сделал изменение **доказуемым** (кто/как/с какой проверкой). RFP-006 добавляет **почему** (какое решение), замыкая полную цепочку провенанса:

```text
Artifact (почему) → Intent → Session → Mutation (как) → Verification (работает?) → Attestation (доказано)
```

Это и есть финальная форма тезиса: **apatch — память об инженерных решениях, привязанная к коду доказуемо.**
