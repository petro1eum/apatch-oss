# Profile: TypeScript / React (frontend)

See also [strip_guide.md](../strip_guide.md) (§ AST-first boundaries) and [cookbook.md](../cookbook.md) § Page → hook.

## Hook strip (рекомендуемый паттерн)

1. `apatch_suggest_until` на `.tsx` — grammar **`tsx`**, top candidate `root_return`, `source: ast`.
2. Манифест: `end_before: "  return ("`, `module_kind: "hook"`, `replace` без trailing `return (`.
3. `apatch_strip_dry_run` → `boundary_assessment.unstable === false`.
4. `apatch_strip` + `auto_wire` + `npm run build && npm run test`.
5. При TS6192 в parent — `apatch_generate` / `apatch_apply` на блок imports.

Референс consumer: `Sales_Pipeline/manifests/apatch_catalog_page_hook_strip.json`, `manifests/README.md`.

```bash
apatch strip -n --strict-overlap --json \
  --file src/pages/Catalog.tsx \
  --manifest manifests/apatch_catalog_page_hook_strip.json \
  --out-dir src/pages/hooks

apatch phase run --profile frontend \
  --manifest manifests/apatch_catalog_page_hook_strip.json \
  --file src/pages/Catalog.tsx \
  --module-out-dir src/pages/hooks --to-module hook \
  --auto-wire \
  --verify "npm run build && npm run test"
```

```bash
apatch init-consumer --target-dir . --profile frontend
```
