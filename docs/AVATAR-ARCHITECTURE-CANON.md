# Avatar Architecture Canon

> **Статус:** CANON (надведомственная точка правды). Версия 1.3 · 2026-08-28.
> **Область:** `trust_chain`, `TrustChain_Platform`, `TrustChain_Agent`, `apatch`, `Human_Capital/HC_Platform` (+ `HC_Tracker`).
> **Назначение:** зафиксировать онтологию экономики аватаров и слоистую архитектуру так, чтобы
> на стыках репозиториев не возникало конкурирующих определений одной и той же сущности.
>
> **Правило приоритета:** при конфликте этот документ имеет приоритет над `vision.md`, `AVATAR_AGENT_CONCEPT.md`,
> отдельными ADR и README. Если другой документ определяет `Avatar`, `ContributionEvent`, `Identity` или `Agent`
> иначе — побеждает канон, а тот документ правится ссылкой сюда. Изменение канона = отдельный PR с обоснованием.

---

## 0. Зачем этот документ существует

Боль на стыках имеет одну причину: **четыре ключевых слова имеют по 3–4 владельца**, и каждый репозиторий
тихо переопределяет чужое. Канон закрывает это, давая каждой сущности **ровно одного владельца** и одно имя.

Конкретные коллизии, которые канон разрешает (раздел [§5 Глоссарий](#5-глоссарий--разрешение-коллизий)):

| Слово | Сколько определений сейчас |
|---|---|
| **Agent** | 4 (строковый `agent_id`+ключ / X.509-сертификат / рантайм-продукт / «присутствие человека») |
| **Contribution** | 5 (эмиттер apatch / таблица ADR-006 / `GitHubWitnessEvent` / транзиентный CV-вектор / PHI-очки) |
| **Avatar** | 3 (apatch-репозиторий / `asset_summary` / `AvatarEconomicsProfile` PI-скаляр) |
| **Identity** | 7 (`key_id` / `agent_id` / cert CN / org `user_id` / `github_login` / `contributor_id` / `professional_uuid`) |

---

## 1. Три существительных

Во всей вселенной есть только три существительных. Всё остальное — слой или проекция над ними.

1. **Подписанный факт работы** (signed fact) — атом. Им владеет крипто-субстрат. Всё доказуемое сводится к цепочке таких фактов.
2. **Аватар** (avatar) — портативная, адресуемая по `key_id`, money-free проекция подписанных фактов одного subject: человека **или** агента. Человеческий портфель связывает несколько subject-key через ownership, не переписывая авторство.
3. **Экономика** (economy) — цена, клиринг, бонус, трансфер. Ею владеет HC_Platform.

---

## 2. Четыре правила

**Правило 1 — Один вход работы: `ContributionEvent`.**
Это единственный мост, способный создать факт работы в аватаре. Одна версионированная схема в одном месте, которую
импортируют и `apatch`, и `HC` — не «две схемы, совпадающие по соглашению». Все источники (apatch, witness,
tracker, ручной ввод) нормализуются в неё. Независимый `OutcomeAttestation` может только подтвердить исход уже
существующего `WorkEpisode`; он не создаёт работу и не меняет identity. Полная спецификация — [§7](#7-contributionevent--канонический-контракт-v1).

**Правило 2 — Аватар = факты + выводы + рабочие активы, без денег.**
`Avatar = fold(ContributionEvents одного subject key) → history + WorkEpisodes + CapabilityEstimates + WorkAsset refs`.
`WorkEpisode` является проверяемым фактом, `CapabilityEstimate` — выводом с неопределённостью,
а цена — отдельной L3-интерпретацией. **Деньги в свёртку не входят никогда.**

**Правило 3 — Один первичный ключ identity: `key_id`, остальное — алиасы.**
Аватар адресуется по `key_id` (sha256 публичного ключа сертификата). `github_login`, org `user_id`,
`professional_uuid`, `contributor_id` — это **алиасы**, резолвящиеся в `key_id`. Полноценный IRS — в тех-долге
([§10](#10-тех-долг--сознательно-отложено)); сегодня резолвер = тривиальная таблица алиасов.

**Правило 4 — Никто не переопределяет чужие сущности, только ссылается.**
Таблица владения — [§4](#4-четыре-слоя-и-владение-сущностями). Любой репозиторий может *ссылаться* на чужую
сущность (по `key_id`, `op_id`, `event_id`, `spec:ID#R`), но не вводить собственную её форму.

---

## 3. Четыре слоя (карта)

```
Слой 4 · Поверхности (адаптеры)      trust-chain.ai · HC Tracker · IDE/MCP    — 0 доменных сущностей
Слой 3 · Экономика                   HC_Platform — PI/GPI, Creator Bonus, клиринг, escrow
Слой 2 · Аватар ◄ ФОКУС              Avatar Compiler — свёртка истории, БЕЗ денег
Слой 1 · Работа и доказательство     apatch — governed mutation, спеки, эмиссия attestation
Слой 0 · Крипто-субстрат             trust_chain (подпись/цепочка) · TrustChain Platform (CA, anchor)

        Спина, связывающая L1 → L2 → L3:  ContributionEvent (единый контракт)
```

Поток: **L1 эмитит** `ContributionEvent` → **L2 сворачивает** их в аватар → **L3 потребляет** аватар.
Поверхности (L4) — только адаптеры: рендерят и инициируют, но не владеют данными.

---

## 4. Четыре слоя и владение сущностями

Каждая сущность имеет **ровно одного владельца**. Остальные только ссылаются.

| Сущность | Единственный владелец | Остальные |
|---|---|---|
| Ключ, сертификат, подписанный факт, anchor/inclusion-proof | `trust_chain` + `TrustChain_Platform` | ссылаются по `key_id` / `op_id` |
| **`ContributionEvent`** (контракт) | **общий версионированный пакет-схема** | `apatch` эмитит, `HC` хранит/раздаёт |
| `Attestation`, `Session`, `Spec`/`Requirement`, `Needle` | `apatch` | `HC` цитирует `spec:ID#R` |
| **`Avatar`** (`history` + `WorkEpisode` + `CapabilityEstimate` + WorkAsset refs) | **`apatch` Avatar Compiler (проекция)** + `HC` (хранение/раздача) | `Tracker` зеркалит |
| PI / GPI / scarcity / Creator Bonus / клиринг / escrow / bonds | `HC_Platform` | потребляют аватар, не реестр |
| UI, рантайм, чат, песочница исполнения | `trust-chain.ai` (canonical web), `HC_Tracker` (native/mobile), IDE | адаптеры, 0 доменных сущностей |
| `Identity` (person↔ключи), алиасы, IRS | `HC_Platform` (резолвер) | все ссылаются по `key_id` |

**Что это значит для каждого репозитория:**

- **`trust_chain`** — крипто-примитивы: подпись, цепочка, верификация, anchor/transparency, CA (dev/локально). Не знает про аватары и деньги.
- **`TrustChain_Platform`** — авторитетный CA + публичный anchor/transparency-сервис + identity-резолвер (хостинг). Выдаёт `key_id`.
- **`apatch`** — Слой 1 + компилятор аватара (Слой 2, проекция). «GitHub для методологии + счётчик, эмитящий подписанные факты работы». **Не владеет экономикой и UI.**
- **`HC_Platform`** — Слой 3 (экономика) + хранение/раздача аватаров (unified log ADR-006). Потребляет аватар, не сырой реестр.
- **`HC_Tracker`** — HC-owned persistence adapter и native/mobile Avatar Home: хранит подписанные snapshots и зеркалит факты, выводы, ownership и отдельную L3-проекцию. Не переопределяет доменные контракты.
- **`TrustChain_Agent`** — execution runtime и BFF публичного продукта `trust-chain.ai`. Хостит canonical web Avatar Home, но **не владеет доменной сущностью Avatar** и не вычисляет способности.

---

## 5. Глоссарий — разрешение коллизий

**`Agent` — слово запрещено к самостоятельному употреблению без квалификатора.** Используй точные термины:

| Было (неоднозначно) | Канон | Что это |
|---|---|---|
| «agent» = ключ/`agent_id` | **`key_id`** | identity-of-action в субстрате |
| «agent» = X.509-сущность Platform | **enrolled cert** (`cert_fingerprint`, CN) | сертификат, выданный CA |
| «TrustChain Agent» = продукт | **execution runtime** | рантайм-поверхность L4, не доменная сущность |
| «avatar agent» = человек | **person / avatar** | принадлежит Слою 2, адресуется `key_id` |

**`Contribution`** — единственная форма: **`ContributionEvent`** ([§7](#7-contributionevent--канонический-контракт-v1)).
Все прежние формы становятся *источниками*, которые нормализуются в неё:

| Источник | `source` | `trust_level` по умолчанию |
|---|---|---|
| apatch governed session | `apatch` | `attested` (есть proof_ref) |
| GitHub Witness (`GitHubWitnessEvent`) | `witness` | `verified` (наблюдён независимо) |
| HC Tracker PHI / self-report | `tracker` | `claimed` (нет proof_ref) |
| ручной ввод | `manual` | `claimed` |

Транзиентный `contribution_vector` (`avatar_attribution.py`) — это **`kind: "claim"` с `cv_delta`**, не отдельная сущность.

**`Avatar`** — единственное определение: [§6](#6-avatar--формальное-определение). `AvatarEconomicsProfile` (PI/GPI-скаляр HC) —
это **экономическая проекция** аватара (Слой 3), а не сам аватар.

**`Identity`** — первичный ключ `key_id`; всё остальное (`github_login`, `user_id`, `professional_uuid`,
`contributor_id`, cert CN) — **алиасы → `key_id`**.

---

## 6. `Avatar` — формальное определение

```
Avatar(subject_key) ≔ fold(ContributionEvent where avatar_id == subject_key,
                           OutcomeAttestation bound to derived WorkEpisode)
                  → { identity, history[], work_episodes[],
                      capability_estimates[], work_asset_refs[], asset_summary }

Portfolio(principal_key) ≔ Avatar(principal_key)
                         ∪ { Avatar(agent_key) where owner(agent_key)=principal_key }
```

- **Детерминированный** — одинаковый вход даёт одинаковый выход (тестируемо).
- **Без денег** — ни PI, ни GPI, ни bonus, ни marketplace в выходе свёртки. Это инвариант, защищённый тестом
  (`tests/test_avatar_compiler.py::test_r3_economic_boundary`).
- **Discovery, не upload** — единственный вход компилятора `target_dir`; аватар *обнаруживается* из governed-истории,
  не «загружается» пользователем (`avatar_compiler.py`, тест `test_r2_discovery_no_upload`).
- **Без смешивания identity** — capability всегда сохраняет `subject_avatar_id`. Ownership
  разрешает показать агента рядом с человеком, но не превращает agent evidence в human evidence.
- **Полезность, не архив** — наружу экспортируется metadata-distillate: только пригодные
  эпизоды, оценки, агрегированные причины исключения и указатели на WorkAssets. Сырые
  логи, промпты, код и rejected-активность остаются локально.

**`asset_summary` v1** (`apatch/avatar_compiler.py`, `kind: "asset_summary"`): `identity{key_id, cert_fingerprint, ca}`,
`trust_level`, `artifact_count`, `attested_artifacts[]`, `spec_ids[]`, `requirement_states{}`, `methodology_tags[]`,
`attested_at_range{first,last}`. Это **proof-проекция** аватара (что сделано, заверено, воспроизводимо).

**Что аватар НЕ есть:** не репозиторий apatch, не рантайм-агент, не число PI. Аватар — это свёртка потока
`ContributionEvent` одной identity и независимо подписанных исходов, привязанных к его эпизодам.

---

## 7. `ContributionEvent` — канонический контракт v1

Единственный мост, создающий работу в цепочке «работа → аватар → экономика». Одна версионированная схема, импортируемая `apatch` и `HC`.
Базируется на реальной `apatch/contribution.py` (`SCHEMA_VERSION = 1`) и форме HC ADR-006.

### 7.1 Схема

```jsonc
{
  "schema_version": 1,
  "event_id":       "<sha256 от (avatar_id + project.id + session + ops), стабильный>",
  "idempotency_key":"<= event_id; дедуп при повторной эмиссии и реплее>",
  "kind":           "fact | claim",
  "avatar_id":      "<= identity.key_id — первичный ключ аватара>",

  "source":         "apatch | witness | tracker | manual | agent",
  "trust_level":    "claimed | attested | verified",

  "identity":   { "key_id": "...", "cert_fingerprint": "sha256:...|null", "ca": "platform|legacy" },
  "project":    { "id": "<hash git remote|root>", "name": "...", "remote": "...|null" },
  "session":    { "intent": "...", "artifacts": ["path#hash", ...],
                  "started_at": "<iso|epoch>", "ended_at": "...", "duration_s": 0 },
  "volume":     { "ops": 0, "files": 0, "insertions": 0, "deletions": 0 },

  // present iff trust_level >= attested:
  "proof_ref":  { "ledger": "trustchain", "op_ids": ["..."], "head": "...", "anchor": "...|null" },

  // kind == "fact":
  "payload":        { /* доменная нагрузка, без секретов и исходников */ },
  // kind == "claim":
  "cv_delta":       { "human": 0.0, "personal_ai": 0.0, "company_ai": 0.0, "company_data": 0.0, "third_party": 0.0 },
  "declares_for":   "<event_id факта, к которому относится claim>",

  "methodology_tags": ["..."],
  "created_at":     "<iso>",
  "signature":      "<Ed25519 над canonical(unsigned)>|null"
}
```

> **Внимание — НЕ переименовывать вслепую.** Поле `attestation{op_ids, head}` в текущем `apatch/contribution.py`
> семантически = `proof_ref` канона, **но** оно входит в аттестованную схему `SPEC-CONTRIB-TIMESHEET-1#R1`
> (тест `test_r1_schema_v1` проверяет `d["attestation"]["op_ids"]`), его читают `timesheet.py`
> (`verify_events`/`build_event`), emit-хук в `runtime.attest`, и уже выписанные подписанные квитанции в
> `~/.trustchain/contributions/<key_id>/*.json` содержат именно `attestation`. Слепой rename ломает тесты,
> ломает live-квитанции и меняет аттестованную схему → R1 уходит в `stale` и требует переаттестации.
> Правильный путь — **версионированный shim** (см. [§9 Фаза 1](#9-план-реализации-фазы)), а не однострочник.

### 7.2 Лестница `trust_level`

| Уровень | Условие | Кто производит |
|---|---|---|
| `claimed` | самозаявка, **нет** `proof_ref` | tracker (PHI), manual |
| `attested` | есть `proof_ref` на подписанную op в реестре | apatch governed session |
| `verified` | `attested` **и** независимо перевоспроизводимо / внешне заякорено (inclusion proof) | witness, anchored apatch |

Экономика (Слой 3) **обязана** учитывать `trust_level`: `claimed`-события не входят в клиринг, только в reporting.

### 7.3 Инварианты (обязательны, тестируемы)

1. **Экономический барьер.** `ContributionEvent` НЕ содержит `pi`, `gpi`, `creator_bonus`, `clearing`, `marketplace`,
   денежных сумм. Эти поля — Слой 3.
2. **Без секретов.** Только пути, хэши, счётчики, тайминги, теги. Никакого исходного кода/секретов (`payload` это соблюдает).
3. **Идемпотентность.** Два события с одинаковым `idempotency_key` — одно и то же; потребитель (HC log) дедуплицирует.
4. **Identity = `key_id`.** `avatar_id` всегда равен `identity.key_id`. Алиасы тут не хранятся.
5. **`claim` всегда ссылается на `fact`** через `declares_for` и требует последующей верификации.

### 7.4 Поток доставки

```
apatch (L1) ── emit ──> ~/.trustchain/contributions/<key_id>/<event>.json
  ├─ operator/server path: export_pending() ──> durable outbox ──> HC bridge
  └─ owner/web path: apatch contributions sync ──> trust-chain.ai scoped tcav_ token ──> HC Tracker
```

Пока HC-приёмник или trust-chain.ai недоступны — события остаются в локальном
per-identity store, а sync/bridge пишут отдельные delivery receipts и остаются
идемпотентными при повторном запуске. HC — единственный владелец центрального хранилища
(Postgres `contribution_events`); apatch — только продюсер.

### 7.5 Обратный поток внешнего исхода

`OutcomeAttestation` — отдельный money-free/content-safe факт независимой приёмки.
Его выпускает естественный контрагент конкретной работы: заказчик, работодатель,
оператор соревнования либо рыночная платформа. Контрагент подписывает exact fact,
связанный с уже сохранённым и криптографически проверенным `WorkEpisode`. Tracker
проверяет purpose-bound registry `key → organization → allowed roles`, хранит read-model,
а apatch забирает аттестат и пересобирает эпизод/оценку.

```text
apatch → Tracker → natural counterparty/TrustChain → Tracker → apatch → CapabilityEstimate
```

Инварианты: один `subject_avatar_id`, один `work_episode_id`; точное совпадение
неизменного `WorkReviewPackage`, исходной task identity и
`hc-evidence:{bundle}:{episode}` pointer; purpose-bound подпись контрагента; только
`passed|failed`; никаких денег или сырого контента. Более поздняя подписанная owner
taxonomy является отдельной классификацией: она может дополнить эпизод, не аннулируя
приёмку результата и не выдавая классификацию за решение контрагента. Самозаявленная роль,
непривязанный ключ, Association или assessor fail closed. Технический gate остаётся
proxy и никогда не становится capability evidence без `external_acceptance` либо
независимо проверенного WorkAsset reuse.

Association решает другой вопрос: соответствует ли профессионал опубликованному
профессиональному стандарту. Такая сертификация требует standard reference,
assessment/examination reference, custody конкретного officer и audit trail. Она может
создать credential, но не `OutcomeAttestation`. TrustChain/Tracker проверяют техническую
достоверность; контрагент принимает результат; Association сертифицирует по стандарту;
участники рынка определяют цену. Эти четыре решения не заменяют друг друга.

### 7.6 Обратный поток owner-governed taxonomy

Когда подписанный `ContributionEvent` содержит content-safe intent, но не имеет
accepted taxonomy, HC Tracker может построить локальные versioned O*NET proposals.
Proposal не является фактом, опытом или skill claim. Только аутентифицированный
владелец Avatar выбирает точные occupation/task/skills либо отклоняет предложение.
Ссылка `spec:*` описывает governed артефакт, а не профессию или навык, и сама по себе
не закрывает детальную O*NET-классификацию.

Tracker выпускает отдельный money-free `TaxonomyDecision`, подписанный TrustChain и
связанный с exact `avatar_id`, `source_event_id`, детерминированным `work_episode_id`,
hash исходного intent и supersede-цепочкой. apatch забирает решение через owner-scoped
канал, проверяет pinned issuer и все связи, после чего детерминированно пересобирает
эпизод. Accepted decision устраняет только `taxonomy_missing`; rejected decision
снимает предыдущую классификацию. Ни proposal, ни decision не создают outcome,
CapabilityEstimate, PI/GPI, salary, scarcity, valuation или deal price.

```text
apatch fact → Tracker proposal → Avatar owner decision → Tracker signature
            → trust-chain.ai owner pull → apatch WorkEpisode re-derivation
```

---

## 8. Identity — стопгэп (что делаем сейчас)

- **Первичный ключ — `key_id`** (sha256 публичного ключа enrolled-сертификата, `resolve_identity()` в `contribution.py`).
- **Алиасы → `key_id`** через тривиальный резолвер. **Реализован:** `avatar_contract.identity.AliasResolver`
  (пакет `avatar-contract`, тесты зелёные). «IRS» сегодня = эта таблица; namespaces ADR-004
  (`github_login`, `org_user_id`, `professional_uuid`, `contributor_id`, `enrolled_cn`, `legacy_agent_id`)
  сохранены и **не схлопываются**: `key_id` = identity *действия*, не вес контрибуции. Привязка
  `avatar_id ≡ contributor_id ≡ key_id` (ADR-006 «four islands») закрывается через `resolve(...)`.
- **Fallback `legacy:<agent_id>`** допустим для неэнролленных сред, но такие события не поднимаются выше `claimed`.

Полноценный IRS, person↔много-ключей, DID-суверенность — [§10 тех-долг](#10-тех-долг--сознательно-отложено).

---

## 9. План реализации (фазы)

> **Статус на 2026-06-19:** фазы 0, 0.1, 1, и большая часть 3 — **сделаны** (контракт-пакет
> `avatar-contract`, shim эмиттера v2, труба apatch→HC, приёмник + дренаж + экран-data в HC,
> сквозной e2e — всё зелёное), см. [RFP-028 §Implementation status](./RFP-028-avatar-wiring.md).
> Источник истины — `git log` + governed-гейты, не проза. Осталось: переаттестация R1/R10,
> фронт-страница аватара, авто-триггер трубы, и Фаза 4 (экономика).

| Фаза | Что | Где | Готово, когда |
|---|---|---|---|
| **0** | Этот канон | (нейтральный docs) | принят как точка правды ✅ |
| **0.1** | **Распространить ссылку на канон** в `AGENTS.md` четырёх репо | apatch, TC_Agent, TC_Platform, HC | дёшево, необратимо-безопасно, останавливает дрейф → **делать первым** |
| **1** | `ContributionEvent` v1 как **общий версионированный пакет** + **shim**, не rename | новый shared-модуль; импорт в apatch + HC | см. чек-лист ниже |
| **2** | Достроить компилятор аватара (свёртка без денег) | `apatch` | `apatch asset` CLI/MCP поднят; экон-барьер под тестом |
| **3** | Реестр/раздача аватаров; закрыть «мёртвый остров» Tracker | `HC_Platform` + `HC_Tracker` | unified log принимает события по `avatar_id`; Tracker читает avatar-economics API |
| **4** | Экономика читает проекцию аватара | `HC_Platform` | PI/GPI/клиринг от аватара, не ad-hoc |

Спина `ContributionEvent` проходит фазы 1→3; всё остальное навешивается на неё.

### Фаза 1 — это координированный breaking-change, не однострочник

`proof_ref` вводится как **версионированный shim** с обратной совместимостью (`attestation` — аттестованное поле, см. предупреждение в [§7.1](#71-схема)):

1. вынести схему в общий версионированный пакет, импортируемый apatch и HC;
2. `bump schema_version 1 → 2`; на чтении принимать **оба** ключа (`proof_ref or attestation`) на переходный период;
3. мигрировать `tests/` + `timesheet.py` + emit-хук `runtime.attest`;
4. **переаттестовать** `SPEC-CONTRIB-TIMESHEET-1#R1` (или rebind через `noop_attest`);
5. синхронно обновить HC-консьюмера (ADR-006 приёмник).

**Координация (критично):** `contribution.py` правится в параллельной сессии под enforce. Не трогать его из
второй сессии — иначе lease contention / drift. Порядок: сначала Фаза 0.1 (только doc + AGENTS.md, файл не
затрагивается); контракт-shim — когда владелец освободит `contribution.py`.

---

## 10. Тех-долг — сознательно отложено

Эти проблемы **реальны, но не блокируют слой аватара**. Стопгэпы выше держат.

1. **Полный IRS / person↔много-ключей / DID-суверенность.** Стопгэп §8 (`AliasResolver`) **реализован 2026-06-19**; полный IRS остаётся. Владелец: HC.
2. **Два transparency-log и два CA** (ядро `VerifiableChainStore`+`Witness` vs Postgres-лог Platform; `x509_pki` vs Platform CA).
   Граница почти проведена (ядро = dev/локально, Platform = авторитет); формализовать позже.
3. **Безопасность apatch-ядра** — ключ читаем локально, anchor по умолчанию выключен, sandbox-хуки обходятся,
   matcher тихо ошибается (whitespace-fuzzy без проверки уникальности, semantic-sid без content-verify, нет
   идемпотентности/детекции конфликтов, confidence = константа). Важно для *доверия к фактам*, отдельный трек.
4. **Персистентность `contribution_vector`** (HC TD-007) и **PHI vs CV** — внутри HC-экономики, ниже слоя аватара.
5. **`attestation → proof_ref`** — ✅ **DONE 2026-06-19**: версионированный shim приземлён (schema_version 1→2,
   чтение обоих ключей, v1-verify заморожен и байт-совместим, HC синхронизирован). **Остаётся:** переаттестация
   `SPEC-CONTRIB-TIMESHEET-1` R1/R10 на реестре (A28-F). См. [RFP-028](./RFP-028-avatar-wiring.md).
6. **5 фактических «сессий» в apatch** (`session_state`, apply, spec_run, checkpoints, knowledge_graph) — свести
   к одному версионированному агрегату с общим ключом; `attested` выводить из реестра, не хранить булевом.

---

## 11. Как репозитории ссылаются на канон

Каждый репозиторий в своём `AGENTS.md` / `README` ставит строку:

> Онтология и границы слоёв — **Avatar Architecture Canon**. Этот документ — точка правды по сущностям
> `Avatar`, `ContributionEvent`, `Identity`, `Agent`. Локальные определения, противоречащие канону, недействительны.

Документы с устаревшими определениями (`vision.md` §compiler-outputs, `AVATAR_AGENT_CONCEPT.md` §avatar-as-agent,
ADR-002/006/007 при расхождениях) правятся ссылкой сюда, а не дублированием.

---

*Канон ведёт Ed Cherednik. Изменения — отдельным PR с обоснованием и bump версии канона.*
