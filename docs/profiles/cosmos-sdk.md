# Profile: Azure Cosmos DB (SDK + IaC)

Patch application SDK code and infrastructure definitions in the repo. Portal operations and data migration are out of scope.

## Workflow

```bash
apatch generate --find "partitionKey: '/id'" --replace "partitionKey: '/tenantId'" \
  --glob "**/*.{ts,bicep,tf}" --out patches.jsonl
apatch apply --logs patches.jsonl --target-dir . --all -y \
  --verify "terraform validate && npm test"
```

## Verify ideas

- `terraform plan` / `az deployment group validate`
- SDK integration tests against emulator or test account

```bash
apatch init-consumer --target-dir . --profile cosmos
```
