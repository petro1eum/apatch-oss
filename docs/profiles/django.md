# Profile: Django

Patch `models.py`, views, and migration **files**; use Django management commands in verify.

## Workflow

```bash
apatch generate --find "auto_now_add=True" --replace "default=timezone.now" \
  --glob "**/models.py" --out patches.jsonl
apatch apply --logs patches.jsonl --target-dir . --all -y \
  --verify "python manage.py migrate --plan && python manage.py test"
```

## Notes

- After model changes run `makemigrations` separately.
- apatch does not run `migrate` against production databases from verify unless you explicitly configure it.
