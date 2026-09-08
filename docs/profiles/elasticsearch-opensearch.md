# Profile: Elasticsearch / OpenSearch

Patch index templates, mappings, ingest pipelines (JSON/YAML), and client code in the repo.

| Artifact | apatch | Verify |
|----------|--------|--------|
| `*-template.json`, `*.yaml` mappings | `generate --match json` / `apply` / `plan --json` | `python3 -m json.tool` / `yamllint` |
| Painless / DSL in code | `apply` | unit tests |
| Bulk API client | batch apply | integration test vs test cluster |

## Example

```bash
# JSON templates
apatch generate --find '"dynamic": true' --replace '"dynamic": "strict"' \
  --match json --glob "**/*template*.json" --out patches.jsonl
# YAML templates (тот же --match json; нужен pip install apatch[yaml])
apatch generate --find "type: text" --replace "type: keyword" \
  --match json --glob "**/*template*.{yaml,yml}" --out patches.jsonl
apatch plan --logs patches.jsonl --target-dir . --json   # strategy: json-semantic при drift
apatch apply --logs patches.jsonl --target-dir . --all -y \
  --verify "./scripts/verify-elastic.sh"
```

`verify-elastic.sh` might run: JSON lint + `curl -fsS "$ES_URL/_cluster/health"`.

```bash
apatch init-consumer --target-dir . --profile elastic
```
