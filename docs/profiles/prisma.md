# Profile: Prisma

Patch `schema.prisma` and client code in the repo; run Prisma CLI in `--verify`.

## Workflow

```bash
apatch generate --find "oldField" --replace "newField" \
  --glob "**/*.{ts,prisma}" --out patches.jsonl
apatch apply --logs patches.jsonl --target-dir . --all -y \
  --verify "prisma validate && npx prisma migrate diff"
```

## Verify ideas

- `prisma validate`
- `prisma migrate diff --from-schema-datamodel prisma/schema.prisma --to-schema-datasource prisma/schema.prisma`
- `npm test` for client code

## Init

```bash
apatch init-consumer --target-dir . --profile default
apatch doctor
```
