"""R55 — dependency-driven execution graph from engineering-pipeline manifests."""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Any, Dict, List, Optional

from apatch.pipeline_run import load_pipeline_manifest
from apatch.workflows import WorkflowError

GRAPH_REL = os.path.join(".apatch", "execution_graph.json")

ACTION_TO_NODE = {
    "plan": "plan",
    "apply": "apply_session",
    "verify_shell": "verify",
    "verify_semantic": "semantic_verify",
    "db_check": "db_check",
    "db_safety": "db_safety",
    "db_revision": "db_revision",
    "arch_check": "arch_check",
    "impact": "impact",
    "trustchain_history": "context",
    "trustchain_intent": "trustchain_commit",
    "index_build": "index",
}

POST_APPLY_NODES = frozenset({
    "verify",
    "semantic_verify",
    "db_check",
    "db_safety",
    "db_revision",
    "arch_check",
    "impact",
    "index",
    "trustchain_commit",
})

DEFAULT_GRAPH: Dict[str, Any] = {
    "nodes": ["plan", "apply_session", "verify", "db_check", "arch_check", "semantic_verify"],
    "edges": [
        ["plan", "apply_session"],
        ["apply_session", "verify"],
        ["apply_session", "db_check"],
        ["apply_session", "arch_check"],
        ["verify", "semantic_verify"],
    ],
}


def _graph_path(root: str, graph_path: Optional[str]) -> str:
    rel = graph_path or GRAPH_REL
    if os.path.isabs(rel):
        return rel
    return os.path.join(os.path.abspath(root), rel)


def topological_order(nodes: List[str], edges: List[List[str]]) -> List[str]:
    """Kahn topological sort; raises WorkflowError on cycle."""
    incoming: Dict[str, int] = {n: 0 for n in nodes}
    adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for src, dst in edges:
        if src not in incoming or dst not in incoming:
            continue
        adj[src].append(dst)
        incoming[dst] += 1
    queue = deque(n for n in nodes if incoming[n] == 0)
    order: List[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for nxt in adj[node]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(nodes):
        raise WorkflowError("execution graph has a cycle or disconnected nodes")
    return order


def build_execution_graph(manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build nodes, edges, and phase bindings from a pipeline manifest."""
    if not manifest:
        out = dict(DEFAULT_GRAPH)
        out["phase_map"] = {}
        out["order"] = topological_order(out["nodes"], out["edges"])
        return out

    phases = manifest.get("phases") or []
    nodes: List[str] = []
    phase_map: Dict[str, List[Dict[str, Any]]] = {}
    for ph in phases:
        action = (ph or {}).get("action") or ""
        node = ACTION_TO_NODE.get(action, action or "phase")
        if node not in nodes:
            nodes.append(node)
        phase_map.setdefault(node, []).append(ph)

    if not nodes:
        out = dict(DEFAULT_GRAPH)
        out["phase_map"] = {}
        out["order"] = topological_order(out["nodes"], out["edges"])
        return out

    edges: List[List[str]] = []
    if "context" in nodes:
        for n in nodes:
            if n not in ("context",):
                edges.append(["context", n])
    if "plan" in nodes:
        targets = [n for n in nodes if n not in ("context", "plan")]
        for n in targets:
            if n == "apply_session":
                edges.append(["plan", "apply_session"])
            elif "apply_session" not in nodes and n != "plan":
                edges.append(["plan", n])
    if "apply_session" in nodes:
        for n in nodes:
            if n in POST_APPLY_NODES:
                edges.append(["apply_session", n])
    elif "plan" in nodes:
        for n in nodes:
            if n in POST_APPLY_NODES:
                edges.append(["plan", n])

    # Linear fallback when manifest has custom actions only
    if not edges and len(nodes) > 1:
        prev = nodes[0]
        for n in nodes[1:]:
            edges.append([prev, n])
            prev = n

    order = topological_order(nodes, edges)
    return {"nodes": nodes, "edges": edges, "order": order, "phase_map": phase_map}


def plan_graph(
    manifest_path: str,
    target_dir: str = ".",
    *,
    graph_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build execution graph from manifest and persist to .apatch/execution_graph.json."""
    root = os.path.abspath(target_dir)
    manifest = load_pipeline_manifest(
        manifest_path if os.path.isabs(manifest_path) else os.path.join(root, manifest_path)
    )
    graph = build_execution_graph(manifest)
    abs_graph = _graph_path(root, graph_path)
    payload = {
        "version": 1,
        "manifest_path": os.path.abspath(
            manifest_path if os.path.isabs(manifest_path) else os.path.join(root, manifest_path)
        ),
        "target_dir": root,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "order": graph["order"],
        "phase_map": graph.get("phase_map") or {},
    }
    os.makedirs(os.path.dirname(abs_graph), exist_ok=True)
    with open(abs_graph, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return {
        "ok": True,
        "graph_path": abs_graph,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "order": graph["order"],
        "node_count": len(graph["nodes"]),
    }


def load_execution_graph(root: str, graph_path: Optional[str] = None) -> Dict[str, Any]:
    abs_graph = _graph_path(root, graph_path)
    if not os.path.isfile(abs_graph):
        raise WorkflowError(f"execution graph not found: {abs_graph}")
    with open(abs_graph, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("order"):
        raise WorkflowError(f"invalid execution graph: {abs_graph}")
    return data


def _resolve_patches_jsonl(manifest: Dict[str, Any], root: str) -> Optional[str]:
    patches_jsonl = manifest.get("patches_jsonl") or manifest.get("logs_path")
    if not patches_jsonl:
        return None
    if os.path.isabs(patches_jsonl):
        return patches_jsonl
    return os.path.join(root, patches_jsonl)


def _phase_for_node(phase_map: Dict[str, List[Dict[str, Any]]], manifest: Dict[str, Any], node: str) -> Dict[str, Any]:
    phases = phase_map.get(node) or []
    if phases:
        return phases[0]
    for ph in manifest.get("phases") or []:
        action = (ph or {}).get("action") or ""
        if ACTION_TO_NODE.get(action, action) == node:
            return ph
    return {}


def _run_apply_session_all(
    logs_path: str,
    root: str,
    phase: Dict[str, Any],
    manifest: Dict[str, Any],
    *,
    chunk_max_files: int,
    dry_run: bool,
) -> Dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True, "node": "apply_session"}
    from apatch.apply_session import run_apply_session

    budget = phase.get("change_budget") or manifest.get("change_budget")
    reports: List[Dict[str, Any]] = []
    last: Dict[str, Any] = {}
    while True:
        last = run_apply_session(
            logs_path,
            root,
            verify=phase.get("verify"),
            chunk_max_files=chunk_max_files,
            verify_deferred=bool(phase.get("verify_deferred", True)),
            no_trustchain=bool(phase.get("no_trustchain")),
            quiet=True,
        )
        reports.append(
            {
                "continue": last.get("continue"),
                "checkpoint": last.get("checkpoint"),
                "progress": last.get("progress"),
                "chunk_result": last.get("chunk_result"),
            }
        )
        if not last.get("continue"):
            break
        if not last.get("ok", False):
            break
    return {
        "ok": last.get("ok", False) and not (last.get("chunk_result") or {}).get("verify_rollback"),
        "node": "apply_session",
        "checkpoint": last.get("checkpoint"),
        "chunks": reports,
        "progress": last.get("progress"),
        "change_budget": budget,
    }


def _execute_node(
    node: str,
    root: str,
    manifest: Dict[str, Any],
    patches_jsonl: Optional[str],
    *,
    phase_map: Dict[str, List[Dict[str, Any]]],
    dry_run: bool,
    chunk_max_files: int,
    manifest_path: str,
) -> Dict[str, Any]:
    phase = _phase_for_node(phase_map, manifest, node)

    if node == "plan":
        if not patches_jsonl:
            return {"ok": False, "node": node, "error": "patches_jsonl required for plan"}
        if dry_run:
            return {"ok": True, "dry_run": True, "node": node}
        from apatch.workflows import plan_from_logs

        preview = plan_from_logs(patches_jsonl, root)
        return {"ok": True, "node": node, "result": preview}

    if node == "apply_session":
        if not patches_jsonl:
            return {"ok": False, "node": node, "error": "patches_jsonl required for apply"}
        return _run_apply_session_all(
            patches_jsonl, root, phase, manifest,
            chunk_max_files=chunk_max_files, dry_run=dry_run,
        )

    if node == "context":
        from apatch.pipeline_run import _run_phase

        return _run_phase(
            root, "trustchain_history", phase, manifest, patches_jsonl,
            manifest_path=manifest_path, dry_run=dry_run,
        ) | {"node": node}

    if node == "verify":
        from apatch.pipeline_run import _run_phase

        return _run_phase(
            root, "verify_shell", phase, manifest, patches_jsonl,
            manifest_path=manifest_path, dry_run=dry_run,
        ) | {"node": node}

    if node == "semantic_verify":
        from apatch.semantic_verify import run_semantic_verify

        sem = run_semantic_verify(root, rules_path=phase.get("rules"), since=phase.get("since", "HEAD"))
        if dry_run:
            return {"ok": True, "dry_run": True, "node": node, "preview": sem}
        return {**sem, "ok": sem.get("ok", False), "node": node}

    if node == "db_check":
        from apatch.db_check import run_db_check

        profile = phase.get("profile") or manifest.get("db_profile") or "sqlalchemy"
        if dry_run:
            return {"ok": True, "dry_run": True, "node": node, "profile": profile}
        chk = run_db_check(root, profile=profile, since=phase.get("since", "HEAD"))
        return {**chk, "ok": chk.get("ok", False), "node": node}

    if node == "db_safety":
        from apatch.db_safety import run_db_safety

        profile = phase.get("profile") or manifest.get("db_profile") or "sqlalchemy"
        if dry_run:
            return {"ok": True, "dry_run": True, "node": node, "profile": profile}
        saf = run_db_safety(root, profile=profile, since=phase.get("since", "HEAD"))
        return {**saf, "ok": saf.get("safe", True), "node": node}

    if node == "db_revision":
        from apatch.pipeline_run import _run_phase

        return _run_phase(
            root, "db_revision", phase, manifest, patches_jsonl,
            manifest_path=manifest_path, dry_run=dry_run,
        ) | {"node": node}

    if node == "arch_check":
        from apatch.arch_check import run_arch_check

        rules = phase.get("rules") or manifest.get("arch_rules_path")
        if dry_run:
            return {"ok": True, "dry_run": True, "node": node, "rules": rules}
        arch = run_arch_check(root, rules_path=rules, since=phase.get("since"))
        return {**arch, "ok": arch.get("ok", False), "node": node}

    if node == "impact":
        from apatch.impact import run_impact

        target = phase.get("target") or ""
        if not target:
            return {"ok": True, "skipped": True, "node": node, "reason": "no impact target"}
        if dry_run:
            return {"ok": True, "dry_run": True, "node": node, "target": target}
        imp = run_impact(target, root, kind=phase.get("kind"), depth=int(phase.get("depth", 1)))
        return {"ok": True, "node": node, "result": imp}

    if node == "index":
        from apatch.project_index import build_project_index

        if dry_run:
            return {"ok": True, "dry_run": True, "node": node}
        return {"ok": True, "node": node, "result": build_project_index(root)}

    if node == "trustchain_commit":
        from apatch.pipeline_run import _run_phase

        return _run_phase(
            root, "trustchain_intent", phase, manifest, patches_jsonl,
            manifest_path=manifest_path, dry_run=dry_run,
        ) | {"node": node}

    if dry_run:
        return {"ok": True, "dry_run": True, "node": node, "skipped": True}
    return {"ok": False, "node": node, "error": f"unknown graph node: {node}"}


def execute_graph(
    target_dir: str = ".",
    *,
    manifest_path: Optional[str] = None,
    graph_path: Optional[str] = None,
    dry_run: bool = False,
    stop_on_failure: bool = True,
    chunk_max_files: int = 5,
    nodes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run execution graph nodes in topological order."""
    root = os.path.abspath(target_dir)
    graph_data: Dict[str, Any]
    abs_manifest: Optional[str] = None

    if graph_path and os.path.isfile(_graph_path(root, graph_path)):
        graph_data = load_execution_graph(root, graph_path)
        abs_manifest = graph_data.get("manifest_path")
    elif manifest_path:
        abs_manifest = manifest_path if os.path.isabs(manifest_path) else os.path.join(root, manifest_path)
        plan_graph(abs_manifest, root, graph_path=graph_path)
        graph_data = load_execution_graph(root, graph_path)
    else:
        default = _graph_path(root, graph_path)
        if os.path.isfile(default):
            graph_data = load_execution_graph(root, graph_path)
            abs_manifest = graph_data.get("manifest_path")
        else:
            raise WorkflowError("execute_graph: provide manifest_path or an existing graph_path")

    if not abs_manifest or not os.path.isfile(abs_manifest):
        raise WorkflowError("execute_graph: manifest_path missing from graph metadata")

    manifest = load_pipeline_manifest(abs_manifest)
    patches_jsonl = _resolve_patches_jsonl(manifest, root)
    order = nodes or graph_data.get("order") or graph_data.get("nodes") or []
    phase_map = graph_data.get("phase_map") or build_execution_graph(manifest).get("phase_map") or {}
    node_results: List[Dict[str, Any]] = []

    for node in order:
        result = _execute_node(
            node,
            root,
            manifest,
            patches_jsonl,
            phase_map=phase_map,
            dry_run=dry_run,
            chunk_max_files=chunk_max_files,
            manifest_path=abs_manifest,
        )
        node_results.append(result)
        if stop_on_failure and not result.get("ok", False) and not result.get("skipped"):
            return {
                "ok": False,
                "dry_run": dry_run,
                "graph_path": graph_data.get("graph_path") or _graph_path(root, graph_path),
                "order": order,
                "node_results": node_results,
                "failed_node": node,
                "reason": result.get("error") or result.get("reason"),
            }

    return {
        "ok": True,
        "dry_run": dry_run,
        "graph_path": _graph_path(root, graph_path),
        "order": order,
        "node_results": node_results,
        "checkpoint": _last_checkpoint(node_results),
    }


def _last_checkpoint(node_results: List[Dict[str, Any]]) -> Optional[str]:
    for item in reversed(node_results):
        ckpt = item.get("checkpoint")
        if ckpt:
            return ckpt
    return None
