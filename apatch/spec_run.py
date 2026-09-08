"""RFP-009 — Spec Run: batch execution of an entire executable spec (SPEC-RUN-1).

Orchestrates all pending requirements ``Rk`` from ``docs/specs/SPEC-<ID>.md`` in document
order. Each ``Rk`` runs a full governed cycle (session → mutate → verify → attest) via
``execute_next_workspace`` internals; TrustChain invariant is preserved per requirement.

Needle delivery (pick one — no driver scripts)
-----------------------------------------------
**Compact specs** — inline ``requirements`` in one MCP call::

    apatch_spec_run(spec="SPEC-X", requirements={
        "R1": {
            "completion_summary": "Created the declared deliverable and passed its check.",
            "needles": [{"action": "create", ...}],
        },
    })

**Large needles** — versioned manifest in repo (preferred over huge inline JSON)::

    apatch_spec_run(spec="SPEC-X", manifest_path="manifests/SPEC-X.run.json")

``dry_run=true`` returns ``manifest_template`` + ``delivery_recommendation``. Save the
template to ``manifests/SPEC-X.run.json`` when inline payload would be unwieldy.
**Never** use Python driver scripts (``manifests/*-spec.py``) calling
``spec_run_enriched()`` — that bypasses MCP/TrustChain lifecycle.

Checkpoint state
----------------
``.apatch/spec_run.json`` tracks ``rk_index``, ``manifest_sha256``, ``per_rk`` status,
``last_checkpoint``. ``resume=true`` (default) continues; ``reset=true`` clears;
``abort=true`` rolls back last checkpoint. Manifest hash mismatch → ``MANIFEST_DRIFT``.

Chunking
--------
``chunk_rk_per_call=0`` (default) processes **all** pending ``Rk`` in one MCP tick.
Set ``chunk_rk_per_call=1`` for long specs (MCP timeout safety). Response ``continue``
tells the agent to call ``apatch_spec_run(spec=...)`` again.

Public API
----------
- ``spec_run_workspace`` / ``spec_run_enriched`` — MCP ``apatch_spec_run``
- ``lint_run_manifest_workspace`` / ``spec_run_manifest_lint_enriched`` — manifest lint
- ``load_spec_run_state`` / ``save_spec_run_state`` / ``clear_spec_run_state``

See: ``docs/RFP-009-spec-run.md``, consumer ``AGENTS.template.md`` §3K.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from apatch.failure_taxonomy import ERROR_VERIFY_FAILED
from apatch.spec_executor import (
    ERROR_SPEC_DEPENDENCY_UNMET,
    bind_runtime_to_active_session,
    check_spec_dependencies,
    execute_next_workspace,
)

DEFAULT_SPEC_RUN_PATH = os.path.join(".apatch", "spec_run.json")


def _spec_run_rel_path(target_dir: str) -> str:
    from apatch.lane import lane_state_path

    return lane_state_path(target_dir, "spec_run.json")
ERROR_MANIFEST_GAP = "MANIFEST_GAP"
ERROR_MANIFEST_DRIFT = "MANIFEST_DRIFT"
ERROR_SPEC_RUN_BLOCKED = "SPEC_RUN_BLOCKED"
ERROR_PLAN_DRIFT = "PLAN_DRIFT"  # R4
ERROR_SPEC_INTERFERENCE_CYCLE = "SPEC_INTERFERENCE_CYCLE"
ERROR_SPEC_INTERFERENCE_STALE = "SPEC_INTERFERENCE_STALE"
ERROR_SPEC_RUN_ORDER_BLOCKED = "SPEC_RUN_ORDER_BLOCKED"

_OPEN_STATES = frozenset({"pending", "in_progress", "stale"})
_VALID_ACTIONS = frozenset({"replace", "create", "delete", "rename", "chmod"})


def _skip_attested_requirement(entry: Any) -> bool:
    """Explicit non-empty maintenance needles reopen attested evidence by default."""
    if not isinstance(entry, dict):
        return True
    if "skip_if_attested" in entry:
        return bool(entry["skip_if_attested"])
    return not bool(entry.get("needles"))


def _result_applied_count(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    chunk = result.get("chunk_result") or {}
    return int(result.get("applied") or chunk.get("applied") or 0)


def _safe_needle_path(root: str, value: Any) -> Optional[str]:
    rel = str(value or "").strip()
    if not rel or os.path.isabs(rel):
        return None
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, rel))
    try:
        if os.path.commonpath([root_real, target]) != root_real:
            return None
    except ValueError:
        return None
    return target


def _needle_already_satisfied(root: str, needle: Any) -> bool:
    """Conservative postcondition check; unknown actions remain executable."""
    if not isinstance(needle, dict):
        return False
    action = str(needle.get("action") or "replace").strip().lower()
    target = _safe_needle_path(root, needle.get("target_file"))
    if action == "replace":
        find_text = needle.get("find_text", needle.get("old_content"))
        replace_text = needle.get("replace_text", needle.get("new_content"))
        if (
            target is None
            or not os.path.isfile(target)
            or not isinstance(find_text, str)
            or not find_text
            or not isinstance(replace_text, str)
            or not replace_text
        ):
            return False
        try:
            with open(target, encoding="utf-8") as handle:
                current = handle.read()
        except (OSError, UnicodeError):
            return False
        if find_text in current or current.count(replace_text) != 1:
            return False
        replacement = replace_text.strip()
        return (
            current.strip() == replacement
            or "\n" in replace_text
            or len(replacement) >= 32
        )
    if action == "create":
        content = needle.get("content")
        if target is None or not os.path.isfile(target) or not isinstance(content, str):
            return False
        try:
            with open(target, encoding="utf-8") as handle:
                return handle.read() == content
        except (OSError, UnicodeError):
            return False
    if action == "delete":
        return target is not None and not os.path.exists(target)
    if action == "rename":
        source = _safe_needle_path(root, needle.get("source_file"))
        return (
            source is not None
            and target is not None
            and not os.path.exists(source)
            and os.path.exists(target)
        )
    if action == "chmod" and target is not None and os.path.isfile(target):
        expected = needle.get("mode")
        if expected is None and "executable" in needle:
            expected_mode = 0o755 if bool(needle.get("executable")) else 0o644
        else:
            text = str(expected or "").lower().removeprefix("0o")
            try:
                expected_mode = int(text[-3:], 8)
            except (TypeError, ValueError):
                return False
        return (os.stat(target).st_mode & 0o777) == expected_mode
    return False


def _prepare_attested_requirements(
    root: str,
    status: Dict[str, Any],
    requirements: Dict[str, Any],
) -> tuple[Dict[str, Any], List[str], List[str], List[str]]:
    execution = {
        rid: dict(entry) if isinstance(entry, dict) else entry
        for rid, entry in requirements.items()
    }
    forced: List[str] = []
    already_satisfied: List[str] = []
    explicitly_skipped: List[str] = []
    for row in status.get("requirements") or []:
        if row.get("state") != "attested":
            continue
        rid = str(row.get("id") or "")
        entry = requirements.get(rid)
        if _skip_attested_requirement(entry):
            if isinstance(entry, dict) and entry.get("skip_if_attested") is True:
                explicitly_skipped.append(rid)
            continue
        needles = list((entry or {}).get("needles") or [])
        remaining = [
            needle
            for needle in needles
            if not _needle_already_satisfied(root, needle)
        ]
        if needles and not remaining:
            already_satisfied.append(rid)
            continue
        forced.append(rid)
        if isinstance(execution.get(rid), dict):
            execution[rid]["needles"] = remaining
    return execution, forced, already_satisfied, explicitly_skipped


def _spec_run_abs(target_dir: str, path: Optional[str] = None) -> str:
    if path:
        if os.path.isabs(path):
            return path
        return os.path.join(os.path.abspath(target_dir), path)
    return _spec_run_rel_path(target_dir)


def load_spec_run_state(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_spec_run_state(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    from apatch.artifact_governance import sync_run_state_registry, workspace_from_artifact_path

    ws = workspace_from_artifact_path(path)
    lease_id = data.get("spec_run_id") or data.get("session_id")
    sync_run_state_registry(
        ws,
        path,
        lease_id=str(lease_id) if lease_id else None,
        created_by_tool="apatch_spec_run",
        reason="spec run checkpoint",
        governed_session_id=data.get("session_id"),
    )


def clear_spec_run_state(path: str) -> None:
    from apatch.artifact_governance import (
        release_spec_run_scoped_artifacts,
        sync_run_state_registry,
        workspace_from_artifact_path,
    )

    ws = workspace_from_artifact_path(path)
    data = load_spec_run_state(path)
    if data:
        sid = str(data.get("session_id") or "")
        run_id = str(data.get("spec_run_id") or "")
        if sid or run_id:
            release_spec_run_scoped_artifacts(
                ws, session_id=sid, spec_run_id=run_id
            )
        sync_run_state_registry(
            ws,
            path,
            lease_id=None,
            created_by_tool="apatch_spec_run",
            reason="spec run completed",
            governed_session_id=data.get("session_id"),
        )
    if os.path.isfile(path):
        os.remove(path)


def _canonical_manifest_bytes(manifest: Dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")


def manifest_sha256(manifest: Dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_manifest_bytes(manifest)).hexdigest()
    return f"sha256:{digest}"


def load_run_manifest(
    *,
    manifest_path: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    target_dir: str = ".",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (manifest dict, abs path or None for inline)."""
    if manifest is not None:
        return dict(manifest), None
    if not manifest_path:
        return None, None
    abs_path = manifest_path
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(os.path.abspath(target_dir), manifest_path)
    if not os.path.isfile(abs_path):
        return None, abs_path
    with open(abs_path, encoding="utf-8") as fh:
        return json.load(fh), abs_path


def manifest_template_for_spec(spec_id: str, status: Dict[str, Any]) -> Dict[str, Any]:
    """Skeleton for dry-run / gap analysis — not written to disk."""
    reqs: Dict[str, Any] = {}
    for row in status.get("requirements") or []:
        rid = row.get("id")
        if not rid:
            continue
        reqs[rid] = {
            "completion_summary": "",
            "needles": [],
            "skip_if_attested": True,
        }
    return {
        "schema_version": 1,
        "spec": spec_id,
        "requirements": reqs,
        "options": {"chunk_rk_per_call": 0, "stop_on_first_failure": True},
    }


def resolve_run_manifest(
    root: str,
    *,
    spec: Optional[str],
    manifest_path: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    requirements: Optional[Dict[str, Any]] = None,
    spec_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Resolve run payload. Inline ``requirements`` is the primary agent path (no file staging)."""
    if manifest is not None:
        return dict(manifest), None, None
    if requirements is not None:
        return {
            "schema_version": 1,
            "spec": spec_id or spec or "",
            "requirements": requirements,
        }, None, None
    if manifest_path:
        mdoc, mpath = load_run_manifest(manifest_path=manifest_path, target_dir=root)
        if mdoc is None:
            return None, mpath, f"manifest not found: {manifest_path}"
        return mdoc, mpath, None
    return None, None, None


def _validate_needle(needle: Dict[str, Any], label: str) -> Optional[str]:
    if needle.get("tool_calls"):
        return None
    action = (needle.get("action") or "replace").lower()
    if action in _VALID_ACTIONS:
        if action == "create":
            if not needle.get("target_file") or needle.get("content") is None:
                return f"{label}: create requires target_file and content"
        elif action == "delete":
            if not needle.get("target_file"):
                return f"{label}: delete requires target_file"
        elif action == "rename":
            if not needle.get("source_file") or not needle.get("target_file"):
                return f"{label}: rename requires source_file and target_file"
        elif action == "chmod":
            if not needle.get("target_file"):
                return f"{label}: chmod requires target_file"
            if (
                needle.get("mode") is None
                and needle.get("file_mode") is None
                and needle.get("new_mode") is None
                and "executable" not in needle
            ):
                return f"{label}: chmod requires mode or executable"
        else:
            if not needle.get("find_text") and not needle.get("old_content"):
                return f"{label}: replace requires find_text"
        return None
    if needle.get("find_text") is not None or needle.get("old_content") is not None:
        return None
    return f"{label}: unknown action {action!r}"


def _req_ids_from_status(status: Dict[str, Any]) -> List[str]:
    return [r["id"] for r in status.get("requirements") or [] if r.get("id")]


def _spec_run_delivery_recommendation(
    spec_id: str,
    pending_count: int,
    *,
    has_manifest_path: bool = False,
) -> Dict[str, Any]:
    """Suggest inline vs manifest_path — avoids ad-hoc Python driver scripts."""
    manifest = f"manifests/{spec_id}.run.json"
    if has_manifest_path:
        return {
            "mode": "manifest_path",
            "agent_next": f"apatch_spec_run(spec={spec_id!r}, manifest_path={manifest!r})",
        }
    if pending_count >= 3:
        return {
            "mode": "manifest_path_recommended",
            "agent_next": (
                f"Save manifest_template → {manifest!r}; "
                f"apatch_spec_run(spec={spec_id!r}, manifest_path={manifest!r})"
            ),
            "alt": f"apatch_spec_run(spec={spec_id!r}, chunk_rk_per_call=1, requirements={{...}})",
            "never": "manifests/*-spec.py drivers calling spec_run_enriched()",
        }
    return {
        "mode": "inline_requirements",
        "agent_next": f"apatch_spec_run(spec={spec_id!r}, requirements={{Rk: {{needles: [...]}}}})",
    }


def _full_execution_plan(spec_id: str, pending_rows: List[Dict[str, Any]]) -> List[str]:
    steps = [
        f"apatch_spec_run(spec={spec_id!r}, dry_run=true)",
        _spec_run_delivery_recommendation(spec_id, len(pending_rows))["agent_next"],
    ]
    for row in pending_rows:
        token = f"{spec_id}#{row['id']}"
        steps.append(f"session_start(requirement={token!r})")
        steps.append(f"generate_batch(needles for {row['id']}) → apply_session")
        if row.get("verify"):
            steps.append(f"verify_run({row['verify']!r})")
        steps.append("attest → session_end")
    steps.append(f"apatch_spec_status(spec={spec_id!r})")
    return steps


def lint_run_manifest_workspace(
    target_dir: str = ".",
    *,
    manifest_path: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    allow_partial: bool = False,
) -> Dict[str, Any]:
    """Validate run manifest against SPEC.md (SPEC-RUN-1 R1)."""
    from apatch.spec import spec_lint_workspace, spec_status_workspace

    root = os.path.abspath(target_dir)
    mdoc, mpath = load_run_manifest(
        manifest_path=manifest_path, manifest=manifest, target_dir=root
    )
    if mdoc is None:
        return {
            "ok": False,
            "error": f"manifest not found: {manifest_path}",
            "error_type": ERROR_MANIFEST_GAP,
        }

    lint = spec_lint_workspace(root, spec=spec or mdoc.get("spec"), spec_path=spec_path)
    if not lint.get("ok"):
        return lint
    status = spec_status_workspace(
        root, spec=spec or mdoc.get("spec"), spec_path=spec_path
    )
    if not status.get("ok"):
        return status

    spec_id = status["spec"]
    if mdoc.get("spec") and mdoc.get("spec") != spec_id:
        return {
            "ok": False,
            "error": f"manifest spec {mdoc.get('spec')!r} != {spec_id!r}",
            "error_type": ERROR_MANIFEST_GAP,
        }

    known: Set[str] = set(_req_ids_from_status(status))
    reqs = mdoc.get("requirements") or {}
    if not isinstance(reqs, dict):
        return {
            "ok": False,
            "error": "manifest requirements must be an object",
            "error_type": ERROR_MANIFEST_GAP,
        }

    warnings: List[str] = []
    errors: List[str] = []
    gaps: List[str] = []

    for rk, entry in reqs.items():
        if rk not in known:
            warnings.append(f"extra requirement {rk!r} not in spec")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{rk}: requirement entry must be an object")
            continue
        needles = entry.get("needles") or []
        if not isinstance(needles, list):
            errors.append(f"{rk}: needles must be a list")
            continue
        for i, needle in enumerate(needles):
            if not isinstance(needle, dict):
                errors.append(f"{rk}: needle[{i}] must be an object")
                continue
            err = _validate_needle(needle, f"{rk}[{i}]")
            if err:
                errors.append(err)

    for row in status.get("requirements") or []:
        rid = row["id"]
        state = row.get("state")
        skip = _skip_attested_requirement(reqs.get(rid))
        if state == "attested" and skip:
            continue
        if state in _OPEN_STATES:
            entry = reqs.get(rid)
            if not isinstance(entry, dict) or "needles" not in entry:
                gaps.append(rid)

    if gaps and allow_partial:
        # Subset batch is a legal delivery: the inline manifest is the work
        # list, remaining pending Rk are reported, not fatal (RFP-009 UX).
        warnings.append(
            f"partial run: pending without needles skipped: {', '.join(gaps)}")
    elif gaps:
        return {
            "ok": False,
            "error": f"pending requirements without needles: {', '.join(gaps)}",
            "error_type": ERROR_MANIFEST_GAP,
            "gaps": gaps,
            "warnings": warnings,
            "manifest_sha256": manifest_sha256(mdoc),
            "manifest_path": mpath,
            "agent_next": (
                f"Needles required for each pending Rk (not auto-generated). "
                f"Per Rk: apatch_execute_next(spec=…, requirement='…#{gaps[0]}', needles=[…]). "
                f"Or fill all gaps then apatch_spec_run. dry_run=true needs no needles."
            ),
            "recommended_tool": "apatch_execute_next",
        }

    if errors:
        return {
            "ok": False,
            "error": "; ".join(errors),
            "error_type": ERROR_MANIFEST_GAP,
            "errors": errors,
            "warnings": warnings,
        }

    result = {
        "ok": True,
        "skipped_pending": gaps if allow_partial else [],
        "spec": spec_id,
        "manifest_sha256": manifest_sha256(mdoc),
        "manifest_path": mpath,
        "warnings": warnings,
        "gaps": [],
    }
    if reqs:
        from apatch.spec_registry import update_spec_registry

        update_spec_registry(
            root,
            spec_id,
            mdoc,
            source_path=status.get("source_path"),
            manifest_sha256=result["manifest_sha256"],
        )
    return result


def _blocked_run(
    message: str,
    *,
    error_type: str = ERROR_SPEC_RUN_BLOCKED,
    recommended_action: str = "rollback",
    **extra: Any,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "error_type": error_type,
        "recommended_action": recommended_action,
        "recoverable": True,
        "execution_phase": "blocked",
        **extra,
    }


_FAILURE_PASSTHROUGH_FIELDS = (
    "verify_output",
    "diagnostics",
    "diagnostic_count",
    "diagnostics_artifact",
    "verify_rollback",
    "rollback_performed",
    "chunk_result",
    "checkpoint",
    "rollback_hint",
    "baseline",
)


def _failure_passthrough(result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep actionable inner failure evidence on the outer spec-run response."""
    return {key: result[key] for key in _FAILURE_PASSTHROUGH_FIELDS if key in result}


def _result_checkpoints(result: Dict[str, Any]) -> List[str]:
    values = result.get("checkpoints") or []
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for value in values:
        checkpoint = str(value or "").strip()
        if checkpoint and checkpoint not in out:
            out.append(checkpoint)
    last = str(result.get("checkpoint") or result.get("last_checkpoint") or "").strip()
    if last and last not in out:
        out.append(last)
    return out


def _pending_rows(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        r
        for r in status.get("requirements") or []
        if r.get("state") in _OPEN_STATES
    ]


def _run_one_requirement(
    root: str,
    spec_id: str,
    req_row: Dict[str, Any],
    needles: List[Dict[str, Any]],
    *,
    logs_path: str,
    verify_deferred: bool,
    previously_failed: bool = False,
    verify_override: Optional[str] = None,
    completion_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Governed cycle for a single Rk: session → mutate → finalize."""
    from apatch.runtime.runtime import MutationRuntime

    req_id = req_row["id"]
    req_token = f"{spec_id}#{req_id}"
    verify_cmd = verify_override or req_row.get("verify")
    steps: List[str] = []

    sess = execute_next_workspace(
        root,
        spec=spec_id,
        requirement=req_token,
        skip_lint=True,
        check_dependencies=False,
    )
    steps.extend(sess.get("steps_completed") or [])
    if not sess.get("ok"):
        sess["steps_completed"] = steps
        sess["requirement_token"] = req_token
        return sess

    if verify_cmd:
        from apatch.tool_paths import run_shell_verify

        v_ok, _ = run_shell_verify(verify_cmd, root)
        if not needles:
            rt = MutationRuntime(root)
            bind_runtime_to_active_session(rt, root)
            if not v_ok:
                steps.append("verify_precheck_failed")
                end = rt.close_session()
                steps.append("session_end")
                blocked = _blocked_run(
                    "verify-only requirement failed before noop attestation",
                    error_type=ERROR_SPEC_RUN_BLOCKED,
                    recommended_action="fix_forward",
                    requirement_token=req_token,
                    current_requirement=req_row,
                    steps_completed=steps,
                )
                blocked["session_end"] = end
                return blocked

            noop = rt.noop_attest([req_id], message=f"verify-only {req_token}")
            steps.append("verify_precheck_passed")
            steps.append("noop_attest")
            if not noop.get("ok"):
                noop["steps_completed"] = steps
                noop["requirement_token"] = req_token
                return noop
            end = rt.close_session()
            steps.append("session_end")
            if not end.get("ok"):
                end["steps_completed"] = steps
                end["requirement_token"] = req_token
                return end
            from apatch.spec import spec_status_workspace

            refreshed = spec_status_workspace(root, spec=spec_id)
            att_row = None
            if refreshed.get("ok"):
                att_row = next(
                    (r for r in refreshed.get("requirements") or [] if r.get("id") == req_id),
                    None,
                )
            return {
                "ok": True,
                "requirement_token": req_token,
                "requirement": att_row or req_row,
                "requirement_attested": (att_row or {}).get("state") == "attested",
                "steps_completed": steps,
                "skipped_mutations": True,
                "spec_status": refreshed,
            }

        # Retry path: if a previous mutation failed but the requirement now verifies,
        # finish the existing governed session without applying another patch.
        if v_ok and previously_failed:
            fin = execute_next_workspace(
                root,
                spec=spec_id,
                requirement=req_token,
                finalize=True,
                completion_summary=completion_summary,
                verify_override=verify_override,
                skip_lint=True,
                check_dependencies=False,
            )
            steps.append("verify_precheck_passed")
            steps.extend(fin.get("steps_completed") or [])
            if not fin.get("ok"):
                fin["steps_completed"] = steps
                fin["requirement_token"] = req_token
                return fin
            return {
                "ok": True,
                "requirement_token": req_token,
                "requirement": req_row,
                "requirement_attested": fin.get("requirement_attested"),
                "steps_completed": steps,
                "skipped_mutations": True,
            }

    mut = execute_next_workspace(
        root,
        spec=spec_id,
        requirement=req_token,
        needles=needles,
        logs_path=logs_path,
        verify_deferred=verify_deferred,
        verify_override=verify_override,
        skip_lint=True,
        check_dependencies=False,
    )
    steps.extend(mut.get("steps_completed") or [])
    if not mut.get("ok"):
        mut["steps_completed"] = steps
        mut["requirement_token"] = req_token
        return mut

    applied_count = _result_applied_count(mut)
    abs_logs = mut.get("logs_path")
    if not abs_logs:
        from apatch.artifact_governance import resolve_ephemeral_logs_path

        abs_logs, _ = resolve_ephemeral_logs_path(root, logs_path)

    rt = MutationRuntime(root)
    bind_runtime_to_active_session(rt, root)
    apply_out = mut
    while apply_out.get("continue"):
        apply_out = rt.apply_session(
            abs_logs,
            verify=verify_cmd,
            verify_deferred=verify_deferred,
            quiet=True,
        )
        steps.append("apply_session")
        applied_count += _result_applied_count(apply_out)
        if not apply_out.get("ok") or apply_out.get("verify_rollback"):
            apply_out["steps_completed"] = steps
            apply_out["requirement_token"] = req_token
            return apply_out

    fin = execute_next_workspace(
        root,
        spec=spec_id,
        requirement=req_token,
        finalize=True,
        completion_summary=completion_summary,
        verify_override=verify_override,
        skip_lint=True,
        check_dependencies=False,
    )
    steps.extend(fin.get("steps_completed") or [])
    if not fin.get("ok"):
        fin["steps_completed"] = steps
        fin["requirement_token"] = req_token
        return fin

    checkpoints = _result_checkpoints(apply_out)
    return {
        "ok": True,
        "requirement_token": req_token,
        "requirement": req_row,
        "requirement_attested": fin.get("requirement_attested"),
        "steps_completed": steps,
        "checkpoint": checkpoints[-1] if checkpoints else None,
        "checkpoints": checkpoints,
        "applied": applied_count,
    }


def spec_run_workspace(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    requirements: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    reset: bool = False,
    abort: bool = False,
    resume: bool = True,
    chunk_rk_per_call: int = 0,
    logs_path: str = "patches-spec-run.jsonl",
    verify_deferred: bool = True,
    skip_lint: bool = False,
    skip_rfp_coverage: bool = False,
    rfp: Optional[str] = None,
    check_dependencies: bool = True,
    stop_on_first_failure: bool = True,
    plan: Optional[str] = None,
    interference_check: bool = False,
    peer_specs: Optional[List[str]] = None,
    allow_partial: bool = False,
) -> Dict[str, Any]:
    """Batch-run pending requirements for one executable spec (SPEC-RUN-1).

    Args:
        target_dir: Workspace root.
        spec / spec_path: SPEC id or path to ``docs/specs/SPEC-*.md``.
        requirements: **Primary** inline map ``{Rk: {needles: [...]}}``.
        manifest_path / manifest: Optional file or dict (CI/review); same schema.
        dry_run: Lint + deps + full plan; no mutations. Without payload → ``manifest_template``.
        reset: Delete ``.apatch/spec_run.json`` before run.
        abort: Rollback ``last_checkpoint`` and clear spec_run state.
        resume: Load existing spec_run state (default True).
        chunk_rk_per_call: Max Rk per call; 0 = all pending in one tick.
        logs_path: Base JSONL path; per-Rk suffix ``-r1`` when multiple Rk.
        verify_deferred: Defer shell verify until end of apply_session per Rk.
        skip_lint / check_dependencies: Gates (same as execute_next).
        stop_on_first_failure: Block entire run on first Rk failure (default True).

    Returns:
        Dict with ``ok``, ``continue``, ``done``, ``execution_phase``, ``progress``,
        ``manifest_sha256``, ``agent_next``. On failure: ``error_type``,
        ``requirement_token``, ``recommended_action``, ``resume_hint``.
    """
    from apatch.spec import spec_lint_workspace, spec_status_workspace
    from apatch.workflows import rollback_workspace

    root = os.path.abspath(target_dir)
    state_path = _spec_run_abs(root)
    steps_completed: List[str] = []

    if abort:
        state = load_spec_run_state(state_path)
        ckpt = (state or {}).get("last_checkpoint")
        rollback_info = None
        if ckpt:
            rollback_info = rollback_workspace(root, ckpt)
        clear_spec_run_state(state_path)
        return {
            "ok": True,
            "aborted": True,
            "checkpoint": ckpt,
            "rollback": rollback_info,
            "continue": False,
            "agent_next": "Fix issue, then apatch_spec_run(reset=true, ...)",
        }

    if reset:
        clear_spec_run_state(state_path)

    pre_mdoc, _, _ = resolve_run_manifest(
        root,
        spec=spec,
        manifest_path=manifest_path,
        manifest=manifest,
        requirements=requirements,
        spec_id=spec,
    )
    effective_spec = spec or ((pre_mdoc or {}).get("spec") if pre_mdoc else None)
    from apatch.worktree_lane import prepare_execution_root

    exec_prep = prepare_execution_root(target_dir, spec=effective_spec)
    root = os.path.abspath(exec_prep["path"])
    state_path = _spec_run_abs(root)

    lint_spec: Optional[Dict[str, Any]] = None
    if not skip_lint:
        lint_spec = spec_lint_workspace(root, spec=effective_spec, spec_path=spec_path)
        steps_completed.append("lint")
        if not lint_spec.get("ok") or not lint_spec.get("passed"):
            return _blocked_run(
                lint_spec.get("error", "spec lint failed"),
                error_type=ERROR_MANIFEST_GAP,
                recommended_action="reduce_scope",
                lint=lint_spec,
                steps_completed=steps_completed,
            )

    if not skip_rfp_coverage:
        from apatch.rfp_coverage import (
            infer_rfp_id_from_spec,
            read_spec_text,
            rfp_spec_coverage_workspace,
            spec_has_rfp_traceability,
        )

        _, spec_raw = read_spec_text(root, effective_spec or "", spec_path=spec_path)
        if spec_raw and (spec_has_rfp_traceability(spec_raw) or rfp):
            rfp_id = rfp or infer_rfp_id_from_spec(spec_raw)
            if rfp_id:
                cov = rfp_spec_coverage_workspace(
                    root,
                    rfp=rfp_id,
                    spec=effective_spec,
                    spec_path=spec_path,
                )
                steps_completed.append("rfp_coverage")
                if not cov.get("ok") or not cov.get("passed"):
                    return _blocked_run(
                        cov.get("error") or "rfp→spec coverage failed",
                        error_type=ERROR_MANIFEST_GAP,
                        recommended_action="reduce_scope",
                        rfp_coverage=cov,
                        steps_completed=steps_completed,
                    )

    status = spec_status_workspace(root, spec=effective_spec, spec_path=spec_path)
    if not status.get("ok"):
        return status

    spec_id = status["spec"]
    source_path = status.get("source_path")
    plan_id: Optional[str] = None
    plan_sha256_val: Optional[str] = None
    plan_version: Optional[int] = None
    if plan:
        from apatch.spec_plan import (
            plan_sha256 as _plan_sha256,
            plan_to_requirements,
            resolve_plan_version,
        )

        loaded_plan, plan_version, perr = resolve_plan_version(root, spec_id, plan)
        if perr or not loaded_plan:
            return {
                "ok": False,
                "error": perr or "registered plan not found",
                "error_type": ERROR_MANIFEST_GAP,
                "steps_completed": steps_completed,
            }
        if requirements is None:
            requirements = plan_to_requirements(loaded_plan)
        plan_sha256_val = _plan_sha256(loaded_plan)
        plan_id = f"plan:{spec_id}@v{plan_version}"


    mdoc, mpath, merr = resolve_run_manifest(
        root,
        spec=spec,
        manifest_path=manifest_path,
        manifest=manifest,
        requirements=requirements,
        spec_id=spec_id,
    )
    manifest_template: Optional[Dict[str, Any]] = None
    if mdoc is None:
        if dry_run:
            mdoc = manifest_template_for_spec(spec_id, status)
            manifest_template = mdoc
            mpath = None
        else:
            return {
                "ok": False,
                "error": merr
                or (
                    "pass requirements={Rk: {needles: [...]}} inline or "
                    "manifest_path='manifests/SPEC-X.run.json' (versioned — not ephemeral staging)"
                ),
                "error_type": ERROR_MANIFEST_GAP,
                "agent_next": (
                    f"apatch_spec_run(spec={spec_id!r}, dry_run=true) for manifest_template, "
                    f"then apatch_spec_run(spec={spec_id!r}, requirements={{...}})"
                ),
                "steps_completed": steps_completed,
            }
    elif mdoc.get("spec") and mdoc.get("spec") != spec_id:
        mdoc["spec"] = spec_id

    mhash = manifest_sha256(mdoc)

    mlint = lint_run_manifest_workspace(
        root,
        manifest=mdoc,
        spec=spec_id,
        spec_path=spec_path,
        allow_partial=allow_partial,
    )
    steps_completed.append("manifest_lint")
    if not mlint.get("ok") and not dry_run:
        return {**mlint, "steps_completed": steps_completed}

    deps: Optional[Dict[str, Any]] = None
    if check_dependencies:
        deps = check_spec_dependencies(root, spec_id, source_path=source_path)
        steps_completed.append("dependencies")
        if not deps.get("ok"):
            unmet = ", ".join(deps.get("unmet") or [])
            return _blocked_run(
                f"upstream spec dependencies not attested: {unmet}",
                error_type=ERROR_SPEC_DEPENDENCY_UNMET,
                recommended_action="reduce_scope",
                dependencies=deps,
                steps_completed=steps_completed,
            )

    if mlint.get("ok") and (mdoc.get("requirements") or {}):
        from apatch.spec_registry import update_spec_registry

        reg = update_spec_registry(
            root,
            spec_id,
            mdoc,
            source_path=source_path,
            manifest_sha256=mhash,
        )
        if reg.get("ok"):
            steps_completed.append("registry_update")

    run_interference_gate = bool(peer_specs) or interference_check
    if run_interference_gate and peer_specs and not dry_run and mlint.get("ok"):
        from apatch.spec_interference import is_spec_fully_attested, spec_interference_workspace

        peer_list = [str(s) for s in peer_specs if s and str(s) != spec_id]
        if peer_list:
            icheck = spec_interference_workspace(
                root,
                specs=[spec_id] + peer_list,
                level=2,
            )
            steps_completed.append("interference_check")
            if icheck.get("has_cycle"):
                return _blocked_run(
                    "cross-spec interference cycle; refactor needles or reorder peer_specs",
                    error_type=ERROR_SPEC_INTERFERENCE_CYCLE,
                    recommended_action="refactor_needles",
                    interference=icheck,
                    steps_completed=steps_completed,
                )
            if icheck.get("validity") == "stale":
                return _blocked_run(
                    "interference snapshot stale; re-run interference before spec_run",
                    error_type=ERROR_SPEC_INTERFERENCE_STALE,
                    recommended_action="re_run_interference",
                    interference=icheck,
                    steps_completed=steps_completed,
                )
            safe_order = list(icheck.get("safe_order") or [])
            if spec_id in safe_order:
                preds = safe_order[: safe_order.index(spec_id)]
                blocked_by = [p for p in preds if not is_spec_fully_attested(root, p)]
                if blocked_by:
                    return _blocked_run(
                        f"predecessors not attested: {blocked_by}",
                        error_type=ERROR_SPEC_RUN_ORDER_BLOCKED,
                        recommended_action="complete_predecessor_first",
                        blocked_by=blocked_by,
                        interference=icheck,
                        steps_completed=steps_completed,
                    )

    if not dry_run and not reset and resume:
        early = load_spec_run_state(state_path)
        if early:
            if early.get("spec") != spec_id:
                return _blocked_run(
                    f"active spec_run for {early.get('spec')!r}; pass reset=true",
                    error_type=ERROR_MANIFEST_DRIFT,
                    recommended_action="retry_chunk",
                )

            if plan_sha256_val and early.get("plan_sha256") and early.get("plan_sha256") != plan_sha256_val:
                return _blocked_run(
                    "registered plan changed during active spec_run; pass reset=true",
                    error_type=ERROR_PLAN_DRIFT,
                    recommended_action="retry_chunk",
                    plan_sha256=plan_sha256_val,
                    active_plan_sha256=early.get("plan_sha256"),
                )

            if early.get("manifest_sha256") != mhash:
                return _blocked_run(
                    "manifest changed during active spec_run; pass reset=true",
                    error_type=ERROR_MANIFEST_DRIFT,
                    recommended_action="retry_chunk",
                    manifest_sha256=mhash,
                    active_sha256=early.get("manifest_sha256"),
                )

    reqs_manifest = mdoc.get("requirements") or {}
    (
        execution_requirements,
        forced_attested,
        already_satisfied_requirements,
        explicitly_skipped_requirements,
    ) = _prepare_attested_requirements(root, status, reqs_manifest)

    if status.get("done") and not forced_attested:
        clear_spec_run_state(state_path)
        return {
            "ok": True,
            "done": True,
            "continue": False,
            "execution_phase": "complete",
            "spec": spec_id,
            "summary": status.get("summary"),
            "hint": "All requirements attested.",
            "applied": 0,
            "already_satisfied_requirements": already_satisfied_requirements,
            "explicitly_skipped_requirements": explicitly_skipped_requirements,
            "steps_completed": steps_completed,
            "lint": lint_spec,
            "dependencies": deps,
            "manifest_sha256": mhash,
            "plan_id": plan_id,
            "plan_sha256": plan_sha256_val,
        }

    rk_order = _req_ids_from_status(status)
    pending_rows = [
        row
        for row in status.get("requirements") or []
        if row.get("state") in _OPEN_STATES or row["id"] in forced_attested
    ]
    skipped_attested = [
        r["id"]
        for r in status.get("requirements") or []
        if r.get("state") == "attested"
        and _skip_attested_requirement(reqs_manifest.get(r["id"]))
    ]

    if dry_run:
        pending_plan = []
        gaps: List[str] = list(mlint.get("gaps") or [])
        for row in pending_rows:
            entry = reqs_manifest.get(row["id"]) or {}
            has_needles = isinstance(entry, dict) and "needles" in entry
            has_nonempty_needles = bool(entry.get("needles")) if isinstance(entry, dict) else False
            pending_plan.append(
                {
                    "id": row["id"],
                    "title": row.get("title"),
                    "verify": row.get("verify"),
                    "has_needles": has_needles,
                    "state": row.get("state"),
                }
            )
            if (
                (not has_needles or (manifest_template is not None and not has_nonempty_needles))
                and row["id"] not in gaps
            ):
                gaps.append(row["id"])
        from apatch.spec_needles_scaffold import spec_needles_scaffold_workspace

        needles_scaffold = (
            spec_needles_scaffold_workspace(
                root, spec=spec_id, spec_path=spec_path, skip_lint=True
            )
            if pending_plan
            else None
        )
        agent_next = (
            (needles_scaffold or {}).get("agent_next")
            or (
                f"apatch_spec_run(spec={spec_id!r}, requirements={{"
                f"{pending_plan[0]['id'] if pending_plan else 'R1'}: {{needles: [...]}}, ...}})"
            )
        )
        return {
            "ok": True,
            "dry_run": True,
            "execution_phase": "discover",
            "spec": spec_id,
            "pending": pending_plan,
            "gaps": gaps,
            "skipped_attested": skipped_attested,
            "already_satisfied_requirements": already_satisfied_requirements,
            "explicitly_skipped_requirements": explicitly_skipped_requirements,
            "applied": 0,
            "manifest_sha256": mhash,
            "manifest_path": mpath,
            "manifest_lint": mlint,
            "execution_plan": _full_execution_plan(spec_id, pending_rows),
            "summary": status.get("summary"),
            "lint": lint_spec,
            "dependencies": deps,
            "steps_completed": steps_completed,
            "manifest_template": manifest_template,
            "needles_scaffold": needles_scaffold,
            "plan_scaffold": (needles_scaffold or {}).get("plan_scaffold"),
            "agent_next": agent_next,
        }

    state = None if reset else (load_spec_run_state(state_path) if resume else None)
    if state:
        if state.get("spec") != spec_id:
            return _blocked_run(
                f"active spec_run for {state.get('spec')!r}; pass reset=true",
                error_type=ERROR_MANIFEST_DRIFT,
                recommended_action="retry_chunk",
            )
        if state.get("manifest_sha256") != mhash:
            return _blocked_run(
                "manifest changed during active spec_run; pass reset=true",
                error_type=ERROR_MANIFEST_DRIFT,
                recommended_action="retry_chunk",
                manifest_sha256=mhash,
                active_sha256=state.get("manifest_sha256"),
            )
    else:
        state = {
            "spec_run_id": f"spec_run_{int(time.time())}",
            "spec": spec_id,
            "manifest_sha256": mhash,
            "manifest_path": mpath,
            "rk_order": rk_order,
            "rk_index": 0,
            "per_rk": {rid: "pending" for rid in rk_order},
            "attested": [],
            "last_checkpoint": None,
            "checkpoints": [],
            "options": {
                "chunk_rk_per_call": chunk_rk_per_call,
                "stop_on_first_failure": stop_on_first_failure,
            },
        }
        save_spec_run_state(state_path, state)

    idx = int(state.get("rk_index", 0))
    options = mdoc.get("options") or {}
    chunk = int(options.get("chunk_rk_per_call", chunk_rk_per_call))
    if chunk <= 0:
        chunk = max(1, len(rk_order) - idx)
    stop_fail = bool(options.get("stop_on_first_failure", stop_on_first_failure))
    attested_list: List[str] = list(state.get("attested") or [])
    per_rk: Dict[str, str] = dict(state.get("per_rk") or {})
    last_result: Optional[Dict[str, Any]] = None
    current_req: Optional[Dict[str, Any]] = None
    applied_total = 0

    processed = 0
    while idx < len(rk_order) and processed < chunk:
        rid = rk_order[idx]
        row = next((r for r in status.get("requirements") or [] if r["id"] == rid), None)
        if row is None:
            idx += 1
            continue

        if row.get("state") == "attested" and rid not in forced_attested:
            satisfied = rid in already_satisfied_requirements
            per_rk[rid] = "already_satisfied" if satisfied else "skipped"
            if rid not in attested_list:
                attested_list.append(rid)
            idx += 1
            processed += 1
            steps_completed.append(
                f"already_satisfied:{rid}" if satisfied else f"skip_attested:{rid}"
            )
            continue

        if row.get("state") not in _OPEN_STATES and row.get("state") != "attested":
            idx += 1
            continue

        entry = execution_requirements.get(rid) or {}
        if allow_partial and (not isinstance(entry, dict) or "needles" not in entry):
            per_rk[rid] = "skipped_partial"
            idx += 1
            steps_completed.append(f"skip_partial:{rid}")
            continue
        if not isinstance(entry, dict) or "needles" not in entry:
            return _blocked_run(
                f"requirement {rid} has no needles entry in manifest",
                error_type=ERROR_MANIFEST_GAP,
                recommended_action="reduce_scope",
                requirement_token=f"{spec_id}#{rid}",
                steps_completed=steps_completed,
                recommended_tool="apatch_execute_next",
                agent_next=(
                    f"Author needles from source for {rid} → "
                    f"apatch_execute_next(spec={spec_id!r}, requirement='{spec_id}#{rid}', needles=[…])"
                ),
            )
        needles = entry.get("needles") or []
        if not isinstance(needles, list):
            return _blocked_run(
                f"requirement {rid} needles must be a list",
                error_type=ERROR_MANIFEST_GAP,
                recommended_action="reduce_scope",
                requirement_token=f"{spec_id}#{rid}",
                steps_completed=steps_completed,
            )

        was_failed = per_rk.get(rid) == "failed"
        per_rk[rid] = "running"
        current_req = row
        rk_logs = logs_path
        if len(rk_order) > 1:
            base, ext = os.path.splitext(logs_path)
            rk_logs = f"{base}-{rid.lower()}{ext}"

        result = _run_one_requirement(
            root,
            spec_id,
            row,
            needles,
            logs_path=rk_logs,
            verify_deferred=verify_deferred,
            previously_failed=was_failed,
            verify_override=entry.get("verify_override"),
            completion_summary=entry.get("completion_summary"),
        )
        steps_completed.extend(result.get("steps_completed") or [])
        last_result = result
        applied_total += _result_applied_count(result)
        result_checkpoints = _result_checkpoints(result)
        state_checkpoints = list(state.get("checkpoints") or [])
        for checkpoint in result_checkpoints:
            if checkpoint not in state_checkpoints:
                state_checkpoints.append(checkpoint)
        state["checkpoints"] = state_checkpoints
        if result_checkpoints:
            state["last_checkpoint"] = result_checkpoints[-1]

        if not result.get("ok"):
            per_rk[rid] = "failed"
            state.update(
                {
                    "rk_index": idx,
                    "per_rk": per_rk,
                    "attested": attested_list,
                    "last_checkpoint": state.get("last_checkpoint"),
                    "checkpoints": state_checkpoints,
                }
            )
            save_spec_run_state(state_path, state)
            verify_failure = bool(
                result.get("verify_rollback")
                or result.get("error_type") == ERROR_VERIFY_FAILED
            )
            chunk_result = result.get("chunk_result") or {}
            rollback_performed = bool(
                result.get("rollback_performed")
                or chunk_result.get("rollback_performed")
            )
            error_type = result.get("error_type") or (
                ERROR_VERIFY_FAILED if verify_failure else ERROR_SPEC_RUN_BLOCKED
            )
            recommended_action = result.get("recommended_action") or (
                "fix_forward" if rollback_performed else "rollback"
            )
            recovery_agent_next = result.get("agent_next") or (
                "Call apatch_resume_session, apply corrected needles, then re-verify and attest."
                if rollback_performed
                else f"Roll back the failed checkpoint, then resume apatch_spec_run(spec={spec_id!r})."
            )
            resume_hint = (
                f"apatch_resume_session() then apply corrected needles and apatch_spec_run(spec={spec_id!r}, resume=true)"
                if rollback_performed
                else f"apatch_rollback(session_id=...) then apatch_spec_run(spec={spec_id!r}, resume=true)"
            )
            out = _blocked_run(
                result.get("error", "requirement failed"),
                error_type=error_type,
                recommended_action=recommended_action,
                requirement_token=result.get("requirement_token"),
                current_requirement=row,
                progress=_progress(attested_list, rk_order, spec_id),
                steps_completed=steps_completed,
                resume_hint=resume_hint,
                agent_next=recovery_agent_next,
                checkpoints=state_checkpoints,
                last_checkpoint=state.get("last_checkpoint"),
                **_failure_passthrough(result),
                **{"continue": False},
            )
            if stop_fail:
                return out
            idx += 1
            processed += 1
            continue

        per_rk[rid] = "attested"
        if rid not in attested_list:
            attested_list.append(rid)
        idx += 1
        processed += 1
        steps_completed.append(f"rk_complete:{rid}")

    state.update(
        {
            "rk_index": idx,
            "per_rk": per_rk,
            "attested": attested_list,
        }
    )

    refreshed = spec_status_workspace(root, spec=spec_id, spec_path=spec_path)
    run_complete = idx >= len(rk_order)
    all_done = run_complete or bool(refreshed.get("done"))
    continue_run = not run_complete and idx < len(rk_order)

    if all_done:
        clear_spec_run_state(state_path)
    else:
        save_spec_run_state(state_path, state)

    progress = _progress(attested_list, rk_order, spec_id, refreshed=refreshed)
    agent_next = (
        f"apatch_spec_run(spec={spec_id!r})"
        if continue_run
        else f"apatch_spec_status(spec={spec_id!r})"
    )

    skipped_partial = sorted(
        rid for rid, st in per_rk.items() if st == "skipped_partial")
    return {
        "ok": True,
        "continue": continue_run,
        "done": all_done and not skipped_partial,
        "skipped_pending": skipped_partial,
        "execution_phase": "complete" if all_done and not skipped_partial else "running",
        "spec": spec_id,
        "spec_run_id": state.get("spec_run_id"),
        "current_requirement": (
            {
                "id": current_req["id"],
                "token": f"{spec_id}#{current_req['id']}",
                "title": current_req.get("title"),
            }
            if current_req
            else None
        ),
        "progress": progress,
        "manifest_sha256": mhash,
        "steps_completed": steps_completed,
        "applied": applied_total,
        "already_satisfied_requirements": already_satisfied_requirements,
        "explicitly_skipped_requirements": explicitly_skipped_requirements,
        "last_checkpoint": state.get("last_checkpoint"),
        "checkpoints": list(state.get("checkpoints") or []),
        "last_rk_result": last_result,
        "spec_status": refreshed if all_done else None,
        "agent_next": agent_next,
    }


def _progress(
    attested: List[str],
    rk_order: List[str],
    spec_id: str,
    refreshed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    total = len(rk_order)
    done = len(attested)
    return {
        "rk_done": done,
        "rk_total": total,
        "percent": round(100.0 * done / total, 1) if total else 100.0,
        "attested": list(attested),
        "spec": spec_id,
        "summary": (refreshed or {}).get("summary"),
    }


def spec_run_manifest_lint_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    result = lint_run_manifest_workspace(root, **kwargs)
    return enrich_tool_response("apatch_spec_run_manifest_lint", result, target_dir=root)


def spec_run_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(target_dir)
    request_id = kwargs.pop("request_id", None)
    from apatch.runtime.request_journal import run_idempotent_request

    result = run_idempotent_request(
        root,
        operation="spec_run",
        request_id=request_id,
        payload=kwargs,
        execute=lambda: spec_run_workspace(root, **kwargs),
    )
    return enrich_tool_response("apatch_spec_run", result, target_dir=root)
