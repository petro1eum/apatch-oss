"""MCP-first agent hints (RFP-004 Phase 1 — no shell for recommended actions)."""

from __future__ import annotations

from typing import Any, List, Optional, Union

OP_PLAN = "plan"
OP_APPLY = "apply"
OP_VERIFY = "verify"
OP_ROLLBACK = "rollback"


def hint_session_start(
    intent: str = "<ADR or task>",
    *,
    artifacts: Optional[List[Union[str, dict]]] = None,
) -> str:
    if artifacts:
        return f"apatch_session_start(intent={intent!r}, artifacts={artifacts!r})"
    return f"apatch_session_start(intent={intent!r})"


def hint_session_end() -> str:
    return "apatch_session_end()"


def hint_session_state() -> str:
    return "apatch_session_state()"


def hint_plan_batch(logs_path: str = "patches.jsonl") -> str:
    return f"apatch_plan_batch(logs_path={logs_path!r})"


def hint_apply(logs_path: str = "patches.jsonl", verify: Optional[str] = None) -> str:
    if verify:
        return f"apatch_apply(logs_path={logs_path!r}, verify={verify!r})"
    return f"apatch_apply(logs_path={logs_path!r})"


def hint_apply_session(logs_path: str = "patches.jsonl") -> str:
    return f"apatch_apply_session(logs_path={logs_path!r})"


def hint_rollback(session_id: Optional[str] = None) -> str:
    if session_id:
        return f"apatch_rollback(session_id={session_id!r})"
    return "apatch_rollback()"


def hint_verify_run(verify: Optional[str] = None, semantic: bool = False) -> str:
    if verify:
        return f"apatch_verify_run(verify={verify!r})"
    if semantic:
        return "apatch_verify_run(semantic=True)"
    return "apatch_verify_run()"


def hint_attest() -> str:
    return "apatch_attest()"


def hint_attestation_export(out_path: str = ".apatch/audit_bundle.json") -> str:
    return f"apatch_attestation_export(out_path={out_path!r})"


def hint_events_tail(limit: int = 20) -> str:
    return f"apatch_events_tail(limit={limit})"


def hint_doctor() -> str:
    return "apatch_doctor()"


def hint_strip_dry_run() -> str:
    return "apatch_strip_dry_run(file=..., manifest=..., strict_overlap=True)"


def hint_for_transition(operation: str, lifecycle: str) -> str:
    if operation == OP_APPLY and lifecycle in ("draft", "planned"):
        return f"{hint_plan_batch()} — then {hint_apply()}"
    if operation == OP_APPLY and lifecycle == "failed":
        return f"{hint_rollback()} — then {hint_plan_batch()}"
    if operation == OP_PLAN and lifecycle == "failed":
        return f"{hint_rollback()} — or {hint_session_start()}"
    if operation == OP_VERIFY:
        return hint_verify_run()
    if operation == OP_ROLLBACK:
        return hint_rollback()
    return hint_session_state()


def governed_workflow_mcp() -> list[str]:
    from apatch.agent_guidance import governed_workflow_steps

    return governed_workflow_steps(with_artifacts=True)


GOVERNED_WORKFLOW_MCP = governed_workflow_mcp()
