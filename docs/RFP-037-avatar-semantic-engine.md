# RFP-037 — Смысловой двигатель аватара: эпизоды → способности → экономические сигналы

> **Status:** Implemented v3 · **Date:** 2026-07-18 · **Owner:** apatch product
> **Anchors:** [Avatar Architecture Canon](./AVATAR-ARCHITECTURE-CANON.md) · [AUC-1](./AVATAR-UTILITY-CONTRACT.md) · [RFP-025](./RFP-025-avatar-foundation.md) · [RFP-026](./RFP-026-contribution-timesheet.md) · [RFP-028](./RFP-028-avatar-wiring.md) · [ADR-002](./adr/ADR-002_Agent_As_Asset_And_Ownership_Edge.md) · [RFP-005 probe](./probe.md) · [RFP-031 WorkAssets](./RFP-031-work-assets.md)
> **External consumers:** HC_Platform (ADR-006/007, HC Capital), HC_Tracker / trust-chain.ai (вкладка «Аватар»)
> **Canon:** §6–7 amended in Avatar Architecture Canon v1.3. Governed O*NET/CBM bridge and counterparty-bound external outcome loop are implemented.

---

## 1. Контекст и проблема

Аватарная цепочка доведена до «данные собраны»: подписанные квитанции на каждый
attested-session (RFP-026), труба apatch → HC (RFP-028), детерминированная свёртка
`asset_summary` (RFP-025), публичный money-free профиль на TrustChain Platform,
контракт полезности recall → use → verify → learn (AUC-1). Раздел «Аватар» на
trust-chain.ai существует — и по сути пуст: он показывает, **сколько** данных собрано,
потому что показать больше нечем.

Ошибка не в исходной идее, а в отождествлении трёх разных переходов:

```text
данные собраны  ≠  опыт извлечён  ≠  экономическая ценность создана
```

`asset_summary` сегодня — это `artifact_count`, `spec_ids`, `methodology_tags`:
проекция **объёма**, не **способностей**. Между подписанными событиями и экономической
моделью HC Capital отсутствует смысловой слой:

```text
события → рабочие эпизоды → способности → экономические сигналы
```

Уникальность данных сама по себе не делает их ценными. Ценность возникает, только если
данные (1) доказывают способность человека, (2) помогают прогнозировать его будущий
результат, (3) позволяют повторно использовать накопленный опыт, (4) разделяют вклад
человека, публичного AI и инфраструктуры компании, (5) снижают неопределённость для
работодателя, инвестора или самого профессионала. Сегодня apatch закрывает первую
половину пункта (1): доказывает, что действия происходили.

### 1.1 Implementation amendment v2

Этот блок заменяет несовместимые v1-названия ниже по документу:

- `WorkEpisode v2` выводится только из сохранённого подписанного `ContributionEvent`;
  bare ledger row никогда не становится опытом.
- `eligible_for_capability` fail-closed проверяет подпись, trust, задачу, независимо
  наблюдаемый outcome, attribution, права и proof. Volume не участвует. Локальный
  `technical_gate` остаётся кандидатом на проверку и не входит в оценку способности.
- `CapabilityEstimate v2` хранит Wilson-95 interval, `observations`, recency,
  cross-context, attribution mix и uncertainty. Технические proxy-only исходы не
  входят в estimate вообще, даже если все локальные проверки зелёные; одно внешнее
  подтверждение не «разблокирует» их объём.
- `WorkReviewPackage v1` даёт контрагенту content-addressed карточку конкретного
  результата: явную цель, описание поставки, критерии приёмки, проверки, ограничения
  и безопасные proof refs. Legacy/incomplete episode остаётся видимым, но не может
  быть принят вслепую.
- `OutcomeAttestation v2` выпускается естественным контрагентом работы через
  TrustChain для одного существующего WorkEpisode и связывает решение с точным
  `review_package_id`, который контрагент видел. Он проходит purpose-bound проверку
  `key → organization → role` в Tracker и применяется apatch при следующей
  синхронизации. Reader сохраняет совместимость с v1. Association/assessor roles fail
  closed: профессиональная сертификация не заменяет приёмку результата.
- `CapabilityEvidence v2` content-addressed и Ed25519-signed. В HC уходят только
  eligible episodes; rejected activity остаётся локально, наружу идут агрегированные
  exclusion reasons.
- `apatch avatar sync` сначала забирает независимые OutcomeAttestation, затем
  пересобирает evidence и доставляет ContributionEvent. Во внутреннем контуре один
  purpose-bound токен на синхронизацию выдаёт attended resolver TrustChain Secrets.
  Нормальный запуск читает только публичный pointer из
  `~/.trustchain/avatar_sync.json`; сам токен не хранится ни в этом файле, ни в argv,
  ни в MCP. Публичные ключи и разрешённые организации/роли живут отдельно в
  versioned `~/.trustchain/avatar_trust_policy.json`, а не исчезают при перезапуске
  процесса. Environment остаётся только явным break-glass override.
  Timeout/отказ resolver возвращается как стабильный `credential_unavailable` без
  traceback, argv и внутренних путей; durable outbox остаётся нетронутым. В
  owner-контуре используется отзывной `tcav_` токен Avatar BFF. Export жёстко
  ограничен текущим `avatar_id`; cross-identity export запрещён тестом. Локально
  сохранённый outbox может вернуть `ok=true`, но не `complete=true`: агрегированный
  статус различает `tracker_unconfigured`, `delivery_pending` и `synchronized`.
- HC хранит immutable snapshots, показывает capabilities каждого subject отдельно и
  рассчитывает только shadow posterior. `pricing_effect_applied=false` до калибровки.
- Автоматический O*NET-mapping не считается принятым. HC Tracker строит локальные,
  объяснимые предложения из уже сохранённого подписанного `ContributionEvent`, но
  только аутентифицированный владелец может выбрать точные occupation/task и 1–8
  skills либо отклонить предложение.
- Решение владельца хранится как append-only `TaxonomyDecision`: оно связывает
  `avatar_id`, source event, детерминированный WorkEpisode, hash исходного intent,
  версию O*NET/matcher и supersede-цепочку. Tracker подписывает exact payload; в
  production apatch принимает его только от запиненного issuer key.
- Принятая taxonomy устраняет только `taxonomy_missing`. Она не создаёт outcome,
  CapabilityEstimate или цену. Proposal без owner decision и rejected latest не
  меняют эпизод; raw intent и любые экономические поля в контракте запрещены.
- Независимая приёмка и owner taxonomy остаются разными решениями. Контрагент
  подписывает неизменный WorkReviewPackage и исходную task identity; более поздняя
  подписанная классификация владельца может дополнить эпизод O*NET-ссылками, не
  аннулируя outcome и не утверждая, что контрагент одобрил эту классификацию.
- `spec:*` остаётся governance/evidence-ссылкой, но не подменяет детальные SOC,
  O*NET task и O*NET skill. Production pull требует pinned Tracker issuer даже при
  пустом ответе, чтобы ошибка provisioning не скрывалась до первого решения.

### 1.2 Live E2E closure, 2026-08-28

Healthy-path прогон подтвердил цепочку на production-shaped локальных сервисах:
подписанный ContributionEvent агента, принадлежащего human principal, стал
WorkEpisode + WorkReviewPackage; Company под активным project contract просмотрела
точную карточку, подписала `passed`, Tracker принял OutcomeAttestation, а следующий
owner sync показал одну capability observation. Статус остался
`insufficient_evidence` с явными причинами неопределённости. Digests экономических
snapshot/use/profile таблиц до и после совпали: приёмка работы не создала цену,
зарплату, GPI, scarcity или PI. Неисполненным operational-сценарием остаётся только
контролируемый разрыв доставки Company → Tracker и идемпотентный retry.

### Что уже есть (фундамент, на который опираемся)

| Слой | Что даёт этому RFP |
|------|--------------------|
| Подписанные примитивы (RFP-005/006/026) | session (intent, artifacts, тайминги), verify-прогоны, аттестации, `ContributionEvent` v2, `trust_level` |
| Plan / adherence (RFP-010–012) | план как артефакт + отклонение факта от плана — сигнал «решения» эпизода |
| Probe (RFP-005) | качество самого гейта (`falsify`): отличает настоящую проверку от театра |
| ADR-002 + `OwnershipGraph` | человеческие и агентские `key_id`, ребро владения — субстрат разделения ролей |
| `cv_delta` (канон §7) | claim-форма долей human / personal_ai / company_ai / company_data / third_party |
| WorkAssets + AUC-1 (RFP-031) | переиспользование метода между проектами — прямое свидетельство переносимости |
| Reality ledger (RFP-005) | внешние наблюдаемые факты (баги/инциденты) — независимый outcome-сигнал |

### Чего не хватает (предмет этого RFP)

- Понятия **рабочего эпизода** — единицы опыта (цель, контекст, решения, результат), а не единицы биллинга (сессия) и не единицы объёма (событие).
- Понятия **способности** — выводимого, доказуемого утверждения «этот человек умеет закрывать задачи класса X с надёжностью Y», с явной неопределённостью.
- **Квалификационного гейта**: не любое AI-взаимодействие — сигнал; количество промптов, токенов, команд и файлов не должно повышать оценку.
- **Экспортного контракта evidence** для HC Capital: индивидуальное обновление рыночного prior, а не «полная стоимость человека».
- **Контракта отображения** вкладки «Аватар»: что человек доказанно умеет — вместо того, сколько данных собрано.

---

## 2. Цель

**Превратить накопленные подписанные факты в доказанные способности и честные
экономические сигналы.** Критерий успеха одной фразой: вкладка «Аватар» отвечает не
«сколько событий записано», а «что этот человек доказанно умеет, на основании чего,
как это развивается, какая часть переносима — и где модель не уверена и почему».

## 3. Новое определение аватара (амендмент канона §6)

> **Аватар** — принадлежащий человеку, привязанный к его идентичности накопительный
> реестр **подтверждённых рабочих эпизодов** и **выведенных из них профессиональных
> способностей**, который улучшает будущую работу с AI (AUC-1) и служит индивидуальной
> доказательной базой для экономической оценки (HC Capital).

Формально свёртка расширяется, оставаясь детерминированной и money-free:

```text
Avatar(identity) ≔ fold(ContributionEvents) → { identity, history[], asset_summary,
                                                episodes[], capabilities[] }
```

Механика канона не ломается: эпизод и способность — **проекции** над теми же
подписанными фактами (Правило 2 канона), экономический барьер сохраняется дословно.
Амендмент принят и уточнён в Avatar Architecture Canon v1.3 (A37-J).

---

## 4. Модель данных

### 4.1 `WorkEpisode` v2 — единица опыта

Детерминированная проекция одной governed-единицы работы (сессия или Rk-цикл
`spec_run`) над уже подписанными примитивами. **Никакой новой инструментации** —
эпизод не «записывается», а выводится (принцип RFP-026: apatch не изобретает факты).

```jsonc
{
  "schema_version": 1,
  "kind": "work_episode",
  "episode_id": "sha256(avatar_id|session_id|ledger_head)[:32]",
  "avatar_id": "<key_id>",

  "task": {
    "intent": "…",                        // из session
    "requirements": ["spec:SPEC-X#R3"],   // якоря спеки
    "task_class": "…",                    // §4.3 таксономия
    "reality_refs": ["REC-…"]             // discharged наблюдаемые факты (если есть)
  },
  "context": {
    "project_id": "…",                    // стабильный cross-machine (RFP-026)
    "stack": ["python", "pytest"],        // выведено из мутаций/verify, дескриптор
    "descriptors": { "files": 3, "cross_file_impact": true }   // ОПИСАНИЕ, не скор
  },
  "decisions": {
    "plan_ref": "plan:SPEC-X@v2|null",
    "adherence": "on_plan|deviated|no_plan",
    "human_claims": ["<event_id claim>"],  // cv_delta-заявки, привязанные к факту
    "asset_reuse": ["workasset:…"]         // применённые наработки (AUC-1 use events)
  },
  "result": {
    "verify": "green|red|none",
    "verify_transitions": ["red->green"],  // наблюдаемая работа, не самоотчёт
    "gate_quality": "falsified|unprobed|false_gate",   // §4.4, из probe
    "attested": true,
    "acceptance": "accepted|rejected|needs_clarification|unknown",  // feedback outcome
    "outcome_refs": ["REC-…"]              // внешний outcome из reality ledger
  },
  "role": {
    "actor_key_id": "…",                   // кто подписывал операции
    "principal_key_id": "…|null",          // владелец агента (OwnershipGraph)
    "attribution": "human|agent_piloted|agent_autonomous|mixed"
  },
  "trust_level": "claimed|attested|verified",
  "qualified": true,                       // прошёл гейт §5
  "disqualify_reasons": [],
  "started_at": 0.0, "ended_at": 0.0,
  "proof_ref": { "op_ids": ["…"], "head": "…" },
  "review_package": {
    "objective": "проверяемая цель из точного SPEC-требования",
    "delivery_summary": "явно указанное описание готового результата",
    "acceptance_criteria": ["название точного SPEC-требования"],
    "verification": { "source_signature": "verified", "gate": "falsified",
                      "proof_refs": ["op_…"] },
    "artifact_refs": ["spec:SPEC-X#R3"],
    "limitations": [],
    "status": "ready|incomplete"
  }
}
```

`review_package` — не архив и не пересказ промпта. Он формируется только из явного
описания готового результата, точных SPEC-якорей и подписанных указателей на
проверку. Приватный intent, промпты, команды, исходники и файлы не экспортируются.
Если данных недостаточно, пакет получает `status=incomplete` и не может быть принят
контрагентом, но сам эпизод остаётся видимым.

Инварианты: money-free (тот же барьер, что и `ContributionEvent`); content-safe
(пути/хэши/счётчики, без исходников); детерминизм (тот же леджер → те же эпизоды);
идемпотентность по `episode_id`; scope = `ledger` (кросс-проектно по `key_id`).

### 4.2 `Capability` v1 — выводимое утверждение о способности

```jsonc
{
  "schema_version": 1,
  "kind": "capability",
  "avatar_id": "<key_id>",
  "task_class": "…",
  "evidence": ["episode_id", "…"],          // ТОЛЬКО qualified-эпизоды; пустых способностей не бывает
  "signals": {
    "reliability":       { "value": 0.92, "n": 14 },   // доля green+accepted, взвешенная §4.4
    "recency":           { "last_evidence_at": 0.0, "state": "fresh|aging|stale" },
    "growth":            "improving|flat|declining",     // тренд по окнам времени
    "transferability":   { "projects": 3, "portable": true },  // ≥2 project_id + WorkAsset reuse
    "company_dependency":{ "value": 0.4 },               // доля эпизодов на company_data/внутр. профилях
    "ai_reliance":       { "human_lead": 0.6, "agent_lead": 0.3, "mixed": 0.1 }  // из role.attribution
  },
  "uncertainty": {
    "level": "low|medium|high",
    "reasons": ["n<5", "no_falsified_gates", "single_project", "stale_evidence"]
  },
  "trust_floor": "attested"                 // минимальный trust_level учтённых эпизодов
}
```

Способность — не самоотчёт и не LLM-оценка: это правило над эпизодами, воспроизводимое
из леджера (`--verify`-путь как у timesheet). Способность **угасает** без новых
свидетельств (`recency: stale`) — та же философия, что `attested → stale` у требований
и стоячий конформанс RFP-035, применённая к человеку.

### 4.3 Таксономия `task_class` — детерминированное ядро, LLM только как claim

MVP-классификация детерминирована: явный тег (`task-class:` в SPEC/intent) → иначе
композиция `spec_family × stack × verify-класс` (например,
`SPEC-INTERFERENCE × python × pytest` → `concurrency-analysis/python`). Этого достаточно,
чтобы двигатель поехал без «умного» компонента.

LLM-ассистированная классификация **разрешена только как предложение**: она оформляется
`ContributionEvent(kind: "claim", declares_for: <episode fact>)` и попадает в таксономию
после явного принятия владельцем — детерминированное ядро не читает LLM-выход напрямую
(граница A37-L; консистентно с запретом LLM-скоринга в RFP-025 AF-1).

### 4.4 Вес свидетельства — качество гейта, не объём

Каждый эпизод входит в `reliability` с весом:

| Фактор | Источник | Эффект |
|--------|----------|--------|
| `gate_quality: falsified` | `apatch probe falsify` прошёл для охраняющего verify | полный вес |
| `gate_quality: unprobed` | verify есть, falsify не запускался | пониженный вес |
| `gate_quality: false_gate` | falsify показал: гейт не умеет краснеть | **нулевой** вес + флаг |
| `weak_verify` / `generic_verify` (lint) | verify не специфичен требованию | пониженный вес |
| `trust_level: verified` | внешне заякорено / witness | повышенный вес |
| `acceptance: rejected` | результат отклонён | отрицательное свидетельство (тоже сигнал) |

`volume` (`ops`, `insertions`, токены, промпты, число файлов) в вес **не входит никогда**
— это тестируемый инвариант, симметричный экономическому барьеру (A37-G).

---

## 5. Квалификационный гейт эпизода

Чтобы эпизод влиял на аватар, одновременно выполняются минимальные условия:

| # | Условие | Проверка (детерминированная) |
|---|---------|------------------------------|
| Q1 | Известен человек | enrolled `key_id`; `legacy:` → не выше `claimed`, в способности не входит |
| Q2 | Определена задача | непустой intent **или** якорь `spec:ID#Rk` |
| Q3 | Виден результат | verify-прогон или внешний outcome (`reality_refs`) |
| Q4 | Понятна роль человека | `role.attribution` выводима (OwnershipGraph + claims); иначе `mixed/unknown` → дисквалификация из `human`-способностей |
| Q5 | Есть проверка или принятие | `verify: green` или `acceptance: accepted` |
| Q6 | Права и конфиденциальность | content-safe пройден; политики WorkAssets/portability соблюдены |
| Q7 | Исключено накручивание | дедуп по `episode_id`; anti-gaming §7 |

Не прошедший гейт эпизод сохраняется как `qualified: false` с причинами (для отладки
и для честности: «AI-сессия может не дать никакого экономически значимого сигнала» —
это нормальный, явный исход, а не потерянные данные).

---

## 6. Границы слоёв — prior и его индивидуальное обновление

Аватар **не является полной стоимостью человека**; он — индивидуальный источник
evidence для её уточнения:

```text
Рыночная база (prior)          O*NET, зарплаты, должности, сектор, CBM   → HC Capital (L3)
        ↓
Avatar evidence (update)       episodes + capabilities + динамика         → apatch (L2, этот RFP)
        ↓
Прогноз будущего вклада        модель поверх prior + evidence             → HC Capital (L3)
        ↓
Scarcity / portability / company dependency → оценка HC Capital           → HC Capital (L3)
```

apatch эмитит **`capability_evidence` bundle v2** (эпизоды + способности +
неопределённость, money-free, content-safe, подписан) — и останавливается. Прогноз,
скарсити и цена — Слой 3; в bundle нет ни PI, ни прогнозов, ни цен (барьер канона §7.3).

---

## 7. Анти-накрутка (adversarial-модель)

Как только evidence влияет на оценку, появляется давление на фарминг. Инварианты:

1. **Volume-blind.** Никакая метрика объёма не повышает ни вес эпизода, ни сигнал способности (тест, A37-G).
2. **Гейт, который не умеет краснеть, не считается.** `false_gate` (probe falsify) обнуляет вес; массовые `unprobed`-эпизоды дают `uncertainty: high` — фарминг тривиальных verify не конвертируется в надёжность.
3. **Агентский фарминг не переносится на человека.** Эпизоды с `attribution: agent_autonomous` формируют способности **агентского** аватара (ADR-002 — это его актив); в человеческие способности они входят только через `human_claims`/пилотирование. Плодить агентов = растить агентские аватары, не свой.
4. **Самоподтверждение ограничено лестницей доверия.** `claimed` не входит в способности; потолок чисто локального леджера — `attested`; `verified` требует внешнего якоря (transparency anchor / witness), который нельзя сфабриковать локально.
5. **Отрицательные свидетельства не удаляются.** `rejected`/`false_gate` — часть append-only истории (супersede, не delete), как в reality ledger.

---

## 8. Поверхности

| Поверхность | Что даёт | Где |
|-------------|----------|-----|
| `apatch avatar episodes [--json]` · MCP `apatch_avatar_episodes` | эпизоды identity (фильтры: spec, project, since) | apatch |
| `apatch avatar capabilities [--json]` · MCP `apatch_avatar_capabilities` | способности + evidence + uncertainty | apatch |
| `apatch avatar evidence-export` · MCP `apatch_avatar_evidence_export` | подписанный `capability_evidence` bundle v2 | apatch |
| `apatch avatar sync` · MCP `apatch_avatar_evidence_sync` | decision-first loop: pull owner `TaxonomyDecision`, then independent outcomes, then reconcile facts/full signed envelopes and recompile evidence; result separates classified, counterparty-accepted and capability-ready work and names the next action; internal mode uses a durable non-secret pointer to an attended TrustChain Secrets binding, owner mode uses revocable `tcav_` credential; public issuer keys are pinned separately in a strict local trust policy for taxonomy and outcomes; failed outbound deliveries remain durable and retry by content identity | apatch ↔ trust-chain.ai Avatar BFF ↔ HC Tracker |
| `--verify` на всех трёх | пере-деривация из сырых ops, non-zero при расхождении | apatch |
| Вкладка «Аватар» (`trust-chain.ai` canonical web / HC Tracker native-mobile) | один HC-owned read-model, контракт отображения §8.1 | HC (waiver) |

### 8.1 Контракт отображения вкладки «Аватар» (v2, заменяет «сколько собрано»)

Вкладка обязана отвечать, в порядке приоритета:

1. **Что уже можно оценить** — статус `estimated` либо честный `insufficient_evidence`;
2. **На основании чего** — observations, Wilson interval, recency и project count;
3. **Кто выполнил работу** — основной ключ или owned agent; subject identity не смешиваются;
4. **Какие методы уже можно повторно использовать** — WorkAssets и live reuse outcomes;
5. **Где модель не уверена и почему** — exclusion и uncertainty reasons видны рядом с выводом;
6. **Как evidence связано с рынком** — отдельный L3 shadow update с явным
   `pricing_effect_applied=false`, пока маппинг и модель не откалиброваны;
7. **Что произошло** — подтверждённая timeline содержит только события с
   проверенной Ed25519-подписью; наличие одного `proof_ref` недостаточно.
8. **Что именно это за работа** — ожидающие owner review O*NET proposals показаны
   отдельно от принятых occupation/task/skills; UI никогда не называет proposal
   способностью и не принимает его автоматически.

Счётчики объёма (`artifact_count` и т.п.) допустимы только как вторичная деталь эпизода,
никогда как заголовок аватара.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A37-A | `WorkEpisode` v1 — детерминированная, идемпотентная, money-free/content-safe проекция governed-единицы работы из уже подписанных примитивов (сессии, verify, attest, plan, claims); никакой новой инструментации; `--verify` пере-деривация из сырых ops | MUST |
| A37-B | Квалификационный гейт эпизода (Q1–Q7 §5): неквалифицированные эпизоды сохраняются с причинами и не участвуют в способностях | MUST |
| A37-C | `Capability` v1 — детерминированное правило над qualified-эпизодами: `reliability`, `recency` (с угасанием), `growth`, `transferability`, `company_dependency`, `ai_reliance`; способность без evidence невозможна | MUST |
| A37-D | Неопределённость — первый класс: каждый signal несёт `n`; capability несёт `uncertainty.level + reasons`; малое n / unprobed-гейты / один проект / протухшее evidence повышают неопределённость | MUST |
| A37-E | Вес свидетельства из качества гейта (§4.4): probe `falsified` — полный, `unprobed` — пониженный, `false_gate` — нулевой; `verified` > `attested`; `rejected` — отрицательное свидетельство | MUST |
| A37-F | Разделение ролей: `attribution` из OwnershipGraph + `cv_delta`-claims; `agent_autonomous`-эпизоды формируют агентский аватар и не входят в человеческие способности без human-claims | MUST |
| A37-G | Anti-gaming инварианты под тестом: volume-blind (ни одна метрика объёма не влияет на вес/сигнал), `claimed` вне способностей, отрицательные свидетельства неудаляемы | MUST |
| A37-H | `capability_evidence` bundle v2 — подписанный money-free/content-safe экспорт (эпизоды + способности + uncertainty) для HC Capital; в bundle нет прогнозов, цен, PI/GPI | MUST |
| A37-I | Вкладка «Аватар» реализует контракт отображения §8.1 (7 вопросов, счётчики не заголовок) | SHOULD |
| A37-J | Амендмент канона §6 (аватар = эпизоды + способности поверх свёртки) принят отдельным PR с bump версии канона | MUST |
| A37-K | Детерминированная таксономия `task_class` (тег → spec_family × stack × verify-класс); LLM-классификация только как `claim` с явным принятием, ядро LLM-выход не читает | MUST |
| A37-L | Executable specs attested с `## RFP traceability` к этой таблице | MUST |
| A37-M | `apatch_project_status` / report показывают capabilities-сводку (Aha-поверхность, симметрично RFP-025 AF-7) | SHOULD |

Canonical ids: this section.

---

## 9. Фазы и SPEC-проекция

| Фаза | Что | SPEC | Покрывает |
|------|-----|------|-----------|
| 1 | Episode builder + гейт + `--verify` | `SPEC-EPISODE-1` (apatch) | A37-A, A37-B |
| 2 | Capability compiler + веса + uncertainty + таксономия | `SPEC-CAPABILITY-1` (apatch) | A37-C, A37-D, A37-E, A37-K |
| 2a | Owner-governed O*NET bridge + signed reverse delivery | HC `SPEC-AVATAR-VIEW-1#R6` + `avatar-contract` | A37-I, A37-K |
| 3 | Роли и anti-gaming (OwnershipGraph, claims, инварианты) | `SPEC-CAPABILITY-1` R-подмножество | A37-F, A37-G |
| 4 | Evidence bundle + поверхности CLI/MCP/status | `SPEC-AVATAR-EVIDENCE-1` (apatch) | A37-H, A37-M |
| 5 | Вкладка «Аватар» v2 | HC `SPEC-AVATAR-VIEW-2` — `waiver: implemented in HC` | A37-I |
| 6 | Амендмент канона | Avatar Architecture Canon v1.3 | A37-J |

Порядок принципиален: эпизоды раньше способностей, способности раньше экспорта,
экспорт раньше вкладки. Фаза 4 HC-экономики (prior+update, прогноз) — вне scope,
потребляет bundle.

---

## 10. Non-goals

- **Не** скоринг людей LLM'ом: ядро детерминировано; LLM — только предложения-claims (A37-K).
- **Не** экономика: ни PI/GPI, ни прогнозы, ни scarcity, ни цены — L3 (HC Capital).
- **Не** слежка: только governed-работа; никаких кейлоггеров, промптов, экранного времени; количество промптов/токенов не собирается как сигнал.
- **Не** замена AUC-1: полезность (recall) и измерение (capability) — две функции над одним субстратом эпизодов; этот RFP строит измерение и субстрат.
- **Не** полный IRS / person↔много-ключей (тех-долг канона §10 #1).
- **Не** пересмотр `asset_summary`: остаётся как proof-проекция; capabilities — отдельная проекция рядом.

## 11. Риски

| Риск | Митигация |
|------|-----------|
| Таксономия задач слишком груба → бессмысленные классы | явный тег главнее эвристики; классы пересматриваемы без потери эпизодов (эпизод хранит сырьё, класс — деривация) |
| Мало falsified-гейтов → всё в `unprobed`-неопределённости | честно показывать (это правда данных); `probe falsify` в onboarding-путь спек; A37-E мотивирует пробовать гейты |
| Способности прочтут как «оценку человека» и начнут бояться | uncertainty первым классом (§8.1 п.7); AUC-1 — честный обмен: без ежедневной пользы измерение = наблюдение, пользователь исказит данные |
| Двойной учёт с timesheet/asset_summary | все три — проекции одного леджера; никакого второго источника истины не появляется |
| HC не успевает с вкладкой | apatch-поверхности (CLI/MCP/status) самодостаточны для dogfood и пилота; вкладка — SHOULD |

## 12. Связь с документами

| Документ | Роль |
|----------|------|
| Канон | онтология; §6 расширяется амендментом (A37-J), барьеры наследуются дословно |
| AUC-1 | продуктивная половина полезности; эпизоды — общий субстрат обеих половин |
| RFP-026/028 | событийный субстрат и труба доставки; bundle едет тем же durable-путём |
| RFP-005 (probe/reality) | качество гейта → вес свидетельства; reality → внешний outcome эпизода |
| ADR-002 | агентские аватары и OwnershipGraph → разделение ролей и анти-фарминг |
| RFP-031 / WorkAssets | reuse-события → transferability; capabilities → ранжирование recall |
