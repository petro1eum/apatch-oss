"""Semantic verification — routes, exports, contracts (R48)."""

from __future__ import annotations
from pathlib import Path

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Set

from apatch.git_util import find_git_root
from apatch.import_paths import find_semantic_rules_path, resolve_consumer_root


def default_rules_path(target_dir: str) -> str:
    found = find_semantic_rules_path(target_dir)
    if found:
        return found
    return os.path.join(resolve_consumer_root(target_dir), "manifests", "semantic-verify.yaml")


def load_semantic_rules(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"semantic verify rules not found: {path}")

    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError("PyYAML required: pip install apatch[yaml]") from e
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

    data.setdefault("version", 1)
    data.setdefault("routes", {"patterns": []})
    data.setdefault("exports", {"patterns": []})
    data.setdefault("events", {"patterns": []})
    data.setdefault("openapi", {})
    return data


def _git_file_at_ref(root: str, ref: str, rel_path: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", root, "show", f"{ref}:{rel_path}"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _read_working(root: str, rel_path: str) -> Optional[str]:
    abs_path = os.path.join(root, rel_path)
    if not os.path.isfile(abs_path):
        return None
    try:
        return Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _extract_matches(text: str, patterns: List[str]) -> Set[str]:
    found: Set[str] = set()
    for pat in patterns:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for line in text.splitlines():
            m = rx.search(line)
            if m:
                found.add(m.group(0).strip())
    return found


def _check_patterns_removed(
    root: str,
    changed_files: List[str],
    since: str,
    patterns: List[str],
    category: str,
) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for rel in changed_files:
        old_text = _git_file_at_ref(root, since, rel)
        new_text = _read_working(root, rel)
        if old_text is None:
            continue
        if new_text is None:
            new_text = ""
        old_sigs = _extract_matches(old_text, patterns)
        new_sigs = _extract_matches(new_text, patterns)
        for missing in sorted(old_sigs - new_sigs):
            violations.append({
                "type": f"{category}_removed",
                "file": rel,
                "signature": missing,
                "severity": "error",
                "message": f"{category} signature removed: {missing}",
            })
    return violations


def _check_openapi_paths(root: str, since: str, openapi_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    path = openapi_cfg.get("path") or openapi_cfg.get("glob")
    if not path:
        return violations

    rel = path
    if "*" in path:
        from apatch.git_util import files_matching_globs

        hits = files_matching_globs(root, [path])
        if not hits:
            return violations
        rel = os.path.relpath(hits[0], root).replace("\\", "/")

    old_text = _git_file_at_ref(root, since, rel)
    new_text = _read_working(root, rel)
    if not old_text or not new_text:
        return violations

    try:
        import yaml

        old_doc = yaml.safe_load(old_text) or {}
        new_doc = yaml.safe_load(new_text) or {}
    except Exception:
        try:
            old_doc = json.loads(old_text)
            new_doc = json.loads(new_text)
        except json.JSONDecodeError:
            return violations

    old_paths = set((old_doc.get("paths") or {}).keys())
    new_paths = set((new_doc.get("paths") or {}).keys())
    for missing in sorted(old_paths - new_paths):
        violations.append({
            "type": "openapi_path_removed",
            "file": rel,
            "path": missing,
            "severity": "error",
            "message": f"OpenAPI path removed: {missing}",
        })
    return violations


def _run_toolchain_verify_fallback(
    target_dir: str,
    *,
    rules_file: str,
    since: str,
    error: FileNotFoundError,
) -> Dict[str, Any]:
    from apatch.doctor import run_doctor
    from apatch.strip_pipeline import _run_verify

    consumer = resolve_consumer_root(target_dir)
    doctor = run_doctor(consumer)
    cmd = doctor.get("recommended_verify") or ""
    if not cmd:
        raise error
    ok, err = _run_verify(cmd, consumer)
    return {
        "ok": ok,
        "fallback": "toolchain_verify",
        "rules_missing": True,
        "rules_file": rules_file,
        "command": cmd,
        "workspace": consumer,
        "warning": str(error),
        "error": err if not ok else None,
        "since": since,
        "changed_files": 0,
        "violations": [],
    }


def run_semantic_verify(
    target_dir: str = ".",
    *,
    rules_path: Optional[str] = None,
    since: str = "HEAD",
    fallback_toolchain: bool = True,
) -> Dict[str, Any]:
    root = find_git_root(target_dir) or os.path.abspath(target_dir)
    rules_file = rules_path or find_semantic_rules_path(target_dir) or default_rules_path(target_dir)
    try:
        rules = load_semantic_rules(rules_file)
    except FileNotFoundError as e:
        if not fallback_toolchain:
            raise
        return _run_toolchain_verify_fallback(
            target_dir, rules_file=rules_file, since=since, error=e
        )

    from apatch.git_util import git_changed_files

    changed = git_changed_files(root, since=since)
    code_files = [
        f
        for f in changed
        if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml", ".json"))
    ]

    violations: List[Dict[str, Any]] = []
    routes = (rules.get("routes") or {}).get("patterns") or []
    exports = (rules.get("exports") or {}).get("patterns") or []
    events = (rules.get("events") or {}).get("patterns") or []

    if routes:
        violations.extend(_check_patterns_removed(root, code_files, since, routes, "route"))
    if exports:
        violations.extend(_check_patterns_removed(root, code_files, since, exports, "export"))
    if events:
        violations.extend(_check_patterns_removed(root, code_files, since, events, "event"))
    violations.extend(_check_openapi_paths(root, since, rules.get("openapi") or {}))

    return {
        "ok": len(violations) == 0,
        "rules_file": os.path.relpath(rules_file, root) if rules_file.startswith(root) else rules_file,
        "since": since,
        "changed_files": len(changed),
        "violations": violations,
    }
