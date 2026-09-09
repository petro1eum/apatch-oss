"""Consumer project profile packs for init-consumer (R24)."""

from __future__ import annotations

from typing import Any, Dict, List

MCP_ROAMING_AGENT_NOTE = """## MCP local roaming

Bound workspace: use `target_dir="."`. A human-registered sibling may use
`target_dir="@alias"` only after `apatch_workspace_inspect(alias=..., include_contract=true)`.
Never pass a raw absolute cross-workspace path. For one executable spec, use
`apatch_remote_task_run(..., plan={"spec": "SPEC-X", "requirements": {...}},
dry_run=false)` so the broker routes `spec_run` directly. To mutate and
re-attest one exact already-attested Rk, use
`plan={"execute_next": true, "spec": "SPEC-X", "requirement": "SPEC-X#Rk", "needles": [...]}`.
If its live verify requires a service restart, add `"defer_finalize": true`, restart through
the service broker, then use `plan={"finalize_current": true}` with a fresh verify.
The same defer flag applies to `fix_forward_current`: it leaves the session open after apply.
Finalization always executes the bound SPEC checks; a supplied shorter verify is additional,
not a replacement. Missing/drifted SPEC bindings or an older worker fail closed before attest.
For mutations spanning two or more specs, use `plan={"specs": [...], "requirements": {...}}`; the broker
routes `spec_run_multi` rather than split the change into standalone sessions. For independent
maintenance targets, add `"execution_mode": "shared_maintenance"`: each file must belong to one
exact Rk, one apply is followed by parallel native Rk verifies, and shared targets fail closed. To verify once,
re-attest open requirements, and enroll conformance remotely, use
`plan={"slug_ratify": true, "slug": "slug", "spec": "SPEC-X"}`. When a shared file
makes sibling requirements stale, use `plan={"rebind_stale": true, "spec": "SPEC-X"}`;
the remote worker verifies and rebinds them without fake source mutations.
After completed sessions, use `plan={"commit_attested": true, "session_ids": [...], "push": true}`
to commit/push only their exact signed file hashes; unrelated dirty files stay untouched.
Before reopening a completed category, run `apatch_slug_cockpit(slug=..., live=true)`.
When its configured `operational_status` hook returns `runtime_work_complete=true`
with empty `reopen_reasons`, preserve the runtime implementation: evidence debt is
reported separately and is not permission to rebuild the category. For a
runtime-complete slug, unavailable feedback replay is yellow/unknown bookkeeping,
not red runtime failure; fresh feedback and explicit runtime defects still reopen it.
Installed-wheel initialization includes the canonical consumer guidance and hooks;
missing resources are an installation error, not a successful partial setup.
Conformance CLI honors configured blocking mode in text and JSON without an extra
flag; --blocking can strengthen advisory mode but does not enable a disabled gate.
Keep the exact venv interpreter in IDE and canonical MCP configs; a binary realpath
does not identify a Python environment. Sync preserves existing env/profile unless
--profile is explicitly selected. Doctor never repairs the config.
Use apatch mcp check --target-dir . --json for isolated configured-command readiness.
A running MCP doctor reports not_checked_in_stdio for that independent check;
server readiness is not proof of host tool availability in a particular task.
"""

PROFILES: Dict[str, Dict[str, Any]] = {
    "olang": {
        "label": "OLang language repository",
        "profile_doc": "",
        "verify": "python3 o_lang/tools/olang_doc_conformance.py --check all",
        "npm_scripts": {},
        "manifest_name": "apatch.olang.example.json",
        "sandbox_protected_globs": [
            "o_lang/**",
            "docs/RFP-*.md",
            "docs/specs/**",
            "AGENTS.md",
        ],
        "sandbox_forbidden_allow_globs": ["docs/*.md", "*.md"],
        "agents_section": """## Stack profile: OLang

All OLang source, executable RFP/SPEC contracts, and this `AGENTS.md` are governed
surfaces. Mutate them only through the exact executable `SPEC#Rk` whose `owns:`
declaration contains every target path. A generic signed session is insufficient.
""",
    },
    "default": {
        "label": "Universal",
        "profile_doc": "docs/cookbook.md",
        "verify": "pytest",
        "npm_scripts": {},
        "manifest_name": "apatch.example.json",
        "agents_section": "",
    },
    "frontend": {
        "label": "TypeScript / React (Vite)",
        "profile_doc": "docs/profiles/frontend.md",
        "verify": "npm run build",
        "npm_scripts": {
            "apatch:phase": "apatch phase run --profile frontend --verify npm run build",
        },
        "manifest_name": "apatch.frontend.example.json",
        "agents_section": """## Stack profile: frontend

Verify: `manifests/PROFILE.frontend.md` + `doctor.recommended_verify_resolved` (часто `npm run build`; для фаз — `npm run build && npm run test`).

```text
apatch_suggest_until(file_path="src/pages/Mono.tsx", manifest_path="manifests/apatch.frontend.example.json", target_dir=".")
apatch_strip_dry_run(file_path="src/pages/Mono.tsx", manifest_path="manifests/apatch.frontend.example.json",
  strict_overlap=true, target_dir=".")
apatch_phase_run(manifest_path="manifests/apatch.frontend.example.json", file_path="src/pages/Mono.tsx",
  profile="frontend", to_module="hook", module_out_dir="src/hooks",
  verify=<doctor.recommended_verify_resolved>, auto_wire=true, target_dir=".")
```
""",
    },
    "sqlalchemy": {
        "label": "SQLAlchemy + Alembic",
        "profile_doc": "docs/profiles/sqlalchemy-alembic.md",
        "verify": 'python3 -m pytest tests/ && alembic upgrade head',
        "npm_scripts": {
            "apatch:apply": (
                "apatch apply --logs patches.jsonl --target-dir . --all -y "
                '--verify "python3 -m pytest tests/ && alembic upgrade head" --verify-deferred'
            ),
        },
        "manifest_name": "apatch.sqlalchemy.example.json",
        "agents_section": """## Stack profile: sqlalchemy

Verify: `manifests/PROFILE.sqlalchemy.md` + `doctor.recommended_verify_resolved` (`pytest` + `alembic upgrade head`).

```text
apatch_generate(find_text="OLD", replace_text="NEW", glob_pattern="**/db_models.py", out_path="patches.jsonl",
  match_mode="whitespace", target_dir=".")
apatch_apply_session(logs_path="patches.jsonl", verify_deferred=true, verify=<resolved>, target_dir=".")
apatch_db_check(profile="sqlalchemy", target_dir=".")
apatch_db_revision(profile="sqlalchemy", message="...", dry_run=true, target_dir=".")
```

Миграции Alembic — **отдельно** после apply.
""",
    },
    "elastic": {
        "label": "Elasticsearch / OpenSearch",
        "profile_doc": "docs/profiles/elasticsearch-opensearch.md",
        "verify": "./scripts/verify-elastic.sh",
        "npm_scripts": {},
        "manifest_name": "apatch.elastic.example.json",
        "agents_section": """## Stack profile: elastic

Verify: `manifests/PROFILE.elastic.md` + `./scripts/verify-elastic.sh` (или `doctor.recommended_verify_resolved`).

```text
apatch_generate(find_text="...", replace_text="...", glob_pattern="**/*template*.{json,yaml,yml}",
  match_mode="json", out_path="patches.jsonl", target_dir=".")
apatch_plan_batch(logs_path="patches.jsonl", target_dir=".")
apatch_apply_session(logs_path="patches.jsonl", verify=<resolved>, target_dir=".")
```
""",
    },
    "prisma": {
        "label": "Prisma ORM",
        "profile_doc": "docs/profiles/prisma.md",
        "verify": "prisma validate && npx prisma migrate diff --from-empty --to-schema-datamodel prisma/schema.prisma",
        "npm_scripts": {
            "apatch:apply": (
                "apatch apply --logs patches.jsonl --target-dir . --all -y "
                '--verify "prisma validate" --verify-deferred'
            ),
        },
        "manifest_name": "apatch.prisma.example.json",
        "agents_section": """## Stack profile: prisma

Verify: `manifests/PROFILE.prisma.md` + `prisma validate`.

```text
apatch_generate(find_text="...", replace_text="...", glob_pattern="**/*.prisma", out_path="patches.jsonl", target_dir=".")
apatch_apply_session(logs_path="patches.jsonl", verify_deferred=true, verify=<resolved>, target_dir=".")
```

`prisma migrate dev` — **отдельно** после apply.
""",
    },
    "django": {
        "label": "Django ORM",
        "profile_doc": "docs/profiles/django.md",
        "verify": "python manage.py migrate --plan && python manage.py test",
        "npm_scripts": {},
        "manifest_name": "apatch.django.example.json",
        "agents_section": """## Stack profile: django

Verify: `manifests/PROFILE.django.md` + `python manage.py test`.

```text
apatch_generate(find_text="...", replace_text="...", glob_pattern="**/models.py", out_path="patches.jsonl", target_dir=".")
apatch_apply_session(logs_path="patches.jsonl", verify_deferred=true, verify=<resolved>, target_dir=".")
```

`makemigrations` — **отдельно** после apply.
""",
    },
    "cosmos": {
        "label": "Azure Cosmos DB (SDK + IaC)",
        "profile_doc": "docs/profiles/cosmos-sdk.md",
        "verify": "terraform validate",
        "npm_scripts": {},
        "manifest_name": "apatch.cosmos.example.json",
        "agents_section": """## Stack profile: cosmos

Verify: `manifests/PROFILE.cosmos.md` + `terraform validate`.

```text
apatch_generate(find_text="...", replace_text="...", glob_pattern="**/*.{bicep,tf,ts}", out_path="patches.jsonl", target_dir=".")
apatch_apply_session(logs_path="patches.jsonl", verify=<resolved>, target_dir=".")
```
""",
    },
}

FRONTEND_MANIFEST = """{
  "verify_command": "npm run build",
  "strips": [
    {
      "label": "example_handlers",
      "start": "// --- Example block start ---",
      "until": "// --- Example block end ---",
      "replace": "  // Stubbed: useExampleHandlers hook\\n",
      "export": "example_handlers.fragment.txt",
      "target_module": "src/features/hooks/useExampleHandlers.ts",
      "module_kind": "hook",
      "parent_import": "import { useExampleHandlers } from '@/features/hooks/useExampleHandlers';",
      "parent_wire": "const { handleOpen } = useExampleHandlers({ drawerOpen, setDrawerOpen });",
      "verify_command": "npm run build"
    }
  ]
}
"""

SQLALCHEMY_MANIFEST = """{
  "verify_command": "python3 -m pytest tests/ && alembic upgrade head",
  "note": "Use apatch generate/apply for db_models.py; add Alembic revision separately."
}
"""

ELASTIC_MANIFEST = """{
  "verify_command": "python3 -m json.tool config/index-template.json > /dev/null",
  "note": "Patch mapping JSON/YAML files; run cluster verify script in --verify."
}
"""

PRISMA_MANIFEST = """{
  "verify_command": "prisma validate",
  "note": "Patch schema.prisma and TypeScript client; run prisma migrate dev separately.",
  "generate_example": "apatch generate --find \\"oldField\\" --replace \\"newField\\" --glob \\"**/*.prisma\\" --out patches.jsonl"
}
"""

DJANGO_MANIFEST = """{
  "verify_command": "python manage.py migrate --plan && python manage.py test",
  "note": "Patch models.py; create migrations with makemigrations after apply.",
  "generate_example": "apatch generate --find \\"auto_now_add=True\\" --replace \\"default=timezone.now\\" --glob \\"**/models.py\\" --out patches.jsonl"
}
"""

COSMOS_MANIFEST = """{
  "verify_command": "terraform validate",
  "note": "Patch SDK + IaC files; portal operations are out of scope."
}
"""


def list_profiles() -> List[str]:
    return sorted(PROFILES.keys())


def get_profile(name: str) -> Dict[str, Any]:
    key = name if name in PROFILES else "default"
    pack = dict(PROFILES[key])
    section = str(pack.get("agents_section") or "").rstrip()
    pack["agents_section"] = (
        section + "\n\n" + MCP_ROAMING_AGENT_NOTE
        if section
        else MCP_ROAMING_AGENT_NOTE
    )
    return pack


def manifest_content_for_profile(profile: str) -> str:
    if profile == "frontend":
        return FRONTEND_MANIFEST.strip() + "\n"
    if profile == "sqlalchemy":
        return SQLALCHEMY_MANIFEST.strip() + "\n"
    if profile == "elastic":
        return ELASTIC_MANIFEST.strip() + "\n"
    if profile == "prisma":
        return PRISMA_MANIFEST.strip() + "\n"
    if profile == "django":
        return DJANGO_MANIFEST.strip() + "\n"
    if profile == "cosmos":
        return COSMOS_MANIFEST.strip() + "\n"
    from apatch.doctor import CONSUMER_MANIFEST_EXAMPLE

    return CONSUMER_MANIFEST_EXAMPLE.strip() + "\n"
