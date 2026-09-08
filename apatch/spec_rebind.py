"""Auto-rebind file_drift-stale requirements in one call (shared-file spec DX).

When several requirements of one spec mutate a shared file (typically a test
file), attesting requirement N drifts the file and flips earlier requirements to
``stale`` with ``stale_reason == "file_drift"`` even though their tests are still
green in the final file state. Re-anchoring them manually is a
``session_start -> verify -> noop_attest -> session_end`` loop per requirement.

``rebind_stale_requirements`` collapses that loop into one operation: for each
file_drift-stale requirement it re-runs the requirement's verify (safety gate)
and, when green, noop-attests it so the ledger re-anchors to the final file.
Requirements stale for any other reason (e.g. ``spec_text_changed``) are left
untouched — those need real re-verification of changed intent, not a rebind.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


_OPEN_STATES = ("pending", "in_progress", "stale")


def attest_verified_requirements(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    verify_results: Mapping[str, bool],
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Attest every verified open Rk in one governed session.

    This is the primary-attestation half of ``slug ratify``.  The caller has
    already executed every unique verify command once.  We resolve the current
    content-hashed artifacts for all green pending/in-progress/stale
    requirements, then commit one signed noop attestation carrying the shared
    verification evidence.  A red or missing verify is never attested.

    One session and one ledger commit replace the former N marker files and N
    per-Rk session/apply/verify/attest lifecycles.
    """

    from apatch.runtime.runtime import MutationRuntime
    from apatch.spec import resolve_requirement
    from apatch.spec_coverage import spec_status_with_coverage

    status = spec_status_with_coverage(target_dir, spec=spec, spec_path=spec_path)
    if not status.get("ok"):
        return status
    spec_id = status["spec"]

    targets: List[Dict[str, Any]] = []
    skipped_red: List[Dict[str, Any]] = []
    skipped_unverified: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    artifacts: List[Dict[str, Any]] = []

    for row in status.get("requirements") or []:
        if row.get("state") not in _OPEN_STATES:
            continue
        req_id = str(row.get("id") or "")
        verify_cmd = row.get("verify")
        if not verify_cmd:
            skipped_unverified.append({"requirement": req_id, "reason": "missing_verify"})
            continue
        if not verify_results.get(req_id, False):
            skipped_red.append({"requirement": req_id, "verify": verify_cmd})
            continue
        resolved = resolve_requirement(
            target_dir,
            "{}#{}".format(spec_id, req_id),
            spec_path=spec_path,
        )
        if not resolved.get("ok"):
            errors.append({"requirement": req_id, "error": resolved.get("error")})
            continue
        targets.append(row)
        artifacts.append(resolved["artifact"])

    if errors or not targets:
        return {
            "ok": not errors,
            "spec": spec_id,
            "attested": [],
            "skipped_red": skipped_red,
            "skipped_unverified": skipped_unverified,
            "errors": errors,
            "sessions_opened": 0,
            "ledger_commits": 0,
            "summary": status.get("summary"),
        }

    req_ids = [str(row["id"]) for row in targets]
    rt = MutationRuntime(target_dir)
    opened = rt.open_session(
        "batch ratify {}: shared verify green for {} requirement(s)".format(
            spec_id, len(req_ids)
        ),
        artifacts=artifacts,
    )
    if not opened.get("ok"):
        return {
            "ok": False,
            "spec": spec_id,
            "attested": [],
            "skipped_red": skipped_red,
            "skipped_unverified": skipped_unverified,
            "errors": [{"error": opened}],
            "sessions_opened": 0,
            "ledger_commits": 0,
            "summary": status.get("summary"),
        }

    att: Dict[str, Any] = {"ok": False, "error": "batch attestation did not run"}
    close: Dict[str, Any] = {"ok": True}
    try:
        att = rt.noop_attest(
            req_ids,
            message=(
                "batch ratify {}: shared verify measured green; primary/stale "
                "requirements attested without marker mutations"
            ).format(spec_id),
            evidence=evidence,
        )
    except Exception as exc:  # pragma: no cover - runtime normally returns DTOs
        att = {"ok": False, "error": str(exc)}
    finally:
        try:
            close = rt.close_session()
        except Exception as exc:  # pragma: no cover - close must not hide attest
            close = {"ok": False, "error": str(exc)}

    if not att.get("ok"):
        errors.append({"error": att})
    if not close.get("ok"):
        errors.append({"error": close})
    final = spec_status_with_coverage(target_dir, spec=spec, spec_path=spec_path)
    return {
        "ok": not errors,
        "spec": spec_id,
        "attested": req_ids if att.get("ok") else [],
        "skipped_red": skipped_red,
        "skipped_unverified": skipped_unverified,
        "errors": errors,
        "sessions_opened": 1,
        "ledger_commits": 1 if att.get("ok") else 0,
        "summary": final.get("summary"),
    }


def rebind_stale_requirements(
    target_dir: str = ".",
    *,
    spec: Optional[str] = None,
    spec_path: Optional[str] = None,
    run_verify: bool = True,
    verify_results: Optional[Mapping[str, bool]] = None,
    stale_reasons: Optional[Sequence[str]] = None,
    requirement_ids: Optional[Sequence[str]] = None,
    exclude_requirement_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Re-anchor every file_drift-stale requirement of ``spec`` in one pass.

    ``verify_results``: precomputed per-requirement verify outcomes keyed by
    requirement id (slug ratify measures each verify command exactly once and
    reuses the result here). When provided, the per-Rk verify is NOT re-run — a
    requirement whose command was measured red lands in ``skipped_red``.

    ``stale_reasons``: which staleness reasons to rebind; default keeps the
    original ``("file_drift",)`` behaviour. Slug ratify widens it to include
    ``spec_text_changed`` because it has just measured the requirement's verify
    green against the current spec text.
    """

    from apatch.runtime.runtime import MutationRuntime
    from apatch.spec import resolve_requirement
    from apatch.spec_coverage import spec_status_with_coverage

    status = spec_status_with_coverage(target_dir, spec=spec, spec_path=spec_path)
    if not status.get("ok"):
        return status
    spec_id = status["spec"]

    available_ids = {str(row.get("id") or "") for row in (status.get("requirements") or [])}

    def normalize(values: Optional[Sequence[str]]) -> tuple[List[str], List[str]]:
        normalized: List[str] = []
        invalid: List[str] = []
        for raw in values or ():
            token = str(raw or "").strip()
            if "#" in token:
                token_spec, token = token.rsplit("#", 1)
                if token_spec != spec_id:
                    invalid.append(str(raw))
                    continue
            if token not in available_ids:
                invalid.append(str(raw))
            elif token not in normalized:
                normalized.append(token)
        return normalized, invalid

    included, invalid_included = normalize(requirement_ids)
    excluded, invalid_excluded = normalize(exclude_requirement_ids)
    invalid = invalid_included + invalid_excluded
    if invalid:
        return {
            "ok": False,
            "error": "requirement filter contains ids outside the selected spec",
            "error_type": "INVALID_REQUIREMENT_FILTER",
            "spec": spec_id,
            "invalid_requirement_ids": invalid,
        }

    reasons = tuple(stale_reasons) if stale_reasons is not None else ("file_drift",)
    targets = [
        r
        for r in (status.get("requirements") or [])
        if r.get("stale") and r.get("stale_reason") in reasons
        and (not requirement_ids or str(r.get("id")) in included)
        and str(r.get("id")) not in excluded
    ]

    rebound: List[str] = []
    skipped_red: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for row in targets:
        req_id = row["id"]
        req_token = "{}#{}".format(spec_id, req_id)
        res = resolve_requirement(target_dir, req_token, spec_path=spec_path)
        if not res.get("ok"):
            errors.append({"requirement": req_id, "error": res.get("error")})
            continue

        verify_cmd = res.get("verify")
        if verify_results is not None and verify_cmd and not verify_results.get(req_id):
            skipped_red.append({"requirement": req_id, "verify": verify_cmd})
            continue

        reason = str(row.get("stale_reason") or "file_drift")
        if reason == "file_drift":
            message = (
                "auto-rebind file_drift-stale {}: verify green, shared file in "
                "final state, no new mutation".format(req_token)
            )
        else:
            message = (
                "auto-rebind {}-stale {}: verify measured green, spec re-anchored, "
                "no new mutation".format(reason, req_token)
            )

        rt = MutationRuntime(target_dir)
        opened = rt.open_session(res["intent"], artifacts=[res["artifact"]])
        if not opened.get("ok"):
            errors.append(
                {
                    "requirement": req_id,
                    "error": opened.get("error") or opened,
                }
            )
            continue
        try:
            if verify_results is None and run_verify and verify_cmd:
                vres = rt.verify_run(verify=verify_cmd, skip_transition_check=True)
                if not vres.get("ok"):
                    skipped_red.append({"requirement": req_id, "verify": verify_cmd})
                    continue
            att = rt.noop_attest(
                [],
                message=message,
            )
            if att.get("ok"):
                rebound.append(req_id)
            else:
                errors.append({"requirement": req_id, "error": att})
        finally:
            try:
                rt.close_session()
            except Exception:  # pragma: no cover - close must never mask result
                pass

    final = spec_status_with_coverage(target_dir, spec=spec, spec_path=spec_path)
    return {
        "ok": True,
        "spec": spec_id,
        "selected_requirement_ids": [str(row.get("id")) for row in targets],
        "excluded_requirement_ids": excluded,
        "rebound": rebound,
        "skipped_red": skipped_red,
        "errors": errors,
        "summary": final.get("summary"),
    }
