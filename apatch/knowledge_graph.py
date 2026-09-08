"""Session knowledge graph (SPEC-KNOWLEDGE-GRAPH-1 / RFP-022 Phase 3)."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCHEMA_VERSION = 1


def _node(node_id: str, kind: str, **props: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": node_id, "kind": kind}
    out.update({k: v for k, v in props.items() if v is not None})
    return out


def _edge(frm: str, to: str, kind: str) -> Dict[str, str]:
    return {"from": frm, "to": to, "kind": kind}


def _load_diagnostics_artifact(root: Path, session_id: str) -> Optional[Dict[str, Any]]:
    path = root / ".apatch" / "diagnostics" / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_session_state(root: Path) -> Dict[str, Any]:
    from apatch.session_state import load_session_state

    return load_session_state(str(root)) or {}


def _session_matches(state: Dict[str, Any], session_id: str) -> bool:
    return session_id in (
        str(state.get("session_id") or ""),
        str(state.get("checkpoint") or ""),
    )


def _mutated_files_from_replay(root: str, session_id: str) -> Tuple[List[str], bool]:
    from apatch.replay import replay_session

    replay = replay_session(root, session_id=session_id, mode="summary")
    files: List[str] = []
    rolled_back = False
    if not replay.get("ok"):
        return files, rolled_back
    for item in replay.get("timeline") or []:
        report = item.get("report") or {}
        if report.get("verify_rollback"):
            rolled_back = True
        for entry in report.get("entries") or []:
            if entry.get("outcome") == "applied" and entry.get("target_file"):
                files.append(str(entry["target_file"]))
    return sorted(set(files)), rolled_back


def _requirement_nodes(
    artifacts: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], List[str]]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    req_ids: List[str] = []
    for art in artifacts or []:
        if isinstance(art, str) and art.startswith("spec:"):
            token = art.split(":", 1)[1]
            if "#" in token:
                spec_id, ref = token.split("#", 1)
            else:
                spec_id, ref = token, None
            art = {"kind": "spec", "id": spec_id, "ref": ref}
        if not isinstance(art, dict) or art.get("kind") != "spec":
            continue
        spec_id = str(art.get("id") or "")
        ref = art.get("ref")
        node_id = f"req:{spec_id}#{ref}" if ref else f"req:{spec_id}"
        nodes.append(_node(node_id, "Requirement", spec=spec_id, ref=ref))
        req_ids.append(node_id)
    return nodes, edges, req_ids


def knowledge_graph_for_session(session_id: str, target_dir: str = ".") -> Dict[str, Any]:
    """Build read-only session graph from diagnostics, session state, and apply reports."""
    root = Path(os.path.abspath(target_dir))
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "session_id required", "error_type": "INVALID_INPUT"}

    diag_doc = _load_diagnostics_artifact(root, sid)
    state = _load_session_state(root)
    if not diag_doc and not _session_matches(state, sid):
        replay_probe, _ = _mutated_files_from_replay(str(root), sid)
        if not replay_probe:
            return {
                "ok": False,
                "error": f"session not found: {sid}",
                "error_type": "SESSION_NOT_FOUND",
            }

    intent = None
    artifacts: List[Any] = []
    attested = False
    rolled_back = False
    verify_command = None
    if _session_matches(state, sid):
        intent = state.get("intent")
        artifacts = list(state.get("artifacts") or [])
        attested = bool(state.get("attested"))
        failure = state.get("failure") if isinstance(state.get("failure"), dict) else None
        if failure and str(failure.get("recommended_action") or "") == "rollback":
            rolled_back = True

    mutated_files, replay_rollback = _mutated_files_from_replay(str(root), sid)
    rolled_back = rolled_back or replay_rollback

    diagnostics = list((diag_doc or {}).get("diagnostics") or [])
    if diag_doc and not verify_command:
        for d in diagnostics:
            if d.get("verify_command"):
                verify_command = d.get("verify_command")
                break

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    seen_files: Set[str] = set()

    intent_id = f"intent:{sid}"
    nodes.append(_node(intent_id, "Intent", session_id=sid, label=intent or sid))

    req_nodes, _, req_ids = _requirement_nodes(artifacts)
    nodes.extend(req_nodes)
    for req_id in req_ids:
        edges.append(_edge(intent_id, req_id, "COVERS"))

    for diag in diagnostics:
        did = str(diag.get("id") or f"diag:{len(nodes)}")
        nodes.append(
            _node(
                did,
                "Diagnostic",
                type=diag.get("type"),
                source=diag.get("source"),
                message=diag.get("message"),
            )
        )
        edges.append(_edge(intent_id, did, "DIAGNOSED_AS"))
        for rel in (diag.get("edges") or {}).get("files") or []:
            fid = f"file:{rel}"
            if fid not in seen_files:
                nodes.append(_node(fid, "File", path=rel))
                seen_files.add(fid)
            edges.append(_edge(did, fid, "COVERS"))

    for rel in mutated_files:
        fid = f"file:{rel}"
        if fid not in seen_files:
            nodes.append(_node(fid, "File", path=rel))
            seen_files.add(fid)
        edges.append(_edge(intent_id, fid, "MUTATED"))

    if attested:
        att_id = f"attestation:{sid}"
        nodes.append(_node(att_id, "Attestation", session_id=sid))
        edges.append(_edge(intent_id, att_id, "ATTESTED_IN"))
    elif rolled_back:
        rb_id = f"rollback:{sid}"
        nodes.append(_node(rb_id, "Rollback", session_id=sid))
        edges.append(_edge(intent_id, rb_id, "ROLLBACK"))

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "session_id": sid,
        "verify_command": verify_command,
        "nodes": nodes,
        "edges": edges,
    }


def diagnostics_summary_workspace(target_dir: str = ".") -> Optional[Dict[str, Any]]:
    """Aggregate latest diagnostics artifact for project status views."""
    root = Path(os.path.abspath(target_dir))
    diag_dir = root / ".apatch" / "diagnostics"
    if not diag_dir.is_dir():
        return None
    candidates = sorted(
        diag_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    latest = candidates[0]
    try:
        doc = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    diags = doc.get("diagnostics") or []
    if not diags and not doc.get("diagnostic_count"):
        return None
    types = [str(d.get("type")) for d in diags if d.get("type")]
    top_type = Counter(types).most_common(1)[0][0] if types else None
    try:
        rel_path = str(latest.relative_to(root))
    except ValueError:
        rel_path = str(latest)
    return {
        "count": int(doc.get("diagnostic_count") or len(diags)),
        "top_type": top_type,
        "last_session_id": doc.get("session_id"),
        "artifact_path": rel_path,
    }


def knowledge_graph_enriched(target_dir: str = ".", *, session_id: str = "") -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response, load_session_state

    root = os.path.abspath(target_dir)
    sid = session_id.strip()
    if not sid:
        st = load_session_state(root)
        sid = str(st.get("checkpoint") or st.get("session_id") or "")
        if not sid:
            summary = diagnostics_summary_workspace(root)
            if summary and summary.get("last_session_id"):
                sid = str(summary["last_session_id"])
    if not sid:
        return enrich_tool_response(
            "apatch_knowledge_graph",
            {"ok": False, "error": "no session_id; pass session_id= or run a governed session"},
            target_dir=root,
        )
    return enrich_tool_response(
        "apatch_knowledge_graph",
        knowledge_graph_for_session(sid, root),
        target_dir=root,
    )