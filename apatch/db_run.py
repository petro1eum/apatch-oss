"""Multi-phase db-refactor manifest runner (R43)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional


def load_db_refactor_manifest(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("kind") not in (None, "db-refactor"):
        raise ValueError(f"unsupported manifest kind: {data.get('kind')!r}")
    if not data.get("phases"):
        raise ValueError("manifest has no phases")
    return data


def run_db_refactor(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    manifest = load_db_refactor_manifest(manifest_path)
    profile = manifest.get("profile") or "sqlalchemy"
    patches_jsonl = manifest.get("patches_jsonl")
    if not patches_jsonl:
        raise ValueError("manifest must set patches_jsonl")

    abs_patches = patches_jsonl if os.path.isabs(patches_jsonl) else os.path.join(root, patches_jsonl)
    if not os.path.isfile(abs_patches):
        raise FileNotFoundError(f"patches_jsonl not found: {abs_patches}")

    phase_results: List[Dict[str, Any]] = []
    context: Dict[str, Any] = {"db_check": {}, "db_safety": {}}

    for phase in manifest.get("phases") or []:
        name = phase.get("name") or phase.get("action") or "phase"
        action = phase.get("action")
        if phase.get("skip_if") == "db_check.ok" and context.get("db_check", {}).get("ok"):
            phase_results.append({
                "name": name,
                "action": action,
                "ok": True,
                "skipped": True,
                "reason": "skip_if db_check.ok",
            })
            continue

        if action == "apply":
            result = _phase_apply(root, abs_patches, phase, dry_run=dry_run)
        elif action == "db_check":
            result = _phase_db_check(root, profile, manifest, phase)
            if not result.get("ok") and manifest.get("auto_revision"):
                rev = _phase_db_revision(
                    root,
                    profile,
                    {"message": phase.get("message") or "apatch auto revision"},
                    dry_run=dry_run,
                )
                phase_results.append({
                    "name": f"{name}_auto_revision",
                    "action": "db_revision",
                    **rev,
                })
                if rev.get("ok") and not dry_run:
                    result = _phase_db_check(root, profile, manifest, phase)
            context["db_check"] = result
        elif action == "db_revision":
            result = _phase_db_revision(root, profile, phase, dry_run=dry_run)
        elif action == "db_safety":
            result = _phase_db_safety(root, profile, phase)
            context["db_safety"] = result
        elif action == "arch_check":
            result = _phase_arch_check(root, phase)
        elif action == "shell":
            result = _phase_shell(root, phase, dry_run=dry_run)
        else:
            result = {"ok": False, "error": f"unknown action: {action}"}

        entry = {"name": name, "action": action, **result}
        phase_results.append(entry)

        if not result.get("ok"):
            if phase.get("optional"):
                entry["optional_failure"] = True
                continue
            return {
                "ok": False,
                "profile": profile,
                "manifest": os.path.basename(manifest_path),
                "phases": phase_results,
                "failed_phase": name,
                "reason": result.get("reason") or result.get("error"),
            }

    return {
        "ok": True,
        "profile": profile,
        "manifest": os.path.basename(manifest_path),
        "phases": phase_results,
        "dry_run": dry_run,
    }


def _phase_apply(root: str, patches: str, phase: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    from apatch.workflows import apply_from_logs

    if dry_run:
        from apatch.workflows import plan_from_logs

        preview = plan_from_logs(patches, root)
        return {
            "ok": preview.get("would_apply", 0) > 0 or preview.get("total", 0) == 0,
            "dry_run": True,
            "would_apply": preview.get("would_apply"),
            "total": preview.get("total"),
        }

    out = apply_from_logs(
        patches,
        root,
        verify=phase.get("verify"),
        verify_deferred=bool(phase.get("verify_deferred")),
        no_trustchain=bool(phase.get("no_trustchain")),
    )
    return {"ok": out.get("ok", False), "result": out}


def _phase_db_check(
    root: str,
    profile: str,
    manifest: Dict[str, Any],
    phase: Dict[str, Any],
) -> Dict[str, Any]:
    from apatch.db_check import run_db_check

    return run_db_check(
        root,
        profile=profile,
        since=phase.get("since", "HEAD"),
        model_glob=manifest.get("model_globs"),
        migration_glob=manifest.get("migration_globs"),
    )


def _phase_db_revision(root: str, profile: str, phase: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    from apatch.db_revision import run_db_revision

    return run_db_revision(
        root,
        profile=profile,
        message=phase.get("message") or "apatch revision",
        dry_run=dry_run,
    )


def _phase_db_safety(root: str, profile: str, phase: Dict[str, Any]) -> Dict[str, Any]:
    from apatch.db_safety import run_db_safety

    result = run_db_safety(
        root,
        profile=profile,
        since=phase.get("since", "HEAD"),
    )
    return {**result, "ok": result.get("safe", True)}


def _phase_arch_check(root: str, phase: Dict[str, Any]) -> Dict[str, Any]:
    from apatch.arch_check import run_arch_check

    return run_arch_check(root, rules_path=phase.get("rules"))


def _phase_shell(root: str, phase: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    cmd = phase.get("command") or ""
    if not cmd:
        return {"ok": False, "error": "shell phase requires command"}
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
