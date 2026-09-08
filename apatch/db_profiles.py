"""Database stack profiles for orchestration tools (R41–R43, R46)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


DB_PROFILES: Dict[str, Dict[str, Any]] = {
    "sqlalchemy": {
        "model_globs": ["**/db_models.py", "**/models.py"],
        "migration_globs": ["**/alembic/versions/*.py"],
        "revision_cmd": ["alembic", "revision", "--autogenerate", "-m", "{message}"],
    },
    "django": {
        "model_globs": ["**/models.py"],
        "migration_globs": ["**/migrations/*.py"],
        "migration_exclude_globs": ["**/migrations/__init__.py"],
        "revision_cmd": ["python", "manage.py", "makemigrations"],
    },
    "prisma": {
        "model_globs": ["**/schema.prisma"],
        "migration_globs": ["**/migrations/**/migration.sql"],
        "revision_cmd": [
            "npx",
            "prisma",
            "migrate",
            "dev",
            "--create-only",
            "--name",
            "{message}",
        ],
    },
}


def get_db_profile(name: str) -> Dict[str, Any]:
    key = (name or "").lower()
    if key not in DB_PROFILES:
        raise ValueError(f"unknown db profile: {name!r} (choose: {', '.join(DB_PROFILES)})")
    return DB_PROFILES[key]


def revision_command(profile: str, message: str) -> List[str]:
    pack = get_db_profile(profile)
    template = pack["revision_cmd"]
    return [part.format(message=message) for part in template]


def migration_globs_for(profile: str, override: Optional[List[str]] = None) -> List[str]:
    if override:
        return override
    return list(get_db_profile(profile)["migration_globs"])


def model_globs_for(profile: str, override: Optional[List[str]] = None) -> List[str]:
    if override:
        return override
    return list(get_db_profile(profile)["model_globs"])
