"""Deterministic needles scaffold — bridge spec_lint → plan/spec_run (RFP-024).

Primary path: ``apatch_spec_lint`` embeds ``plan_scaffold`` when ``passed``.
This module powers that embed and the optional ``apatch_spec_needles_scaffold`` MCP tool.

Does **not** LLM-generate ``find_text``/``replace_text``. Strict ownership SPECs use
only each requirement's declared ``owns:`` paths; legacy SPECs retain verify/prose
inference. Returns a schema v2 ``plan_scaffold`` skeleton.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set

_PATH_PREFIXES = (
    "apatch|tests|docs|src|app|scripts|manifests|packages|services|server|e2e|o_lang"
)
_PATH_EXTENSIONS = (
    "py|md|json|ts|tsx|js|jsx|yaml|yml|toml|hpp|h|cpp|cc|c|rs|go|cmake"
)
_PATH_IN_TEXT = re.compile(
    rf"(?<![`'{chr(34)}])"
    rf"((?:{_PATH_PREFIXES})/[\w./-]+\.(?:{_PATH_EXTENSIONS}))"
)
_PATH_IN_BACKTICKS = re.compile(
    rf"`((?:{_PATH_PREFIXES})/[\w./-]+\.(?:{_PATH_EXTENSIONS}))`"
)
_PYTEST_NODE = re.compile(r"(tests/[\w./-]+\.py(?:::[\w.]+)?)")


def infer_target_files_from_verify(verify: Optional[str]) -> List[str]:
    if not verify:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for m in _PYTEST_NODE.finditer(verify):
        path = m.group(1).split("::", 1)[0]
        if path not in seen:
            seen.add(path)
            out.append(path)
    _collect_paths(verify, seen, out)
    return out


def _collect_paths(text: str, seen: Set[str], out: List[str]) -> None:
    for pattern in (_PATH_IN_BACKTICKS, _PATH_IN_TEXT):
        for m in pattern.finditer(text or ""):
            path = m.group(1)
            if path not in seen:
                seen.add(path)
                out.append(path)


def infer_target_files_from_text(text: str) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    _collect_paths(text, seen, out)
    return out


def _requirement_body(spec_text: str, req_line: int) -> str:
    lines = spec_text.splitlines()
    if req_line <= 0 or req_line > len(lines):
        return ""
    start = req_line - 1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^##\s+\S", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def _needle_templates(target_files: List[str]) -> List[Dict[str, Any]]:
    primary = target_files[0] if target_files else "path/to/file.py"
    return [
        {
            "action": "replace",
            "target_file": primary,
            "find_text": "<literal from source>",
            "replace_text": "<replacement>",
        },
        {
            "action": "create",
            "target_file": primary if not target_files else "path/to/new_file.py",
            "content": "<file content>",
        },
    ]


def scaffold_execution_plan_entry(
    *,
    rk: str,
    title: str,
    verify: Optional[str],
    body_text: str,
    state: str,
    attested_needles: Optional[List[Dict[str, Any]]] = None,
    strict_ownership: bool = False,
    declared_ownership: Optional[List[str]] = None,
) -> Dict[str, Any]:
    target_files: List[str] = []
    if strict_ownership:
        for path in declared_ownership or []:
            if path not in target_files:
                target_files.append(path)
        sources: Dict[str, List[str]] = {"declared_ownership": list(target_files)}
    else:
        from_verify = infer_target_files_from_verify(verify)
        from_body = infer_target_files_from_text(body_text)
        sources = {"verify": from_verify, "spec_body": from_body}
        for path in from_verify + from_body:
            if path not in target_files:
                target_files.append(path)

    entry: Dict[str, Any] = {
        "rk": rk,
        "title": title,
        "verify": verify,
        "state": state,
        "target_files": target_files,
        "target_files_sources": sources,
        "needles": list(attested_needles or []),
        "needle_templates": _needle_templates(target_files),
        "needle_templates_note": (
            "Structural examples only — copy shape, not content; never auto-applied"
        ),
    }
    if attested_needles:
        entry["needles_source"] = "ledger_attested"
    if state in ("pending", "stale"):
        entry["agent_next"] = (
            f"Fill needles for {rk} → apatch_spec_plan_register(plan=…) "
            f"OR apatch_execute_next(spec=…, needles=[…]) "
            f"OR apatch_spec_run(requirements={{{rk!r}: {{needles: [...]}}}})"
        )
    return entry


def plan_scaffold_v2(
    spec_id: str,
    execution_entries: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    execution_plan: Dict[str, Any] = {}
    for rk, row in execution_entries.items():
        execution_plan[rk] = {
            "target_files": row.get("target_files") or [],
            "needles": row.get("needles") or [],
            "rationale": "",
        }
    return {
        "schema_version": 2,
        "spec": spec_id,
        "decision_plan": {
            "chosen_strategy": "",
            "rejected_alternatives": ["<option not taken>"],
            "assumptions": [],
            "risks": [],
        },
        "execution_plan": execution_plan,
    }


def manifest_scaffold_v1(
    spec_id: str,
    execution_entries: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    requirements: Dict[str, Any] = {}
    for rk, row in execution_entries.items():
        requirements[rk] = {
            "needles": row.get("needles") or [],
            "skip_if_attested": True,
            "target_files": row.get("target_files") or [],
        }
    return {
        "schema_version": 1,
        "spec": spec_id,
        "requirements": requirements,
        "options": {"chunk_rk_per_call": 0, "stop_on_first_failure": True},
    }


def _load_attested_needles_by_rk(target_dir: str, spec_id: str) -> Dict[str, List[Dict[str, Any]]]:
    try:
        from apatch.spec import _ledger_entries
        from apatch.spec_interference import load_needles_for_spec

        entries, _active = _ledger_entries(target_dir)
        needles, _sources = load_needles_for_spec(
            target_dir,
            spec_id,
            entries,
            include_attested=True,
            include_planned=False,
        )
    except Exception:
        return {}
    by_rk: Dict[str, List[Dict[str, Any]]] = {}
    for n in needles:
        rk = (n.get("requirement") or "").strip()
        if not rk:
            continue
        by_rk.setdefault(rk, []).append({k: v for k, v in n.items() if k != "requirement"})
    return by_rk


def spec_needles_scaffold_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    include_attested: bool = True,
    skip_lint: bool = False,
) -> Dict[str, Any]:
    from apatch.spec import parse_spec_file, spec_lint_workspace, spec_status_workspace

    root = os.path.abspath(target_dir)
    lint: Optional[Dict[str, Any]] = None
    if not skip_lint:
        lint = spec_lint_workspace(root, spec=spec, spec_path=spec_path)
        if not lint.get("ok") or not lint.get("passed"):
            return {
                "ok": False,
                "passed": False,
                "error": "spec lint failed — fix before needles scaffold",
                "lint": lint,
            }

    status = spec_status_workspace(root, spec=spec, spec_path=spec_path)
    if not status.get("ok"):
        return status

    spec_id = status["spec"]
    source_path = status.get("source_path")
    if not source_path or not os.path.isfile(source_path):
        return {"ok": False, "error": f"spec source not found for {spec_id}"}

    parsed = parse_spec_file(source_path, spec_id=spec_id)
    with open(source_path, encoding="utf-8") as handle:
        spec_text = handle.read()
    attested_by_rk = _load_attested_needles_by_rk(root, spec_id) if include_attested else {}

    req_by_id = {r.id: r for r in parsed.requirements}
    execution_entries: Dict[str, Dict[str, Any]] = {}
    pending: List[str] = []
    stale: List[str] = []

    for row in status.get("requirements") or []:
        rid = row.get("id")
        if not rid:
            continue
        req = req_by_id.get(rid)
        body = _requirement_body(spec_text, req.line if req else 0)
        attested = attested_by_rk.get(rid) if include_attested else None
        entry = scaffold_execution_plan_entry(
            rk=rid,
            title=(req.title if req else row.get("title")) or rid,
            verify=(req.verify if req else row.get("verify")),
            body_text=body,
            state=str(row.get("state") or "pending"),
            attested_needles=attested,
            strict_ownership=parsed.strict_ownership,
            declared_ownership=list(req.owns) if req else [],
        )
        execution_entries[rid] = entry
        st = entry["state"]
        if st == "pending":
            pending.append(rid)
        elif st == "stale":
            stale.append(rid)

    workflow = [
        f"apatch_spec_lint(spec={spec_id!r})  # format + rfp gates + plan_scaffold",
        "Fill decision_plan + execution_plan.{Rk}.needles (agent cognition — not auto-generated)",
        f"apatch_spec_plan_register(spec={spec_id!r}, plan=<schema v2 scaffold>)",
        f"apatch_spec_run(spec={spec_id!r}, plan='v1')",
        f"apatch_spec_status(spec={spec_id!r})",
    ]

    from apatch.agent_guidance import autonomy_boundary

    ab = autonomy_boundary()
    if len(pending) == 1:
        rk = pending[0]
        agent_next = (
            f"Cognition: read target_files for {rk}, author needles → "
            f"apatch_execute_next(spec={spec_id!r}, requirement='{spec_id}#{rk}', needles=[…])"
        )
    elif len(pending) > 1:
        agent_next = (
            f"{len(pending)} pending Rk ({', '.join(pending[:5])}{'…' if len(pending) > 5 else ''}) — "
            f"scaffold does NOT auto-generate needles. Per Rk: apatch_execute_next(…); "
            f"batch apatch_spec_run only when ALL pending have needles. See autonomy_boundary."
        )
    else:
        agent_next = f"apatch_spec_status(spec={spec_id!r})"

    return {
        "ok": True,
        "passed": True,
        "spec": spec_id,
        "source_path": source_path,
        "pending": pending,
        "stale": stale,
        "requirements": list(execution_entries.values()),
        "execution_plan": execution_entries,
        "plan_scaffold": plan_scaffold_v2(spec_id, execution_entries),
        "manifest_scaffold": manifest_scaffold_v1(spec_id, execution_entries),
        "workflow": workflow,
        "autonomy_boundary": ab,
        "scaffold_vs_templates": {
            "plan_scaffold": "RFP-011 schema v2 skeleton — fill execution_plan.{Rk}.needles",
            "needle_templates": "Per-Rk structural examples in requirements[] — NOT applied",
            "needles": "Empty until agent authors mutation dicts from source",
            "not_codegen": ab["scaffold_does_not"][0],
        },
        "agent_next": agent_next,
        "note": ab["scaffold_does_not"][0],
    }


def spec_needles_scaffold_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.agent_guidance import enrich_tool_response

    root = os.path.abspath(target_dir)
    out = spec_needles_scaffold_workspace(root, **kwargs)
    return enrich_tool_response("apatch_spec_needles_scaffold", out, target_dir=root)