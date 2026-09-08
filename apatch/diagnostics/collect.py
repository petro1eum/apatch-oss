"""Collect unified diagnostics from verify failures."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from apatch.build_diagnose import looks_like_compiler_output, run_build_diagnose
from apatch.diagnostics.adapters.clang import adapt_clang_log
from apatch.diagnostics.adapters.pytest import adapt_pytest_log
from apatch.diagnostics.adapters.trust import adapt_failure_info
from apatch.diagnostics.artifacts import write_session_diagnostics
from apatch.diagnostics.edges import merge_edges_into_diagnostic
from apatch.failure_taxonomy import classify_failure


def _session_id_from_result(result: Dict[str, Any]) -> Optional[str]:
    sid = result.get("session_id")
    if sid:
        return str(sid)
    session = result.get("session")
    if isinstance(session, dict) and session.get("session_id"):
        return str(session["session_id"])
    return None


def _governed_session_id(target_dir: str) -> Optional[str]:
    """Active governed session from session_state when verify result omits session_id."""
    from apatch.session_state import load_session_state

    st = load_session_state(target_dir)
    if not st or st.get("ended_at"):
        return None
    for key in ("session_id", "checkpoint"):
        val = st.get(key)
        if val:
            return str(val)
    return None


def _resolve_session_id(
    result: Dict[str, Any],
    target_dir: str,
    session_id: Optional[str],
) -> Optional[str]:
    return session_id or _session_id_from_result(result) or _governed_session_id(target_dir)


def collect_diagnostics(
    result: Dict[str, Any],
    target_dir: str,
    *,
    log_text: str = "",
    verify: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_name: str = "apatch_verify_run",
    write_artifacts: bool = True,
) -> List[Dict[str, Any]]:
    """Parse verify output and failure metadata into unified Diagnostic dicts."""
    sid = _resolve_session_id(result, target_dir, session_id)
    collected: List[Dict[str, Any]] = []
    text = log_text or result.get("verify_output") or ""

    if text and looks_like_compiler_output(text):
        for d in adapt_clang_log(text, target_dir):
            if sid:
                d["session_id"] = sid
            if verify:
                d["verify_command"] = verify
            collected.append(d)
    elif text and "FAILED " in text and "::" in text:
        action = result.get("recommended_action") or "fix_forward"
        for d in adapt_pytest_log(text, recommended_action=action):
            if sid:
                d["session_id"] = sid
            if verify:
                d["verify_command"] = verify
            collected.append(d)

    if result.get("ok") is False or result.get("verify_rollback"):
        fi = classify_failure({**result, "tool": tool_name}, tool_name)
        if fi and not any(d.get("source") == "pytest" for d in collected):
            for d in adapt_failure_info(fi, session_id=sid):
                if verify and "verify_command" not in d:
                    d["verify_command"] = verify
                collected.append(d)

    for d in collected:
        merge_edges_into_diagnostic(d, target_dir)

    if collected:
        result["diagnostics"] = collected
        result["diagnostic_count"] = len(collected)
        result["agent_next"] = (
            "Read diagnostics[0].recommended_action and diagnostics[].suggestions; "
            "craft needles via apatch_generate_batch — do not parse stderr."
        )
        artifact_path = write_session_diagnostics(
            target_dir, sid or "unknown", collected, write_artifacts=write_artifacts
        )
        if artifact_path:
            result["diagnostics_artifact"] = str(artifact_path)
            for d in collected:
                ev = d.setdefault("evidence", {})
                paths = list(ev.get("artifact_paths") or [])
                paths.append(str(artifact_path))
                ev["artifact_paths"] = paths

    return collected


def enrich_verify_failure(
    result: Dict[str, Any],
    target_dir: str,
    *,
    log_text: str,
    verify: Optional[str] = None,
    write_artifacts: bool = True,
) -> Dict[str, Any]:
    """Unified diagnostics[] + legacy build_diagnose block for clang logs."""
    collect_diagnostics(
        result,
        target_dir,
        log_text=log_text,
        verify=verify,
        write_artifacts=write_artifacts,
    )
    if looks_like_compiler_output(log_text):
        legacy = run_build_diagnose(
            target_dir,
            log_text=log_text,
            verify=verify,
            write_artifacts=False,
        )
        result["build_diagnose"] = {
            "diagnostic_count": legacy.get("diagnostic_count", 0),
            "diagnostics": legacy.get("diagnostics", []),
            "build_ok": legacy.get("build_ok"),
        }
        if legacy.get("artifacts"):
            result["build_diagnose_artifacts"] = legacy["artifacts"]
    return result