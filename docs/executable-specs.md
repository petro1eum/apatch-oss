# Executable Specifications (RFP-007)

> **Канонический RFP:** [RFP-007-executable-specifications.md](./RFP-007-executable-specifications.md)  
> **Стандарт авторинга:** [spec-authoring.md](./spec-authoring.md)

> **Статус:** MVP. Слой `Specification → Requirement → Coverage` поверх RFP-004 (governed
> session) и RFP-006 (artifacts). Не отдельный продукт — естественное продолжение модели.

Спецификация перестаёт быть текстом-ссылкой и становится **управляемым объектом** с
выводимым состоянием. apatch не пишет ТЗ (это делают люди/агент) — он парсит его, привязывает
исполнение к требованиям и доказывает покрытие из подписанного ledger.

## Модель

```
SPEC.md  →  Requirements (spec:ID#Rk)  →  Intent  →  Session  →  Mutation  →  Verify  →  Attestation
 (текст)     (apatch/spec.py)            ─────────────  переиспользуется RFP-004/006  ─────────────
```

Каждое требование — артефакт `spec:<SPEC_ID>#<REQ_ID>@<content_hash>`.

## Формат SPEC.md

```markdown
# SPEC-42 Avatar Economics

## R1 Add Sandbox (verify: pytest tests/test_sandbox.py)
Тело требования.

## R2 Add Lease
Тело.
verify: pytest tests/test_lease.py

## R3 Add Doctor Status
Без verify — «готово» = аттестованная мутация.
```

- **Spec id:** первый токен `# H1` → иначе имя файла.
- **Требование:** заголовок `##`/`###`/`####`, первый токен которого оканчивается цифрой
  (`R1`, `FR-2`, `NFR3`, `REQ-10`).
- **Acceptance check (опц.):** `(verify: <cmd>)` в заголовке или строка `verify: <cmd>` в теле.
- **content_hash:** считается по блоку требования — правка одного требования делает `stale`
  только его.

## Состояния (выводятся из ledger)

| Состояние | Когда |
|-----------|-------|
| `pending` | нет intent/мутации, привязанных к `spec:ID#Rk` |
| `in_progress` | есть intent и/или мутация, но нет аттестации |
| `attested` | есть мутация **и** аттестация (в governed-цикле attest идёт после verify) |
| `stale` | было `attested`, но текущий `content_hash` ≠ записанному при аттестации |
| `blocked` | зарезервировано (явное аттестованное событие; не эмитится в MVP) |

**Важно:** статус — не поле, которое пишет агент. Он считается из подписанных мутаций
(`apatch_trustchain_coverage`), поэтому `complete` нельзя «поставить галочкой».

## Цикл исполнения

```text
apatch_spec_next(spec='SPEC-42')                  # что ещё не закрыто?
apatch_session_start(requirement='SPEC-42#R3')    # авто intent + spec:SPEC-42#R3@<hash>
apatch_generate_batch(needles=[…]) → apatch_apply_session  # реализация (create/replace/…)
apatch_verify_run                                 # прогон verify требования
apatch_attest                                     # подпись → требование → 'attested'
apatch_spec_status(spec='SPEC-42')                # повтор, пока done
```

Агент возобновляет «продолжай SPEC-42» через `apatch_spec_next` — не перечитывая документ.

## Surface

| MCP | CLI |
|-----|-----|
| `apatch_spec_lint(spec=, spec_path=)` | `apatch spec lint --spec SPEC-42` |
| `apatch_spec_status(spec=, spec_path=)` | `apatch spec status --spec SPEC-42` |
| `apatch_spec_next(spec=, spec_path=)` | `apatch spec next --spec SPEC-42` |
| `apatch_session_start(requirement='SPEC-42#R3')` | `apatch session start --requirement SPEC-42#R3` |

Стандарт авторинга и шаблон спеки: [`docs/spec-authoring.md`](spec-authoring.md).
Запускай `apatch spec lint` до начала трекинга — он ловит `id_mismatch`,
`missing_verify`, `generic_verify`, `weak_verify`.

Discovery: `spec_path` → иначе `docs/specs/<id>.md` → иначе запомненный указатель
`.apatch/specs/<id>.json` (только путь, не статус).

## Границы MVP

- Авторинг ТЗ не входит — пишут люди/агент.
- Гранулярность — требование (`Rk`), не под-пункт.
- Хранятся только ссылки `kind:id@hash`, не тело из Jira/wiki.
