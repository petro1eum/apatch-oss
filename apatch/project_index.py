"""Project memory index for symbol/import lookup (R50)."""

from __future__ import annotations
from pathlib import Path

import json
import os
import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from apatch.import_graph import build_import_graph
from apatch.symbol_index import build_symbol_index

_INDEX_NAME = ".apatch/project_index.json"
_INDEX_VERSION = 2

_ROUTE_PATTERNS = [
    re.compile(r'@(app|router)\.(get|post|put|delete|patch|api_route)\([^)]+\)', re.I),
    re.compile(r"@app\.route\([^)]+\)", re.I),
]

_MIGRATION_GLOBS = (
    "**/alembic/versions/*.py",
    "**/migrations/versions/*.py",
    "**/prisma/migrations/**/migration.sql",
    "**/migrations/**/*.sql",
)

_ADR_GLOBS = (
    "**/docs/adr/**/*.md",
    "**/docs/rfc/**/*.md",
    "**/adr/**/*.md",
    "**/ADR-*.md",
)

_TITLE_RE = re.compile(r"^#\s+(.+)", re.M)


def index_path(target_dir: str) -> str:
    return os.path.join(os.path.abspath(target_dir), _INDEX_NAME)


def build_project_index(target_dir: str) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    graph = build_import_graph(root)
    symbols = build_symbol_index(root)

    symbol_rows = [
        {"name": name, "file": path}
        for name, path in sorted(symbols.definitions.items())
    ]
    import_edges = [
        {"from": src, "to": dst, "via": via}
        for src, dst, via in graph.edges
    ]

    routes: List[Dict[str, str]] = []
    for abs_path in _iter_index_files(root):
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        try:
            text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rx in _ROUTE_PATTERNS:
            for m in rx.finditer(text):
                routes.append({"file": rel, "signature": m.group(0).strip()})

    model_files = [
        os.path.relpath(p, root).replace("\\", "/")
        for p in _iter_index_files(root)
        if p.endswith((".py", ".prisma"))
        and (
            "model" in os.path.basename(p).lower()
            or "schema.prisma" in p
            or "db_models" in p
        )
    ]

    migration_files = sorted(
        {
            os.path.relpath(p, root).replace("\\", "/")
            for p in _iter_index_files(root)
            if _is_migration_path(os.path.relpath(p, root).replace("\\", "/"))
        }
    )
    adr_docs = _collect_adr_docs(root)

    payload = {
        "version": _INDEX_VERSION,
        "root": root,
        "symbols": symbol_rows,
        "imports": import_edges,
        "routes": routes,
        "model_files": sorted(set(model_files)),
        "migration_files": migration_files,
        "adr_docs": adr_docs,
        "stats": {
            "symbols": len(symbol_rows),
            "import_edges": len(import_edges),
            "routes": len(routes),
            "model_files": len(model_files),
            "migration_files": len(migration_files),
            "adr_docs": len(adr_docs),
        },
    }
    os.makedirs(os.path.dirname(index_path(root)), exist_ok=True)
    with open(index_path(root), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    from apatch.artifact_governance import register_on_write

    register_on_write(
        root,
        _INDEX_NAME,
        class_name="REGISTRY",
        created_by_tool="apatch_index_build",
        reason="project memory index",
        gc_allowed=False,
        replay_critical=True,
    )
    return payload


def load_project_index(target_dir: str) -> Dict[str, Any]:
    path = index_path(target_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"index not built; run: apatch index build --target-dir {target_dir}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def query_project_index(target_dir: str, query: str) -> Dict[str, Any]:
    idx = load_project_index(target_dir)
    q = query.strip()
    ql = q.lower()

    symbol_hits = [s for s in idx.get("symbols", []) if ql in s["name"].lower()]
    route_hits = [r for r in idx.get("routes", []) if ql in r.get("signature", "").lower()]
    file_hits = [
        m
        for m in idx.get("model_files", [])
        if ql in m.lower()
    ]
    migration_hits = [
        m
        for m in idx.get("migration_files", [])
        if ql in m.lower()
    ]
    doc_hits = [
        d
        for d in idx.get("adr_docs", [])
        if ql in d.get("file", "").lower()
        or ql in (d.get("title") or "").lower()
        or ql in (d.get("adr_id") or "").lower()
    ]

    usages: List[str] = []
    if symbol_hits:
        from apatch.impact import run_impact

        impact = run_impact(symbol_hits[0]["name"], target_dir, kind="symbol", depth=2)
        usages = impact.get("affected_files") or []

    return {
        "query": query,
        "symbols": symbol_hits[:25],
        "routes": route_hits[:25],
        "model_files": file_hits[:25],
        "migration_files": migration_hits[:25],
        "adr_docs": doc_hits[:25],
        "usages": usages,
        "stats": idx.get("stats"),
    }


def _path_matches_glob(rel: str, pattern: str) -> bool:
    # case-insensitive glob without PurePath.match(case_sensitive=...) (3.12+ only):
    # lowercase both sides so it works on Python 3.10/3.11.
    p = PurePosixPath(rel.lower())
    pat_l = pattern.lower()
    pat = pat_l[3:] if pat_l.startswith("**/") else pat_l
    return p.match(pat) or p.match(pat_l)


def _is_migration_path(rel: str) -> bool:
    base = os.path.basename(rel)
    if base == "__init__.py":
        return False
    if any(_path_matches_glob(rel, g) for g in _MIGRATION_GLOBS):
        return True
    # Django-style app/migrations/0001_initial.py
    parts = PurePosixPath(rel).parts
    if "migrations" in parts and rel.endswith(".py") and re.match(r"^\d{4}_", base):
        return True
    return False


def _collect_adr_docs(root: str) -> List[Dict[str, str]]:
    docs: List[Dict[str, str]] = []
    seen: set[str] = set()
    for abs_path in _iter_index_files(root):
        if not abs_path.endswith(".md"):
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        if rel in seen:
            continue
        if not any(_path_matches_glob(rel, g) for g in _ADR_GLOBS):
            continue
        seen.add(rel)
        title = ""
        adr_id = ""
        try:
            text = open(abs_path, encoding="utf-8", errors="replace").read(4096)
            m = _TITLE_RE.search(text)
            if m:
                title = m.group(1).strip()
            id_m = re.search(r"(ADR[-_]?\d+)", rel, re.I) or re.search(r"(ADR[-_]?\d+)", title, re.I)
            if id_m:
                adr_id = id_m.group(1).upper().replace("_", "-")
        except OSError:
            pass
        docs.append({"file": rel, "title": title, "adr_id": adr_id})
    return sorted(docs, key=lambda d: d["file"])


def _iter_index_files(root: str):
    from apatch.path_index import iter_target_files

    for abs_path in iter_target_files(root):
        if abs_path.endswith(
            (".py", ".ts", ".tsx", ".js", ".jsx", ".prisma", ".yaml", ".yml", ".json", ".md", ".sql")
        ):
            yield abs_path
