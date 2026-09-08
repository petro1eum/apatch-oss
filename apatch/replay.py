"""R57 — deterministic replay of apply_session chunk history."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apatch.apply_session import default_session_path, load_session

REPLAY_LOG_REL = os.path.join(".apatch", "replay_log.json")


def _session_abs(target_dir: str, session_path: Optional[str]) -> str:
    if session_path:
        if os.path.isabs(session_path):
            return session_path
        return os.path.join(os.path.abspath(target_dir), session_path)
    return default_session_path(target_dir)


def _load_chunk_report(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"raw": data}
    except (OSError, json.JSONDecodeError):
        return None


def replay_session(
    target_dir: str = ".",
    *,
    session_id: Optional[str] = None,
    session_path: Optional[str] = None,
    mode: str = "deterministic",
) -> Dict[str, Any]:
    """Reconstruct apply_session timeline from persisted chunk reports."""
    root = os.path.abspath(target_dir)
    abs_session = _session_abs(root, session_path)
    session = load_session(abs_session)
    if not session:
        return {
            "ok": False,
            "error": f"no apply session at {abs_session}",
            "hint": "run apatch_apply_session first",
        }

    checkpoints: List[str] = list(session.get("checkpoints") or [])
    last_ckpt = session.get("last_checkpoint")
    if session_id:
        valid = set(checkpoints)
        if last_ckpt:
            valid.add(last_ckpt)
        if session_id not in valid:
            return {
                "ok": False,
                "error": f"session_id not found: {session_id}",
                "checkpoints": checkpoints,
                "last_checkpoint": last_ckpt,
            }

    chunks: List[List[int]] = session.get("chunks") or []
    chunk_reports: List[str] = list(session.get("chunk_reports") or [])
    timeline: List[Dict[str, Any]] = []
    totals = {"applied": 0, "failed": 0, "skipped": 0, "rolled_back": 0}

    for idx, report_path in enumerate(chunk_reports):
        steps = chunks[idx] if idx < len(chunks) else []
        report = _load_chunk_report(report_path)
        chunk_result: Dict[str, Any] = {}
        if report:
            chunk_result = {
                "applied": report.get("applied", report.get("summary", {}).get("applied", 0)),
                "failed": report.get("failed", report.get("summary", {}).get("failed", 0)),
                "skipped": report.get("skipped", report.get("summary", {}).get("skipped", 0)),
                "rolled_back": report.get("rolled_back", 0),
                "verify_rollback": report.get("verify_rollback", False),
            }
            for key in totals:
                totals[key] += int(chunk_result.get(key) or 0)

        ckpt = checkpoints[idx] if idx < len(checkpoints) else None
        timeline.append(
            {
                "chunk_index": idx,
                "step_indices": steps,
                "checkpoint": ckpt,
                "report_path": report_path,
                "chunk_result": chunk_result,
                "report": report,
            }
        )

    record: Dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "deterministic": mode == "deterministic",
        "session_path": abs_session,
        "session_id": session_id or last_ckpt,
        "logs_path": session.get("logs_path"),
        "target_dir": session.get("target_dir") or root,
        "chunks_total": len(chunks),
        "chunks_recorded": len(chunk_reports),
        "chunk_index_at_replay": session.get("chunk_index", 0),
        "checkpoints": checkpoints,
        "last_checkpoint": last_ckpt,
        "timeline": timeline,
        "totals": totals,
        "replay_at": datetime.now(timezone.utc).isoformat(),
    }

    if mode == "deterministic":
        record["patch_application_order"] = [
            {"chunk": t["chunk_index"], "steps": t["step_indices"]} for t in timeline
        ]
        record["verify_outputs"] = [
            {
                "chunk": t["chunk_index"],
                "verify_rollback": (t.get("chunk_result") or {}).get("verify_rollback"),
            }
            for t in timeline
        ]

    log_path = os.path.join(root, REPLAY_LOG_REL)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
    record["replay_log_path"] = log_path
    return record
