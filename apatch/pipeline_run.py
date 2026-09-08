"""Unified engineering change pipeline (R52, partial R51)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


def load_pipeline_manifest(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("kind") not in (None, "engineering-pipeline"):
        raise ValueError(f"unsupported manifest kind: {data.get('kind')!r}")
    if not data.get("phases"):
        raise ValueError("manifest has no phases")
    return data


def run_engineering_pipeline(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    manifest = load_pipeline_manifest(manifest_path)
    phase_results: List[Dict[str, Any]] = []
    patches_jsonl = manifest.get("patches_jsonl")
    if patches_jsonl and not os.path.isabs(patches_jsonl):
        patches_jsonl = os.path.join(root, patches_jsonl)

    for phase in manifest.get("phases") or []:
        action = phase.get("action")
        name = phase.get("name") or action or "phase"
        result = _run_phase(
            root, action, phase, manifest, patches_jsonl,
            manifest_path=manifest_path, dry_run=dry_run,
        )
        phase_results.append({"name": name, "action": action, **result})
        if not result.get("ok"):
            return {
                "ok": False,
                "manifest": os.path.basename(manifest_path),
                "phases": phase_results,
                "failed_phase": name,
                "reason": result.get("error") or result.get("reason"),
            }

    return {
        "ok": True,
        "manifest": os.path.basename(manifest_path),
        "phases": phase_results,
        "dry_run": dry_run,
    }


def _run_phase(
    root: str,
    action: Optional[str],
    phase: Dict[str, Any],
    manifest: Dict[str, Any],
    patches_jsonl: Optional[str],
    *,
    manifest_path: str,
    dry_run: bool,
) -> Dict[str, Any]:
    if action == "plan":
        if not patches_jsonl:
            return {"ok": False, "error": "patches_jsonl required"}
        from apatch.workflows import plan_from_logs

        preview = plan_from_logs(patches_jsonl, root)
        return {"ok": True, "result": preview}

    if action == "apply":
        if not patches_jsonl:
            return {"ok": False, "error": "patches_jsonl required"}
        if dry_run:
            return {"ok": True, "dry_run": True}
        from apatch.workflows import apply_from_logs

        budget = phase.get("change_budget") or manifest.get("change_budget")
        out = apply_from_logs(
            patches_jsonl,
            root,
            verify=phase.get("verify"),
            verify_deferred=bool(phase.get("verify_deferred")),
            no_trustchain=bool(phase.get("no_trustchain")),
            change_budget=budget,
        )
        return {"ok": out.get("ok", False), "result": out}

    if action == "verify_shell":
        from apatch.db_run import _shell_phase

        return _shell_phase(root, {"command": phase.get("command")}, dry_run=dry_run)

    if action == "verify_semantic":
        from apatch.semantic_verify import run_semantic_verify

        sem = run_semantic_verify(root, rules_path=phase.get("rules"), since=phase.get("since", "HEAD"))
        return {**sem, "ok": sem.get("ok", False)}

    if action == "db_check":
        from apatch.db_check import run_db_check

        profile = phase.get("profile") or manifest.get("db_profile") or "sqlalchemy"
        chk = run_db_check(root, profile=profile, since=phase.get("since", "HEAD"))
        return {**chk, "ok": chk.get("ok", False)}

    if action == "db_safety":
        from apatch.db_safety import run_db_safety

        profile = phase.get("profile") or manifest.get("db_profile") or "sqlalchemy"
        saf = run_db_safety(root, profile=profile, since=phase.get("since", "HEAD"))
        return {**saf, "ok": saf.get("safe", True)}

    if action == "db_revision":
        from apatch.db_revision import run_db_revision

        profile = phase.get("profile") or manifest.get("db_profile") or "sqlalchemy"
        return run_db_revision(
            root,
            profile=profile,
            message=phase.get("message", "apatch revision"),
            dry_run=dry_run,
        )

    if action == "impact":
        from apatch.impact import run_impact

        target = phase.get("target") or ""
        if not target:
            return {"ok": False, "error": "impact requires target"}
        imp = run_impact(target, root, kind=phase.get("kind"), depth=int(phase.get("depth", 1)))
        return {"ok": True, "result": imp}

    if action == "arch_check":
        from apatch.arch_check import run_arch_check

        arch = run_arch_check(root, rules_path=phase.get("rules"), since=phase.get("since"))
        return {**arch, "ok": arch.get("ok", False)}

    if action == "index_build":
        from apatch.project_index import build_project_index

        return {"ok": True, "result": build_project_index(root)}

    if action == "trustchain_history":
        from apatch.trustchain_helper import TrustChainHelper

        from apatch.artifact import normalize_manifest_artifacts

        query = phase.get("query")
        artifact = phase.get("artifact")
        if not query and not artifact:
            arts = normalize_manifest_artifacts(manifest)
            if len(arts) == 1:
                artifact = f"{arts[0]['kind']}:{arts[0]['id']}"
            else:
                query = manifest.get("intent") or manifest.get("adr")
        tc = TrustChainHelper(root, auto_init=False)
        if not tc.has_trustchain():
            if phase.get("require_trustchain"):
                return {"ok": False, "error": "no trustchain"}
            return {"ok": True, "skipped": True, "reason": "no trustchain", "entries": []}
        hist = tc.list_intent_history(
            query=str(query) if query else None,
            artifact=str(artifact) if artifact else None,
            limit=int(phase.get("limit", 10)),
        )
        entries = hist.get("entries") or []
        latest = entries[-1] if entries else None
        if phase.get("require_prior") and not entries:
            return {
                "ok": False,
                "error": "no matching intent/adr in trustchain history",
                "query": query,
                "entries": [],
            }
        return {
            "ok": True,
            "dry_run": dry_run,
            "query": query,
            "entries": entries,
            "latest": latest,
            "count": len(entries),
        }

    if action == "trustchain_intent":
        from apatch.artifact import normalize_manifest_artifacts

        manifest_artifacts = normalize_manifest_artifacts(manifest)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "intent": phase.get("intent") or manifest.get("intent"),
                "adr": phase.get("adr") or manifest.get("adr"),
                "artifacts": manifest_artifacts,
            }
        from apatch.trustchain_helper import TrustChainHelper

        tc = TrustChainHelper(root, auto_init=True)
        if not tc.has_trustchain():
            return {"ok": True, "skipped": True, "reason": "no trustchain"}
        payload = {
            "action": "engineering_pipeline",
            "intent": phase.get("intent") or manifest.get("intent"),
            "manifest": os.path.basename(manifest_path),
        }
        if manifest_artifacts:
            payload["artifacts"] = manifest_artifacts
            adr_ids = [a["id"] for a in manifest_artifacts if a.get("kind") == "adr"]
            if len(adr_ids) == 1:
                payload["adr"] = adr_ids[0]
        else:
            legacy_adr = phase.get("adr") or manifest.get("adr")
            if legacy_adr:
                payload["adr"] = legacy_adr
        ok = tc.commit_action("apatch", payload)
        return {"ok": bool(ok), "payload": payload}

    return {"ok": False, "error": f"unknown action: {action}"}
