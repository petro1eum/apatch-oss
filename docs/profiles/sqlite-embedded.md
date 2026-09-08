# Profile: SQLite (embedded schema)

For `.sql` files and embedded schema strings in application code.

```bash
apatch generate --find "INTEGER PRIMARY KEY" --replace "INTEGER PRIMARY KEY AUTOINCREMENT" \
  --glob "**/*.sql" --out patches.jsonl
apatch apply --logs patches.jsonl --target-dir . --all -y \
  --verify "python3 -m pytest tests/"
```

Optional verify: `sqlite3 :memory: < schema.sql` in a small shell script.
