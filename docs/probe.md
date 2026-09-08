# apatch probe — «возмути и смотри на дельту»

> Один примитив, под который сведены три проверки качества гейта: **falsify**, **regress**,
> **ratify**. Реализация — `apatch/probe.py` (`differential_probe`); CLI — `apatch probe *`; MCP — `apatch_probe(mode=…)`.
> Универсально (RFP-005): никакой завязки на конкретный домен — сигнал и возмущение задаются
> снаружи. Смежные документы: [conformance.md](./conformance.md) (держится ли весь контракт),
> [reality.md](./reality.md) (покрыта ли наблюдаемая реальность).

---

## Зачем

Зелёный verify сам по себе **ничего не доказывает**, пока ты не знаешь, что он *умеет* краснеть
и что он *всё ещё* краснеет на том, на чём должен. Три отдельных вопроса об одном гейте:

| Вопрос | Драйвер |
|--------|---------|
| Этот гейт вообще тестирует то, что охраняет? | `falsify` |
| Не появилось ли новых падений против записанного корпуса? | `regress` |
| Аттестованный гейт всё ещё зелёный сейчас? | `ratify` |

Оказалось, у всех трёх **одна форма**: применить *возмущение* → пере-измерить *сигнал* → судить
*дельту* против ожидаемой *полярности*.

```
              возмущение               сигнал            полярность        вердикт
falsify   портим охраняемые файлы   verify rc (red?)   must_diverge   real | false
regress   новый код vs корпус       набор падений      must_hold      stable | regressed
ratify    прошедшее время / мир     verify rc (green?) must_hold      ratified | stale
```

- **`must_diverge`** (чувствительность) — возмущение **обязано** ухудшить сигнал. Если гейт
  остался зелёным при порче того, что он охраняет, — это **ложный гейт**: он не тестирует то,
  за что отвечает.
- **`must_hold`** (стабильность) — сигнал **обязан** остаться здоровым. Новое падение против
  корпуса = регрессия; покрасневший аттестованный гейт = протухшая аттестация.

Возмущение бывает **эндогенным** (мы сами портим файлы — falsify) или **экзогенным** (мир уже
сдвинулся: новый код, прошедшее время — regress/ratify, возмущение = identity).

---

## Ядро

```python
from apatch.probe import differential_probe, POLARITY_DIVERGE, POLARITY_HOLD

res = differential_probe(
    measure=lambda: {"ok": bool, "failures": frozenset(), "rc": int},  # любой сигнал
    perturb=None | (lambda: restore_callable),                         # обратимое возмущение
    polarity=POLARITY_DIVERGE | POLARITY_HOLD,
    baseline=None | {"ok": ..., "failures": ...},                      # эталон (или измерить до)
)
# → {"verdict": "diverged"|"held"|"baseline_red", "passed": bool, "new_failures": [...], ...}
```

- `measure()` — любой `() -> health`. Здоровье — словарь с `ok: bool` и опц. `failures: set`.
- `perturb()` применяет обратимое возмущение и возвращает `restore()`; `restore()` **всегда**
  выполняется (`finally`) — дерево не остаётся грязным. Для экзогенного возмущения — `None`.
- `baseline` — эталонное здоровье; если `None`, измеряется до возмущения.
- Вердикт — **чистая функция** от `(baseline, observed, polarity)`. Сравнение `_degraded()`
  **baseline-aware по построению**: пред-существующие падения никогда не считаются, поэтому
  семантика регрессии выпадает из той же логики, что и falsify.

`falsify` / `regress` / `ratify` — тонкие драйверы поверх ядра (валидация входов + маппинг в
доменный вердикт). `conformance.falsify_requirement` остался как back-compat имя, делегирующее
драйверу.

---

## CLI

| Намерение | Команда | Зелёный / красный |
|-----------|---------|-------------------|
| Гейт реально тестирует охраняемое? | `apatch probe falsify --verify "<cmd>" --files a.py,b.py` | `real` / `false` |
| Нет новых падений против корпуса? | `apatch probe regress --verify "<cmd>" [--baseline-failures ...] [--allow ...]` | `stable` / `regressed` |
| Аттестованный гейт всё ещё зелёный? | `apatch probe ratify --verify "<cmd>"` | `ratified` / `stale` |

Все три принимают `--target-dir` и `--json` (машинный вывод + exit code 0/1 для CI).
`--verify` — **любая** shell-команда (pytest, скрипт, линтер) — отсюда доменная независимость.
`regress` без `--baseline-failures` читает `.apatch/verify_baseline.json` (см.
[conformance.md](./conformance.md) — baseline-aware verify).

```bash
# falsify: корраптим файл, что охраняет R5 → verify ОБЯЗАН покраснеть
apatch probe falsify --verify "pytest tests/test_auth.py" --files apatch/auth.py
#   ✓ real gate — corrupting ['apatch/auth.py'] made verify RED, restored → GREEN.
#   ✗ FALSE gate — verify остался зелёным: тест не проверяет то, что охраняет. Чини тест.

# ratify: аттестованный гейт обязан всё ещё проходить
apatch probe ratify --verify "pytest tests/test_billing.py"
#   ✓ ratified — gate всё ещё ЗЕЛЁНЫЙ   /   ✗ stale — гейт ПОКРАСНЕЛ, ре-верифай / ре-аттест

# regress: ни одного нового падения против записанного корпуса
apatch probe regress --verify "pytest -q" --allow "test_flaky_xyz"
#   ✓ stable   /   ✗ regressed — N НОВЫХ падений vs baseline: [...]
```

### Связь с Аватаром

В активной governed-сессии CLI/MCP-вызов `falsify` записывает результат как
подписанный `apatch_probe`-факт. Только `real` с успешным восстановлением даёт
`gate_quality=falsified`; `false` даёт `false_gate`. Сырой verify-командой Avatar
не обменивается: в леджер попадает только SHA-256 команды, список охраняемых файлов и
proof reference. Вне governed-сессии probe остаётся диагностикой и не повышает
способность.

Повторная проверка того же гейта замещает его текущее состояние в вычисляемом
ContributionEvent, но старый отрицательный факт остаётся в append-only леджере.
Непочиненный `false_gate` другого гейта блокирует доверие ко всей сессии.

---

## probe ratify vs conformance stale

Оба про «аттестовано, а сейчас?» — но с разных сторон:

- **`apatch conformance status --live`** — *оптовый* проход по **всему** контракту: классифицирует
  каждую спеку (`conformant` / `drifted` / `stale` / `unproven`) разом. Это стоячий гейт.
- **`apatch probe ratify`** — *точечный* вопрос про **один** гейт: «эта конкретная аттестация
  всё ещё заслужена?» Удобно перед тем, как опереться на старую подпись, или в узком CI-шаге.

`drifted` (conformance) и `stale` (probe ratify) — это **одно и то же явление** (был зелёным,
покраснел), увиденное на разном масштабе. `probe` даёт примитив; `conformance` — стоячую политику
поверх него.

---

## Чего probe НЕ делает

- Не решает, *что* охраняет требование — список файлов для `falsify` даёшь ты (или
  `--files` из спеки). Пустой список → вердикт `no_files`.
- Не чинит ложный гейт — он его **показывает**. Чинить тест — твоя работа.
- Не включает блокировку сам по себе — `--json` + exit code даёт тебе материал для CI;
  *политику* (блокировать ли) определяет проект, как и в [conformance.md](./conformance.md).
