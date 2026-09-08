"""Architecture drift check (R44)."""

from __future__ import annotations
from pathlib import Path

import os
import re
import time
from typing import Any, Dict, List, Optional, Set

from apatch.arch_rules import default_rules_path, load_arch_rules
from apatch.git_util import changed_rel_under_globs, git_changed_files, path_matches
from apatch.import_graph import build_import_graph, file_layer


def run_arch_check(
    target_dir: str,
    *,
    rules_path: Optional[str] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    t0 = time.time()
    root = os.path.abspath(target_dir)
    rules_file = rules_path or default_rules_path(root)
    rules = load_arch_rules(rules_file)

    graph = build_import_graph(root)
    violations: List[Dict[str, Any]] = []

    for rule in rules.get("forbidden_imports") or []:
        violations.extend(_check_forbidden_import(graph, rule))

    violations.extend(_check_layer_rules(graph, rules.get("layer_rules") or []))
    violations.extend(_check_service_rules(root, rules.get("service_rules") or []))

    if since is not None:
        changed = set(git_changed_files(root, since=since))
        violations = [
            v
            for v in violations
            if _violation_in_diff(v, changed)
        ]

    duration_ms = int((time.time() - t0) * 1000)
    return {
        "ok": len(violations) == 0,
        "rules_file": os.path.relpath(rules_file, root) if rules_file.startswith(root) else rules_file,
        "violations": violations,
        "checked_files": len({e[0] for e in graph.edges}),
        "duration_ms": duration_ms,
    }


def _violation_in_diff(v: Dict[str, Any], changed: Set[str]) -> bool:
    files = {v.get("file"), v.get("from")}
    files.discard(None)
    to = v.get("to") or v.get("import")
    if to and not to.endswith(".py"):
        to = to.replace(".", "/") + ".py"
    if to:
        files.add(to)
    return bool(files & changed)


def _check_forbidden_import(graph, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    from_glob = rule.get("from") or rule.get("from_glob") or ""
    to_glob = rule.get("to") or rule.get("to_glob") or ""
    out: List[Dict[str, Any]] = []
    for src, dst, via in graph.edges:
        if from_glob and not path_matches(src, from_glob):
            continue
        if to_glob and not path_matches(dst, to_glob):
            continue
        if from_glob or to_glob:
            out.append({
                "type": "forbidden_import",
                "from": src,
                "to": dst,
                "severity": "error",
                "message": rule.get("message") or f"{src} must not import {dst}",
                "via": via,
            })
    return out


def _check_layer_rules(graph, layer_rules: List[Any]) -> List[Dict[str, Any]]:
    layers: List[Dict[str, Any]] = []
    global_rules: List[str] = []

    for entry in layer_rules:
        if isinstance(entry, dict) and entry.get("layer"):
            layers.append(entry)
        elif isinstance(entry, dict) and entry.get("rule"):
            global_rules.append(entry["rule"])
        elif isinstance(entry, str):
            global_rules.append(entry)

    layer_by_name = {l["layer"]: l for l in layers}
    violations: List[Dict[str, Any]] = []

    for rule_text in global_rules:
        parts = rule_text.lower().split(" cannot import ")
        if len(parts) != 2:
            continue
        src_layer, dst_layer = parts[0].strip(), parts[1].strip()
        src_paths = layer_by_name.get(src_layer, {}).get("paths") or [f"{src_layer}/**"]
        dst_paths = layer_by_name.get(dst_layer, {}).get("paths") or [f"{dst_layer}/**"]
        for src, dst, via in graph.edges:
            if file_layer(src, src_paths) and file_layer(dst, dst_paths):
                violations.append({
                    "type": "layer_violation",
                    "rule": rule_text,
                    "file": src,
                    "import": dst,
                    "severity": "error",
                    "via": via,
                })

    for layer in layers:
        may_import = layer.get("may_import") or []
        paths = layer.get("paths") or []
        if not may_import:
            continue
        for src, dst, via in graph.edges:
            if not file_layer(src, paths):
                continue
            if file_layer(dst, may_import):
                continue
            if dst.startswith(".") or "/" not in dst:
                continue
            violations.append({
                "type": "layer_violation",
                "rule": f"{layer.get('layer')} may_import whitelist",
                "file": src,
                "import": dst,
                "severity": "error",
                "via": via,
            })

    return violations


def _check_service_rules(root: str, service_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for rule in service_rules:
        pattern = rule.get("pattern") or ""
        if not pattern:
            continue
        rx = re.compile(pattern)
        max_per = int(rule.get("max_per_file", 1))
        scope = rule.get("paths") or ["**/*"]
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv"}]
            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                rel = os.path.relpath(abs_path, root).replace("\\", "/")
                if not any(path_matches(rel, g) for g in scope):
                    continue
                try:
                    text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                count = len(rx.findall(text))
                if count > max_per:
                    violations.append({
                        "type": "service_rule",
                        "id": rule.get("id") or "service_rule",
                        "file": rel,
                        "severity": "error",
                        "message": rule.get("description") or f"pattern count {count} > {max_per}",
                        "count": count,
                    })
    return violations
