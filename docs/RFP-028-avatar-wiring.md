# RFP-028 — Avatar wiring: живой поток вклада и первый взгляд на аватар

> **Status:** Historical tranche implemented; production loop incomplete · **Date:** 2026-06-18 · **Reconciled:** 2026-08-28 · **Owner:** apatch core
> **Source of truth:** git history (`git log`) + governed gates (`apatch rfp coverage`, spec lint, tests). The snapshot below is dated, not a maintained ledger.
> **Anchors:** [Avatar Architecture Canon](./AVATAR-ARCHITECTURE-CANON.md) · [RFP-025 Avatar Foundation](./RFP-025-avatar-foundation.md) · [RFP-026 Contribution Timesheet](./RFP-026-contribution-timesheet.md)
> **External consumers:** Human_Capital/HC_Platform (ADR-006 unified log, ADR-007 avatar, HC_Tracker)

---

## 0. Reconciliation amendment (2026-08-28)

This RFP records the first ContributionEvent wiring tranche. Its 2026-06 problem
statement and status table are historical snapshots, not the current Avatar product
definition. The current authority is
[AVATAR-ARCHITECTURE-CANON](./AVATAR-ARCHITECTURE-CANON.md) v1.3,
[AVATAR-UTILITY-CONTRACT](./AVATAR-UTILITY-CONTRACT.md), and
[RFP-037](./RFP-037-avatar-semantic-engine.md).

Current facts:

- `avatar-contract` is a versioned `0.5.0` dependency pinned by commit SHA in both
  apatch's `avatar` extra and HC Tracker; it is no longer a local editable-only contract.
- `apatch avatar sync` has a durable local outbox and reports delivery completeness
  separately from local queue safety. `ok=true` does not mean remote delivery completed.
- Contribution counters are not the Avatar's useful output. WorkEpisodes,
  independently accepted outcomes, CapabilityEvidence and reusable WorkAssets form the
  semantic projection consumed by the product surfaces.
- `trust-chain.ai` owns the canonical web Avatar Home. HC Tracker owns the backend
  read model and native/mobile professional surface; it is not a second Avatar identity.
- The real 2026-08-28 ledger baseline was 1,048 episodes, zero independently accepted
  outcomes/capabilities and 321 queued sync items with Tracker unconfigured. Therefore
  the automated tests are green, but the live owner-to-counterparty loop is not complete.

The sections below remain useful as design history. Any conflicting current-tense claim
is superseded by this amendment and the three documents above.

---

## 1. Historical context and problem (2026-06-18)

Слой аватара собран из «полуфабрикатов», которые росли по отдельности и разъезжались
на стыках. Канон ([AVATAR-ARCHITECTURE-CANON](./AVATAR-ARCHITECTURE-CANON.md)) навёл
порядок в смыслах: четыре слоя (крипто-субстрат → работа/доказательство → аватар →
экономика), один контракт `ContributionEvent` как спина, один первичный ключ
идентичности `key_id`. На уровне *определений* связка теперь непротиворечива.

Но **связка не живёт и её не видно**:

1. **Поток разорван.** apatch умеет фиксировать вклад и подписывать его, на стороне HC
   есть хранилище-приёмник — но между ними нет трубы. Вклад, сделанный в инструменте,
   не доезжает до площадки автоматически. Сегодня событие в лучшем случае ложится в
   локальный per-identity store (`~/.trustchain/contributions/`) и там и остаётся.
2. **Человек ничего не видит.** В кабинете профессионала (HC_Tracker) экран аватара —
   «мёртвый остров»: он показывает самозаявленный игровой PHI, а не *подтверждённую*
   историю реальных дел. То есть главный смысл — «носи с собой доказуемый портфель
   наработок» — пользователю не предъявлен.
3. **Контракт на тот момент ещё не был «настоящим».** Общий пакет
   `avatar-contract` существовал и был зелёным, но держался на локальной
   editable-связке этой машины; обе стороны ещё не подключили его как закреплённую
   зависимость.
4. **apatch-эмиттер не закрыт по governance.** Переход эмиттера на v2-форму
   (`proof_ref` + `avatar_id`, обратная совместимость с v1-квитанциями) реализован и
   зелёный в рабочем дереве, но не проведён через governed-commit и переаттестацию.

Состояние «построено, но не работает и не видно» — это и есть граница, которую
закрывает RFP-028.

### Что уже есть (фундамент, на который опираемся)

- **Контракт** `avatar_contract.ContributionEvent` v2 (`proof_ref`, `avatar_id`,
  `schema_version` 2), с обратной совместимостью чтения v1 и **доказанной байт-в-байт
  совместимостью подписи** старых квитанций.
- **Стопгэп идентичности** `avatar_contract.identity.AliasResolver` — `key_id` как
  первичный ключ, внешние имена резолвятся в него (закрывает «four islands»).
- **Приёмник на стороне HC** — модель `contribution_events` (append-only, формы
  fact/claim), ingest-сервис с идемпотентностью и транзакционным outbox, тесты зелёные.
- **apatch-эмиттер v2** — в рабочем дереве, тесты зелёные, спека R1/R10 обновлены.

### Чего не хватало в снимке 2026-06-18 (предмет этого RFP)

- Трубы apatch → HC (экспорт + надёжная доставка без потерь и дублей).
- Публикации общего контракта как настоящей зависимости обеих сторон.
- Экрана аватара в HC_Tracker — подтверждённая история, видимая человеку.
- Завершения apatch-эмиттера по governance (commit + переаттестация).
- Сквозной демонстрации всей цепочки на одной реальной сессии.

---

## 2. Цель

**Превратить собранный слой аватара в живую, видимую цепочку:** человек делает
governed-работу → apatch её фиксирует → вклад доезжает до хранилища HC без потерь →
человек видит свой растущий, *подтверждённый* аватар. Один контракт проходит насквозь;
ни один слой не переопределяет сущности другого; деньги в пути вклада не участвуют.

Критерий успеха одной фразой: **на одной реальной сессии можно показать путь от
«поработал» до «вот мой аватар на экране» — и каждый шаг доказуем.**

---

## 3. Объём работ (что именно строим)

Пять связанных частей; часть — закрепление готового, часть — новый код.

### 3.1 Общий контракт как настоящая зависимость *(закрепление)*
Вынести `avatar-contract` из локальной editable-связки в публикуемый пакет, который
**и apatch, и HC** объявляют в зависимостях (как уже сделано для `trustchain` через
`git+https`). Одна схема, импортируемая обеими сторонами, — не «две, совпадающие по
соглашению». Версионирование схемы зафиксировано (`schema_version`).

### 3.2 apatch-эмиттер v2: закрыть по governance *(закрепление)*
Провести уже реализованный переход на v2-форму через governed-commit и
**переаттестацию `SPEC-CONTRIB-TIMESHEET-1` R1/R10**. Жёсткий инвариант: ранее
подписанные v1-квитанции на диске **продолжают верифицироваться без изменений**
(верификация версионно-зависима, v1-путь заморожен).

### 3.3 Идентичность: `key_id`-якорь *(закрепление)*
Каждое событие адресуется по `key_id`; внешние имена (github_login, org user id,
contributor_id и пр.) резолвятся в него через `AliasResolver`. Полный IRS — вне scope
(тех-долг канона §10); здесь — рабочий стопгэп, достаточный для привязки `avatar_id`.

### 3.4 Труба apatch → HC *(новый код)*
Надёжная доставка вклада от инструмента к хранилищу:
- apatch экспортирует событие в durable-очередь (outbox), не теряя его при отсутствии
  связи (RFP-025: «queues until HC table exists»);
- HC принимает событие через ingest, **идемпотентно** по `(source, idempotency_key)`,
  без потерь и без дублей;
- источник истины при недоступности брокера — durable-таблица/outbox; Kafka — best-effort
  fan-out, не жёсткая зависимость.
- перед sync producer сверяет полный локальный набор `event_id` для одного `avatar_id`
  с Tracker; потерянная после восстановления базы строка повторно отправляется даже
  при наличии старого локального ACK. Идентичностный конфликт не ремонтируется
  переатрибуцией и оставляет sync незавершённым.

### 3.5 Экран аватара в HC_Tracker *(новый код)*
Закрыть «мёртвый остров»: кабинет профессионала показывает **подтверждённую** историю
вкладов из `contribution_events` (а не игровой PHI) — таймлайн дел с уровнем доверия
(`claimed` / `attested` / `verified`). Это первая осязаемая отдача и проверка всей
цепочки живыми глазами.

---

## 4. Подход и ключевые решения

- **Контракт — единственная спина.** Всё, что течёт между слоями, — это
  `ContributionEvent`. Источники (apatch, witness, tracker, manual) нормализуются в
  него; HC хранит его как есть. Никаких параллельных форм.
- **Подпись vers-aware, v1 заморожен.** Проверка подписи канонизирует сырой dict по
  версии: v1 — с `attestation`, v2 — с `proof_ref`. Старые квитанции неприкосновенны
  (ключ = идентичность, перевыпустить нельзя).
- **Деньги вне пути вклада (инвариант).** В `ContributionEvent` запрещены любые
  экономические поля (pi/gpi/creator_bonus/clearing/…); барьер enforced в контракте.
  PI/GPI/клиринг живут в Слое 3 (HC) и потребляют аватар, а не сырой реестр.
- **Append-only хранилище.** Никаких UPDATE/DELETE: исправление — новым событием
  (supersede). Факты и заявки (fact/claim) физически разделены.
- **Durable-first доставка.** Сначала надёжная запись (outbox/таблица), потом
  best-effort стриминг. Потеря брокера не теряет вклад.
- **Discovery, не upload.** Аватар *обнаруживается* из governed-истории, человек ничего
  не «загружает».

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A28-A | Единый контракт `ContributionEvent` существует как один publishable-пакет, объявленный в зависимостях и импортируемый обеими сторонами (apatch и HC) — одна схема, не две | MUST |
| A28-B | apatch эмитит v2-событие (`proof_ref` + `avatar_id`); каждая ранее подписанная v1-квитанция продолжает верифицироваться без изменений | MUST |
| A28-C | Любой вклад адресуется одним якорем идентичности (`key_id`); внешние имена (github login, org user id, contributor id) резолвятся в него | MUST |
| A28-D | Вклад доезжает из apatch до хранилища HC без потерь и без дублей (идемпотентность по `(source, idempotency_key)`, durable hand-off); сверка producer↔Tracker обнаруживает и повторно доставляет строки, утраченные после восстановления серверной базы | MUST |
| A28-E | Хранилище HC append-only; факты и заявки разделены; ни одно экономическое значение не попадает в событие вклада | MUST |
| A28-F | Переход apatch-эмиттера на v2 закоммичен и заверен governed-циклом (переаттестация `SPEC-CONTRIB-TIMESHEET-1` R1/R10) | MUST |
| A28-G | Человек открывает кабинет и видит свою накопленную, *подтверждённую* историю вкладов — «мёртвый остров» HC_Tracker закрыт | SHOULD |
| A28-H | Вся цепочка продемонстрирована сквозно на одной реальной сессии: работа → событие → сохранено → видно | MAY |

---

## Historical implementation status (2026-06-19)

Снимок на 2026-06-19 (источник истины — `git log` + governed-гейты + тесты, не этот текст):

| Id | Статус | Где |
|----|--------|-----|
| A28-A | ✅ DONE in tranche; superseded by `avatar-contract` 0.5.0 pinned at `08fba415...` | historical: avatar-contract (172bafd), 16 tests; current: 83 tests |
| A28-B | ✅ DONE (байт-совместимость v1 доказана на живой квитанции) | apatch contribution.py/timesheet.py (e40d97d) |
| A28-C | ✅ DONE | avatar-contract `AliasResolver` |
| A28-D | ✅ DONE | apatch contribution_export (e40d97d, 4) + HC drain + e2e |
| A28-E | ✅ DONE | HC ingest/model/migration (6c61431, 6) |
| A28-F | 🟡 PARTIAL — закоммичено; **переаттестация R1/R10 за владельцем** | SPEC-CONTRIB-TIMESHEET-1 |
| A28-G | ✅ UI/read-model implemented; operational evidence loop remains partial | HC Tracker + canonical trust-chain.ai Avatar Home |
| A28-H | 🟡 Automated path covered; live owner/counterparty production run remains open | 2026-08-28 baseline: 1,048 episodes, 0 accepted capabilities, 321 queued items |

---

## 5. Фазы (последовательность реализации)

1. **Фундамент-замок.** Закрепить контракт (3.1) и идентичность (3.3) спекой
   `SPEC-AVATAR-CONTRACT-1`. Закрыть apatch-эмиттер по governance (3.2 → A28-F).
2. **Труба.** Построить экспорт + надёжную доставку (3.4 → A28-D) спекой
   `SPEC-CONTRIB-PIPE-1`; на стороне HC — `SPEC-HC-CONTRIB-INGEST-1` (формализует
   уже построенный приёмник, A28-E).
3. **Видимость.** Построить экран аватара (3.5 → A28-G) спекой `SPEC-AVATAR-VIEW-1`
   (HC consumer-repo).
4. **Сквозная демонстрация** (A28-H) — ручной прогон одной реальной сессии.

Спина `ContributionEvent` проходит фазы 1→3; всё навешивается на неё.

---

## 6. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Слепой rename `attestation→proof_ref` ломает живые подписанные квитанции (неперевыпускаемы) | vers-aware верификация; v1-путь заморожен; байт-совместимость доказана на живой квитанции |
| Экономические данные протекают в событие вклада | барьер-инвариант в контракте, под тестом; A28-E |
| Потеря вклада при недоступности брокера | durable-first: outbox/таблица — источник истины, Kafka best-effort |
| Локальный ACK пережил восстановление/пересоздание Tracker DB либо событие сохранено без verification receipt | bounded reconciliation по `avatar_id + event_id` отдельно сообщает `missing`, `present+verified` и `present+unverified`; отсутствующие и непроверенные события повторно проходят обычный идемпотентный ingest, а завершение подтверждается второй сверкой |
| v1 receipt не содержит top-level `avatar_id` | identity scope читает замороженный fallback `identity.key_id`; байты legacy-конверта не переписываются |
| Дубли при повторной доставке | идемпотентность по `(source, idempotency_key)`; append-only |
| Отсутствие полного IRS блокирует привязку | стопгэп `AliasResolver` + `legacy_identity` как fallback; полный IRS — тех-долг |
| Кросс-репо координация (apatch / HC / пакет) | один контракт-пакет как единственная точка связи; владение по канону §4 |

---

## 7. Non-goals (вне scope)

- **Экономика** (PI/GPI, Creator Bonus, клиринг, escrow) — Слой 3, отдельный RFP.
- **Полный IRS / person↔много-ключей / DID-суверенность** — тех-долг (Канон §10).
- **Kafka как жёсткая зависимость** — durable-таблица/outbox самодостаточны; стриминг best-effort.
- **Безопасность ядра apatch** (локальный ключ, anchor по умолчанию, обходимость хуков) — отдельный трек тех-долга.

---

## 8. Зависимости и владение (по Канону §4)

- **`trust_chain` / TrustChain Platform** (Слой 0) — подпись, цепочка, anchor, CA, `key_id`.
- **`apatch`** (Слой 1) — эмиттер вклада, спеки, governed-attest; владеет эмиттером и контракт-конформностью.
- **`avatar-contract`** — единственный общий контракт + стопгэп идентичности; импортируют обе стороны.
- **HC_Platform / HC_Tracker** (Слой 3 + поверхность) — хранилище (ADR-006), экран аватара; потребляют аватар, не сырой реестр.

---

## 9. SPEC-проекция (multi-spec)

Часть фундамента уже построена — соответствующие критерии *ссылаются* на сиблинг-спеки (waiver), а не переписываются.

| RFP id | SPEC | Disposition |
|--------|------|-------------|
| A28-A | SPEC-AVATAR-CONTRACT-1 | covered (контракт-пакет) |
| A28-B | SPEC-CONTRIB-TIMESHEET-1 | waiver: implemented in SPEC-CONTRIB-TIMESHEET-1 (R1/R10) |
| A28-C | SPEC-AVATAR-CONTRACT-1 | covered (резолвер идентичности) |
| A28-D | SPEC-CONTRIB-PIPE-1 | covered (экспорт + доставка) |
| A28-E | SPEC-HC-CONTRIB-INGEST-1 | waiver: implemented in HC SPEC-HC-CONTRIB-INGEST-1 (consumer repo) |
| A28-F | SPEC-CONTRIB-TIMESHEET-1 | waiver: governed re-attestation существующего спека |
| A28-G | SPEC-AVATAR-VIEW-1 | waiver: implemented in HC SPEC-AVATAR-VIEW-1 (consumer repo) |
| A28-H | — | waiver: ручная сквозная демонстрация, вне авто-scope MVP |
