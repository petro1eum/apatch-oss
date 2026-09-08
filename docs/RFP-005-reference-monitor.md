# RFP-005: apatch как контрольный монитор изменений кода

* **Статус**: **Активный** — фундаментальная архитектура; задаёт критерии 0.2 / 0.3
* **Автор**: Ed Cherednik
* **Дата**: 2026-06-07
* **Зависимости**: RFP-004 (Mutation Runtime — реализован), sandbox/enforcement/TrustChain (частично)

> Документ назван по предметной области, а не по реализации. Тезис «контрольный монитор» переживёт любые переписывания конкретных хуков, CLI и MCP.

---

## 1. Тезис

apatch — не diff-tool, не refactoring-tool и не MCP-сервер. Это **транзакционный слой для изменений кода, выполняемых людьми и AI-агентами**: единый примитив «управляемое изменение», которое по своей природе:

* воспроизводимо,
* проверяемо,
* откатываемо,
* доказуемо (provenance).

Аналогия по уровню: базы данных дали миру **транзакцию** для данных, Git дал **коммит** для истории файлов. apatch претендует дать **транзакцию для изменений кода** — единицу, которая проходит формальный жизненный цикл и оставляет криптографический след.

```text
Intent → Plan → Apply → Verify → Attest → Replay/Rollback
```

**Ключевой архитектурный вывод:** ценность всех остальных слоёв (provenance, sandbox, orchestration, profiles) — *производная* от одного свойства: **изменение нельзя сделать в обход монитора.** Журнал, который можно обойти, — не журнал. Страховка с дырами — не страховка. Транзакция, которую можно пропустить, — не транзакция.

Поэтому несущая конструкция платформы — **контрольный монитор** (reference monitor): единственный санкционированный канал изменений с тремя классическими свойствами:

1. **Нельзя обойти** (complete mediation) — другого пути изменить «истину» нет.
2. **Неподделываемость** (tamper-proof) — сам монитор и его журнал нельзя подменить.
3. **Проверяемость** (verifiable) — ядро доверия мало и аудируемо.

---

## 2. Архитектурная иерархия «5 больших идей»

| Идея | Архитектурная роль | Как фундамент |
|------|--------------------|---------------|
| Governed Mutations | сам примитив (ядро) | **несущая** |
| TrustChain / Provenance | долговечный неподделываемый журнал | **несущая** (память/идентичность) |
| Sandbox / Enforcement | гарантия неприкосновенности примитива | **несущая** (целостность) |
| AI-Safe Runtime | режим применения примитива | надстройка |
| Autonomous Workflows | прикладной слой поверх | надстройка / витрина |

Несущая тройка — **примитив изменения + журнал-первоисточник + гарантия неприкосновенности.** Остальное — клиенты этого монитора.

---

## 3. Два контура двери

«Нельзя обойти» для файлов на ноутбуке физически недостижимо — нельзя запретить человеку печатать в редакторе. Поэтому граница проводится двумя слоями:

```text
        AI-агент / человек
              │
   ┌──────────▼───────────┐
   │  КОНТУР 1: канал ИИ   │  ← реальное время; медиация агента; быстрая обратная связь
   │  (агент меняет код    │     НЕ авторитетная граница (неполнота допустима)
   │   только через apatch)│
   └──────────┬───────────┘
              │  локальные правки человека проходят…
   ┌──────────▼───────────┐
   │  КОНТУР 2: вход в     │  ← АВТОРИТЕТНАЯ граница (серверная)
   │  общую историю        │     в main/CI входит только подписанное изменение,
   │  (только с подписью)  │     каким бы инструментом оно ни было сделано
   └──────────┬───────────┘
              ▼
        Общая правда (то, что задеплоится)
```

**Контур 1 (канал ИИ):** Cursor-хуки (`preToolUse`, `beforeShellExecution`, `beforeMCPExecution`, все `failClosed`) перекрывают прямую запись, шелл-мутации и чужие MCP. Даёт сегодняшнюю ценность — полную медиацию ИИ в реальном времени.

**Контур 2 (вход в историю):** единственное место, где неприкосновенность *настоящая*. Модель Git, доведённая до конца: файлы правь как хочешь, но «общая правда» недостижима в обход подписанных ворот.

**Принципиальное упрощение:** не нужно перечислять все способы записать файл (это бесконечная гонка). Контур 2 пропускает только **подписанные** изменения — поэтому неполнота Контура 1 не критична. Переход от «блокируем плохие инструменты» к «реальны только подписанные изменения».

---

## 4. Модель угроз

Уровни — это лестница; рунги нельзя перепрыгивать.

| | От кого защищаемся | Мотив | Архитектурное требование |
|---|--------------------|-------|--------------------------|
| **У1** | честная ошибка ИИ/человека | невнимательность | ограждения + хорошие дефолты |
| **У2** | срезание углов под дедлайном (`--no-verify`, `APATCH_SANDBOX=0`, `mode:off`) | стимулы | нельзя в одиночку отключить монитор; контроль на сервере |
| **У3** | злоумышленник / взломанный агент | атака | якорь доверия вне репо; журнал append-only с серверной проверкой; монитор, который актор не перенастроит |

**Поворот для ИИ:** промпт-инъекция или сбитый с толку агент — это фактически У3, даже при честном человеке. Для ИИ «ошибка» и «атака» неразличимы, потому что ИИ — сам канал атаки. Значит, тянуть к скелету У3 придётся в любом случае.

**Целевой уровень платформы: пол — У2, скелет архитектуры — готовый к У3.**

* Только У1 убивает тезис: совещательный охранник = линтер с приятным UX, отличие испаряется.
* Полный У3 сразу = смерть от объёма (формальная верификация, HSM).
* У2 — золотая середина: те же шаги, что закрывают У2 (якорь вне репо, серверный гейт, запрет одностороннего отключения), являются фундаментом У3.

---

## 5. Аудит лазеек (2026-06-07)

> Снимок на момент RFP. Источник: ревизия `enforcement.py`, `sandbox.py`, `sandbox_watch.py`, Cursor-хуков, git-хука, CI-гейта.

| # | Лазейка | Контур | Серьёзность | Статус |
|---|---------|--------|-------------|--------|
| 1 | Конфиг и журнал монитора (`.apatch/sandbox.json`, `.apatch/enforcement.json`, `.trustchain/**`, `.cursor/hooks*`) лежат в записываемой зоне — агент мог отключить монитор, переписав его конфиг | оба | Критическая | **Закрыто (write-канал): control-plane lock** |
| 2 | `APATCH_SANDBOX=0` / `APATCH_GOVERNED_MODE=off` выключают охрану через env | 1 | Критическая | **Закрыто (ratchet-only env):** env может только ужесточать, не ослаблять закоммиченную границу (§5.5) |
| 3 | Якорь доверия неясен: если приватный ключ/проверка внутри репо — подпись «сам себе», подделываема. Корень должен быть снаружи (секрет CI/KMS) | 2 | Критическая | **Закрыто:** enrolled-подпись → leaf → root (§5.3); enroll + CI-verify + op_id inclusion-proof (§5.3, §5.4) |
| 4 | Хуки существуют только внутри Cursor; другой редактор/терминал/агент не медиируются | 1 | Высокая | Снято by design (Контур 2 авторитетен) |
| 5 | Список шелл-мутаций неполон (`python -c`, `cp`, `mv`, `dd`, `patch`, `git apply`, `git checkout --`) | 1 | Высокая | Снято by design |
| 6 | `git commit --no-verify` обходит локальный pre-commit одним флагом | 2 | Высокая | **Закрыто (capability):** diff-aware `ci-gate --base` ловит закоммиченное в обход Контура 1 (§5.6); требует включения required-check |
| 7 | `ci-gate` — шаблон, не гарантия; без required-check в защите веток PR вливается мимо | 2 | Высокая | **Частично:** гейт авторитетен над diff PR (§5.6); required-check в branch protection — настройка репо-админа (не код) |
| 8 | Дефолт мягкий: `governed_mode=off`, sandbox `mode=off` пропускает всё | оба | Средняя | **Закрыто:** `init-consumer --with-*` → `strict`/`auto_session`/`enforce`; `off`≠незащищён (state-machine гейтит по enforcement); зафиксировано тестами (§5.7) |
| 9 | Сам репозиторий apatch не прогоняет `ci-gate` в своём CI | 2 | Средняя | **Закрыто:** job `apatch-gate` в `.github/workflows/ci.yml` (§5.6) |

**Вердикт по трём свойствам на момент аудита:** «нельзя обойти» ❌, «неподделываемость» ❌, «проверяемость» ⚠️. Лазейки №1–3 — это не «недокрытие», а отсутствие якоря: контур доверия замкнут сам на себя («замок с ключом, приклеенным к двери»).

### 5.1. Первая правка (закрыто 2026-06-07)

`apatch/sandbox.py`: введён **control-plane** — `CONTROL_GLOBS` + `is_control_path()`. Файлы монитора (его политика, хуки, журнал) жёстко запрещены к записи через канал агента (`evaluate_write_policy` → `control_plane_locked`), **даже при наличии lease**. Доверенный рантайм apatch пишет их напрямую (`write_sandbox_config`/`write_enforcement_config`/TrustChain), не через медиированный канал, поэтому не блокируется. Аудит/watcher (Контур 2) не затронут — правка конфига оператором не считается нарушением источника. Тесты: `tests/test_sandbox.py::test_control_plane_*`.

---

## 5.2. Якорь доверия = TrustChain PKI (лазейка №3)

**Ключевой вывод ревизии TrustChain (core / Pro / Platform):** якорь доверия **изобретать не нужно — он уже есть**. apatch его просто не использует.

PKI-иерархия уже реализована и работает:

```text
Root CA (offline / HSM в проде, TC_ROOT_CA_EXTERNAL=1)
  └── Platform Intermediate CA
        └── [Org CA на тенант, опц.]
              └── Agent leaf cert  ← приватный ключ держит клиент (никогда в репо)
```

* **Где живёт корень:** `TrustChain_Platform/CAService`; публичные PEM раздаются на `GET /api/pub/root-ca`, `/api/pub/ca`, `/api/pub/agents/{id}/cert`, `/api/pub/crl`. Приватный root-ключ — вне репо (offline/HSM).
* **Идентичность (модель B):** enrollment через `tc cert request --auto` (или `POST /api/enroll/csr`) → `agent.key` (приватный, у клиента), `agent.crt`, `ca.pem`, `root-ca.pem`. Реализация: `trustchain.v2.x509_pki` (`TrustChainCA`, `AgentCertificate.verify_chain`).
* **Авторитетная проверка (модель A, серверная):** Platform `POST /api/log/append` уже проверяет цепочку leaf → intermediate → root перед приёмом. CI верифицирует офлайн: `tc-verify --full-chain` против запиненного `root-ca.pem` + Merkle-proof инклюзии. Есть интеграционный тест `TrustChain_Platform/backend/tests/test_apatch_bridge.py`.

Это **ровно** рекомендованная связка B+A — и она уже существует в продукте.

### Почему «не работает через корни» сейчас — и что чинить

| Где | Проблема | Правка |
|-----|----------|--------|
| **apatch** | `trustchain_helper` подписывает `.trustchain/` **эфемерным** ключом, а не enrolled-идентичностью → локальный журнал самоподписан без якоря | Грузить `APATCH_AGENT_KEY` (`key_file` / `LocalFileKeyProvider`) в `TrustChain.sign()`, чтобы подпись соответствовала leaf-серту, цепляющемуся к корню |
| **apatch** | Нет enrollment; путь Platform best-effort, глотает ошибки, `push_step` не возвращает `op_id` | Команда enrollment (или мост к `tc cert request`); `push_step` возвращает `op_id`; команда `apatch verify chain` (обёртка `tc-verify --full-chain`) |
| **apatch** | Рассинхрон: docs дают неверные env (`APATCH_PLATFORM_*`); подпись hex vs base64 в `/api/pub/verify` | Выправить env-имена и кодировку |
| **apatch** | `doctor` не показывает уровень якоря | `doctor`: `ephemeral (insecure dev)` vs `ca-issued identity → root` |
| **TrustChain (core)** | **Подтверждено e2e (см. ниже):** при `enable_pki=True` `_bootstrap_pki` выдаёт leaf-серт **без** ключа подписанта → leaf сертифицирует чужой ключ, подпись журнала не верифицируется против серта | Чинить в `trust_chain/__init__.py::_bootstrap_pki`: `issue_agent_cert(..., public_key_b64=self._signer.get_public_key())` — leaf сертифицирует ключ `Signer` |
| **TrustChain (Platform)** | `log.py` на append проверяет только `[platform_intermediate, root]` → org-CA leaf может не пройти | Принять цепочку `[org_ca, platform_intermediate, root]` |

> Директива: всё идёт через корневые сертификаты. Где OSS/Platform не поддерживает — правим в самом TrustChain, а не обходим в apatch.

### Доказательство (e2e-проба, 2026-06-07)

API уже есть: `TrustChainConfig(key_file=, key_env_var=, key_provider=, certificate=, enable_pki=, pki_agent_id=)` — apatch их не передаёт, отсюда эфемерная самоподпись (`trustchain_helper.commit_action` → `TrustChain(cfg).sign()` с пустым `cfg`).

Прогон `enable_pki=True` показал:

```text
cert.verify_chain([intermediate, root]) = True     # leaf корректно цепляется к корню
signer pub == cert pub ?                  = False   # но подпись журнала — ДРУГИМ ключом
```

Корневой серт есть, цепочка валидна — но журнал подписан ключом, который этот серт **не** сертифицирует. Якорь декоративен, пока не закрыты обе правки: (а) apatch грузит enrolled-ключ в `Signer`; (б) `trust_chain._bootstrap_pki` привязывает leaf к ключу `Signer`.

### 5.3. Привязка подписи к корню (закрыто 2026-06-07)

Обе правки реализованы:

* **TrustChain core** (`trust_chain/trustchain/v2/core.py::_bootstrap_pki`): шаг 3 теперь передаёт `public_key_b64=self._signer.get_public_key()` → leaf-серт сертифицирует ключ подписанта. Регресс-тест `tests/test_x509_pki.py::TestSubAgentDelegation::test_agent_cert_certifies_signer_key` (`signer pub == cert pub`). Проба после правки:

```text
signer pub == cert pub ? = True
cert.verify_chain([intermediate, root]) = True
```

* **apatch** (`apatch/trust_identity.py` + `trustchain_helper._tc_config`): при `APATCH_AGENT_ID` + `APATCH_AGENT_KEY` (enrolled PEM) журнал `.trustchain/` подписывается enrolled-ключом через soft-KMS `key_provider`; без них — эфемерный dev-ключ (как раньше). `doctor` отдаёт блок `trust_anchor` (`level: ca-issued | ephemeral`) и предупреждает при `enforcement + ephemeral`. Тесты: `tests/test_trust_anchor.py`.

* **Enrollment + CI-verify (закрыто):** `apatch trustchain enroll` (MCP `apatch_trust_enroll`) — мост к `tc cert request`: генерирует ключ, шлёт CSR с invitation, сохраняет `agent.key`/`agent.crt`/`root-ca.pem`. `apatch trustchain verify-anchor` (MCP `apatch_verify_anchor`) — CI-гейт: проверяет PKIX-цепочку leaf→intermediate→root (PEM или live-registry `/api/pub/*`), CRL, и **привязку ключа** (ключ подписи apatch == ключ leaf-серта). Чистый `cryptography`, без сетевой зависимости в PEM-режиме. Тесты: `tests/test_trust_anchor.py::test_verify_anchor_*`.

### 5.4. Platform-правки (закрыто 2026-06-07)

Текущая работа потребовала и улучшила сам TrustChain:

* **op_id воспроизводим из подписи** (`trust_chain/trustchain/v2/verifiable_log.py::content_op_id`, делегирует и in-memory, и PG-store): id больше не зависит от серверного wall-clock — `sha256(tool|data|signature)` при наличии подписи (иначе fallback на timestamp). `POST /api/log/append` теперь возвращает `op_id` в ответе; клиент/CI берёт его для `GET /api/pub/log/proof/{op_id}` после async-flush. `apatch.platform_client.push_step(return_op_id=True)` пробрасывает его наружу. Тесты: `trust_chain/tests/test_verifiable_log.py::TestContentOpId`, `TrustChain_Platform/backend/tests/test_apatch_bridge.py` (round-trip `op_id` == persisted id).

* **org-CA leaf принимается на append** (`ca_service.verify_agent_chain`): `POST /api/log/append` принимает и платформенный leaf (`leaf→intermediate→root`), и org-CA leaf (`leaf→org_ca→intermediate→root`) — единый якорь на платформенном корне. `org_id` (tenant) сужает поиск org-CA. Тесты: `TrustChain_Platform/backend/tests/test_verify_agent_chain.py`.

**Итог по лазейке №3:** якорь доверия реализован сквозно — apatch подписывает enrolled-идентичностью (§5.3), цепочка проверяется до корня (CI + серверный append), op_id даёт inclusion-proof. Остаётся только UX-обвязка (enrollment-доки, required-check в CI самого apatch — лазейки №7, №9).

### 5.5. Env-переключатели только ужесточают (закрыто 2026-06-07)

Монитор не должен отключаться той стороной, которую он контролирует. Раньше env мог **ослабить** границу:

* `APATCH_SANDBOX=0` отключал sandbox даже при закоммиченном `mode:enforce`;
* `APATCH_GOVERNED_MODE` перебивал `enforcement.json` в любую сторону, включая `=off`.

Теперь действует **ratchet-only**:

* `apatch/sandbox.py::is_sandbox_enabled` — kill-switch `APATCH_SANDBOX=0` учитывается только для неэнфорсящих dev-режимов (`warn`/`audit`); закоммиченный `enforce` через env не выключить.
* `apatch/enforcement.py::resolve_governed_mode` — `enforcement.json` это пол; `APATCH_GOVERNED_MODE` может только поднять строгость (`off→strict`), попытка понизить игнорируется. Ранжирование `_GOVERNED_MODE_RANK`.

Env `APATCH_ENFORCE` уже был ratchet-only (только включает). Тесты: `tests/test_sandbox.py::test_env_killswitch_*`, `tests/test_governed_mode.py::test_resolve_governed_mode_env_cannot_weaken_committed_floor`.

### 5.6. Серверная граница: diff-aware ci-gate + dogfood CI (2026-06-07)

Контур 1 (Cursor-хуки) — удобство, не граница: его обходит другой редактор, терминал или `git commit --no-verify`. Авторитет должен быть в точке входа в **общую историю** — CI (Контур 2).

* **`ci-gate --base <ref>`** (`apatch/sandbox_watch.py`): гейт проверяет точный набор `ref..HEAD` плюс staged index. Закоммиченное в обход локального хука изменение защищённого пути всё равно всплывает, а unrelated unstaged/untracked owner work не попадает в PR-гейт. Без `--base` локальный аудит по-прежнему проверяет всё рабочее дерево. Сквозь стек: `run_sandbox_ci_gate → run_sandbox_watch_once → scan_unleased_violations → _collect_candidate_paths(base=...)`. CLI `apatch sandbox ci-gate --base`, MCP `apatch_sandbox_ci_gate(base=...)`. Тесты: `tests/test_sandbox_ci_gate.py::test_ci_gate_base_*`.

* **Транзакционная модель записи:** незавершённая мутация допускается только по живому exact path lease; после `session_end` те же байты допускаются по committed proof — exact SHA-256 подписанной мутации и более поздней signed session attestation. Индекс v3 хранит latest mutation отдельно от latest attested state: неподтверждённая следующая попытка или rollback не могут стереть предыдущее валидное доказательство. Signed mutation без attestation остаётся заблокированной.

* **Dogfood-CI** (`.github/workflows/ci.yml` job `apatch-gate`): apatch прогоняет собственный гейт над diff PR (`--base origin/$base_ref`). Сейчас gracefully skip без `.apatch/{sandbox,enforcement}.json`; готов стать блокирующим.

**Остаётся (репо-админ / org-решение, не код):**

1. [x] Закоммитить `.apatch/sandbox.json` + `enforcement.json` для apatch-репо (`protected_globs: ["apatch/**"]`, `mode=enforce`, `governed_mode=auto_session`); `.gitignore` с `!.apatch/{sandbox,enforcement}.json`. Ring-2 ci-gate не флагает control-plane JSON (operator commit; tamper-evident через `policy.lock`).
2. [~] Сделать job `apatch-gate` (**`apatch sandbox ci-gate (self)`**) **required status check** в branch protection GitHub — org-админ.

### 5.7. Безопасные дефолты (2026-06-07)

Монитор opt-in: без `.apatch/{sandbox,enforcement}.json` его нет вовсе (by design). Но как только включён — поза защитная по умолчанию, без «мягких» дыр:

* **`init-consumer --with-enforcement`** → `mode=strict` + `governed_mode=auto_session` (реально работающий session-gate, не молчаливый `off`). **`--with-sandbox`** → `mode=enforce`. Дефолты заданы и в CLI (`--governed-mode auto_session`), и в `init_consumer` (сигнатура), и в `write_sandbox_config` (`enforce`).
* **`governed_mode=off` ≠ «пропускает всё»:** требование governed-сессии под enforcement держит сам state-machine (`_session_block_message` завязан на `is_enforcement_enabled`, не на governed_mode). Бара `{"mode":"strict"}` всё равно блокирует мутации без сессии. `governed_mode` лишь выбирает *как* получить сессию: `auto_session` (авто-создание, zero-friction) vs `strict` (явная) vs `off` (без авто-создания — но enforcement-гейт всё равно требует сессию).

Зафиксировано тестами: `test_init_consumer_enforcement_default_is_safe`, `test_init_consumer_sandbox_default_enforces`, `test_runtime_plan_blocked_without_session` (off+enforce → блок без сессии).

### 5.8. Подписанная политика — tamper-evident конфиг монитора (2026-06-07)

Control-plane lock (§audit №1) закрывает *канал агента*: конфиг монитора нельзя переписать через Cursor Write / MCP. Но правка **вне канала** (терминал, другой редактор, скомпрометированный процесс) не блокируется — и, что хуже, **не детектируется**. Монитор, чью политику можно тихо подменить, не является эталонным монитором (свойство tamper-evidence).

Решение — **подписанная политика** (`apatch/policy_lock.py`):

* `apatch policy sign` считает канонический манифест `{path: sha256}` конфиг-файлов монитора (`.apatch/sandbox.json`, `.apatch/enforcement.json`, `.cursor/hooks.json`) и подписывает его enrolled-идентичностью (Ed25519, тот же ключ, что якорит `.trustchain/` к root — §5.2). Результат: `.apatch/policy.lock.json` (manifest + signature + signer pub + key_id). Без enrolled-ключа подпись отклоняется (эфемерная подделывается любым с доступом к репо).
* `apatch policy verify` пересчитывает хеши, проверяет подпись и сравнивает с подписанным манифестом → `drift` (`added`/`removed`/`changed`), `signature_ok`, `secure` (ключ = root-anchored). Любая правка конфига через *любой* канал теперь детектируема.
* Интеграция: `doctor` репортит `policy` блок и предупреждает при дрейфе/битой подписи; `ci-gate` под enforcement **фейлит** при дрейфе подписанной политики (`reason="policy drift"`). `.apatch/policy.lock.json` добавлен в `CONTROL_GLOBS` (агент не может переписать сам lock).

Якорность: подделать lock требует root-anchored приватный ключ, а не просто доступ на запись. CLI `apatch policy sign|verify`, MCP `apatch_policy_sign` / `apatch_policy_verify`. Тесты: `tests/test_policy_lock.py`.

### 5.9. Подключаемый KMS/HSM — приватный ключ вне процесса (2026-06-07)

До этого подписи apatch (журнал `.trustchain/`, `policy.lock`) опирались на enrolled-ключ в виде **PEM на диске**. Атакующий с доступом на чтение крадёт ключ и подделывает всё — фундамент якоря (§5.2) и подписанной политики (§5.8) хрупок. Цель У3: приватный ключ **никогда не входит в процесс**, подпись делегируется HSM / облачному KMS.

`trustchain.kms` уже описывал hard-KMS контракт (`get_seed()` бросает `KeyProviderError`; signer обязан звать `sign()`/`verify()`), но ядро `TrustChain._load_or_create_signer` всегда извлекало seed — hard-KMS провайдеры (Vault Transit, PKCS#11) физически не могли подписывать журнал. Закрыто на двух уровнях:

* **trust_chain (ядро):** `Signer.from_provider()` — делегирующий signer: держит только публичный ключ (для `verify`/`get_public_key`), каждую подпись гонит через `provider.sign()`; `_private_key=None`, `export_keys()` запрещён. `_load_or_create_signer` ловит `KeyProviderError` на `get_seed()` и строит делегирующий signer (soft-KMS остаётся seed-путём). Тест: `tests/test_hard_kms_signer.py`.
* **apatch:** `CommandKeyProvider` (hard-KMS бридж к любому внешнему signer-CLI: PKCS#11, KMS-CLI, агент HSM). Контракт команды: stdin = байты, stdout = base64(signature); публичный ключ — `APATCH_AGENT_PUBKEY` (out-of-band). `resolve_key_provider()` выбирает backend по `APATCH_KEY_BACKEND` (`command` | `pem`). `load_local_identity` / `_tc_config` подхватывают провайдер автоматически → и журнал, и `policy.lock` подписываются через устройство. `doctor` репортит `trust_anchor.backend`; `verify-anchor` умеет key-binding по `APATCH_AGENT_PUBKEY` (без PEM). Тесты: `tests/test_kms_backend.py`.

Env-контракт (`command` backend): `APATCH_KEY_BACKEND=command`, `APATCH_AGENT_SIGN_CMD`, `APATCH_AGENT_PUBKEY`, `APATCH_AGENT_ID`. Готовые soft/hard провайдеры (`AwsSecretsManagerKeyProvider`, `VaultTransitKeyProvider`) теперь тоже работают с журналом напрямую через `TrustChainConfig.key_provider`.

### 5.10. Внешний якорь журнала — tamper-proof off-machine (2026-06-07)

Локальный журнал `.trustchain/` лежит на том же диске, что и код: кто переписал код, переписал и журнал. Tamper-*proofness* требует **внешнего append-only якоря** — verifiable log Platform (Merkle-дерево). Control-plane lock (§5.8) и подпись (§5.2) делают локальную подмену *детектируемой*, но не защищают от полного стирания истории на машине. Внешний лог — защищает: что туда зафиксировано, локальным wipe не убрать.

* **Запись якоря:** при успешном push в Platform apatch фиксирует возвращённый `op_id` (content-addressable, из подписанного конверта — §verifiable_log) в `.apatch/inclusion.jsonl` (`_maybe_push_to_platform` → `push_step(return_op_id=True)` → `record_inclusion`). Файл в `CONTROL_GLOBS` — канал агента не перепишет.
* **Проверка:** `verify_inclusion` для каждого записанного `op_id` тянет публичный proof `GET /api/pub/log/proof/{op_id}` (эндпоинт уже есть в Platform) и проверяет Merkle audit-path от листа до корня (`verify_audit_path` — только хеши и siblings, серверную сериализацию листа воспроизводить не нужно). `missing` / `inconsistent` / `errors` → fail.
* **Consistency (fork detection):** после inclusion-проверок apatch тянет `GET /api/pub/log/merkle-root` и сверяет снимки `(chain_length, root_at_proof_time)` из proofs с текущей головой: конфликтующие корни на одной длине или proof-root ≠ current-root на head → `consistency_ok=false` (детект переписанного лога).
* **Coverage:** если якорение начато (`inclusion.jsonl` непуст + `APATCH_PLATFORM_URL`), каждая локально подписанная запись `.trustchain/` должна иметь запись в `inclusion.jsonl`; `uncovered = local_signed − anchored` → fail. `doctor` репортит `inclusion` блок и предупреждает при gap.
* **Интеграция:** CLI `apatch trustchain verify-inclusion`, MCP `apatch_verify_inclusion`; `ci-gate` под enforcement фейлит при `external inclusion failed`. Opt-in: без записей или без Platform гейт зелёный. Тесты: `tests/test_inclusion.py` (inclusion, fork, coverage, audit-path, ci-gate).

---

## 6. Критерии релизов

### 0.2 — «Контур 2 становится настоящим» (пол У2)

* [x] Control-plane lock: конфиг/журнал монитора нельзя писать через канал агента (лазейка №1).
* [x] Якорь доверия через TrustChain PKI (см. §5.2–5.4): apatch подписывает `.trustchain/` enrolled-идентичностью (leaf-серт → root); `apatch trustchain enroll` + `verify-anchor`; op_id для inclusion-proof; org-CA leaf принимается на append; самоподпись-в-репо — явный dev-режим в `doctor` (лазейка №3).
* [x] env-выключатели (`APATCH_SANDBOX`, `APATCH_GOVERNED_MODE`) ratchet-only — могут только ужесточать, не ослаблять закоммиченную границу (§5.5, лазейка №2).
* [~] Серверная граница (§5.6): `ci-gate --base` + dogfood-config закоммичен (`apatch/**` protected); job `apatch-gate` в CI. Остаётся: required-check в branch protection (репо-админ).
* [x] Безопасные дефолты (§5.7): `--with-enforcement` → `strict`/`auto_session`, `--with-sandbox` → `enforce`; `governed_mode=off` не означает «незащищён» (state-machine гейтит по enforcement); зафиксировано тестами (лазейка №8).
* [~] apatch ест свою еду: job `apatch-gate` в собственном CI (§5.6, лазейка №9); остаётся сделать его required-check в branch protection (репо-админ).

**Определение готовности 0.2:** монитор нельзя отключить в одиночку; в `main` apatch и эталонного consumer входят только подписанные изменения; `doctor` честно показывает уровень гарантий.

### 0.3+ — «Скелет У3 → реализация»

* [x] Подключаемый бэкенд ключей (KMS/HSM) (§5.9): hard-KMS делегирующий signer в ядре + apatch `CommandKeyProvider`; приватный seed не входит в процесс. `APATCH_KEY_BACKEND=command`. Готовые Vault/AWS провайдеры работают с журналом.
* [x] Подписанная политика (§5.8): конфиг монитора подписан enrolled-ключом (`policy.lock.json`), дрейф детектируется в `doctor` и фейлит `ci-gate` под enforcement. `apatch policy sign|verify`, MCP `apatch_policy_{sign,verify}`.
* [x] Журнал только-на-дозапись с внешним якорем и серверной верификацией (§5.10): `op_id` в `.apatch/inclusion.jsonl`, Merkle inclusion + consistency (fork) + coverage локального журнала; `ci-gate` / `doctor`.
* [—] Маленькое изолированное, аудируемое ядро доверия (TCB) — **вне скоупа 0.3**; отдельный RFP при необходимости (формализация границы TCB, не дискретная фича).

**Определение готовности 0.3 (скелет У3):** подписанная политика tamper-evident; ключ может жить вне процесса (HSM/KMS); внешний append-only якорь с inclusion/consistency/coverage; `doctor` честно показывает уровень гарантий по каждому слою.

### 0.2 / 0.3 — оставшиеся org-действия (не код)

1. [x] Dogfood-config закоммичен (`.apatch/{sandbox,enforcement}.json`, `apatch/**` protected).
2. [~] Job `apatch sandbox ci-gate (self)` → **required status check** в branch protection GitHub (Settings → Branches → `master` → Require status checks). Требует GitHub Pro или public repo на текущем плане.

---

## 7. Не в этом RFP (явные анти-паттерны)

* Гонка за полным чёрным списком способов записи файла (Контур 1 принципиально неполон — авторитетен Контур 2).
* Полный У3 «сразу» без прохождения У2.
* Контроль, который один разработчик может в одиночку отключить и при этом выглядеть «легитимным».
