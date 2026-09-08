"""RFP-018 — compiler output → structured diagnostics + advisory suggestions."""

from __future__ import annotations

import difflib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

_CLANG_ERROR_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+)(?::\d+)?:\s*"
    r"(?P<severity>error|warning|fatal error):\s*(?P<message>.+)$",
    re.MULTILINE,
)
_MISSING_MEMBER_RE = re.compile(
    r"no member named ['\"](?P<member>[^'\"]+)['\"] in ['\"](?P<type>[^'\"]+)['\"]",
)


def parse_compiler_output(text: str) -> List[Dict[str, Any]]:
    """Parse clang/gcc ``file:line: error:`` lines into diagnostic dicts."""
    diags: List[Dict[str, Any]] = []
    for match in _CLANG_ERROR_RE.finditer(text or ""):
        severity = match.group("severity")
        if severity not in ("error", "fatal error"):
            continue
        message = match.group("message").strip()
        entry: Dict[str, Any] = {
            "file": match.group("file").strip(),
            "line": int(match.group("line")),
            "severity": "error",
            "message": message,
            "raw": match.group(0).strip(),
        }
        mm = _MISSING_MEMBER_RE.search(message)
        if mm:
            entry["error_type"] = "missing_member"
            entry["member"] = mm.group("member")
            entry["type"] = mm.group("type")
        diags.append(entry)
    return diags


def _short_type_name(qualified: str) -> str:
    return qualified.split("::")[-1].strip()


def _find_type_header(root: str, class_name: str) -> Optional[Dict[str, Any]]:
    root_path = Path(root)
    for pattern in ("*.h", "*.hpp", "*.hh"):
        for path in root_path.rglob(pattern):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(rf"\b(class|struct)\s+{re.escape(class_name)}\b", text):
                try:
                    rel = str(path.relative_to(root_path))
                except ValueError:
                    rel = str(path)
                return {"file": rel, "abs_path": str(path), "source": text}
    return None


def extract_cpp_class_members(source: str, class_name: str) -> List[str]:
    """Regex MVP: method names declared inside a class/struct body."""
    decl = re.search(
        rf"\b(class|struct)\s+{re.escape(class_name)}\b[^{{]*\{{",
        source,
    )
    if not decl:
        return []
    start = decl.end()
    depth = 1
    idx = start
    while idx < len(source) and depth > 0:
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        idx += 1
    body = source[start : idx - 1]
    members: List[str] = []
    method_re = re.compile(
        r"^\s*(?:virtual\s+)?(?:[\w:<>,\s*&]+\s+)+(\w+)\s*\([^;]*\)\s*"
        r"(?:const)?\s*(?:override)?\s*;",
        re.MULTILINE,
    )
    for m in method_re.finditer(body):
        name = m.group(1)
        if name not in {"if", "for", "while", "switch"}:
            members.append(name)
    return sorted(set(members))


def _fuzzy_best(member: str, candidates: List[str]) -> tuple[Optional[str], float]:
    best_name: Optional[str] = None
    best_ratio = 0.0
    for cand in candidates:
        ratio = difflib.SequenceMatcher(None, member.lower(), cand.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = cand
    return best_name, best_ratio


def _suggestions_for_missing_member(
    member: str,
    qualified_type: str,
    type_def: Optional[Dict[str, Any]],
    members: List[str],
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    short = _short_type_name(qualified_type)
    header = (type_def or {}).get("file")
    best, ratio = _fuzzy_best(member, members)
    if best and ratio >= 0.5 and best != member:
        suggestions.append(
            {
                "strategy": "rename_call_site",
                "confidence": round(ratio, 2),
                "detail": f"Call site may mean `{best}` instead of `{member}`",
                "suggested_member": best,
            }
        )
    suggestions.append(
        {
            "strategy": "restore_api",
            "confidence": 0.85,
            "detail": f"Add `{member}` to `{short}` in {header or 'header'}",
            "type": qualified_type,
            "member": member,
        }
    )
    if not best or ratio < 0.4:
        suggestions.append(
            {
                "strategy": "adapter_or_literal",
                "confidence": 0.5,
                "detail": (
                    f"No close member for `{member}` — use literal or thin adapter "
                    "at call site"
                ),
            }
        )
    return suggestions


def _enrich_diagnostic(diag: Dict[str, Any], root: str) -> Dict[str, Any]:
    out = dict(diag)
    if diag.get("error_type") != "missing_member":
        return out
    qualified = diag.get("type") or ""
    class_name = _short_type_name(qualified)
    type_def = _find_type_header(root, class_name)
    if type_def:
        members = extract_cpp_class_members(type_def["source"], class_name)
        out["type_definition"] = {"file": type_def["file"], "members": members}
        out["suggestions"] = _suggestions_for_missing_member(
            diag["member"], qualified, type_def, members
        )
    else:
        out["type_definition"] = None
        out["suggestions"] = _suggestions_for_missing_member(
            diag["member"], qualified, None, []
        )
    return out


def looks_like_compiler_output(text: str) -> bool:
    """True when verify stderr/stdout likely contains compiler diagnostics."""
    if not text:
        return False
    if _CLANG_ERROR_RE.search(text):
        return True
    return bool(
        re.search(r":\d+:\d*:\s*(?:error|fatal error):", text)
        or re.search(r":\d+:\s*(?:error|fatal error):", text)
    )


def enrich_verify_failure(
    result: Dict[str, Any],
    target_dir: str,
    *,
    log_text: str,
    verify: Optional[str] = None,
    write_artifacts: bool = True,
) -> Dict[str, Any]:
    """Attach unified diagnostics[] (SPEC-DIAGNOSTIC-GRAPH-1) + legacy build_diagnose."""
    from apatch.diagnostics.collect import enrich_verify_failure as _collect_enrich

    return _collect_enrich(
        result,
        target_dir,
        log_text=log_text,
        verify=verify,
        write_artifacts=write_artifacts,
    )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_build_diagnose(
    target_dir: str = ".",
    *,
    verify: Optional[str] = None,
    log_text: Optional[str] = None,
    write_artifacts: bool = True,
) -> Dict[str, Any]:
    """Run verify (or parse ``log_text``) and emit structured diagnostics."""
    root = os.path.abspath(target_dir)
    verify_cmd = verify
    build_ok = True

    if log_text is None:
        if not verify_cmd:
            from apatch.doctor import run_doctor

            doc = run_doctor(root)
            verify_cmd = doc.get("recommended_verify_resolved") or doc.get(
                "recommended_verify"
            )
        if not verify_cmd:
            return {
                "ok": False,
                "error": "provide log_text= or verify= command",
                "error_type": "RUNTIME_TRANSITION",
                "recoverable": True,
                "recommended_action": "reduce_scope",
                "workspace": root,
            }
        from apatch.strip_pipeline import _run_verify

        build_ok, err = _run_verify(verify_cmd, root)
        log_text = err or ""
    else:
        build_ok = _CLANG_ERROR_RE.search(log_text or "") is None

    raw_diags = parse_compiler_output(log_text or "")
    diagnostics = [_enrich_diagnostic(d, root) for d in raw_diags]
    build_ok = bool(build_ok and len(diagnostics) == 0)

    result: Dict[str, Any] = {
        "ok": True,
        "workspace": root,
        "build_ok": build_ok,
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
        "verify": verify_cmd,
        "agent_next": (
            "Read diagnostics[].suggestions; craft needles via apatch_generate_batch "
            "(restore_api / rename_call_site) — suggestions are advisory, not auto-applied."
            if diagnostics
            else "Build clean — no compiler diagnostics."
        ),
    }

    if write_artifacts:
        apatch_dir = os.path.join(root, ".apatch")
        os.makedirs(apatch_dir, exist_ok=True)
        build_log_path = os.path.join(apatch_dir, "build_log.json")
        diag_path = os.path.join(apatch_dir, "diagnostics.json")
        build_doc = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": _utcnow_iso(),
            "build_ok": build_ok,
            "verify": verify_cmd,
            "log_text": log_text,
        }
        diag_doc = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": _utcnow_iso(),
            "diagnostic_count": len(diagnostics),
            "diagnostics": diagnostics,
        }
        with open(build_log_path, "w", encoding="utf-8") as fh:
            json.dump(build_doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        with open(diag_path, "w", encoding="utf-8") as fh:
            json.dump(diag_doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        result["artifacts"] = {
            "build_log": build_log_path,
            "diagnostics": diag_path,
        }

    return result
