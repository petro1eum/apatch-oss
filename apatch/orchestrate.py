"""R54 — single-entry orchestrator: simulate → plan graph → execute graph."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from apatch.graph_execute import execute_graph, plan_graph
from apatch.pipeline_run import load_pipeline_manifest
from apatch.workflows import WorkflowError, simulate_workspace

HIGH_RISK_ABORT = 0.75


def run_orchestrate(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
    skip_simulate: bool = False,
    abort_on_high_risk: bool = True,
    chunk_max_files: int = 5,
    graph_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified pipeline entry with dependency-driven execution order."""
    root = os.path.abspath(target_dir)
    abs_manifest = manifest_path if os.path.isabs(manifest_path) else os.path.join(root, manifest_path)
    if not os.path.isfile(abs_manifest):
        raise FileNotFoundError(abs_manifest)

    manifest = load_pipeline_manifest(abs_manifest)
    patches_jsonl = manifest.get("patches_jsonl") or manifest.get("logs_path")
    simulate_result: Optional[Dict[str, Any]] = None

    if not skip_simulate:
        try:
            simulate_result = simulate_workspace(
                root,
                logs_path=patches_jsonl,
                manifest_path=abs_manifest,
                chunk_max_files=chunk_max_files,
            )
        except WorkflowError as e:
            if patches_jsonl:
                simulate_result = {"ok": False, "error": str(e)}
            else:
                simulate_result = {"ok": True, "skipped": True, "reason": "no patches_jsonl for simulate"}

        if (
            abort_on_high_risk
            and not dry_run
            and simulate_result
            and simulate_result.get("ok")
            and float(simulate_result.get("rollback_probability") or 0) >= HIGH_RISK_ABORT
        ):
            return {
                "ok": False,
                "aborted": True,
                "reason": "preflight rollback_probability too high",
                "manifest": os.path.basename(abs_manifest),
                "simulate": simulate_result,
                "recommended_action": "reduce_scope",
            }

    graph_result = plan_graph(abs_manifest, root, graph_path=graph_path)
    exec_result = execute_graph(
        root,
        manifest_path=abs_manifest,
        graph_path=graph_path or graph_result.get("graph_path"),
        dry_run=dry_run,
        chunk_max_files=chunk_max_files,
    )

    return {
        "ok": exec_result.get("ok", False),
        "dry_run": dry_run,
        "manifest": os.path.basename(abs_manifest),
        "simulate": simulate_result,
        "graph": graph_result,
        "execution": exec_result,
        "checkpoint": exec_result.get("checkpoint"),
        "agent_next": (
            "Orchestration complete. apatch_verify_notarization(staged=true); git commit."
            if exec_result.get("ok")
            else f"Failed at node {exec_result.get('failed_node')!r}; see execution.node_results"
        ),
    }
