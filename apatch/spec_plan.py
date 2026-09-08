"""Plan as artifact (RFP-011 / SPEC-PLAN-ARTIFACT-1). Schema v1 + v2."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from apatch.apatch_paths import normalize_rel as _normalize_rel
from apatch.spec import _load_spec
from apatch.spec_run import _validate_needle, manifest_sha256

PLAN_SCHEMA_V1 = 1
PLAN_SCHEMA_V2 = 2
PLAN_SCHEMA_VERSION = 2  # alias for tests expecting constant name
ERROR_PLAN_DRIFT = "PLAN_DRIFT"
_PLANS_DIR = os.path.join(".apatch", "plans")
_V2_UPGRADE_WARNING = (
    "upgrade to schema_version 2 for decision_plan (Engineering Truth)"
)


def _plans_abs(root: str) -> str:
    return os.path.join(os.path.abspath(root), _PLANS_DIR)


def _plan_id(spec_id: str, version: int) -> str:
    return f"plan:{spec_id}@v{version}"


def _canonical_plan_bytes(plan: Dict[str, Any]) -> bytes:
    return json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")


def plan_sha256(plan: Dict[str, Any]) -> str:
    return manifest_sha256(plan)


def _spec_hash(spec_path: str) -> Optional[str]:
    if not spec_path or not os.path.isfile(spec_path):
        return None
    with open(spec_path, "rb") as fh:
        return f"sha256:{hashlib.sha256(fh.read()).hexdigest()}"


def _plan_spec_id(plan: Dict[str, Any]) -> Optional[str]:
    sid = plan.get("spec") or plan.get("spec_id")
    return str(sid).strip() if sid else None


def execution_plan_section(plan: Dict[str, Any]) -> Dict[str, Any]:
    ep = plan.get("execution_plan")
    if isinstance(ep, dict) and ep:
        return ep
    reqs = plan.get("requirements")
    if isinstance(reqs, dict):
        return reqs
    return {}


def decision_plan_section(plan: Dict[str, Any]) -> Dict[str, Any]:
    dp = plan.get("decision_plan")
    if isinstance(dp, dict):
        return dp
    out: Dict[str, Any] = {}
    if plan.get("rationale"):
        out["rationale"] = plan["rationale"]
    return out


def _decision_plan_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    dp = decision_plan_section(plan)
    cs = str(dp.get("chosen_strategy") or "").strip()
    ra = dp.get("rejected_alternatives") or []
    if not isinstance(ra, list):
        ra = []
    truncated = cs if len(cs) <= 120 else cs[:117] + "..."
    return {
        "chosen_strategy": truncated,
        "rejected_count": len(ra),
    }


def _lint_rk_entry(rk: str, entry: Any, errors: List[str]) -> None:
    if not isinstance(entry, dict):
        errors.append(f"{rk}: requirement entry must be object")
        return
    needles = entry.get("needles")
    if needles is None:
        needles = []
    if not isinstance(needles, list):
        errors.append(f"{rk}: needles must be array")
        return
    for i, needle in enumerate(needles):
        if not isinstance(needle, dict):
            errors.append(f"{rk}: needle[{i}] must be object")
            continue
        err = _validate_needle(needle, f"{rk}[{i}]")
        if err:
            errors.append(err)
    tf = entry.get("target_files")
    if tf is None:
        tf = entry.get("files")
    if tf is not None and not isinstance(tf, list):
        errors.append(f"{rk}: target_files must be array")


def lint_plan_dict(
    plan: Dict[str, Any],
    *,
    spec_id: str,
    requirement_ids: List[str],
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    if not plan:
        return {
            "ok": False,
            "errors": ["empty plan"],
            "warnings": [],
            "schema_version": None,
            "decision_plan_present": False,
            "execution_rk_count": 0,
        }

    schema = plan.get("schema_version", PLAN_SCHEMA_V1)
    if schema not in (PLAN_SCHEMA_V1, PLAN_SCHEMA_V2):
        errors.append(f"schema_version must be {PLAN_SCHEMA_V1} or {PLAN_SCHEMA_V2}")

    pid = _plan_spec_id(plan)
    if pid and pid != spec_id:
        errors.append(f"plan spec {pid!r} != {spec_id!r}")

    if schema == PLAN_SCHEMA_V2:
        dp = plan.get("decision_plan")
        if not isinstance(dp, dict):
            errors.append("decision_plan must be an object")
        else:
            if not str(dp.get("chosen_strategy") or "").strip():
                errors.append("decision_plan.chosen_strategy must be non-empty string")
            ra = dp.get("rejected_alternatives")
            if not isinstance(ra, list) or not ra:
                errors.append(
                    "decision_plan.rejected_alternatives must be a non-empty array"
                )
            elif not all(isinstance(x, str) and x.strip() for x in ra):
                errors.append(
                    "decision_plan.rejected_alternatives entries must be non-empty strings"
                )
    else:
        warnings.append(_V2_UPGRADE_WARNING)
        dp = decision_plan_section(plan)
        if not str(dp.get("chosen_strategy") or "").strip():
            warnings.append(
                "schema v1: missing decision_plan.chosen_strategy; "
                "top-level rationale only is weak Engineering Truth"
            )
        if not dp.get("rejected_alternatives"):
            warnings.append(
                "schema v1: missing rejected_alternatives; "
                "future agents cannot see discarded options"
            )

    exec_plan = execution_plan_section(plan)
    if not exec_plan:
        errors.append("execution_plan (or requirements) must be a non-empty object")

    spec_set = set(requirement_ids)
    for rk, entry in exec_plan.items():
        if rk not in spec_set:
            warnings.append(f"unknown requirement {rk}")
        _lint_rk_entry(rk, entry, errors)

    decision_present = schema == PLAN_SCHEMA_V2 and isinstance(
        plan.get("decision_plan"), dict
    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "schema_version": schema,
        "decision_plan_present": decision_present,
        "execution_rk_count": len(exec_plan),
    }


def spec_plan_lint_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    if plan is None:
        return {"ok": False, "error": "plan dict required"}
    req_ids = [r.id for r in parsed.requirements]
    lint = lint_plan_dict(plan, spec_id=parsed.id, requirement_ids=req_ids)
    return {
        "ok": lint["ok"],
        "spec": parsed.id,
        "passed": lint["ok"],
        "errors": lint["errors"],
        "warnings": lint["warnings"],
        "schema_version": lint["schema_version"],
        "decision_plan_present": lint["decision_plan_present"],
        "execution_rk_count": lint["execution_rk_count"],
    }


def _list_local_plans(root: str, spec_id: str) -> List[Dict[str, Any]]:
    base = _plans_abs(root)
    if not os.path.isdir(base):
        return []
    out: List[Dict[str, Any]] = []
    prefix = f"{spec_id}.v"
    for name in sorted(os.listdir(base)):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        m = re.match(rf"{re.escape(spec_id)}\.v(\d+)\.json$", name)
        if not m:
            continue
        path = os.path.join(base, name)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        out.append({"version": int(m.group(1)), "path": path, "plan": doc})
    out.sort(key=lambda x: x["version"])
    return out


def _next_plan_version(root: str, spec_id: str) -> int:
    plans = _list_local_plans(root, spec_id)
    if not plans:
        return 1
    return plans[-1]["version"] + 1


def _save_local_plan(root: str, spec_id: str, version: int, doc: Dict[str, Any]) -> str:
    base = _plans_abs(root)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"{spec_id}.v{version}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    return path


def register_plan_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    if plan is None:
        return {"ok": False, "error": "plan dict required"}
    root = os.path.abspath(target_dir)
    req_ids = [r.id for r in parsed.requirements]
    lint = lint_plan_dict(plan, spec_id=parsed.id, requirement_ids=req_ids)
    if not lint["ok"]:
        return {"ok": False, "error": "; ".join(lint["errors"]), "lint": lint}

    spec_hash = _spec_hash(parsed.source_path or "")
    version = _next_plan_version(root, parsed.id)
    prev = version - 1
    doc = dict(plan)
    doc.setdefault("schema_version", plan.get("schema_version", PLAN_SCHEMA_V1))
    doc["spec"] = parsed.id
    if doc.get("spec_id"):
        doc["spec_id"] = parsed.id
    doc["spec_content_hash"] = spec_hash
    if prev >= 1:
        doc["supersedes"] = prev
    psha = plan_sha256(doc)
    plan_id = _plan_id(parsed.id, version)
    summary = _decision_plan_summary(doc)

    op_id: Optional[str] = None
    from apatch.trustchain_helper import TrustChainHelper

    tc = TrustChainHelper(root, auto_init=False)
    if tc.has_trustchain():
        payload = {
            "action": "plan_register",
            "plan_id": plan_id,
            "plan_sha256": psha,
            "spec": parsed.id,
            "spec_content_hash": spec_hash,
            "version": version,
            "supersedes": doc.get("supersedes"),
            "schema_version": doc.get("schema_version"),
            "decision_plan_summary": summary,
            "artifacts": [{"kind": "plan", "id": f"{parsed.id}@v{version}"}],
        }
        if tc.commit_action("apatch_plan", payload):
            for row in reversed(list(tc.iter_ledger_entries())):
                pl = row.get("payload") or {}
                if pl.get("plan_id") == plan_id:
                    op_id = row.get("id") or row.get("signature")
                    break

    local_path = _save_local_plan(root, parsed.id, version, doc)
    return {
        "ok": True,
        "plan_id": plan_id,
        "version": version,
        "plan_sha256": psha,
        "spec_content_hash": spec_hash,
        "local_path": local_path,
        "op_id": op_id,
        "supersedes": doc.get("supersedes"),
        "decision_plan_summary": summary,
        "schema_version": doc.get("schema_version"),
    }


def resolve_plan_version(
    root: str,
    spec_id: str,
    plan_ref: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
    """Load plan by 'latest', 'v3', or '3'."""
    plans = _list_local_plans(root, spec_id)
    if not plans:
        return None, None, f"no registered plan for {spec_id}"
    ref = (plan_ref or "latest").strip().lower()
    if ref == "latest":
        chosen = plans[-1]
        return chosen["plan"], chosen["version"], None
    if ref.startswith("v"):
        ref = ref[1:]
    try:
        ver = int(ref)
    except ValueError:
        return None, None, f"invalid plan ref: {plan_ref!r}"
    for p in plans:
        if p["version"] == ver:
            return p["plan"], ver, None
    return None, None, f"plan v{ver} not found for {spec_id}"


def plan_to_requirements(plan: Dict[str, Any]) -> Dict[str, Any]:
    reqs: Dict[str, Any] = {}
    for rk, entry in execution_plan_section(plan).items():
        if isinstance(entry, dict):
            reqs[rk] = {"needles": list(entry.get("needles") or [])}
    return reqs


def _field_delta(old: Dict[str, Any], new: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    delta: Dict[str, Any] = {}
    for key in keys:
        ov = old.get(key)
        nv = new.get(key)
        if ov != nv:
            delta[key] = {"from": ov, "to": nv}
    return delta


def plan_diff_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    from_version: int = 1,
    to_version: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        parsed = _load_spec(target_dir, spec=spec)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    root = os.path.abspath(target_dir)
    plans = _list_local_plans(root, parsed.id)
    by_ver = {p["version"]: p["plan"] for p in plans}
    if from_version not in by_ver:
        return {"ok": False, "error": f"from_version v{from_version} not found"}
    if to_version is None:
        to_version = max(by_ver)
    if to_version not in by_ver:
        return {"ok": False, "error": f"to_version v{to_version} not found"}
    old = by_ver[from_version]
    new = by_ver[to_version]

    old_dp = decision_plan_section(old)
    new_dp = decision_plan_section(new)
    decision_plan_delta = _field_delta(
        old_dp,
        new_dp,
        ["chosen_strategy", "rejected_alternatives", "assumptions", "risks", "rationale"],
    )

    old_exec = execution_plan_section(old)
    new_exec = execution_plan_section(new)
    execution_plan_delta: Dict[str, Any] = {}
    all_rk = set(old_exec) | set(new_exec)
    for rk in sorted(all_rk):
        o = old_exec.get(rk) or {}
        n = new_exec.get(rk) or {}
        if o != n:
            execution_plan_delta[rk] = {"from": o, "to": n}

    return {
        "ok": True,
        "spec": parsed.id,
        "from_version": from_version,
        "to_version": to_version,
        "decision_plan_delta": decision_plan_delta,
        "execution_plan_delta": execution_plan_delta,
        "delta": execution_plan_delta,
    }


def show_plan_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    version: str = "latest",
) -> Dict[str, Any]:
    try:
        parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    root = os.path.abspath(target_dir)
    loaded, ver, err = resolve_plan_version(root, parsed.id, version)
    if err or not loaded or ver is None:
        return {"ok": False, "error": err or "plan not found"}
    plan_id = _plan_id(parsed.id, ver)
    return {
        "ok": True,
        "spec": parsed.id,
        "plan_id": plan_id,
        "version": ver,
        "plan_sha256": plan_sha256(loaded),
        "schema_version": loaded.get("schema_version", PLAN_SCHEMA_V1),
        "decision_plan": decision_plan_section(loaded),
        "execution_plan": execution_plan_section(loaded),
        "plan": loaded,
    }


def planned_files_for_rk(plan: Dict[str, Any], rk: str) -> List[str]:
    entry = execution_plan_section(plan).get(rk) or {}
    explicit = entry.get("target_files")
    if explicit is None:
        explicit = entry.get("files")
    if isinstance(explicit, list) and explicit:
        return sorted({_normalize_rel(str(f)) for f in explicit if f})
    files: set = set()
    for needle in entry.get("needles") or []:
        if not isinstance(needle, dict):
            continue
        tf = needle.get("target_file") or needle.get("source_file")
        if tf:
            files.add(_normalize_rel(str(tf)))
    return sorted(files)


def spec_plan_lint_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_plan_lint", spec_plan_lint_workspace(root, **kwargs), target_dir=root
    )


def register_plan_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_plan_register",
        register_plan_workspace(root, **kwargs),
        target_dir=root,
    )


def plan_diff_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_plan_diff", plan_diff_workspace(root, **kwargs), target_dir=root
    )


def show_plan_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    return enrich_tool_response(
        "apatch_spec_plan_show", show_plan_workspace(root, **kwargs), target_dir=root
    )
