"""Detect project toolchain for verify recommendations (R25)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apatch.path_index import find_git_root, list_workspace_file_paths
from apatch.tool_paths import (
    build_subprocess_env,
    detect_tool_versions,
    materialize_verify_command,
    resolve_executable,
)

# Re-export for backward compatibility.
__all__ = [
    "detect_toolchain",
    "recommend_verify",
    "discover_npm_path",
    "build_subprocess_env",
    "materialize_verify_command",
    "clear_toolchain_cache",
]

_MODEL_FILENAMES = frozenset({"models.py", "db_models.py"})
_ELASTIC_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
_COSMOS_SUFFIXES = frozenset({".bicep", ".tf", ".py", ".ts", ".cs"})
_TOOLCHAIN_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def clear_toolchain_cache() -> None:
    """Drop cached ``detect_toolchain`` results (tests)."""
    _TOOLCHAIN_CACHE.clear()


def discover_npm_path(workspace: str) -> Optional[str]:
    """Locate npm — delegates to centralized resolver."""
    return resolve_executable("npm", workspace=workspace)


def _marker_signature(root: Path) -> float:
    mtimes: List[float] = []
    for rel in (
        "package.json",
        "pyproject.toml",
        "alembic.ini",
        "manage.py",
        "CMakeLists.txt",
        "prisma/schema.prisma",
    ):
        path = root / rel
        if path.is_file():
            mtimes.append(path.stat().st_mtime)
    git_root = find_git_root(str(root))
    if git_root:
        index_path = os.path.join(git_root, ".git", "index")
        if os.path.isfile(index_path):
            mtimes.append(os.path.getmtime(index_path))
    return max(mtimes) if mtimes else 0.0


def _workspace_files(root: Path) -> List[str]:
    return list_workspace_file_paths(str(root))


def _read_head(path: str, *, limit: int = 4000) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return None


def _has_sqlalchemy_models(root: Path) -> bool:
    for abs_path in _workspace_files(root):
        if os.path.basename(abs_path) not in _MODEL_FILENAMES:
            continue
        text = _read_head(abs_path)
        if text and ("sqlalchemy" in text.lower() or "Column(" in text):
            return True
    return False


def _has_elastic_artifacts(root: Path) -> bool:
    for abs_path in _workspace_files(root):
        suffix = Path(abs_path).suffix.lower()
        if suffix not in _ELASTIC_SUFFIXES:
            continue
        text = _read_head(abs_path)
        if not text:
            continue
        if '"mappings"' in text and '"properties"' in text:
            return True
        if "index_patterns" in text or "opensearch" in text.lower():
            return True
    return False


def _has_cosmos_artifacts(root: Path) -> bool:
    for abs_path in _workspace_files(root):
        suffix = Path(abs_path).suffix.lower()
        if suffix not in _COSMOS_SUFFIXES:
            continue
        text = _read_head(abs_path)
        if text and ("Microsoft.DocumentDB" in text or "cosmos" in text.lower()):
            return True
    return False


def detect_toolchain(workspace: str, *, use_cache: bool = True) -> Dict[str, Any]:
    """Scan workspace for common verify tools and stack markers."""
    root = Path(workspace).resolve()
    key = str(root)
    sig = _marker_signature(root)
    if use_cache and key in _TOOLCHAIN_CACHE:
        cached_sig, cached = _TOOLCHAIN_CACHE[key]
        if cached_sig == sig:
            return cached

    tools = detect_tool_versions(str(root))

    markers: Dict[str, bool] = {
        "alembic_ini": (root / "alembic.ini").is_file(),
        "prisma_schema": (root / "prisma" / "schema.prisma").is_file(),
        "package_json": (root / "package.json").is_file(),
        "pyproject_toml": (root / "pyproject.toml").is_file(),
        "cmake_lists": (root / "CMakeLists.txt").is_file(),
        "django_manage": (root / "manage.py").is_file(),
        "terraform": any(root.glob("*.tf")) or (root / "terraform").is_dir(),
    }

    profiles: List[str] = []
    if markers["alembic_ini"] or _has_sqlalchemy_models(root):
        profiles.append("sqlalchemy")
    if markers["prisma_schema"]:
        profiles.append("prisma")
    if markers["django_manage"]:
        profiles.append("django")
    if markers["package_json"]:
        pkg = _read_json(root / "package.json") or {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if any(k in deps for k in ("react", "vite", "@vitejs/plugin-react")):
            profiles.append("frontend")
    if markers["cmake_lists"]:
        profiles.append("cpp")
    if _has_elastic_artifacts(root):
        profiles.append("elastic")
    if _has_cosmos_artifacts(root):
        profiles.append("cosmos")

    recommended = recommend_verify(workspace, tools, markers, profiles)
    result = {
        "tools": tools,
        "markers": markers,
        "detected_profiles": profiles,
        "recommended_verify": recommended,
        "recommended_verify_resolved": materialize_verify_command(recommended, str(root)),
        "path_prefix": build_subprocess_env(str(root)).get("PATH", "").split(":")[:8],
    }
    _TOOLCHAIN_CACHE[key] = (sig, result)
    return result


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def recommend_verify(
    workspace: str,
    tools: Optional[Dict[str, Dict[str, Any]]] = None,
    markers: Optional[Dict[str, bool]] = None,
    profiles: Optional[List[str]] = None,
) -> str:
    """Build a suggested --verify shell chain for the detected stack."""
    if tools is None or markers is None or profiles is None:
        data = detect_toolchain(workspace)
        tools = data["tools"]
        markers = data["markers"]
        profiles = data["detected_profiles"]

    parts: List[str] = []
    if (
        markers.get("package_json")
        and "frontend" in profiles
        and tools.get("npm", {}).get("found")
    ):
        parts.append("npm run build")
    tests_dir = Path(workspace, "tests").is_dir()
    if tests_dir and (
        markers.get("pyproject_toml")
        or markers.get("django_manage")
        or "sqlalchemy" in profiles
    ):
        if tools.get("pytest", {}).get("found"):
            parts.append("python3 -m pytest tests/")
        else:
            parts.append("python3 -m pytest tests/")
    if markers.get("alembic_ini") and tools.get("alembic", {}).get("found"):
        parts.append("alembic upgrade head")
    if markers.get("prisma_schema") and tools.get("prisma", {}).get("found"):
        parts.append("prisma validate")
    if (
        markers.get("package_json")
        and tools.get("npm", {}).get("found")
        and "npm run build" not in parts
    ):
        parts.append("npm run build")
    if markers.get("cmake_lists") and tools.get("cmake", {}).get("found"):
        parts.append("cmake --build build")
    if markers.get("terraform") and tools.get("terraform", {}).get("found"):
        parts.append("terraform validate")

    if not parts:
        if tools.get("pytest", {}).get("found"):
            return "python3 -m pytest tests/"
        if tools.get("npm", {}).get("found"):
            return "npm test"
        return "pytest"

    return " && ".join(parts)
