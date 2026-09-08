"""Remote JSON worker entrypoint for apatch SSH transport."""

from __future__ import annotations

from contextlib import redirect_stdout
import json
import sys
from typing import Any, Dict, Mapping


PROTOCOL_VERSION = 1
DEFAULT_MAX_APPLY_ITERATIONS = 100


def _remote_generation_channel(plan: Mapping[str, Any], args: Mapping[str, Any]) -> str:
    if plan.get("fix_forward_current"):
        return "apatch_remote_task_run:fix_forward"
    if plan.get("requirement") or args.get("requirement"):
        return "apatch_remote_task_run:requirement"
    return "apatch_remote_task_run"

# Only these governed operations may run through the remote worker. Anything
# else is rejected before dispatch so a malformed envelope cannot invoke an
# arbitrary remote code path.
ALLOWED_OPERATIONS = frozenset(
    {
        "apatch_doctor",
        "apatch_execute_next",
        "apatch_spec_run",
        "apatch_spec_run_multi",
        "apatch_slug_ratify",
        "apatch_rebind_stale",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_rollback",
        "apatch_verify_run",
        "apatch_resume_session",
        "apatch_noop_attest",
        "apatch_attest",
        "apatch_commit_attested",
        "apatch_session_end",
        "apatch_session_state",
    }
)


def _protocol_compatible(requested: Any) -> bool:
    try:
        return int(requested) == PROTOCOL_VERSION
    except (TypeError, ValueError):
        return False


def dispatch(payload: Mapping[str, Any]) -> Dict[str, Any]:
    operation = payload.get("operation")
    args = payload.get("arguments")
    if args is None:
        args = {}
    target_dir = "."

    requested_version = payload.get("protocol_version", PROTOCOL_VERSION)
    if not _protocol_compatible(requested_version):
        return _fail(
            "REMOTE_PROTOCOL_ERROR",
            "Worker protocol version mismatch.",
            expected=PROTOCOL_VERSION,
            requested=requested_version,
            recommended_action="Align local and remote apatch so protocol_version matches.",
        )

    if not isinstance(args, Mapping):
        return _fail("REMOTE_PROTOCOL_ERROR", "Worker arguments must be a JSON object.")

    if operation not in ALLOWED_OPERATIONS:
        return _fail(
            "REMOTE_OPERATION_UNSUPPORTED",
            "Operation is not in the remote worker allowlist.",
            operation=operation,
        )

    if operation == "apatch_doctor":
        from apatch.doctor import run_doctor

        return run_doctor(target_dir)

    if operation == "apatch_execute_next":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_execute_next plan must be a JSON object.",
            )
        spec_id = str(plan.get("spec") or "").strip()
        requirement = str(plan.get("requirement") or "").strip()
        needles = plan.get("needles")
        if not spec_id.startswith("SPEC-") or not requirement:
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_execute_next requires plan.spec and plan.requirement.",
            )
        if not isinstance(needles, list) or not needles:
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_execute_next requires a non-empty plan.needles list.",
            )
        targets = {
            str(needle.get("target_file") or needle.get("source_file") or "").strip()
            for needle in needles
            if isinstance(needle, Mapping)
        }
        targets.discard("")
        if not targets or len(targets) > 5:
            return _fail(
                "REMOTE_EXECUTE_NEXT_SCOPE_INVALID",
                "Remote execute_next is limited to five explicit target files.",
                recoverable=True,
                recommended_action="Reduce scope or use remote spec_run for a larger batch.",
                target_count=len(targets),
            )

        from apatch.spec_executor import execute_next_enriched

        mutated = execute_next_enriched(
            target_dir,
            spec=spec_id,
            requirement=requirement,
            needles=[dict(needle) for needle in needles],
            logs_path=str(plan.get("logs_path") or "patches-execute-next.jsonl"),
            verify_deferred=bool(plan.get("verify_deferred", True)),
            defer_verify=bool(plan.get("defer_finalize", False)),
            skip_lint=bool(plan.get("skip_lint", False)),
            check_dependencies=bool(plan.get("check_dependencies", True)),
            governed_session_id=str(args.get("governed_session_id") or "") or None,
            session_token=str(args.get("session_token") or "") or None,
            request_id=str(args.get("request_id") or "") or None,
        )
        if not mutated.get("ok") or mutated.get("continue"):
            return mutated

        if bool(plan.get("defer_finalize", False)):
            deferred = dict(mutated)
            deferred.update(
                {
                    "applied": True,
                    "finalize_deferred": True,
                    "agent_next": (
                        "Restart the governed service, then run remote_task with "
                        "plan={'finalize_current': True} and the fresh live verify."
                    ),
                }
            )
            return deferred

        finalized = execute_next_enriched(
            target_dir,
            spec=spec_id,
            requirement=requirement,
            finalize=True,
            skip_lint=bool(plan.get("skip_lint", False)),
            check_dependencies=bool(plan.get("check_dependencies", True)),
            governed_session_id=str(args.get("governed_session_id") or "") or None,
            session_token=str(args.get("session_token") or "") or None,
            request_id=(str(args.get("request_id")) + ":finalize") if args.get("request_id") else None,
        )
        if finalized.get("ok"):
            finalized["applied"] = True
            finalized["mutation_result"] = {
                "checkpoint": mutated.get("checkpoint"),
                "requirement_token": mutated.get("requirement_token"),
                "steps_completed": mutated.get("steps_completed"),
            }
        return finalized

    if operation == "apatch_slug_ratify":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_slug_ratify plan must be a JSON object.",
            )
        slug = str(plan.get("slug") or "").strip()
        spec_id = str(plan.get("spec") or "").strip() or None
        if not slug:
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_slug_ratify requires plan.slug.",
            )

        from apatch.slug_ratify import slug_ratify_enriched

        return slug_ratify_enriched(
            target_dir,
            slug=slug,
            spec=spec_id,
            dry_run=bool(plan.get("dry_run", False)),
        )

    if operation == "apatch_rebind_stale":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_rebind_stale plan must be a JSON object.",
            )
        spec_id = str(plan.get("spec") or "").strip()
        if not spec_id.startswith("SPEC-"):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_rebind_stale requires plan.spec.",
            )

        from apatch.spec_rebind import rebind_stale_requirements

        filters = {}
        if plan.get("requirement_ids") is not None:
            filters["requirement_ids"] = plan.get("requirement_ids")
        if plan.get("exclude_requirement_ids") is not None:
            filters["exclude_requirement_ids"] = plan.get("exclude_requirement_ids")
        return rebind_stale_requirements(
            target_dir,
            spec=spec_id,
            spec_path=str(plan.get("spec_path") or "").strip() or None,
            run_verify=True,
            **filters,
        )

    if operation == "apatch_spec_run":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_spec_run plan must be a JSON object.",
            )
        spec_id = str(plan.get("spec") or "").strip()
        specs = plan.get("specs")
        if not spec_id and isinstance(specs, list) and len(specs) == 1:
            spec_id = str(specs[0] or "").strip()
        if not spec_id.startswith("SPEC-"):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_spec_run requires plan.spec or one plan.specs entry.",
            )
        requirements = plan.get("requirements") or args.get("requirements")
        if isinstance(requirements, Mapping) and spec_id in requirements:
            requirements = requirements[spec_id]
        if requirements is not None and not isinstance(requirements, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_spec_run plan.requirements must be a JSON object.",
            )

        from apatch.spec_run import spec_run_enriched

        return spec_run_enriched(
            target_dir,
            spec=spec_id,
            requirements=dict(requirements) if requirements is not None else None,
            dry_run=bool(plan.get("dry_run", False)),
            reset=bool(plan.get("reset", False)),
            resume=bool(plan.get("resume", True)),
            logs_path=str(plan.get("logs_path") or "patches-spec-run.jsonl"),
            verify_deferred=bool(plan.get("verify_deferred", True)),
            skip_lint=bool(plan.get("skip_lint", False)),
            check_dependencies=bool(plan.get("check_dependencies", True)),
            request_id=str(args.get("request_id") or "") or None,
        )

    if operation == "apatch_spec_run_multi":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_spec_run_multi plan must be a JSON object.",
            )
        specs = plan.get("specs") or args.get("specs")
        requirements = plan.get("requirements") or args.get("requirements")
        if not isinstance(specs, list) or len(specs) < 2:
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_spec_run_multi requires plan.specs with at least two SPEC ids.",
            )
        if requirements is not None and not isinstance(requirements, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_spec_run_multi plan.requirements must be a JSON object.",
            )

        from apatch.spec_run_multi import spec_run_multi_enriched

        return spec_run_multi_enriched(
            target_dir,
            specs=[str(spec) for spec in specs],
            requirements=dict(requirements) if requirements is not None else None,
            cross_verify=bool(plan.get("cross_verify", False)),
            re_interference=bool(plan.get("re_interference", True)),
            logs_path=str(
                plan.get("logs_path") or "patches-spec-run-multi.jsonl"
            ),
            verify_deferred=bool(plan.get("verify_deferred", True)),
            execution_mode=str(plan.get("execution_mode") or "serial"),
            maintenance_verify=(
                str(plan.get("maintenance_verify"))
                if plan.get("maintenance_verify")
                else None
            ),
            verify_jobs=_as_int(plan.get("verify_jobs"), 8),
            verify_timeout=_as_float(plan.get("verify_timeout"), 120.0),
            maintenance_chunk_max_files=_as_int(
                plan.get("maintenance_chunk_max_files"), 100
            ),
        )

    if operation == "apatch_session_start":
        from apatch.runtime.runtime import MutationRuntime

        intent = args.get("intent") or "remote apatch task"
        artifacts = args.get("artifacts")
        if args.get("requirement"):
            from apatch.spec import resolve_requirement

            resolved = resolve_requirement(
                target_dir,
                str(args.get("requirement")),
                spec_path=args.get("spec_path") or None,
            )
            if not resolved.get("ok"):
                return resolved
            intent = args.get("intent") or resolved["intent"]
            artifacts = list(artifacts) if artifacts else []
            artifacts.append(resolved["artifact"])

        return MutationRuntime(target_dir).open_session(
            intent,
            artifacts=artifacts,
            request_id=str(args.get("request_id") or "") or None,
        )

    if operation == "apatch_generate_batch":
        from apatch.workflows import generate_patch_jsonl_batch

        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail("REMOTE_PLAN_INVALID", "apatch_generate_batch plan must be a JSON object.")
        binding_error = _bind_session_capability(
            target_dir, args, operation="generate_batch"
        )
        if binding_error:
            return binding_error
        needles = plan.get("needles") or args.get("needles")
        slug_close_summary = None
        if needles is None and isinstance(plan.get("slug_close"), Mapping):
            from apatch.slug_close import DEFAULT_API_URL, slug_close_workspace

            close_plan = plan["slug_close"]
            close = slug_close_workspace(
                target_dir,
                slug=str(close_plan.get("slug") or plan.get("slug") or ""),
                api_url=str(close_plan.get("api_url") or DEFAULT_API_URL),
                triage_path=close_plan.get("triage_path"),
                approved_path=close_plan.get("approved_path"),
                size=_as_int(close_plan.get("size"), 5),
                timeout=_as_float(close_plan.get("timeout"), 30.0),
                limit=_as_int(close_plan.get("limit"), 0),
                include_rows=False,
            )
            if not close.get("ok"):
                return close
            needles = close.get("proposed_needles") or []
            slug_close_summary = {
                "slug": close.get("slug"),
                "rows_scanned": close.get("rows_scanned"),
                "summary": close.get("summary"),
                "non_fixed_count": len(
                    [
                        row
                        for row in close.get("suggestions") or []
                        if row.get("suggested_status") != "fixed"
                    ]
                ),
            }
        if not isinstance(needles, list):
            return _fail("REMOTE_PLAN_INVALID", "apatch_generate_batch requires plan.needles as a list.")
        if not needles:
            return _fail(
                "REMOTE_PLAN_EMPTY",
                "apatch_generate_batch produced no needles.",
                slug_close=slug_close_summary,
            )
        out_path = plan.get("out_path") or args.get("out_path") or ".apatch/remote/patches.jsonl"
        result = generate_patch_jsonl_batch(
            needles=needles,
            target_dir=target_dir,
            out_path=out_path,
            append=bool(plan.get("append") or args.get("append")),
            default_glob_pattern=str(plan.get("glob_pattern") or "**/*"),
            default_match_mode=str(plan.get("match_mode") or "literal"),
            default_replace_all=bool(plan.get("replace_all")),
            created_by_tool=_remote_generation_channel(plan, args),
            governed_session_id=(
                str(args.get("governed_session_id"))
                if args.get("governed_session_id")
                else None
            ),
        )
        if result.get("ok"):
            result["logs_path"] = result.get("out_path_rel") or result.get("out_path")
        if slug_close_summary:
            result["slug_close"] = slug_close_summary
        return result

    if operation == "apatch_simulate":
        from apatch.workflows import simulate_workspace

        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            plan = {}
        logs_path = args.get("logs_path") or plan.get("logs_path") or plan.get("out_path") or ".apatch/remote/patches.jsonl"
        return simulate_workspace(
            target_dir,
            logs_path=logs_path,
            chunk_max_files=_as_int(plan.get("chunk_max_files") or args.get("chunk_max_files"), 5),
            replace_all=bool(plan.get("replace_all") or args.get("replace_all")),
            only_drifted=bool(plan.get("only_drifted") or args.get("only_drifted")),
            min_confidence=plan.get("min_confidence") or args.get("min_confidence"),
            budget=plan.get("budget") or args.get("budget"),
            max_files=plan.get("max_files") or args.get("max_files"),
            max_insertions=plan.get("max_insertions") or args.get("max_insertions"),
            max_deletions=plan.get("max_deletions") or args.get("max_deletions"),
        )

    if operation == "apatch_apply_session":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            plan = {}
        logs_path = args.get("logs_path") or plan.get("logs_path") or plan.get("out_path") or ".apatch/remote/patches.jsonl"
        return _run_apply_session_to_completion(
            target_dir,
            args,
            plan,
            logs_path=logs_path,
        )

    if operation == "apatch_rollback":
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(target_dir).rollback(
            args.get("session_id"),
            preview=bool(args.get("preview")),
        )

    if operation == "apatch_commit_attested":
        plan = args.get("plan") or {}
        if not isinstance(plan, Mapping):
            return _fail(
                "REMOTE_PLAN_INVALID",
                "apatch_commit_attested plan must be a JSON object.",
            )
        from apatch.workflows import commit_attested_workspace

        raw_sessions = plan.get("session_ids") or []
        if isinstance(raw_sessions, str):
            raw_sessions = [raw_sessions]
        return commit_attested_workspace(
            target_dir,
            governed_session_id=plan.get("governed_session_id"),
            session_ids=list(raw_sessions),
            message=str(plan.get("message") or args.get("message") or "").strip(),
            push=bool(plan.get("push")),
            remote=str(plan.get("remote") or "origin"),
            dry_run=bool(plan.get("dry_run")),
        )

    if operation == "apatch_verify_run":
        runtime = _mutation_runtime(target_dir, args)
        verify = args.get("verify")
        if args.get("enforce_requirement_verify"):
            return _verify_for_finalization(runtime, target_dir, args)
        if isinstance(verify, Mapping):
            return runtime.verify_run(
                verify=verify.get("command"),
                semantic=bool(verify.get("semantic")),
                notarization=bool(verify.get("notarization")),
                pipeline_manifest=verify.get("pipeline_manifest"),
                dry_run=bool(verify.get("dry_run")),
                rules_path=verify.get("rules_path"),
                since=verify.get("since") or "HEAD",
                staged=bool(verify.get("staged")),
                working_tree=bool(verify.get("working_tree")),
                baseline=verify.get("baseline") or "off",
                allowed_failures=verify.get("allowed_failures"),
                async_mode=bool(verify.get("async_mode")),
            )
        return runtime.verify_run(verify=verify)

    if operation == "apatch_resume_session":
        return _mutation_runtime(target_dir, args).resume_session()

    if operation == "apatch_noop_attest":
        covered_by = args.get("covered_by") or args.get("noop_covered_by") or []
        if isinstance(covered_by, str):
            covered_by = [part.strip() for part in covered_by.split(",") if part.strip()]
        else:
            covered_by = list(covered_by or [])
        return _mutation_runtime(target_dir, args).noop_attest(
            covered_by,
            message=args.get("message") or args.get("intent"),
        )

    if operation == "apatch_attest":
        return _mutation_runtime(target_dir, args).attest(
            message=args.get("message") or args.get("intent")
        )

    if operation == "apatch_session_end":
        result = _mutation_runtime(target_dir, args).close_session()
        if result.get("ok"):
            cleaned = _cleanup_remote_apply_state(target_dir, args.get("session_path"))
            if cleaned:
                result["remote_apply_state_cleaned"] = cleaned
        return result

    if operation == "apatch_session_state":
        from apatch.session_state import read_session_state_workspace

        return read_session_state_workspace(target_dir)

    return _fail("REMOTE_OPERATION_UNSUPPORTED", "Unsupported remote operation.", operation=operation)


def _verify_for_finalization(runtime, target_dir: str, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve required checks from the bound session, never from the caller's plan."""
    from apatch.session_state import load_session_state
    from apatch.spec import resolve_requirement

    binding_error = _bind_session_capability(target_dir, args, operation="verify_run")
    if binding_error:
        return binding_error
    raw = load_session_state(target_dir)
    if not raw.get("session_id") or raw.get("ended_at"):
        return _fail("REMOTE_SPEC_VERIFY_SESSION_MISSING", "Finalization needs an active session.")

    supplied = args.get("verify")
    if isinstance(supplied, Mapping):
        if any(value for key, value in supplied.items() if key != "command"):
            return _fail(
                "REMOTE_SPEC_VERIFY_OPTIONS_INVALID",
                "Finalization cannot use dry-run, baseline allowances, or alternate verify modes.",
            )
        supplied = supplied.get("command")
    if supplied is not None and not isinstance(supplied, (str, list, tuple)):
        return _fail("REMOTE_SPEC_VERIFY_OPTIONS_INVALID", "verify must be a command or argv.")

    commands = []
    requirements = []
    for artifact in raw.get("artifacts") or []:
        if not isinstance(artifact, Mapping) or artifact.get("kind") != "spec":
            continue
        token = str(artifact.get("id") or "")
        if "#" not in token or not artifact.get("content_hash"):
            return _fail(
                "REMOTE_SPEC_VERIFY_BINDING_MISSING",
                "SPEC finalization needs an exact hash-bound requirement, not a bare SPEC label.",
            )
        resolved = resolve_requirement(target_dir, token)
        if not resolved.get("ok"):
            return _fail(
                "REMOTE_SPEC_VERIFY_UNRESOLVED", "Cannot resolve the bound requirement.",
                requirement=token,
            )
        if artifact["content_hash"] != resolved.get("content_hash"):
            return _fail(
                "REMOTE_SPEC_VERIFY_DRIFT",
                "Requirement changed after session binding; rebind through the SPEC executor.",
                requirement=token,
            )
        command = resolved.get("verify")
        if not isinstance(command, str) or not command.strip():
            return _fail(
                "REMOTE_SPEC_VERIFY_MISSING", "Bound requirement has no executable verify.",
                requirement=token,
            )
        requirements.append(token)
        if command not in commands:
            commands.append(command)
    if supplied and supplied not in commands:
        commands.append(supplied)
    if not commands:
        return _fail("REMOTE_SPEC_VERIFY_MISSING", "Finalization needs an executable verify.")

    completed = []
    for command in commands:
        result = runtime.verify_run(verify=command)
        if result.get("ok") is not True:
            return {**result, "requirement_verification": {
                "complete": False, "requirements": requirements, "completed_commands": completed,
            }}
        if result.get("verify_job_id") or result.get("verify_job_state") in {"running", "pending"}:
            return {
                **result, "ok": False, "error_type": "REMOTE_SPEC_VERIFY_PENDING",
                "message": "Required verification is still running; attestation remains deferred.",
                "recoverable": True, "recommended_action": "Poll verification before finalizing.",
                "requirement_verification": {"complete": False, "requirements": requirements},
            }
        completed.append(command)
    return {
        **result,
        "requirement_verification": {
            "complete": True, "requirements": requirements, "completed_commands": completed,
        },
    }


def _mutation_runtime(target_dir: str, args: Mapping[str, Any]):
    from apatch.runtime.runtime import MutationRuntime

    session_id = str(args.get("governed_session_id") or "").strip()
    session_token = args.get("session_token")
    if not session_id and not session_token:
        return MutationRuntime(target_dir)
    return MutationRuntime(
        target_dir,
        session_id=session_id or None,
        session_token=str(session_token) if session_token else None,
        enforce_binding=True,
    )


def _bind_session_capability(
    target_dir: str,
    args: Mapping[str, Any],
    *,
    operation: str,
) -> Dict[str, Any] | None:
    session_id = str(args.get("governed_session_id") or "").strip()
    session_token = args.get("session_token")
    if not session_id and not session_token:
        return None

    from apatch.runtime.errors import SessionBindingError
    from apatch.runtime.session_binding import capture_session_binding

    try:
        capture_session_binding(
            target_dir,
            expected_session_id=session_id or None,
            session_token=str(session_token) if session_token else None,
            require_capability=True,
            operation=operation,
        )
    except SessionBindingError as exc:
        return exc.to_dict()
    return None


def _run_apply_session_to_completion(
    target_dir: str,
    args: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    logs_path: str,
) -> Dict[str, Any]:
    runtime = _mutation_runtime(target_dir, args)
    reset = bool(plan.get("reset") or args.get("reset"))
    max_iterations = _as_int(
        plan.get("max_apply_iterations") or args.get("max_apply_iterations"),
        DEFAULT_MAX_APPLY_ITERATIONS,
    )
    checkpoints = []

    for iteration in range(1, max_iterations + 1):
        result = runtime.apply_session(
            logs_path,
            session_path=plan.get("session_path") or args.get("session_path"),
            verify=None,
            chunk_max_files=_as_int(
                plan.get("chunk_max_files") or args.get("chunk_max_files"), 5
            ),
            replace_all=bool(plan.get("replace_all") or args.get("replace_all")),
            only_drifted=bool(
                plan.get("only_drifted") or args.get("only_drifted")
            ),
            min_confidence=plan.get("min_confidence") or args.get("min_confidence"),
            verify_deferred=bool(plan.get("verify_deferred", True)),
            no_trustchain=bool(
                plan.get("no_trustchain") or args.get("no_trustchain")
            ),
            tool=plan.get("tool") or args.get("tool"),
            keyword=plan.get("keyword") or args.get("keyword"),
            reset=reset,
            quiet=True,
        )
        checkpoint = result.get("checkpoint")
        if checkpoint:
            checkpoints.append(checkpoint)
        if not result.get("ok", True) or not result.get("continue"):
            out = dict(result)
            out["worker_iterations"] = iteration
            if checkpoints:
                out["worker_checkpoints"] = checkpoints
            return out
        reset = False

    return _fail(
        "REMOTE_APPLY_SESSION_DID_NOT_CONVERGE",
        "Remote apply_session kept returning continue=true inside one worker.",
        worker_iterations=max_iterations,
        worker_checkpoints=checkpoints,
        recoverable=True,
        recommended_action=(
            "Inspect the task-scoped remote apply state or raise "
            "plan.max_apply_iterations intentionally."
        ),
        **{"continue": False},
    )


def _cleanup_remote_apply_state(
    target_dir: str,
    session_path: Any,
) -> list[str]:
    if not isinstance(session_path, str) or not session_path:
        return []

    import os

    root = os.path.abspath(target_dir)
    absolute = (
        session_path
        if os.path.isabs(session_path)
        else os.path.abspath(os.path.join(root, session_path))
    )
    try:
        relative = os.path.relpath(absolute, root).replace("\\", "/")
    except ValueError:
        return []
    if (
        not relative.startswith(".apatch/tmp/")
        or os.path.basename(absolute) != "remote-apply-session.json"
    ):
        return []

    removed = []
    directory = os.path.dirname(absolute)
    names = ["remote-apply-session.json"]
    if os.path.isdir(directory):
        names.extend(
            name
            for name in os.listdir(directory)
            if name.startswith("apply_session_chunk_") and name.endswith(".json")
        )
    for name in names:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(os.path.relpath(path, root).replace("\\", "/"))
    return sorted(set(removed))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, Mapping):
            result = _fail("REMOTE_PROTOCOL_ERROR", "Worker payload must be a JSON object.")
        else:
            # stdout is the framing channel: library/Rich chatter belongs on stderr.
            with redirect_stdout(sys.stderr):
                result = dispatch(payload)
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": _exception_error_type(exc),
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "recoverable": True,
            "recommended_action": _exception_recommended_action(exc),
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


def _exception_error_type(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, ModuleNotFoundError) and ("apatch" in text or getattr(exc, "name", "") == "apatch"):
        return "REMOTE_APATCH_MISSING"
    return "REMOTE_EXCEPTION"


def _exception_recommended_action(exc: Exception) -> str:
    if _exception_error_type(exc) == "REMOTE_APATCH_MISSING":
        return "Run apatch remote init with apatch_runtime.path and bootstrap the local broker runtime; do not use raw SSH copy commands."
    return "Inspect remote stderr/logs or rerun the remote worker after fixing the typed cause."


def _fail(error_type: str, message: str, **extra: Any) -> Dict[str, Any]:
    result = {"ok": False, "error_type": error_type, "message": message}
    result.update({k: v for k, v in extra.items() if v is not None})
    return result


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
