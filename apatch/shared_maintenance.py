"""Partitioned cross-SPEC mechanical maintenance.

This opt-in workflow amortizes one physical apply and parallel requirement
verification without weakening exact SPEC ownership.  Every target file belongs
to exactly one bound SPEC#Rk and the signed mutation carries an artifact_files
partition, so requirement file hashes never become all-to-all.
"""

from __future__ import annotations

import json
import os
import posixpath
import time
from typing import Any, Dict, List, Mapping, Optional

from apatch.apatch_paths import normalize_rel


ERROR_PLAN = "SPEC_SHARED_MAINTENANCE_INVALID"
ERROR_SHARED_FILE = "SPEC_SHARED_FILE_SCOPE_UNSUPPORTED"
ERROR_VERIFY = "VERIFY_FAILED"
_CHANNEL = "apatch_spec_run_multi:shared_maintenance"


def _applied_count(result: Mapping[str, Any]) -> int:
    chunk = result.get("chunk_result") or {}
    return int(result.get("applied") or chunk.get("applied") or 0)


def prepare_shared_maintenance(
    target_dir: str,
    *,
    specs: List[str],
    requirements: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate and flatten the fail-closed v1 shared-maintenance manifest."""
    root = os.path.abspath(target_dir)
    spec_list = [str(spec).strip() for spec in specs if str(spec).strip()]
    if len(spec_list) < 2 or set(requirements) != set(spec_list):
        return _error(
            ERROR_PLAN,
            "requirements must explicitly cover every and only scheduled SPEC",
        )

    from apatch.spec import resolve_requirement

    artifacts: List[str] = []
    artifact_files: Dict[str, List[str]] = {}
    flattened: List[Dict[str, Any]] = []
    verify_rows: List[Dict[str, str]] = []
    path_owner: Dict[str, str] = {}
    selected: List[str] = []
    seen_needles = set()

    for spec_id in spec_list:
        spec_requirements = requirements.get(spec_id)
        if not isinstance(spec_requirements, Mapping) or not spec_requirements:
            return _error(ERROR_PLAN, f"{spec_id} requires a non-empty Rk map")
        for raw_rk, raw_entry in spec_requirements.items():
            rk = str(raw_rk or "").strip()
            token = f"{spec_id}#{rk}"
            if not rk or not isinstance(raw_entry, Mapping):
                return _error(ERROR_PLAN, f"{token} must be an Rk object")
            if raw_entry.get("skip_if_attested") is True:
                return _error(
                    ERROR_PLAN,
                    f"{token} explicitly sets skip_if_attested=true and cannot mutate",
                )
            needles = raw_entry.get("needles")
            if not isinstance(needles, list) or not needles:
                return _error(ERROR_PLAN, f"{token} requires at least one needle")

            resolved = resolve_requirement(root, token)
            if not resolved.get("ok"):
                return _error(ERROR_PLAN, str(resolved.get("error") or token))
            verify = str(resolved.get("verify") or "").strip()
            if not verify:
                return _error(ERROR_PLAN, f"{token} has no executable verify")

            artifact = str(resolved["artifact"])
            artifact_key = artifact.split("@", 1)[0]
            artifacts.append(artifact)
            selected.append(token)
            verify_rows.append({"id": token, "verify": verify})
            owned_paths: List[str] = []

            for needle in needles:
                if not isinstance(needle, Mapping):
                    return _error(ERROR_PLAN, f"{token} contains a non-object needle")
                action = str(needle.get("action") or "replace").strip().lower()
                if action != "replace":
                    return _error(
                        ERROR_PLAN,
                        f"{token} action {action!r} is outside shared-maintenance v1",
                    )
                if needle.get("glob_pattern") or needle.get("source_file"):
                    return _error(ERROR_PLAN, f"{token} requires one explicit target_file")
                raw_path = str(needle.get("target_file") or "").strip()
                normalized_rel = normalize_rel(raw_path)
                rel = posixpath.normpath(normalized_rel) if normalized_rel else ""
                basename = posixpath.basename(rel)
                is_slug_contract_yaml = (
                    posixpath.dirname(rel) == "docs/specs/slug_contracts"
                    and basename.endswith(".yaml")
                    and not basename.startswith(".")
                )
                if (
                    not rel
                    or rel == "."
                    or normalized_rel != rel
                    or os.path.isabs(raw_path)
                    or rel.startswith("../")
                    or (rel.startswith("docs/specs/") and not is_slug_contract_yaml)
                ):
                    return _error(ERROR_PLAN, f"{token} has unsafe target {raw_path!r}")
                prior = path_owner.get(rel)
                if prior and prior != token:
                    return _error(
                        ERROR_SHARED_FILE,
                        f"{rel} is assigned to both {prior} and {token}",
                        shared_file=rel,
                        owners=[prior, token],
                    )
                path_owner[rel] = token
                owned_paths.append(rel)
                normalized = dict(needle)
                normalized["action"] = "replace"
                normalized["target_file"] = rel
                fingerprint = json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if fingerprint not in seen_needles:
                    seen_needles.add(fingerprint)
                    flattened.append(normalized)

            artifact_files[artifact_key] = sorted(set(owned_paths))

    return {
        "ok": True,
        "artifacts": artifacts,
        "artifact_files": artifact_files,
        "needles": flattened,
        "verify_rows": verify_rows,
        "selected_requirements": selected,
        "target_count": len(path_owner),
    }


def shared_maintenance_workspace(
    target_dir: str,
    *,
    specs: List[str],
    requirements: Mapping[str, Any],
    schedule: Optional[Dict[str, Any]] = None,
    maintenance_verify: Optional[str] = None,
    verify_jobs: int = 8,
    verify_timeout: float = 120.0,
    chunk_max_files: int = 100,
) -> Dict[str, Any]:
    """Apply a partitioned batch once, verify exact Rk in parallel, attest once."""
    root = os.path.abspath(target_dir)
    prepared = prepare_shared_maintenance(
        root,
        specs=specs,
        requirements=requirements,
    )
    if not prepared.get("ok"):
        return prepared

    from apatch.runtime.runtime import MutationRuntime
    from apatch.simulate import simulate_workspace
    from apatch.workflows import generate_patch_jsonl_batch

    rt = MutationRuntime(root)
    opened = rt.open_session(
        "shared maintenance: {} exact requirement(s) across {} SPECs".format(
            len(prepared["selected_requirements"]), len(specs)
        ),
        artifacts=prepared["artifacts"],
        artifact_files=prepared["artifact_files"],
    )
    if not opened.get("ok"):
        return opened

    checkpoints: List[str] = []
    logs_path = os.path.join(
        ".apatch",
        "tmp",
        str(rt.session_id),
        "patches-shared-maintenance.jsonl",
    )
    result: Dict[str, Any]
    try:
        generated = generate_patch_jsonl_batch(
            needles=prepared["needles"],
            target_dir=root,
            out_path=logs_path,
            created_by_tool=_CHANNEL,
            governed_session_id=rt.session_id,
        )
        if not generated.get("ok"):
            return _with_stage(generated, "generate")

        actual_logs = (
            generated.get("out_path")
            or generated.get("out_path_rel")
            or logs_path
        )
        simulated = simulate_workspace(
            root,
            logs_path=str(actual_logs),
            chunk_max_files=max(1, int(chunk_max_files or 100)),
        )
        if not simulated.get("ok"):
            return _with_stage(simulated, "simulate")

        apply_result: Dict[str, Any] = {}
        applied_total = 0
        while True:
            apply_result = rt.apply_session(
                str(actual_logs),
                chunk_max_files=max(1, int(chunk_max_files or 100)),
                verify=None,
                verify_deferred=True,
                quiet=True,
            )
            applied_total += _applied_count(apply_result)
            for checkpoint in apply_result.get("checkpoints") or []:
                if checkpoint and checkpoint not in checkpoints:
                    checkpoints.append(str(checkpoint))
            checkpoint = apply_result.get("checkpoint")
            if checkpoint and checkpoint not in checkpoints:
                checkpoints.append(str(checkpoint))
            if not apply_result.get("ok"):
                _rollback(root, checkpoints)
                return {
                    **_with_stage(apply_result, "apply"),
                    "checkpoints": checkpoints,
                    "rolled_back": True,
                }
            if not apply_result.get("continue"):
                break

        verify_result = _verify_selected(
            root,
            prepared["verify_rows"],
            maintenance_verify=maintenance_verify,
            jobs=verify_jobs,
            timeout=verify_timeout,
        )
        if not verify_result.get("ok"):
            rolled = _rollback(root, checkpoints)
            return _verify_failure_result(
                verify_result,
                checkpoints=checkpoints,
                rollback_performed=rolled,
            )

        verified = rt._finish("apatch_verify_run", verify_result)
        if not verified.get("ok"):
            rolled = _rollback(root, checkpoints)
            return {
                **_with_stage(verified, "verify"),
                "checkpoints": checkpoints,
                "rolled_back": rolled,
                "rollback_performed": rolled,
            }

        attested = rt.attest(
            message=(
                "partitioned shared maintenance: exact requirement verifies green; "
                "one physical apply with signed artifact_files"
            )
        )
        if not attested.get("ok"):
            rolled = _rollback(root, checkpoints)
            return {
                **_with_stage(attested, "attest"),
                "checkpoints": checkpoints,
                "rolled_back": rolled,
            }

        coverage = _coverage_postcheck(
            root,
            prepared["artifact_files"],
        )
        if not coverage.get("ok"):
            rolled = _rollback(root, checkpoints)
            return {
                **_with_stage(coverage, "coverage"),
                "checkpoints": checkpoints,
                "rolled_back": rolled,
            }

        result = {
            "ok": True,
            "execution_mode": "shared_maintenance",
            "completed_specs": list(specs),
            "selected_requirements": prepared["selected_requirements"],
            "target_count": prepared["target_count"],
            "applied": applied_total,
            "checkpoints": checkpoints,
            "last_checkpoint": checkpoints[-1] if checkpoints else None,
            "verify": verify_result,
            "coverage": coverage,
            "schedule": schedule,
            "continue": False,
            "agent_next": "Shared maintenance applied, verified, attested, and partition coverage checked.",
        }
        return result
    finally:
        try:
            rt.close_session()
        except Exception:
            pass


def _verify_selected(
    root: str,
    rows: List[Dict[str, str]],
    *,
    maintenance_verify: Optional[str],
    jobs: int,
    timeout: float,
) -> Dict[str, Any]:
    from apatch.conformance import run_spec_verify

    started = time.monotonic()
    ran, failures, broken, details = run_spec_verify(
        root,
        rows,
        timeout=max(1.0, float(timeout or 120.0)),
        capture_details=True,
        fail_fast=False,
        requirement_jobs=max(1, int(jobs or 1)),
    )
    maintenance = None
    if maintenance_verify:
        from apatch.tool_paths import run_shell_verify

        ok, output = run_shell_verify(str(maintenance_verify), root)
        maintenance = {
            "command": str(maintenance_verify),
            "green": bool(ok),
            "output_tail": str(output or "")[-2000:],
        }
        if not ok:
            failures.append("maintenance_verify")

    return {
        "ok": not failures and not broken,
        "verify_kind": "partitioned_shared_maintenance",
        "requirements_ran": int(ran),
        "jobs": max(1, int(jobs or 1)),
        "elapsed_sec": round(time.monotonic() - started, 3),
        "failures": list(failures),
        "broken": list(broken),
        "details": list(details)[:20],
        "maintenance_verify": maintenance,
    }


def _verify_failure_result(
    verify_result: Mapping[str, Any],
    *,
    checkpoints: List[str],
    rollback_performed: bool,
) -> Dict[str, Any]:
    """Return one canonical failure after shared verify requested rollback."""
    failures = [str(item) for item in verify_result.get("failures") or []]
    broken = [str(item) for item in verify_result.get("broken") or []]
    raw_details = verify_result.get("details") or []
    diagnostics = [
        dict(item) if isinstance(item, Mapping) else {"detail": str(item)}
        for item in raw_details
    ]
    maintenance = verify_result.get("maintenance_verify")
    if not diagnostics:
        diagnostics = [{
            "kind": "shared_maintenance_verify",
            "failures": failures,
            "broken": broken,
            "maintenance_verify": maintenance,
        }]

    failed_items = failures + broken
    summary = ", ".join(failed_items) or "acceptance command returned non-zero"
    rolled_back = bool(rollback_performed)
    return {
        **_with_stage(verify_result, "verify"),
        "error_type": ERROR_VERIFY,
        "error": f"Shared-maintenance verification failed: {summary}.",
        "recoverable": True,
        "recommended_action": "start_new_session" if rolled_back else "rollback",
        "verify_rollback": True,
        "rollback_performed": rolled_back,
        "rolled_back": rolled_back,
        "checkpoints": list(checkpoints),
        "diagnostics": diagnostics,
        "diagnostic_count": len(diagnostics),
        "agent_next": (
            "Start a new governed session with corrected needles; the failed "
            "shared-maintenance batch was rolled back and its session ended."
            if rolled_back
            else "Rollback did not complete; inspect the returned checkpoints."
        ),
    }


def _coverage_postcheck(
    root: str,
    artifact_files: Mapping[str, List[str]],
) -> Dict[str, Any]:
    from apatch.spec_coverage import spec_status_with_coverage

    checked: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    by_spec: Dict[str, List[str]] = {}
    for artifact_key in artifact_files:
        token = artifact_key.split(":", 1)[1]
        spec_id, rk = token.split("#", 1)
        by_spec.setdefault(spec_id, []).append(rk)

    for spec_id, rks in by_spec.items():
        status = spec_status_with_coverage(root, spec=spec_id)
        rows = {str(row.get("id")): row for row in status.get("requirements") or []}
        for rk in rks:
            key = f"spec:{spec_id}#{rk}"
            expected = sorted(artifact_files[key])
            row = rows.get(rk) or {}
            actual = sorted(row.get("files") or [])
            item = {
                "requirement": f"{spec_id}#{rk}",
                "state": row.get("state"),
                "expected_files": expected,
                "actual_files": actual,
            }
            checked.append(item)
            if row.get("state") != "attested" or actual != expected:
                errors.append(item)

    return {"ok": not errors, "checked": checked, "errors": errors}


def _rollback(root: str, checkpoints: List[str]) -> bool:
    from apatch.workflows import rollback_workspace

    ok = True
    for checkpoint in reversed(checkpoints):
        rolled = rollback_workspace(root, checkpoint)
        ok = bool(rolled.get("ok")) and ok
    return ok


def _with_stage(result: Mapping[str, Any], stage: str) -> Dict[str, Any]:
    return {**dict(result), "ok": False, "stage": stage}


def _error(error_type: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_type": error_type,
        "error": message,
        "recoverable": True,
        "recommended_action": "fix the exact shared-maintenance manifest before retry",
        **extra,
    }
