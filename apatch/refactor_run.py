"""Refactor bundle runner — rename_symbol and phased refactors (R47)."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Set

from apatch.generate import MatchMode, generate_patches, write_jsonl


def load_refactor_manifest(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    kind = data.get("kind")
    if kind not in (None, "refactor-bundle"):
        raise ValueError(f"unsupported manifest kind: {kind!r}")
    op = data.get("operation")
    if op not in ("rename_symbol",):
        raise ValueError(f"unsupported operation: {op!r}")
    if not data.get("symbol") or not data.get("to"):
        raise ValueError("rename_symbol requires symbol and to")
    return data


def _scope_files(target_dir: str, manifest: Dict[str, Any], impact_files: List[str]) -> Set[str]:
    scope = manifest.get("scope") or {}
    globs = scope.get("globs") or ["**/*"]
    exclude = scope.get("exclude") or []
    from apatch.git_util import path_matches

    selected: Set[str] = set()
    if impact_files:
        for rel in impact_files:
            rel = rel.replace("\\", "/")
            if any(path_matches(rel, ex) for ex in exclude):
                continue
            if any(path_matches(rel, g) for g in globs):
                selected.add(rel)
    if not selected:
        from apatch.generate import iter_matching_files

        for g in globs:
            for rel in iter_matching_files(target_dir, g):
                if any(path_matches(rel, ex) for ex in exclude):
                    continue
                selected.add(rel.replace("\\", "/"))
    return selected


def run_refactor_bundle(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    manifest = load_refactor_manifest(manifest_path)
    find_text = manifest["symbol"]
    replace_text = manifest["to"]
    scope_globs = (manifest.get("scope") or {}).get("globs") or ["**/*"]
    glob_pattern = scope_globs[0] if len(scope_globs) == 1 else "**/*"

    phase_results: List[Dict[str, Any]] = []
    impact_result: Dict[str, Any] = {}
    patches_path: Optional[str] = None
    budget = manifest.get("change_budget")

    try:
        for phase in manifest.get("phases") or []:
            action = phase.get("action")
            name = phase.get("name") or action or "phase"

            if action == "impact":
                from apatch.impact import run_impact

                target = phase.get("target") or find_text
                impact_result = run_impact(
                    target,
                    root,
                    kind=phase.get("kind", "symbol"),
                    depth=int(phase.get("depth", 2)),
                )
                result = {"ok": True, "result": impact_result}
            elif action == "generate":
                impact_files = list(impact_result.get("affected_files") or [])
                if impact_result.get("defined_in"):
                    impact_files.append(impact_result["defined_in"])
                files = _scope_files(root, manifest, impact_files)
                match_mode: MatchMode = phase.get("match_mode", "literal")
                patches: List[dict] = []
                step = 1
                for rel in sorted(files):
                    partial = generate_patches(
                        find=find_text,
                        replace=replace_text,
                        target_dir=root,
                        glob_pattern=rel,
                        replace_all=bool(phase.get("replace_all", True)),
                        match_mode=match_mode,
                    )
                    for p in partial:
                        p["step_index"] = step
                        step += 1
                    patches.extend(partial)
                if not patches:
                    result = {"ok": False, "error": "no patches generated", "files_scanned": len(files)}
                else:
                    fd, patches_path = tempfile.mkstemp(suffix=".jsonl", prefix="apatch_refactor_")
                    os.close(fd)
                    with open(patches_path, "w", encoding="utf-8") as out:
                        write_jsonl(patches, out)
                    result = {
                        "ok": True,
                        "patch_count": len(patches),
                        "patches_jsonl": patches_path,
                        "files_scanned": len(files),
                    }
            elif action == "plan":
                if not patches_path:
                    result = {"ok": False, "error": "no patches_jsonl (run generate first)"}
                else:
                    from apatch.workflows import plan_from_logs

                    preview = plan_from_logs(patches_path, root)
                    result = {
                        "ok": preview.get("would_apply", 0) > 0 or preview.get("total", 0) == 0,
                        "result": preview,
                    }
            elif action == "apply":
                if not patches_path:
                    result = {"ok": False, "error": "no patches_jsonl"}
                elif dry_run:
                    result = {"ok": True, "dry_run": True}
                else:
                    from apatch.workflows import apply_from_logs

                    out = apply_from_logs(
                        patches_path,
                        root,
                        replace_all=True,
                        verify=phase.get("verify"),
                        verify_deferred=bool(phase.get("verify_deferred")),
                        no_trustchain=bool(phase.get("no_trustchain")),
                        change_budget=budget,
                    )
                    result = {"ok": out.get("ok", False), "result": out}
            elif action == "verify":
                result = _shell_phase(root, phase, dry_run=dry_run)
            elif action == "arch_check":
                from apatch.arch_check import run_arch_check

                arch = run_arch_check(root, rules_path=phase.get("rules"))
                result = {**arch, "ok": arch.get("ok", False)}
            else:
                result = {"ok": False, "error": f"unknown action: {action}"}

            phase_results.append({"name": name, "action": action, **result})
            if not result.get("ok"):
                return {
                    "ok": False,
                    "operation": manifest.get("operation"),
                    "symbol": find_text,
                    "to": replace_text,
                    "manifest": os.path.basename(manifest_path),
                    "phases": phase_results,
                    "failed_phase": name,
                    "impact": impact_result,
                }

        return {
            "ok": True,
            "operation": manifest.get("operation"),
            "symbol": find_text,
            "to": replace_text,
            "manifest": os.path.basename(manifest_path),
            "phases": phase_results,
            "impact": impact_result,
            "dry_run": dry_run,
        }
    finally:
        if patches_path and os.path.isfile(patches_path):
            try:
                os.remove(patches_path)
            except OSError:
                pass


def _shell_phase(root: str, phase: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    import subprocess

    cmd = phase.get("command") or ""
    if not cmd:
        return {"ok": False, "error": "verify phase requires command"}
    if dry_run:
        return {"ok": True, "dry_run": True, "command_run": cmd}
    proc = subprocess.run(cmd, cwd=root, shell=True, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "command_run": cmd,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "exit_code": proc.returncode,
    }
