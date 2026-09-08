# Command Grammar

> Domain verbs · porcelain aliases in parentheses

## Session

```bash
apatch session start --intent "<why>"
apatch session continue --logs <patches.jsonl>   # resume chunked apply
apatch session status [--json]
apatch session end [--json]
```

Porcelain: `apply --logs` (opens implicit session via checkpoints)

## Mutation

```bash
apatch mutation plan …    # alias → apatch plan
apatch mutation apply …   # alias → apatch apply
apatch mutation strip …   # alias → apatch strip (via MutationRuntime)
apatch mutation replay …  # alias → apatch replay (apply_session timeline)
```

## Verification

```bash
apatch verify status [--json]   # lifecycle + recommended verify + policy
apatch verify run [--verify CMD]   # default: doctor.recommended_verify_resolved (shell)
apatch verify run --semantic | --notarization | --pipeline manifest
apatch verify semantic …
apatch verify notarization …
```

## Attestation

```bash
apatch attestation show [--json]
apatch attestation commit [--message "…"]   # TrustChain attest (runtime)
apatch attestation export --out audit-bundle.json
apatch proof show         # alias
```

## Rollback & replay

```bash
apatch rollback [--to checkpoint]
apatch replay <transcript|log>
```

## Console

```bash
apatch                  # TTY → interactive Mutation Console
apatch console [--once]
```

**Что делать и зачем (пошагово):** [console-guide.md](./console-guide.md)

Клавиши: `i` цель → `l` лог ИИ → `p` посмотреть без записи → `a` применить → `v` проверить → `r` обновить → `q` выход

## JSON schema

All `--json` views include `schema_version`. Session: `apatch session status --json`. Attestation: `apatch attestation show --json`.
