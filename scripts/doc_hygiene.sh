#!/usr/bin/env bash
# Batch doc refresh using apatch generate → plan → apply (dogfooding).
# Run from repo root: ./scripts/doc_hygiene.sh [--apply]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PATCHES="${ROOT}/.apatch/doc-hygiene.jsonl"
APPLY=false
[[ "${1:-}" == "--apply" ]] && APPLY=true

mkdir -p "${ROOT}/.apatch"
rm -f "$PATCHES"
touch "$PATCHES"

replace_in() {
  local dir="$1" find="$2" replace="$3"
  local chunk="${ROOT}/.apatch/doc-hygiene.chunk.jsonl"
  apatch generate \
    --find "$find" \
    --replace "$replace" \
    --glob "**/*.md" \
    --target-dir "$dir" \
    --out "$chunk" \
    --all 2>/dev/null || true
  if [[ -s "$chunk" ]]; then
    cat "$chunk" >> "$PATCHES"
  fi
}

replace() {
  replace_in "$ROOT/docs" "$1" "$2"
  replace_in "$ROOT" "$1" "$2"
}

# Stale “planned” wording → present tense
replace "когда доступны R44–R46" "при оркестрации (R44–R46)"
replace "(спецификация R41–R43)" "(справочник)"
replace "## Рефакторинг с базой данных (R41–R43, спецификация)" "## Рефакторинг с базой данных"
replace "# R45: Dependency Impact Graph — спецификация" "# R45: Dependency Impact Graph — справочник"
replace "# R44: Architecture Drift Check — спецификация" "# R44: Architecture Drift Check — справочник"
replace "../PHASE_CHECKLIST.md" "./PHASE_CHECKLIST.md"

# Dedupe navigation: point arch/impact tool pages at docs index
NAV='> Навигация: [README.md](./README.md) · Рецепты: [cookbook.md](./cookbook.md) · MCP: [mcp_setup.md](./mcp_setup.md)'
COUNT=$(python3 -c "
import json,sys
n=0
with open('$PATCHES') as f:
    for line in f:
        if line.strip(): n+=1
print(n)
")

echo "Generated $COUNT patch step(s) → $PATCHES"
if [[ "$COUNT" -eq 0 ]]; then
  echo "Nothing to change."
  exit 0
fi

apatch plan --logs "$PATCHES" --target-dir "$ROOT" --json | python3 -c "
import json,sys
rows=json.load(sys.stdin)
ok=sum(1 for r in rows if r.get('would_apply'))
print(f'Would apply: {ok}/{len(rows)}')
"

if $APPLY; then
  apatch apply --logs "$PATCHES" --target-dir "$ROOT" --all -y
  echo "Applied doc hygiene patches."
else
  echo "Dry-run only. Re-run: $0 --apply"
fi
