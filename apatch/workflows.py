"""Shared apatch workflows for CLI and MCP (no Rich/Click dependencies)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from apatch.backup import BackupManager
from apatch.import_paths import resolve_consumer_root
from apatch.dangling_refs import merge_dangling_reports
from apatch.generate import generate_patches, write_jsonl
from apatch.ingestor import LogIngestor
from apatch.strip import default_export_ext
from apatch.strip_pipeline import (
    StripPipelineConfig,
    StripPipelineResult,
    check_native_duplicates,
    load_specs_from_manifest_or_markers,
    run_strip_pipeline,
)
from apatch.trustchain_helper import TrustChainHelper
from apatch.tui import InteractiveTUI


class WorkflowError(ValueError):
    """Invalid workflow arguments."""


def commit_attested_workspace(
    target_dir: str = ".",
    *,
    governed_session_id: Optional[str] = None,
    session_ids: Optional[Sequence[str]] = None,
    message: str,
    push: bool = False,
    remote: str = "origin",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Commit only files proven by explicit, subsequently attested sessions."""
    from apatch.git_commit import commit_attested_workspace as _commit_attested

    return _commit_attested(
        target_dir,
        governed_session_id=governed_session_id,
        session_ids=session_ids,
        message=message,
        push=push,
        remote=remote,
        dry_run=dry_run,
    )


def filter_patch_candidates(
    candidates: Sequence[Any],
    *,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    steps: Optional[str] = None,
    range_str: Optional[str] = None,
) -> List[Any]:
    out = list(candidates)
    if tool:
        out = [c for c in out if tool.lower() in c.tool_name.lower()]
    if keyword:
        kw = keyword.lower()
        out = [
            c
            for c in out
            if kw in c.old_content.lower() or kw in c.new_content.lower()
        ]
    if steps:
        try:
            step_list = [int(s.strip()) for s in steps.split(",") if s.strip()]
        except ValueError as e:
            raise WorkflowError("--steps must be comma-separated integers") from e
        out = [c for c in out if c.step_index in step_list]
    if range_str:
        try:
            parts = range_str.split("-")
            if len(parts) != 2:
                raise ValueError()
            start_r, end_r = int(parts[0]), int(parts[1])
        except ValueError as e:
            raise WorkflowError("--range must be formatted as start-end (e.g. 10-20)") from e
        out = [c for c in out if start_r <= c.step_index <= end_r]
    return out


def rollback_trustchain_checkpoint(
    target_dir: str,
    session_id: Optional[str] = None,
    *,
    preview: bool = False,
    governed_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Soft-reset TrustChain HEAD to an apatch session checkpoint (mirrors CLI rollback)."""
    result: Dict[str, Any] = {"reset": False, "checkpoint": None, "warning": None, "preview": preview}
    try:
        tc_helper = TrustChainHelper(target_dir)
        if not tc_helper.has_trustchain():
            return result
        checkpoints_dir = os.path.join(tc_helper.trustchain_dir, "refs", "checkpoints")
        if not os.path.isdir(checkpoints_dir):
            return result
        ckpt_files = sorted(
            [
                f
                for f in os.listdir(checkpoints_dir)
                if f.startswith("apatch_") and f.endswith(".ref")
            ],
            reverse=True,
        )
        if not ckpt_files:
            return result
        if session_id:
            matched = [c for c in ckpt_files if session_id in c.replace(".ref", "")]
            if not matched:
                result["warning"] = f"TrustChain checkpoint not found: {session_id}"
                return result
            target_ckpt = matched[0].replace(".ref", "")
        else:
            target_ckpt = ckpt_files[0].replace(".ref", "")
        result["checkpoint"] = target_ckpt
        if not preview:
            if governed_session_id:
                reset = tc_helper.rollback_checkpoint(
                    target_ckpt,
                    governed_session_id=governed_session_id,
                )
            else:
                reset = tc_helper.rollback_checkpoint(target_ckpt)
            result["reset"] = bool(reset)
    except Exception as e:
        result["warning"] = str(e)
    return result


def rollback_workspace(
    target_dir: str,
    session_id: Optional[str] = None,
    *,
    preview: bool = False,
    governed_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """TrustChain checkpoint rollback + physical file restore from .apatch/backups/."""
    if governed_session_id:
        if not session_id:
            return {
                "ok": False,
                "restored": [],
                "count": 0,
                "error": "Governed rollback requires an exact owned checkpoint.",
                "error_type": "ROLLBACK_CHECKPOINT_MISSING",
                "mutation_performed": False,
            }
        try:
            ownership = BackupManager.list_session_files(target_dir, session_id)
        except ValueError:
            ownership = None
        if ownership is not None:
            actual_owner = str(ownership.get("governed_session_id") or "")
            if actual_owner != governed_session_id:
                return {
                    "ok": False,
                    "restored": [],
                    "count": 0,
                    "error": "Rollback checkpoint ownership does not match the governed session.",
                    "error_type": "ROLLBACK_CHECKPOINT_MISMATCH",
                    "expected": governed_session_id,
                    "actual": actual_owner or None,
                    "mutation_performed": False,
                }
    if preview:
        try:
            preview_info = BackupManager.list_session_files(target_dir, session_id)
        except ValueError as e:
            return {"ok": False, "preview": True, "error": str(e)}
        tc = rollback_trustchain_checkpoint(
            target_dir,
            preview_info["session_id"],
            preview=True,
            governed_session_id=governed_session_id,
        )
        return {
            "ok": True,
            "preview": True,
            "session_id": preview_info["session_id"],
            "files": preview_info["files"],
            "count": preview_info["count"],
            "timestamp": preview_info.get("timestamp", ""),
            "available_sessions": preview_info.get("available_sessions", []),
            "trustchain": tc,
        }

    if session_id:
        session_dir = os.path.join(
            os.path.abspath(target_dir), ".apatch", "backups", str(session_id)
        )
        if not os.path.isdir(session_dir):
            return {
                "ok": False,
                "restored": [],
                "count": 0,
                "trustchain": {
                    "reset": False,
                    "checkpoint": None,
                    "warning": None,
                    "preview": False,
                },
                "error": (
                    f"Backups for '{session_id}' were pruned (gc); "
                    "rollback unavailable."
                ),
                "error_type": "BACKUPS_PRUNED",
                "recoverable": True,
                "recommended_action": (
                    "start a fresh session; pruned backups cannot be restored"
                ),
            }

    tc = rollback_trustchain_checkpoint(
        target_dir,
        session_id,
        governed_session_id=governed_session_id,
    )
    restored: List[str] = []
    error: Optional[str] = None
    backup_session = session_id or tc.get("checkpoint")
    if backup_session:
        _sess_dir = os.path.join(
            os.path.abspath(target_dir), ".apatch", "backups", str(backup_session)
        )
        if not os.path.isdir(_sess_dir):
            # RFP-027 U27-D: distinguish pruned backups from generic errors.
            return {
                "ok": False,
                "restored": [],
                "count": 0,
                "trustchain": tc,
                "error": (
                    f"Backups for '{backup_session}' were pruned (gc); "
                    "rollback unavailable."
                ),
                "error_type": "BACKUPS_PRUNED",
                "recoverable": True,
                "recommended_action": (
                    "start a fresh session; pruned backups cannot be restored"
                ),
            }
    try:
        restored = BackupManager.rollback_session(target_dir, backup_session)
    except Exception as e:
        error = str(e)
    return {
        "ok": error is None,
        "restored": restored,
        "count": len(restored),
        "trustchain": tc,
        "error": error,
    }


def strip_rollback_transaction(
    tc_helper: Optional[TrustChainHelper],
    tc_checkpoint: Optional[str],
    file_path: str,
    exported_meta: List[dict],
    report_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Revert strip artifacts and restore source (used on verify failure)."""
    restored_tc = False
    restored_backup: List[str] = []
    workspace = resolve_consumer_root(file_path)
    if tc_helper and tc_checkpoint:
        try:
            restored_tc = bool(tc_helper.rollback_checkpoint(tc_checkpoint))
        except Exception:
            restored_tc = False
        try:
            restored_backup = BackupManager.rollback_session(workspace, tc_checkpoint)
        except ValueError:
            pass
    else:
        for sid, _ts in BackupManager.get_available_sessions(workspace):
            if sid.startswith("apatch_strip_"):
                try:
                    restored_backup = BackupManager.rollback_session(workspace, sid)
                except ValueError:
                    pass
                break

    removed_paths: List[str] = []
    for meta in exported_meta:
        for key in ("raw_path", "export_path", "module_path"):
            path = meta.get(key) or ""
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    removed_paths.append(path)
                except OSError:
                    pass
    removed_paths.extend(_revert_barrel_from_report_path(report_path))
    if report_path and os.path.exists(report_path):
        try:
            os.remove(report_path)
            removed_paths.append(report_path)
        except OSError:
            pass
    return {
        "trustchain_restored": restored_tc,
        "backup_restored": restored_backup,
        "removed_artifacts": removed_paths,
    }


def _revert_barrel_from_report_data(report_data: Optional[Dict[str, Any]]) -> List[str]:
    if not report_data:
        return []
    barrel = report_data.get("barrel_export") or {}
    from apatch.barrel_export import revert_barrel_exports

    result = revert_barrel_exports(barrel.get("barrel_path"), barrel.get("added") or [])
    reverted = result.get("reverted") or []
    path = barrel.get("barrel_path")
    return [path] if path and reverted else []


def _revert_barrel_from_report_path(report_path: Optional[str]) -> List[str]:
    if not report_path or not os.path.exists(report_path):
        return []
    try:
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return _revert_barrel_from_report_data(data)


def remove_strip_artifacts_from_results(file_results: List[Dict[str, Any]]) -> List[str]:
    """Remove exported strip artifacts listed in bundle file_results (R37)."""
    removed: List[str] = []
    seen: set = set()
    for fr in file_results:
        for meta in fr.get("exported_meta") or []:
            for key in ("raw_path", "export_path", "module_path"):
                path = meta.get(key) or ""
                if not path or path in seen or not os.path.exists(path):
                    continue
                try:
                    os.remove(path)
                    removed.append(path)
                    seen.add(path)
                except OSError:
                    pass
        for path in _revert_barrel_from_report_data(fr.get("report_data")):
            if path and path not in seen:
                removed.append(path)
                seen.add(path)
        report_path = fr.get("report_path") or ""
        for path in _revert_barrel_from_report_path(report_path):
            if path and path not in seen:
                removed.append(path)
                seen.add(path)
        if report_path and report_path not in seen and os.path.exists(report_path):
            try:
                os.remove(report_path)
                removed.append(report_path)
                seen.add(report_path)
            except OSError:
                pass
    return removed


def rollback_bundle_failure(
    target_dir: str,
    file_results: List[Dict[str, Any]],
    tc_checkpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """Restore sources from TrustChain and remove strip artifacts on bundle failure (R37)."""
    restored: List[str] = []
    trustchain_info: Dict[str, Any] = {}
    if tc_checkpoint:
        ws = rollback_workspace(target_dir, tc_checkpoint)
        restored.extend(ws.get("restored") or [])
        trustchain_info = ws.get("trustchain") or {}
    else:
        for fr in reversed(file_results):
            ckpt = (fr.get("trustchain") or {}).get("checkpoint")
            if not ckpt:
                continue
            ws = rollback_workspace(target_dir, ckpt)
            restored.extend(ws.get("restored") or [])
            if not trustchain_info:
                trustchain_info = ws.get("trustchain") or {}
    removed = remove_strip_artifacts_from_results(file_results)
    return {
        "restored": restored,
        "removed_artifacts": removed,
        "trustchain": trustchain_info,
    }


def plan_from_logs(
    logs_path: str,
    target_dir: str = ".",
    *,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    steps: Optional[str] = None,
    range_str: Optional[str] = None,
    show_diff: bool = False,
    workers: int = 0,
) -> Dict[str, Any]:
    """Dry-run plan for all candidates in a JSONL log (mirrors apatch plan --json)."""
    from apatch.match_session import MatchSession
    from apatch.path_index import PathIndex
    from apatch.plan_worker import evaluate_plan_entry, parallel_plan_evaluate
    from apatch.resolver import resolve_smart_path
    from apatch.scale_config import get_plan_workers

    ingestor = LogIngestor(logs_path)
    candidates = filter_patch_candidates(
        ingestor.parse(),
        tool=tool,
        keyword=keyword,
        steps=steps,
        range_str=range_str,
    )
    target_dir = os.path.abspath(target_dir)
    path_index = PathIndex.build(target_dir)

    entries: List[Dict[str, Any]] = []
    jobs: List[dict] = []
    job_indices: List[int] = []
    for cand in candidates:
        resolved = resolve_smart_path(
            target_dir, cand.target_file, cand.action_type, index=path_index
        )
        entry: Dict[str, Any] = {
            "step_index": cand.step_index,
            "tool_name": cand.tool_name,
            "target_file": cand.target_file,
            "resolved_path": resolved,
            "action_type": cand.action_type,
            "strategy": "unresolved-path" if not resolved else None,
            "confidence": 0.0,
            "would_apply": False,
            "warnings": [],
            "diff": "",
        }
        entries.append(entry)
        if resolved:
            job_indices.append(len(entries) - 1)
            jobs.append(
                {
                    "entry": entry,
                    "resolved_path": resolved,
                    "old_content": cand.old_content,
                    "new_content": cand.new_content,
                    "action_type": cand.action_type,
                    "replace_all": getattr(cand, "replace_all", False),
                    "show_diff": show_diff,
                    "basename": os.path.basename(resolved),
                }
            )

    worker_count = get_plan_workers(workers)
    if worker_count == -1:
        worker_count = os.cpu_count() or 1

    if jobs:
        if worker_count > 1:
            evaluated = parallel_plan_evaluate(jobs, workers=worker_count)
        else:
            session = MatchSession()
            evaluated = [evaluate_plan_entry(job, match_session=session) for job in jobs]
        for idx, result in zip(job_indices, evaluated):
            entries[idx] = result

    would = sum(1 for e in entries if e["would_apply"])
    return {
        "entries": entries,
        "total": len(entries),
        "would_apply": would,
    }


def apply_from_logs(
    logs_path: str,
    target_dir: str = ".",
    *,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    steps: Optional[str] = None,
    range_str: Optional[str] = None,
    replace_all: bool = False,
    min_confidence: Optional[float] = None,
    only_drifted: bool = False,
    verify: Optional[str] = None,
    verify_deferred: bool = False,
    verify_baseline: Optional[Dict[str, Any]] = None,
    no_trustchain: bool = False,
    dry_run: bool = False,
    report_path: Optional[str] = None,
    change_budget: Optional[Dict[str, int]] = None,
    quiet: bool = False,
    sandbox_owns_lease: bool = True,
    interactive: bool = False,
) -> Dict[str, Any]:
    """Apply patches from JSONL (mirrors apatch apply -y / interactive apply).

    Set quiet=True when stdout is the MCP JSON-RPC channel (stdio transport).
    Set interactive=True for human TUI (apatch apply without -y).
    """
    from apatch.enforcement import assert_can_apply

    block = assert_can_apply(target_dir, no_trustchain=no_trustchain)
    if block:
        raise WorkflowError(block)

    ingestor = LogIngestor(logs_path)
    candidates = filter_patch_candidates(
        ingestor.parse(),
        tool=tool,
        keyword=keyword,
        steps=steps,
        range_str=range_str,
    )
    if change_budget and not dry_run:
        from apatch.change_budget import check_budget, estimate_from_candidates

        estimate = estimate_from_candidates(
            candidates,
            target_dir,
            replace_all=replace_all,
            min_confidence=min_confidence,
            only_drifted=only_drifted,
        )
        budget_result = check_budget(estimate, change_budget)
        if not budget_result["ok"]:
            return {
                "ok": False,
                "reason": budget_result["reason"],
                "change_budget": budget_result,
                "total": len(candidates),
                "applied": 0,
                "skipped": 0,
                "failed": 0,
                "rolled_back": 0,
                "verify_rollback": False,
                "dry_run": dry_run,
                "report_path": None,
                "entries": [],
            }
    if report_path is None:
        report_path = os.path.join(target_dir, ".apatch", "mcp_apply_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    if verify and not dry_run and verify_baseline is None:
        from apatch.session_state import load_session_state
        from apatch.verify_baseline import capture_baseline

        state = load_session_state(target_dir)
        verify_baseline = capture_baseline(
            target_dir,
            verify_command=verify,
            session_id=str(state.get("session_id") or "") or None,
        )
    tui = InteractiveTUI(
        candidates=candidates,
        target_dir=target_dir,
        non_interactive=not interactive,
        dry_run=dry_run,
        verify_cmd=verify,
        verify_deferred=verify_deferred,
        verify_baseline=verify_baseline,
        no_trustchain=no_trustchain,
        replace_all=replace_all,
        min_confidence=min_confidence,
        only_drifted=only_drifted,
        report_path=report_path,
        quiet=quiet if not interactive else False,
    )
    from apatch.sandbox import SandboxError, collect_paths_from_candidates, sandbox_apply_scope

    lease_paths = collect_paths_from_candidates(candidates, target_dir)
    try:
        with sandbox_apply_scope(
            target_dir,
            lease_paths,
            tool="apatch_apply",
            dry_run=dry_run,
            owns_lease=sandbox_owns_lease,
        ):
            tui.run_apply_loop()
    except SandboxError as exc:
        raise WorkflowError(str(exc)) from exc
    applied = sum(1 for e in tui.report_entries if e.get("outcome") == "applied")
    skipped = sum(1 for e in tui.report_entries if e.get("outcome") in ("skipped", "rolled_back"))
    failed = sum(1 for e in tui.report_entries if e.get("outcome") == "failed")
    rolled_back = sum(1 for e in tui.report_entries if e.get("outcome") == "rolled_back")
    tc_active = bool(
        not no_trustchain and tui.tc_helper and tui.tc_helper.has_trustchain()
    )
    out = {
        "ok": failed == 0 and not tui.verify_rollback,
        "total": len(candidates),
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "rolled_back": rolled_back,
        "verify_rollback": tui.verify_rollback,
        "verify_output": getattr(tui, "last_verify_output", ""),
        "verify_baseline": getattr(tui, "last_verify_baseline", {}),
        "dry_run": dry_run,
        "checkpoint": tui.tc_checkpoint_name,
        "trustchain": {
            "active": tc_active,
            "checkpoint": tui.tc_checkpoint_name,
            "path": tui.tc_helper.trustchain_dir if tc_active else None,
        },
        "report_path": report_path if os.path.exists(report_path) else None,
        "entries": tui.report_entries,
        "rollback_hint": (
            f"apatch_rollback(session_id={tui.tc_checkpoint_name!r})"
            if tui.tc_checkpoint_name
            else "apatch_rollback() after failed verify"
        ),
        "trustchain_committed": tui.trustchain_committed,
    }
    if tui.verify_rollback and out.get("verify_output"):
        from apatch.build_diagnose import enrich_verify_failure

        enrich_verify_failure(
            out,
            target_dir,
            log_text=out["verify_output"],
            verify=verify,
        )
    return out


def _strip_result_payload(
    result: StripPipelineResult,
    *,
    tc_checkpoint: Optional[str] = None,
    trustchain_active: bool = False,
) -> Dict[str, Any]:
    dangling = result.report_data.get("dangling_references")
    if not dangling:
        dangling = merge_dangling_reports(result.exported_meta)
    boundary_warnings = [
        w
        for r in result.results
        for w in getattr(r, "boundary_warnings", [])
    ]
    for meta in result.exported_meta:
        boundary_warnings.extend(meta.get("boundary_warnings", []))
    boundary_warnings = list(dict.fromkeys(boundary_warnings))
    pipeline_warnings = list(getattr(result, "pipeline_warnings", []) or [])

    return {
        "ok": result.ok,
        "errors": result.errors,
        "exported_meta": result.exported_meta,
        "report_path": result.report_path,
        "report_data": result.report_data,
        "dangling_references": dangling,
        "boundary_warnings": boundary_warnings,
        "pipeline_warnings": pipeline_warnings,
        "exit_code": result.exit_code,
        "strip_results": [
            {
                "label": r.spec.label or r.spec.start[:40],
                "start_line": r.start_line,
                "end_line": r.end_line,
                "removed_lines": r.removed_lines,
            }
            for r in result.results
        ],
        "trustchain": {
            "active": trustchain_active,
            "checkpoint": tc_checkpoint,
        },
    }


def run_strip(
    file_path: str,
    *,
    manifest_path: Optional[str] = None,
    start_marker: Optional[str] = None,
    until_marker: Optional[str] = None,
    replace_text: str = "",
    export_filename: Optional[str] = None,
    dry_run: bool = False,
    out_dir: Optional[str] = None,
    export_ext: Optional[str] = None,
    no_trustchain: bool = False,
    to_native: Optional[str] = None,
    to_module: Optional[str] = None,
    module_out_dir: Optional[str] = None,
    native_out_dir: Optional[str] = None,
    post_hook: Optional[str] = None,
    strict: bool = False,
    strict_overlap: bool = False,
    strict_dangling: bool = False,
    emit_wiring: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    auto_wire: bool = False,
    emit_barrel: Optional[str] = None,
    specs: Optional[List] = None,
    shared_tc_helper: Optional[TrustChainHelper] = None,
    shared_tc_checkpoint: Optional[str] = None,
    skip_tc_commit: bool = False,
) -> Dict[str, Any]:
    """Run strip pipeline (mirrors apatch strip)."""
    if specs is None:
        specs = load_specs_from_manifest_or_markers(
            manifest_path, start_marker, until_marker, replace_text, export_filename
        )
    if export_ext is None:
        export_ext = default_export_ext(file_path)

    workspace = resolve_consumer_root(file_path)
    tc_helper = shared_tc_helper
    tc_checkpoint = shared_tc_checkpoint
    if not dry_run and tc_helper is None:
        if not no_trustchain:
            tc_helper = TrustChainHelper(workspace, auto_init=True)
            tc_checkpoint = tc_helper.begin_mutating_session(
                "apatch_strip",
                source_files=[file_path],
            )
        else:
            import time

            tc_checkpoint = f"apatch_strip_{int(time.time())}"
            BackupManager(workspace, session_id=tc_checkpoint).create_backup(
                os.path.abspath(file_path)
            )

    cfg = StripPipelineConfig(
        file_path=file_path,
        specs=specs,
        dry_run=dry_run,
        out_dir=out_dir,
        export_ext=export_ext,
        export_filename=export_filename,
        to_native=to_native,
        to_module=to_module,
        module_out_dir=module_out_dir,
        native_out_dir=native_out_dir,
        post_hook=post_hook,
        strict=strict,
        strict_overlap=strict_overlap,
        strict_dangling=strict_dangling,
        no_trustchain=no_trustchain,
        emit_wiring=emit_wiring,
        verify_cmd=verify_cmd,
        as_json=True,
        manifest_path=manifest_path,
        auto_wire=auto_wire,
        emit_barrel=emit_barrel,
        skip_tc_commit=skip_tc_commit,
    )

    def _on_rollback(helper, ckpt, fp, exported, report_path=None):
        strip_rollback_transaction(helper, ckpt, fp, exported, report_path)

    from apatch.sandbox import collect_strip_scope_paths, sandbox_apply_scope

    scope_paths = collect_strip_scope_paths(
        workspace,
        file_path=file_path,
        module_out_dir=module_out_dir,
        native_out_dir=native_out_dir,
        out_dir=out_dir,
        emit_barrel=emit_barrel,
        emit_wiring=emit_wiring,
    )
    with sandbox_apply_scope(
        workspace,
        scope_paths,
        tool="apatch_strip",
        dry_run=dry_run,
        session_checkpoint=tc_checkpoint,
    ):
        result = run_strip_pipeline(
            cfg,
            tc_helper=tc_helper,
            tc_checkpoint=tc_checkpoint,
            rollback_fn=_on_rollback,
        )
    payload = _strip_result_payload(
        result,
        tc_checkpoint=tc_checkpoint,
        trustchain_active=bool(tc_helper and tc_helper.has_trustchain()),
    )
    if native_out_dir or out_dir:
        search_dir = native_out_dir or out_dir
        if search_dir and os.path.exists(search_dir):
            payload["native_duplicates"] = check_native_duplicates(search_dir)
    return payload


def compile_markdown_file(
    file_path: str,
    *,
    out_dir: Optional[str] = None,
    dry_run: bool = False,
    no_trustchain: bool = False,
) -> Dict[str, Any]:
    """Compile markdown with semantic IDs (mirrors apatch compile)."""
    from apatch.semantic_parser import compile_markdown

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    tc_helper = None
    tc_checkpoint = None
    if not dry_run and not no_trustchain:
        workspace = resolve_consumer_root(file_path)
        tc_helper = TrustChainHelper(workspace, auto_init=True)
        tc_checkpoint = tc_helper.begin_mutating_session("apatch_compile")

    compiled_text, knowledge_map = compile_markdown(content)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "blocks_count": len(knowledge_map),
            "knowledge_map": knowledge_map,
        }

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(compiled_text)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        map_path = os.path.join(out_dir, "knowledge_map.json")
    else:
        map_path = os.path.join(os.path.dirname(os.path.abspath(file_path)), "knowledge_map.json")

    with open(map_path, "w", encoding="utf-8") as f_map:
        json.dump(knowledge_map, f_map, indent=2, ensure_ascii=False)

    committed = False
    if tc_helper and tc_helper.has_trustchain() and tc_checkpoint:
        try:
            payload = {
                "action": "compile",
                "checkpoint": tc_checkpoint,
                "source_file": os.path.abspath(file_path),
                "knowledge_map_path": os.path.abspath(map_path),
                "blocks_count": len(knowledge_map),
            }
            committed = bool(tc_helper.commit_action("apatch", payload))
        except Exception:
            committed = False

    return {
        "ok": True,
        "dry_run": False,
        "source_file": os.path.abspath(file_path),
        "knowledge_map_path": os.path.abspath(map_path),
        "blocks_count": len(knowledge_map),
        "trustchain_committed": committed,
    }


def scan_transcripts(
    *,
    extra_paths: Optional[List[str]] = None,
    limit: int = 20,
    include_cwd: bool = True,
    count_candidates: bool = True,
) -> List[Dict[str, Any]]:
    from apatch.discovery import count_candidates as count_fn
    from apatch.discovery import discover_transcripts

    transcripts = discover_transcripts(
        extra_paths=list(extra_paths or []),
        include_cwd=include_cwd,
    )[:limit]
    rows: List[Dict[str, Any]] = []
    for t in transcripts:
        row = t.as_dict()
        if count_candidates:
            row["candidate_count"] = count_fn(t.path)
        rows.append(row)
    return rows


def view_log_candidates(
    logs_path: str,
    *,
    tool: Optional[str] = None,
    keyword: Optional[str] = None,
    steps: Optional[str] = None,
    range_str: Optional[str] = None,
) -> List[Dict[str, Any]]:
    ingestor = LogIngestor(logs_path)
    candidates = filter_patch_candidates(
        ingestor.parse(),
        tool=tool,
        keyword=keyword,
        steps=steps,
        range_str=range_str,
    )
    return [
        {
            "step_index": c.step_index,
            "tool_name": c.tool_name,
            "target_file": c.target_file,
            "action_type": c.action_type,
            "source_format": c.source_format,
            "old_len": len(c.old_content),
            "new_len": len(c.new_content),
            "replace_all": c.replace_all,
        }
        for c in candidates
    ]


def natives_check(directory: str) -> Dict[str, Any]:
    dups = check_native_duplicates(directory)
    return {"ok": not dups, "duplicates": dups}


def generate_patch_jsonl(
    *,
    find_text: str,
    replace_text: str,
    target_dir: str = ".",
    glob_pattern: str = "**/*",
    out_path: str = "patches.jsonl",
    replace_all: bool = True,
    match_mode: str = "literal",
    find_pattern: Optional[str] = None,
    append: bool = False,
    governed_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.artifact_governance import register_ephemeral_logs, resolve_ephemeral_logs_path
    from apatch.generate import next_step_index, read_jsonl_patches

    abs_out, rel_out = resolve_ephemeral_logs_path(target_dir, out_path)
    if governed_session_id is None:
        from apatch.session_state import load_session_state

        raw = load_session_state(target_dir)
        if raw.get("session_id") and not raw.get("ended_at"):
            governed_session_id = str(raw.get("session_id"))
    from apatch.runtime.atomic_io import exclusive_file_lock
    from apatch.runtime.session_binding import assert_patch_log_write_owner

    os.makedirs(os.path.dirname(abs_out), exist_ok=True)
    with exclusive_file_lock(abs_out):
        assert_patch_log_write_owner(
            target_dir,
            abs_out,
            expected_session_id=governed_session_id,
        )
        existing = read_jsonl_patches(abs_out) if append else []
        patches = generate_patches(
            find=find_text,
            replace=replace_text,
            target_dir=target_dir,
            glob_pattern=glob_pattern,
            replace_all=replace_all,
            match_mode=match_mode,  # type: ignore[arg-type]
            find_pattern=find_pattern,
            start_step=next_step_index(existing),
        )
        from apatch.spec_ownership import authorize_spec_owned_needles

        authorization = authorize_spec_owned_needles(
            target_dir,
            patches,
            governed_session_id=governed_session_id,
            created_by_tool="apatch_generate",
        )
        if not authorization.get("ok"):
            return authorization
        mode = "a" if append and existing else "w"
        with open(abs_out, mode, encoding="utf-8") as f:
            count = write_jsonl(patches, f)
        register_ephemeral_logs(
            target_dir,
            rel_out,
            created_by_tool="apatch_generate",
            governed_session_id=governed_session_id,
        )
    return {
        "ok": True,
        "count": count,
        "total": len(existing) + count,
        "out_path": abs_out,
        "out_path_rel": rel_out,
        "append": append,
    }


def generate_patch_jsonl_batch(
    *,
    needles: List[Any],
    target_dir: str = ".",
    out_path: str = "patches.jsonl",
    append: bool = False,
    default_glob_pattern: str = "**/*",
    default_match_mode: str = "literal",
    default_replace_all: bool = False,
    created_by_tool: str = "apatch_generate_batch",
    spec_run_id: Optional[str] = None,
    governed_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate JSONL from an ordered list of mutations (replace/create/delete/rename)."""
    from apatch.artifact_governance import register_ephemeral_logs, resolve_ephemeral_logs_path
    from apatch.generate import (
        generate_patches_batch,
        next_step_index,
        read_jsonl_patches,
    )
    from apatch.spec_ownership import authorize_spec_owned_needles

    authorization = authorize_spec_owned_needles(
        target_dir,
        needles,
        governed_session_id=governed_session_id,
        created_by_tool=created_by_tool,
    )
    if not authorization.get("ok"):
        return authorization

    abs_out, rel_out = resolve_ephemeral_logs_path(target_dir, out_path)
    if governed_session_id is None:
        from apatch.session_state import load_session_state

        raw = load_session_state(target_dir)
        if raw.get("session_id") and not raw.get("ended_at"):
            governed_session_id = str(raw.get("session_id"))
    from apatch.runtime.atomic_io import exclusive_file_lock
    from apatch.runtime.session_binding import assert_patch_log_write_owner

    os.makedirs(os.path.dirname(abs_out), exist_ok=True)
    with exclusive_file_lock(abs_out):
        assert_patch_log_write_owner(
            target_dir,
            abs_out,
            expected_session_id=governed_session_id,
        )
        existing = read_jsonl_patches(abs_out) if append else []
        try:
            patches = generate_patches_batch(
                needles,
                target_dir=target_dir,
                default_glob_pattern=default_glob_pattern,
                default_match_mode=default_match_mode,  # type: ignore[arg-type]
                default_replace_all=default_replace_all,
                start_step=next_step_index(existing),
            )
        except ValueError as exc:
            from apatch.generate import NeedleOverlapError

            if isinstance(exc, NeedleOverlapError):
                return {
                    "ok": False,
                    "error": str(exc),
                    "error_type": "NEEDLE_OVERLAP",
                    "recoverable": True,
                    "recommended_action": "retry_split",
                    "mutation_performed": False,
                    "out_path": abs_out,
                    "retry_plan": {
                        "strategy": "sequential_batches",
                        "safe_batches": exc.safe_batches,
                        "conflicts": exc.conflicts[:10],
                        "conflict_count": len(exc.conflicts),
                    },
                }
            return {"ok": False, "error": str(exc), "out_path": abs_out}
        mode = "a" if append and existing else "w"
        with open(abs_out, mode, encoding="utf-8") as f:
            count = write_jsonl(patches, f)
        register_ephemeral_logs(
            target_dir,
            rel_out,
            created_by_tool=created_by_tool,
            spec_run_id=spec_run_id,
            governed_session_id=governed_session_id,
        )
    return {
        "ok": True,
        "count": count,
        "total": len(existing) + count,
        "needles": len(needles),
        "out_path": abs_out,
        "out_path_rel": rel_out,
        "append": append,
        "agent_next": f"apatch_simulate(logs_path={abs_out!r}) → apatch_apply_session(...)",
    }


def phase_run(
    file_path: Optional[str],
    manifest_path: str,
    *,
    profile: str = "frontend",
    out_dir: str = "extracted",
    native_out_dir: Optional[str] = None,
    module_out_dir: Optional[str] = None,
    to_module: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    strict: bool = False,
    emit_wiring: Optional[str] = None,
    no_trustchain: bool = False,
    auto_wire: bool = False,
    emit_barrel: Optional[str] = None,
    target_dir: str = ".",
) -> Dict[str, Any]:
    """Orchestrate strip + native/module conversion + verify (mirrors apatch phase run)."""
    from apatch.manifest_bundle import load_manifest_bundle

    bundle = load_manifest_bundle(manifest_path, default_file=file_path)
    if bundle.is_multi or (len(bundle.files) == 1 and not file_path):
        return phase_run_bundle(
            manifest_path,
            profile=profile,
            out_dir=out_dir,
            native_out_dir=native_out_dir,
            module_out_dir=module_out_dir,
            to_module=to_module,
            verify_cmd=verify_cmd,
            strict=strict,
            emit_wiring=emit_wiring,
            no_trustchain=no_trustchain,
            auto_wire=auto_wire,
            emit_barrel=emit_barrel,
            target_dir=target_dir,
        )

    resolved_file = bundle.files[0].path if bundle.files else file_path
    if not resolved_file:
        return {"ok": False, "errors": ["--file required for single-file manifest"]}

    to_native = None
    export_ext = None
    module_kind = to_module
    if profile == "cpp":
        to_native = "auto"
        export_ext = ".extracted.cpp"
    elif profile == "frontend":
        # Only convert to a module when explicitly requested (strip-only is valid).
        export_ext = default_export_ext(resolved_file)
        if not verify_cmd:
            verify_cmd = bundle.verify_command or "npm run build"
    elif bundle.verify_command and not verify_cmd:
        verify_cmd = bundle.verify_command

    abs_file = resolved_file
    if not os.path.isabs(abs_file):
        abs_file = os.path.join(target_dir, resolved_file)

    return run_strip(
        abs_file,
        manifest_path=None,
        dry_run=False,
        out_dir=out_dir,
        export_ext=export_ext,
        to_native=to_native,
        to_module=module_kind,
        module_out_dir=module_out_dir,
        native_out_dir=native_out_dir,
        strict=strict,
        strict_overlap=True,
        strict_dangling=False,
        emit_wiring=emit_wiring,
        verify_cmd=verify_cmd,
        no_trustchain=no_trustchain,
        auto_wire=auto_wire,
        emit_barrel=emit_barrel,
        specs=bundle.files[0].strips if bundle.files else None,
    )


def phase_run_bundle(
    manifest_path: str,
    *,
    profile: str = "frontend",
    out_dir: str = "extracted",
    native_out_dir: Optional[str] = None,
    module_out_dir: Optional[str] = None,
    to_module: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    strict: bool = False,
    emit_wiring: Optional[str] = None,
    no_trustchain: bool = False,
    auto_wire: bool = False,
    emit_barrel: Optional[str] = None,
    target_dir: str = ".",
) -> Dict[str, Any]:
    """Run strip across multiple files from one manifest; single verify at end."""
    from apatch.manifest_bundle import load_manifest_bundle

    bundle = load_manifest_bundle(manifest_path)
    if not bundle.files:
        return {"ok": False, "errors": ["manifest has no files"]}

    final_verify = verify_cmd or bundle.verify_command or (
        "npm run build" if profile == "frontend" else None
    )
    abs_out = out_dir if os.path.isabs(out_dir) else os.path.join(target_dir, out_dir)
    abs_module = None
    if module_out_dir:
        abs_module = (
            module_out_dir
            if os.path.isabs(module_out_dir)
            else os.path.join(target_dir, module_out_dir)
        )
    workspace = os.path.abspath(target_dir)
    abs_files = [
        e.path if os.path.isabs(e.path) else os.path.join(target_dir, e.path)
        for e in bundle.files
    ]

    tc_helper: Optional[TrustChainHelper] = None
    tc_checkpoint: Optional[str] = None
    if not no_trustchain and len(bundle.files) > 1:
        tc_helper = TrustChainHelper(workspace, auto_init=True)
        tc_checkpoint = tc_helper.begin_mutating_session(
            "apatch_phase_bundle",
            source_files=abs_files,
        )

    file_results: List[Dict[str, Any]] = []
    multi = len(bundle.files) > 1
    try:
        for entry, abs_file in zip(bundle.files, abs_files):
            result = run_strip(
                abs_file,
                dry_run=False,
                out_dir=abs_out,
                to_module=to_module if profile == "frontend" else None,
                module_out_dir=abs_module,
                native_out_dir=native_out_dir,
                to_native="auto" if profile == "cpp" else None,
                strict=strict,
                strict_overlap=True,
                emit_wiring=emit_wiring if not multi else None,
                verify_cmd=None,
                no_trustchain=no_trustchain or multi,
                auto_wire=auto_wire,
                emit_barrel=emit_barrel if not multi else None,
                specs=entry.strips,
                shared_tc_helper=tc_helper if multi else None,
                shared_tc_checkpoint=tc_checkpoint if multi else None,
                skip_tc_commit=multi,
            )
            file_results.append({"file": entry.path, **result})
            if not result.get("ok"):
                rollback_info = rollback_bundle_failure(
                    target_dir,
                    file_results,
                    tc_checkpoint if multi else None,
                )
                return {
                    "ok": False,
                    "errors": result.get("errors", []),
                    "files": file_results,
                    "failed_file": entry.path,
                    "trustchain": {"checkpoint": tc_checkpoint},
                    "rollback": rollback_info,
                }

        if final_verify:
            from apatch.strip_pipeline import _run_verify

            v_ok, v_err = _run_verify(final_verify, workspace)
            if not v_ok:
                rollback_info = rollback_bundle_failure(
                    target_dir,
                    file_results,
                    tc_checkpoint if multi else None,
                )
                return {
                    "ok": False,
                    "errors": [v_err or "verify failed"],
                    "files": file_results,
                    "verify": final_verify,
                    "trustchain": {"checkpoint": tc_checkpoint},
                    "rollback": rollback_info,
                }

        if multi and tc_helper and tc_checkpoint and tc_helper.has_trustchain():
            try:
                tc_helper.commit_action(
                    "apatch",
                    {
                        "action": "phase_bundle",
                        "checkpoint": tc_checkpoint,
                        "files": [r["file"] for r in file_results],
                        "verify": final_verify,
                    },
                )
            except Exception:
                pass

        return {
            "ok": True,
            "files": file_results,
            "verify": final_verify,
            "trustchain": {"checkpoint": tc_checkpoint, "bundle": multi},
        }
    except Exception as e:
        rollback_info = rollback_bundle_failure(
            target_dir,
            file_results,
            tc_checkpoint if multi else None,
        )
        return {
            "ok": False,
            "errors": [str(e)],
            "files": file_results,
            "rollback": rollback_info,
        }


def suggest_until_for_file(file_path: str, start_marker: str) -> List[Dict[str, Any]]:
    from apatch.boundary_ranker import rank_until_candidates

    with open(file_path, encoding="utf-8") as handle:
        lines = handle.read().splitlines(keepends=True)
    start_idx = next((i for i, ln in enumerate(lines) if start_marker in ln), -1)
    if start_idx < 0:
        return []
    return rank_until_candidates(file_path, lines, start_idx)


def arch_check_workspace(
    target_dir: str = ".",
    *,
    rules_path: Optional[str] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.arch_check import run_arch_check

    return run_arch_check(target_dir, rules_path=rules_path, since=since)


def impact_analysis(
    target: str,
    target_dir: str = ".",
    *,
    kind: Optional[str] = None,
    depth: int = 1,
    include_tests: bool = True,
) -> Dict[str, Any]:
    from apatch.impact import run_impact

    return run_impact(
        target,
        target_dir,
        kind=kind,
        depth=depth,
        include_tests=include_tests,
    )


def db_check_workspace(
    target_dir: str = ".",
    *,
    profile: str,
    since: str = "HEAD",
    model_glob: Optional[List[str]] = None,
    migration_glob: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from apatch.db_check import run_db_check

    return run_db_check(
        target_dir,
        profile=profile,
        since=since,
        model_glob=model_glob,
        migration_glob=migration_glob,
    )


def db_revision_workspace(
    target_dir: str = ".",
    *,
    profile: str,
    message: str = "apatch revision",
    dry_run: bool = False,
) -> Dict[str, Any]:
    from apatch.db_revision import run_db_revision

    return run_db_revision(
        target_dir,
        profile=profile,
        message=message,
        dry_run=dry_run,
    )


def db_safety_workspace(
    target_dir: str = ".",
    *,
    profile: str,
    since: Optional[str] = "HEAD",
    migration_glob: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from apatch.db_safety import run_db_safety

    return run_db_safety(
        target_dir,
        profile=profile,
        since=since,
        migration_glob=migration_glob,
    )


def db_run_manifest(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    from apatch.db_run import run_db_refactor

    return run_db_refactor(manifest_path, target_dir, dry_run=dry_run)


def refactor_run_manifest(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    from apatch.refactor_run import run_refactor_bundle

    return run_refactor_bundle(manifest_path, target_dir, dry_run=dry_run)


def semantic_verify_workspace(
    target_dir: str = ".",
    *,
    rules_path: Optional[str] = None,
    since: str = "HEAD",
) -> Dict[str, Any]:
    from apatch.semantic_verify import run_semantic_verify

    return run_semantic_verify(target_dir, rules_path=rules_path, since=since)


def pipeline_run_manifest(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    from apatch.pipeline_run import run_engineering_pipeline

    return run_engineering_pipeline(manifest_path, target_dir, dry_run=dry_run)


def index_build_workspace(target_dir: str = ".") -> Dict[str, Any]:
    from apatch.project_index import build_project_index

    return build_project_index(target_dir)


def index_query_workspace(target_dir: str, query: str) -> Dict[str, Any]:
    from apatch.project_index import query_project_index

    return query_project_index(target_dir, query)


def simulate_workspace(
    target_dir: str = ".",
    *,
    logs_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
    chunk_max_files: int = 5,
    replace_all: bool = False,
    only_drifted: bool = False,
    min_confidence: Optional[float] = None,
    budget: Optional[str] = None,
    max_files: Optional[int] = None,
    max_insertions: Optional[int] = None,
    max_deletions: Optional[int] = None,
) -> Dict[str, Any]:
    from apatch.simulate import simulate_workspace as _simulate

    return _simulate(
        target_dir,
        logs_path=logs_path,
        manifest_path=manifest_path,
        chunk_max_files=chunk_max_files,
        replace_all=replace_all,
        only_drifted=only_drifted,
        min_confidence=min_confidence,
        budget=budget,
        max_files=max_files,
        max_insertions=max_insertions,
        max_deletions=max_deletions,
    )


def verify_notarization_workspace(
    target_dir: str = ".",
    *,
    staged: bool = False,
    working_tree: bool = False,
    rebuild_index: bool = False,
) -> Dict[str, Any]:
    from apatch.enforcement import verify_notarization_workspace as _verify

    return _verify(
        target_dir,
        staged=staged,
        working_tree=working_tree,
        rebuild_index=rebuild_index,
    )


def trustchain_intent_history_workspace(
    target_dir: str = ".",
    query: Optional[str] = None,
    *,
    artifact: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    from apatch.trustchain_helper import TrustChainHelper

    from apatch.agent_guidance import attach_artifact_guidance

    tc = TrustChainHelper(target_dir, auto_init=False)
    out = tc.list_intent_history(query=query, artifact=artifact, limit=limit)
    return attach_artifact_guidance(out, "apatch_trustchain_history", target_dir=target_dir)


def trustchain_coverage_workspace(
    target_dir: str = ".",
    *,
    artifact: Optional[str] = None,
    op_id: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.agent_guidance import attach_artifact_guidance
    from apatch.trustchain_helper import TrustChainHelper

    tc = TrustChainHelper(target_dir, auto_init=False)
    if op_id:
        out = tc.resolve_op_id(op_id)
    else:
        out = tc.artifact_coverage(artifact=artifact)
    return attach_artifact_guidance(out, "apatch_trustchain_coverage", target_dir=target_dir)


def plan_graph_workspace(
    manifest_path: str,
    target_dir: str = ".",
    *,
    graph_path: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.graph_execute import plan_graph

    return plan_graph(manifest_path, target_dir, graph_path=graph_path)


def execute_graph_workspace(
    target_dir: str = ".",
    *,
    manifest_path: Optional[str] = None,
    graph_path: Optional[str] = None,
    dry_run: bool = False,
    stop_on_failure: bool = True,
    chunk_max_files: int = 5,
) -> Dict[str, Any]:
    from apatch.graph_execute import execute_graph

    return execute_graph(
        target_dir,
        manifest_path=manifest_path,
        graph_path=graph_path,
        dry_run=dry_run,
        stop_on_failure=stop_on_failure,
        chunk_max_files=chunk_max_files,
    )


def orchestrate_workspace(
    manifest_path: str,
    target_dir: str = ".",
    *,
    dry_run: bool = False,
    skip_simulate: bool = False,
    abort_on_high_risk: bool = True,
    chunk_max_files: int = 5,
) -> Dict[str, Any]:
    from apatch.orchestrate import run_orchestrate

    return run_orchestrate(
        manifest_path,
        target_dir,
        dry_run=dry_run,
        skip_simulate=skip_simulate,
        abort_on_high_risk=abort_on_high_risk,
        chunk_max_files=chunk_max_files,
    )


def replay_session_workspace(
    target_dir: str = ".",
    *,
    session_id: Optional[str] = None,
    session_path: Optional[str] = None,
    mode: str = "deterministic",
) -> Dict[str, Any]:
    from apatch.replay import replay_session

    return replay_session(
        target_dir,
        session_id=session_id,
        session_path=session_path,
        mode=mode,
    )


def build_diagnose_workspace(
    target_dir: str = ".",
    *,
    verify: Optional[str] = None,
    log_text: Optional[str] = None,
    write_artifacts: bool = True,
) -> Dict[str, Any]:
    from apatch.build_diagnose import run_build_diagnose

    return run_build_diagnose(
        target_dir,
        verify=verify,
        log_text=log_text,
        write_artifacts=write_artifacts,
    )


def project_status_workspace(target_dir: str = ".") -> Dict[str, Any]:
    from apatch.project_status import project_status_workspace as _workspace

    return _workspace(target_dir)


def knowledge_graph_workspace(
    target_dir: str = ".",
    *,
    session_id: str = "",
) -> Dict[str, Any]:
    from apatch.knowledge_graph import knowledge_graph_enriched

    return knowledge_graph_enriched(target_dir, session_id=session_id)


def _workspace_registry_call(fn, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    from apatch.mcp.workspace_registry import WorkspaceRegistryError

    try:
        return fn(*args, **kwargs)
    except WorkspaceRegistryError as exc:
        return exc.to_result()


def workspace_list_workspace(
    target_dir: str = ".",
    *,
    include_contract: bool = False,
) -> Dict[str, Any]:
    from apatch.mcp.workspace_registry import list_local_workspaces

    return _workspace_registry_call(
        list_local_workspaces,
        include_contract=include_contract,
    )


def workspace_inspect_workspace(
    target_dir: str = ".",
    *,
    alias: str,
    include_contract: bool = False,
) -> Dict[str, Any]:
    from apatch.mcp.workspace_registry import inspect_local_workspace

    return _workspace_registry_call(
        inspect_local_workspace,
        alias,
        include_contract=include_contract,
    )


def workspace_register_workspace(
    alias: str,
    root: str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    from apatch.mcp.workspace_registry import register_local_workspace

    return _workspace_registry_call(
        register_local_workspace,
        alias,
        root,
        force=force,
    )


def workspace_remove_workspace(alias: str) -> Dict[str, Any]:
    from apatch.mcp.workspace_registry import remove_local_workspace

    return _workspace_registry_call(remove_local_workspace, alias)


def workspace_roaming_status_workspace(
    effective_workspace: str,
    *,
    bound_workspace: Optional[str] = None,
) -> Dict[str, Any]:
    from apatch.mcp.workspace_registry import local_roaming_status

    return _workspace_registry_call(
        local_roaming_status,
        effective_workspace,
        bound_workspace=bound_workspace,
    )


def extension_list_workspace(
    target_dir: str = ".",
    *,
    lock_path: str = ".apatch/extensions.lock.json",
    include_disabled: bool = True,
) -> Dict[str, Any]:
    from apatch.extensions.host import list_extensions
    return list_extensions(target_dir, lock_path=lock_path, include_disabled=include_disabled)


def extension_inspect_workspace(
    target_dir: str,
    *,
    extension_id: str,
    lock_path: str = ".apatch/extensions.lock.json",
) -> Dict[str, Any]:
    from apatch.extensions.host import inspect_extension
    return inspect_extension(target_dir, extension_id, lock_path=lock_path)


def extension_validate_workspace(
    target_dir: str = ".",
    *,
    extension_id: str = "",
    lock_path: str = ".apatch/extensions.lock.json",
) -> Dict[str, Any]:
    from apatch.extensions.host import validate_extensions
    return validate_extensions(target_dir, extension_id, lock_path=lock_path)


def extension_run_workspace(
    target_dir: str,
    *,
    tool_id: str,
    arguments: Any,
    lock_path: str = ".apatch/extensions.lock.json",
) -> Dict[str, Any]:
    from apatch.extensions.host import run_extension_tool
    return run_extension_tool(target_dir, tool_id, arguments, lock_path=lock_path)
