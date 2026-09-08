"""Self-contained agent playbook embedded in MCP tool responses (no external docs required)."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from apatch.agent_playbooks import (
    mcp_playbook_index,
    runtime_hygiene_playbook,
    tool_usage_playbook,
)

_GUIDANCE_REF = (
    "apatch_doctor OR apatch://playbook/* — protocol_contract, runtime_hygiene, spec_run once per session"
)


def mcp_guidance_mode(target_dir: str = ".") -> str:
    """``doctor_only`` (default consumer) vs ``full`` inline playbooks on every tool."""
    raw = os.environ.get("APATCH_MCP_GUIDANCE", "").strip().lower()
    if raw in ("full", "doctor_only"):
        return raw
    try:
        from apatch.mcp_health import is_apatch_source_workspace

        if is_apatch_source_workspace(os.path.abspath(target_dir)):
            return "full"
    except Exception:
        pass
    return "doctor_only"


def sandbox_agent_protocol() -> Dict[str, Any]:
    """What agents must NOT do under sandbox enforce — embedded in apatch_doctor."""
    return {
        "mode": "sandbox enforce blocks agent shell/package mutations",
        "never_via_shell": [
            "pip install / pip uninstall / python -m pip install (reinstall apatch)",
            "npm install / yarn / pnpm add",
            "sed, awk, tee, perl -pi, and file-output redirects (cmd > file, >> file)",
            "direct Write/StrReplace in protected paths (apatch/**, src/**, …)",
        ],
        "shell_allowed": [
            "Read-only commands run freely: pytest, python -m pytest, build, lint, git status",
            "'>'/'<' and sed/tee INSIDE quotes are fine (e.g. python -c \"assert a < b\")",
            "fd redirects (2>&1, >&2) and pipes (| tail) are NOT file mutations — allowed",
            "Only an UNQUOTED file-writing redirect (>/>>) or sed/awk/tee/perl -pi is blocked",
        ],
        "never_via_agent": [
            "Starting or restarting the MCP server (Cursor UI / human only)",
            "Editing IDE MCP config after apatch upgrade — run apatch mcp sync instead",
        ],
        "human_only_tooling": [
            "pip install -e '/path/to/apatch[mcp]' after apatch source changes",
            "Restart MCP server user-apatch in Cursor Settings",
            "Optional: apatch mcp sync --target-dir . [--ide-path .cursor/mcp.json]",
        ],
        "if_mcp_stale_or_missing_tools": (
            "Read apatch_doctor.tooling_refresh.human_steps (apatch source repo) or "
            "mcp_health.warnings. Paste commands to user; wait. Do not retry pip."
        ),
        "after_apatch_source_edits": (
            "apply_session may return tooling_refresh — agent copies human_steps to user, "
            "then waits. MCP restart required; pip optional for .py-only changes."
        ),
        "mutations_use": "apatch_* MCP only (apatch_generate → apatch_apply_session, …)",
        "inspection_mcp_allowed": (
            "cursor-ide-browser (navigate, snapshot, screenshot) — read-only visual review; "
            "allowed under sandbox enforce via mcp_extra_servers default"
        ),
    }


def artifact_anchored_intent_playbook() -> Dict[str, Any]:
    """Cheat sheet returned by ``apatch_doctor`` and key lifecycle tools."""
    return {
        "model": "Artifact → Intent → Session → Mutation → Verification → Attestation",
        "artifact_token": "kind:id or kind:id@content_hash",
        "artifact_kinds": "open set: spec, adr, ticket, incident, compliance, …",
        "session_start": {
            "tool": "apatch_session_start",
            "example": (
                "apatch_session_start(intent='implement billing v2', "
                "artifacts=['spec:SPEC-42@sha256:…', 'adr:ADR-001'])"
            ),
        },
        "ledger_auto_stamp": (
            "During an active governed session, TrustChain commits auto-include "
            "governed_session_id, intent, artifacts[]"
        ),
        "after_attest": (
            "apatch_trustchain_coverage(artifact='kind:id') — check coverage.complete "
            "(true = mutation + attestation linked)"
        ),
        "reverse_lookup": "apatch_trustchain_coverage(op_id='<ledger op_id>')",
        "history": "apatch_trustchain_history(artifact='kind:id')",
        "read_session": "apatch_session_state → session.artifacts[]",
        "executable_specs": (
            "RFP-007: a SPEC.md becomes requirement artifacts spec:ID#Rk. "
            "apatch_spec_status / apatch_spec_next derive per-requirement state from the "
            "ledger; apatch_session_start(requirement='SPEC-42#R3') anchors a session."
        ),
        "not_provided": [
            "spec authoring — humans/agents write SPEC.md (apatch parses & tracks it)",
            "sub-clause mapping below requirement granularity (Rk is the unit)",
            "Jira/wiki content storage — only kind:id@hash refs",
        ],
    }


def spec_authoring_requirements() -> Dict[str, Any]:
    """Executable-spec authoring rules — embedded in doctor and every spec_* MCP tool."""
    return {
        "discovery": (
            "Fresh agent: apatch_doctor → read spec_authoring + spec_execution + spec_run. "
            "Do not guess format — apatch_spec_lint enforces it."
        ),
        "location": "docs/specs/SPEC-<ID>.md (auto-discovered by spec id)",
        "lint_tool": "apatch_spec_lint(spec='SPEC-<ID>') — format + rfp gates + plan_scaffold when passed (primary onboarding gate)",
        "onboarding_doc": "docs/agent-onboarding.md — fresh agent playbook",
        "needles_scaffold_tool": "Inside apatch_spec_lint response (plan_scaffold, needles_scaffold, agent_next); standalone MCP tool optional",
        "rules": [
            "H1 first token IS the spec id: `# SPEC-FOO-1 — Title` → id `SPEC-FOO-1`",
            "Declared artifact MUST match H1: `> **apatch artifact:** \\`spec:SPEC-FOO-1\\``",
            "Requirements: `## R1 <title>`, `## R2`, … (id ends with a digit)",
            "Each Rk needs `(verify: <cmd>)` — requirement-specific, not one global build",
            "verify must exercise the requirement (pytest node), not only tsc/npm build",
            "Needles (spec_run / execute_next): mutation dicts `{action, target_file, …}` — not prose",
        ],
        "lint_errors": {
            "id_mismatch": "H1 id ≠ declared spec: artifact — breaks TrustChain anchors",
            "missing_verify": "Rk without (verify:) — attested would not mean 'done'",
            "generic_verify": "same verify on every Rk — one green test proves nothing",
        },
        "template_minimal": (
            "# SPEC-<ID> — <title>\\n"
            "> **apatch artifact:** `spec:SPEC-<ID>`\\n"
            "## R1 <title>\\n(verify: pytest tests/test_<id>_r1.py)"
        ),
        "consumer_scaffold": "init-consumer creates docs/specs/SPEC-TEMPLATE.md + .cursor/rules/",
    }


def spec_execution_playbook() -> Dict[str, Any]:
    """RFP-007 Executable Specifications loop — embedded in ``apatch_doctor``."""
    return {
        "model": "SPEC.md → Requirements (spec:ID#Rk) → Intent → Session → Verify → Attestation",
        "requirement_unit": "Each '## Rk' heading is a requirement artifact spec:ID#Rk@<hash>",
        "acceptance_check": "Optional '(verify: <cmd>)' per requirement — 'done' means its check is green",
        "status_is_derived": (
            "apatch_spec_status reads state from the attested ledger — it is NOT an "
            "agent-writable field, so 'attested' cannot be faked"
        ),
        "states": "pending → in_progress → attested; stale if spec text changed or file_drift vs ledger hashes (R5)",
        "authoring": spec_authoring_requirements(),
        "workflow_choice": {
            "one_rk_manual": "§3I: spec_lint → spec_next → session_start(requirement) → mutate → verify → attest",
            "one_rk_auto": "§3J: apatch_execute_next(needles=[mutation dicts])",
            "whole_spec_small": "§3K: apatch_spec_run(requirements={Rk: {needles: [...]}}) — few Rk, compact needles",
            "whole_spec_large": "§3K: commit manifests/SPEC-X.run.json → apatch_spec_run(manifest_path=…) or chunk_rk_per_call=1 with inline requirements per Rk",
        },
        "loop": [
            "apatch_spec_lint(spec='SPEC-42')  # format + rfp gates + plan_scaffold when passed",
            "apatch_spec_next(spec='SPEC-42')  # what's not done?",
            "apatch_session_start(requirement='SPEC-42#R3')  # auto intent+artifact",
            "apatch_generate → apatch_apply_session  # implement",
            "apatch_verify_run  # run the requirement's verify",
            "apatch_attest  # sign → requirement flips to 'attested'",
            "apatch_spec_status(spec='SPEC-42')  # repeat until done",
        ],
        "resume": "Agent resumes 'continue SPEC-42' via apatch_spec_next — no need to re-read the doc",
    }


def agent_onboarding_playbook() -> Dict[str, Any]:
    """Fresh-agent playbook — embedded in apatch_doctor and linked from spec_lint."""
    return {
        "doc": "docs/agent-onboarding.md",
        "first_call": "apatch_doctor(target_dir='.') once per session",
        "authoritative_tool_count": "mcp_health.mcp_tool_catalog.count (not IDE Settings tool list)",
        "primary_spec_gate": (
            "apatch_spec_lint(spec='SPEC-X') → passed + rfp_coverage + plan_scaffold + agent_next"
        ),
        "never": [
            "Trust IDE MCP tool count over doctor mcp_tool_catalog.count (the authoritative, never-hardcoded source)",
            "Call missing standalone tools when spec_lint embeds the same gates",
            "Hand-stage patches.jsonl for SPEC workflows",
            "Write/StrReplace on protected paths",
            "Use raw ssh/scp/tar pipes to move local source to a remote workspace",
            "Use Codex direct file-edit tools when codex-doctor says brokered_edits_required",
        ],
        "pipeline": [
            "apatch_spec_lint(spec='SPEC-X')",
            "Read autonomy_boundary — scaffold ≠ auto-codegen",
            "Per Rk while discovering: apatch_execute_next(requirement='SPEC-X#Rk', needles=[…])",
            "When all pending needles ready: apatch_spec_run(requirements=…) until continue=false",
            "apatch_spec_status(spec='SPEC-X')",
        ],
        "autonomy_boundary": autonomy_boundary(),
        "workflow_choice": spec_execution_playbook().get("workflow_choice"),
    }


def remote_workspace_playbook() -> Dict[str, Any]:
    """Remote-work routing rules embedded in protocol_contract."""
    return {
        "policy": "Remote host/path/credential topology is local broker policy, not agent context.",
        "remote_task": "Use apatch_remote_task_run(remote_target='<alias>', intent=..., plan={'needles': [...]}, plan={'logs_path': '<remote JSONL>'}, plan={'spec': 'SPEC-X', 'requirements': {...}} for one remote spec_run, plan={'execute_next': True, 'spec': 'SPEC-X', 'requirement': 'SPEC-X#Rk', 'needles': [...]} for one exact already-attested requirement, or plan={'specs': [...], 'requirements': {...}, 'execution_mode': 'shared_maintenance'} for one partitioned apply plus parallel exact Rk verifies, with dry_run=false for governed remote code WRITES.",
        "remote_task_write_contract": {
            "dry_run_default_is_true": "Without dry_run=false the run SIMULATES the whole doctor/session/generate/apply/verify/attest/session_end ceremony (incl. attest, phase=complete) against a FAKE transport and writes NOTHING — the response carries applied:false and transport:'fake'. A simulated attest is NOT a real apply; never report success.",
            "to_actually_write": "Pass dry_run=false AND either put the edit in plan.needles or point plan.logs_path at an already generated remote JSONL patch log. No needles/logs_path -> the apply step is skipped -> nothing is written.",
            "spec_owned": (
                "For one existing SPEC, use plan.spec + plan.requirements so the broker "
                "runs spec_run directly; plan.requirement remains supported for one-Rk "
                "fix-forward/bootstrap work. If targets belong to two or more SPECs, use "
                "plan.specs + plan.requirements so the broker runs spec_run_multi. "
                "An unbound remote task fails with SPEC_WORKFLOW_REQUIRED."
            ),
            "confirm": (
                "Verify result['applied'] is true (a real apatch_apply_session step ran). "
                "The exec channel (apatch_remote_service_action exec) is best-effort read-only diagnostics; "
                "use policy-defined service command actions for maintenance."
            ),
        },
        "source_handoff": (
            "If the remote machine cannot fetch the repo because credentials live locally, "
            "use apatch_remote_source_handoff(alias='<alias>', execute=true)."
        ),
        "service_lifecycle": (
            "Use apatch_remote_service_action for policy-approved status/restart/logs/healthcheck "
            "and policy-defined maintenance command actions."
        ),
        "never": [
            "Do not run git clone on the remote to work around missing credentials.",
            "Do not run scp/rsync or tar | ssh to copy local source.",
            "Do not disclose SSH host, remote path, jump host, or credential location in agent-visible plans.",
            "Do not ask the user for remote GitHub credentials when local source handoff is enabled.",
        ],
        "if_tool_missing": (
            "Stop and ask for MCP reload/restart; do not fall back to raw shell transport."
        ),
    }


def protocol_contract() -> Dict[str, Any]:
    """Non-negotiable agent contract — why humans keep pasting the same correction."""
    return {
        "first_call": (
            "apatch_doctor once per session OR fetch apatch://playbook/index then linked resources"
        ),
        "invariant": "Intent → Session → Mutation → Verification → Attestation | Rollback",
        "spec_ownership_gate": {
            "rule": (
                "Files owned by an existing executable SPEC may mutate only through "
                "spec_run/execute_next with the exact SPEC#Rk session artifact."
            ),
            "what_is_owned": (
                "Ownership is opt-in and is never implied by a SPEC merely describing a "
                "file. A SPEC.md owns itself. An existing slug contract protects bounded "
                "legacy category paths and its explicitly declared category surface, "
                "not shared_services, global_sources, or a slug word in a shared filename. "
                "Strict '> **ownership mode:** strict' with an 'owns:' line names exact "
                "paths or 'dir/**' prefixes and takes precedence over slug ownership. "
                "A file outside these surfaces is unowned: any governed session may "
                "write it, subject to the remaining sandbox and signature checks."
            ),
            "authority": (
                "resolve_spec_owned_targets decides ownership; prose in a SPEC, an RFP or "
                "a shared filename never does; bounded legacy conventions are explicit compatibility rules."
            ),
            "enforcement": "pre-generation hard fail",
            "failure": (
                "SPEC_WORKFLOW_REQUIRED; no patch JSONL is written and no apply begins."
            ),
            "not_enough": (
                "A signed generic session or bare spec artifact does not authorize the mutation."
            ),
        },
        "remote_workspaces": remote_workspace_playbook(),
        "codex_no_stop": {
            "doctor": "apatch mcp codex-doctor --target-dir <target> --json",
            "if_brokered_edits_required": (
                "Continue through apatch_generate_batch/apatch_apply_session or remote broker tools; "
                "do not use apply_patch/direct file writes and do not ask the user for filesystem approval."
            ),
            "why": "MCP broker tools are the trusted executor; Codex filesystem tools are not part of governed apatch work.",
        },
        "recovery": {
            "red_test_not_fatal": (
                "A failed verify moves the session to lifecycle 'failed' — call "
                "apatch_resume_session, then fix and re-verify/attest. Do NOT "
                "session_end + restart for an ordinary failure (RFP-027)."
            ),
            "RUNTIME_TRANSITION": (
                "op not allowed in lifecycle 'failed'/'verifying' → apatch_resume_session"
            ),
            "shared_file_stale": (
                "One module satisfies several Rk → earlier Rk go stale (file_drift). "
                "Fastest: apatch_rebind_stale(spec='SPEC-X') — re-verifies each "
                "file_drift-stale Rk and noop-attests the green ones in one call. "
                "Manual fallback: apatch_noop_attest(covered_by='R1,R4') per Rk — no marker file."
            ),
            "SESSION_ALREADY_COMPLETE": (
                "apply_session applied nothing → reset=true to re-apply a changed patch "
                "set, or proceed to verify/attest"
            ),
            "BACKUPS_PRUNED": (
                "rollback target gc'd → start a fresh session; pruned backups cannot be restored"
            ),
            "self_edit_restart_required": (
                "apply_session.restart_required → send the user the steps; MCP restart needed "
                "for changed apatch modules to take effect"
            ),
            "governance_doc_unnotarized": (
                "An RFP/SPEC.md (or any doc) created with Write/Edit is never_notarized, so "
                "apatch_verify_notarization(staged=true) blocks the commit. Route it through a "
                "governed mutation (a real edit, e.g. a footer, via generate_batch -> "
                "apply_session -> attest); only files touched by an attested mutation enter "
                "the notarized index."
            ),
            "spec_text_changed_on_tail_edit": (
                "Editing the file tail stales the LAST requirement: its content_hash spans to "
                "EOF, so a footer after the final '## Non-goals' trips spec_text_changed "
                "(which rebind_stale skips). Re-anchor that one Rk: "
                "session_start(requirement='SPEC-X#Rk') -> apatch_noop_attest(covered_by='Rk'). "
                "Or keep trailing edits above the last '## Rk'."
            ),
            "spec_dependency_unmet_false_positive": (
                "SPEC_DEPENDENCY_UNMET naming a spec that is not really upstream: the prose has "
                "a dependency keyword (dependency/requires/depends on/prerequisite) on the SAME "
                "line as another SPEC-id, and parse_spec_dependencies treats it as an upstream "
                "dep. Keep those keywords off any line that mentions another SPEC-id."
            ),
        },
        "task_routing": {
            "implement_whole_spec": (
                "RFP-009 §3K: apatch_spec_run(spec='SPEC-X', requirements={Rk: {needles}}) for compact specs; "
                "OR apatch_spec_run(manifest_path='manifests/SPEC-X.run.json') when needles are large "
                "— ONE workflow; repeat while continue=true"
            ),
            "continue_spec": (
                "apatch_spec_status → apatch_spec_run(spec='SPEC-X') resume "
                "(NOT N× manual spec_next + hand jsonl)"
            ),
            "one_requirement": (
                "§3J: apatch_execute_next(spec='SPEC-X', needles=[mutation dicts]) "
                "— generate_batch inside, no hand-staged jsonl"
            ),
            "inspection_dogfood": (
                "§3I: spec_next → session_start(requirement) → generate_batch → apply_session "
                "→ verify_run(verify from spec) → attest → session_end"
            ),
            "mass_refactor_not_spec": (
                "§3B: session_start → generate_batch → simulate(out_path) → apply_session(out_path) "
                "→ verify → attest → session_end (runtime_hygiene.mass_refactor_cycle)"
            ),
        },
        "user_phrases_use_spec_run": [
            "implement SPEC-",
            "continue SPEC-",
            "выполни спеку",
            "dogfood SPEC-",
            "attest SPEC-",
            "whole spec",
            "all requirements",
        ],
        "never": [
            "Hand-write or stage patches.jsonl for SPEC workflows",
            "Ephemeral manifests/*.run.json regenerated ad-hoc each run (use committed manifest_path or inline requirements)",
            "Python/shell driver scripts calling spec_run_enriched() to bypass MCP (use apatch_spec_run MCP or CLI)",
            "apatch_generate → save jsonl → apatch_plan_batch as primary spec path",
            "Write/StrReplace/sed on protected src/** (use apply_session lease)",
            "finalize/attest in a different session than mutations for the same Rk",
            "apatch_spec_run without needles for all pending Rk (MANIFEST_GAP — use execute_next per Rk while discovering)",
            "Read apatch repo docs/RFP-009-*.md — consumer has no access; use MCP spec_run field",
            "Hand-create patches-*.jsonl in repo root (use session + generate_batch → .apatch/tmp/)",
            "Raw ssh/scp/rsync/tar pipes for remote source handoff (use apatch_remote_source_handoff)",
            "Skip apatch_session_end after mass refactor (orphan EPHEMERAL)",
            "pip install / restart MCP from agent under sandbox enforce (human-only)",
        ],
        "spec_rk_cycle": [
            "apatch_spec_lint(spec) — format + rfp_coverage + plan_scaffold when passed",
            "While discovering: execute_next per Rk; when all pending needles ready: spec_run(requirements=…)",
            "apatch_spec_next(spec) OR apatch_spec_run(dry_run=true) for batch resume",
            "apatch_session_start(requirement='SPEC-X#Rk')",
            "apatch_generate_batch(needles=[{action, target_file, …}]) — MCP only, not disk staging",
            "apatch_simulate → apatch_apply_session(verify_deferred=true)",
            "apatch_verify_run(verify=<from spec Rk>)",
            "apatch_attest → apatch_session_end",
        ],
        "rfp_009_summary": (
            "RFP-009 Spec Run: apatch_spec_run runs lint+deps+all pending Rk in governed cycles. "
            "Delivery: inline requirements={Rk: {needles}} for compact specs; "
            "manifest_path='manifests/SPEC-X.run.json' (versioned, linted) for large needles. "
            "Embedded in every apatch_doctor and apatch_spec_* response."
        ),
    }


def autonomy_boundary() -> Dict[str, Any]:
    """RFP-024 — what is autonomous vs agent cognition (embedded in scaffold/spec_lint)."""
    return {
        "doc": "docs/agent-onboarding.md#autonomy-boundary-rfp-024",
        "scaffold_provides": [
            "plan_scaffold + target_files hints + needle_templates (structure only)",
            "routing: lint → fill needles → spec_run or execute_next",
        ],
        "scaffold_does_not": [
            "LLM-generate find_text/replace_text (RFP-024 non-goal)",
            "auto-apply needle_templates",
            "run spec_run without needles (returns MANIFEST_GAP)",
        ],
        "orchestration_autonomous": (
            "apatch_spec_run when manifest/requirements include needles for ALL pending Rk — "
            "then loops session→apply→verify→attest until done (chunk_rk_per_call optional)"
        ),
        "cognition_required": (
            "Agent reads source and authors mutation dicts. While discovering: "
            "apatch_execute_next per Rk. When all pending needles ready: spec_run batch."
        ),
        "manifest_gap_means": (
            "Needles missing for one or more pending Rk — expected until you fill them. "
            "Not missing autonomy; use execute_next for current Rk or fill plan_scaffold for all."
        ),
        "workflows": {
            "many_rk_needles_unknown": "apatch_execute_next(spec, requirement='SPEC-X#Rk', needles=[…]) per Rk",
            "all_needles_ready": "apatch_spec_run(spec, requirements={…}) or manifest_path",
            "discover_gaps_only": "apatch_spec_run(spec, dry_run=true) — no needles required",
        },
    }


def spec_run_playbook() -> Dict[str, Any]:
    """RFP-009 batch spec workflow — embedded in doctor and spec_run tools."""
    return {
        "rfp": "RFP-009 Spec Run — entire executable spec in one MCP workflow (not N× §3I)",
        "tool": "apatch_spec_run",
        "when": (
            "User says implement/continue/dogfood a SPEC; OR ≥2 pending Rk; "
            "needles known or from dry_run gaps"
        ),
        "primary_path": "compact: inline requirements={Rk: {needles}}; large: manifest_path='manifests/SPEC-X.run.json'",
        "large_spec_path": "apatch_spec_run(spec=…, manifest_path='manifests/SPEC-X.run.json') — versioned, linted, no MCP payload bloat",
        "per_rk_internal": (
            "session_start → generate_batch(needles) → apply_session(verify_deferred=true) "
            "→ verify_run → attest → session_end in ONE session"
        ),
        "needles": (
            "Mutation dicts only: {action: replace|create|delete|rename|chmod|shift_outline|insert_before|insert_section, "
            "target_file, find_text/replace_text | content | mode/executable | after/before/levels/delta/shift_following}. "
            "Numbered markdown: insert_section + shift_following, not N× heading replace (SPEC-DOC-OUTLINE-1)."
        ),
        "verify_deferred": "true (default) — chunk verify rolls back patches if false",
        "attestation": "mutate + finalize must stay in the same governed session per Rk",
        "ephemeral_logs": (
            "JSONL → .apatch/tmp/<session_id>/; use generate out_path for simulate/apply"
        ),
        "lessons": [
            "Numbered markdown restructure: {action: insert_section, before: '## 3.', shift_following: {levels:[2,3], delta:1}} — not N× replace on headings (SPEC-DOC-OUTLINE-1)",
            "Use literal strings from source in find_text (e.g. /api/v1/work-items/ not /items/)",
            "dry_run first → fill gaps → spec_run until continue=false",
            "≥2 pending Rk: apatch_spec_run (§3K); large needles → manifest_path, not Python driver scripts",
            "Never manifests/*-spec.py drivers calling spec_run_enriched() — use MCP apatch_spec_run or CLI apatch spec run",
            "Shared file across Rk: after batch-run, early Rk go stale (file_drift). "
            "Fix in one call: apatch_rebind_stale(spec='SPEC-X') re-verifies each "
            "file_drift-stale Rk and noop-attests the green ones (skips spec_text_changed). "
            "Manual fallback: re-attest stale Rk via apatch_noop_attest (no shared-file mutation again).",
            "New doc (RFP/SPEC.md) from Write is never_notarized: route it through a governed "
            "mutation (footer via generate_batch + attest) before commit, else "
            "verify_notarization blocks it (protocol_contract.recovery.governance_doc_unnotarized)",
            "Trailing edits stale the last Rk (content_hash spans to EOF); a 'dependency' "
            "keyword next to another SPEC-id makes it a phantom upstream dep "
            "(SPEC_DEPENDENCY_UNMET) — see protocol_contract.recovery",
            "Verify substrings in (verify:) must appear literally in target docs "
            "(e.g. assert 'L2' in text — doc needs L2, not only L1–L5 range)",
        ],
        "autonomy_boundary": autonomy_boundary(),
        "needles_scaffold": (
            "RFP-024 scaffold = structure only (target_files, empty needles). "
            "Orchestration autonomous after needles filled; cognition per Rk until then — see autonomy_boundary"
        ),
        "loop": [
            "apatch_spec_lint(spec='SPEC-X')  # format + rfp gates + plan_scaffold",
            "apatch_spec_run(spec='SPEC-X', dry_run=true)  # resume batch run",
            "apatch_spec_plan_register(plan=<plan_scaffold>) OR apatch_spec_run(requirements={...})",
            "while continue: apatch_spec_run(spec='SPEC-X')",
            "apatch_spec_status(spec='SPEC-X')",
        ],
    }


def governed_workflow_steps(*, with_artifacts: bool = True) -> List[str]:
    from apatch.mcp.profiles import mcp_profile_name

    if mcp_profile_name() == "compact":
        return [
            "apatch_doctor(target_dir='.')",
            "apatch_spec_lint(spec='SPEC-X')",
            "apatch_execute_next(spec='SPEC-X', requirement='SPEC-X#Rk', needles=[...], request_id='<uuid-mutate>')  # anchors requirement artifacts",
            "apatch_execute_next(spec='SPEC-X', requirement='SPEC-X#Rk', finalize=true, governed_session_id='<id>', session_token='<token>', request_id='<uuid-finalize>')",
            "apatch_spec_status(spec='SPEC-X')",
        ]
    steps = [
        "apatch_doctor(target_dir='.')",
    ]
    if with_artifacts:
        steps.append(
            "apatch_session_start(intent='<why>', artifacts=['spec:<ID>@sha256:…'])"
        )
    else:
        steps.append("apatch_session_start(intent='<why>')")
    steps.extend(
        [
            "apatch_generate / apatch_plan_batch → apatch_simulate → apatch_apply_session",
            "apatch_verify_run(target_dir='.')",
            "apatch_attest(target_dir='.')",
        ]
    )
    if with_artifacts:
        steps.append("apatch_trustchain_coverage(artifact='spec:<ID>')")
    steps.extend(
        [
            "apatch_attestation_export(out_path='.apatch/audit_bundle.json')",
            "apatch_session_end(target_dir='.')",
        ]
    )
    return steps


def primary_artifact_token(target_dir: str) -> Optional[str]:
    try:
        from apatch.session_state import load_session_state

        raw = load_session_state(target_dir)
        arts = raw.get("artifacts") or []
        if not arts or not isinstance(arts[0], dict):
            return None
        kind = arts[0].get("kind")
        ident = arts[0].get("id")
        if kind and ident:
            return f"{kind}:{ident}"
    except Exception:
        pass
    return None


def hint_trustchain_coverage(target_dir: str = ".") -> str:
    token = primary_artifact_token(target_dir)
    if token:
        return f"apatch_trustchain_coverage(artifact={token!r})"
    return "apatch_trustchain_coverage(artifact='kind:id')"


def _attach_slim_guidance(
    out: Dict[str, Any],
    tool_name: str,
    *,
    target_dir: str = ".",
) -> Dict[str, Any]:
    """Hot-path tools: pointer to doctor, no multi-KB playbooks (RFP-019 L1-5)."""
    out["guidance_mode"] = "doctor_only"
    out["guidance_ref"] = _GUIDANCE_REF
    if tool_name in (
        "apatch_generate",
        "apatch_generate_batch",
        "apatch_plan",
        "apatch_plan_batch",
    ):
        out["if_spec_task"] = (
            "STOP: for SPEC-* use apatch_spec_run or apatch_execute_next — "
            "not standalone generate→jsonl; see apatch_doctor.protocol_contract"
        )
        out["allowed_when"] = (
            "§3B mass refactor with session; or internal call from execute_next/spec_run"
        )
        out["logs_path_hint"] = (
            "Use generate response out_path for simulate/apply_session; "
            "fetch apatch://playbook/runtime_hygiene if unsure"
        )
    elif tool_name == "apatch_spec_lint" and not out.get("passed"):
        out["fix_hint"] = "Fix SPEC.md per apatch_doctor.spec_authoring; re-run apatch_spec_lint"
    elif tool_name == "apatch_spec_lint" and out.get("passed") and out.get("plan_scaffold"):
        out["autonomy_boundary"] = autonomy_boundary()
        pending_n = len((out.get("needles_scaffold") or {}).get("pending") or [])
        if pending_n > 1:
            out["recommended_tool"] = "apatch_execute_next"
            out["recommended_workflow"] = (
                f"{pending_n} pending Rk — cognition per Rk: execute_next with needles from source; "
                "batch spec_run only when ALL pending have needles (see autonomy_boundary)"
            )
        else:
            out["recommended_tool"] = "apatch_execute_next"
            out["recommended_workflow"] = (
                "Fill needles from source → execute_next OR plan_register then spec_run"
            )
    elif tool_name == "apatch_spec_needles_scaffold" and out.get("ok"):
        pending = out.get("pending") or []
        out["autonomy_boundary"] = autonomy_boundary()
        out["templates_vs_needles"] = (
            "needle_templates[] = structural examples; plan_scaffold.execution_plan.{Rk}.needles "
            "stays empty until YOU fill — templates are never auto-applied"
        )
        out["recommended_tool"] = "apatch_execute_next"
        out["recommended_workflow"] = (
            "Read target_files + autonomy_boundary; per Rk: execute_next with needles from source; "
            "batch spec_run only when ALL pending have needles"
        )
        if pending:
            out["agent_next"] = out.get("agent_next") or (
                f"Cognition for {', '.join(pending)} — templates are examples only; "
                f"author needles → execute_next per Rk"
            )
    elif tool_name == "apatch_simulate" and out.get("needles_hints"):
        out["templates_vs_needles"] = (
            "needles_hints[] are advisory partial dicts from simulate drift — "
            "re-read source, fix find_text, then apply_session"
        )
        out["recommended_workflow"] = (
            "Review needles_hints for drift; author literal find_text from source; "
            "do not auto-apply hints"
        )
    elif tool_name == "apatch_spec_next" and out.get("next"):
        remaining = out.get("remaining") or []
        if len(remaining) >= 2:
            out["recommended_tool"] = "apatch_spec_run"
            out["recommended_workflow"] = "§3K: apatch_spec_run(spec, requirements={Rk: {needles}})"
            out["anti_pattern"] = "Do NOT N× spec_next + hand-staged patches.jsonl"
        else:
            out["recommended_tool"] = "apatch_execute_next"
            out["recommended_workflow"] = "§3J: apatch_execute_next(needles=[mutation dicts])"
    elif tool_name in (
        "apatch_spec_run",
        "apatch_spec_run_manifest_lint",
        "apatch_execute_next",
        "apatch_spec_status",
        "apatch_spec_coverage",
        "apatch_spec_interference",
        "apatch_spec_schedule",
        "apatch_spec_cross_verify",
        "apatch_spec_run_multi",
        "apatch_spec_plan_lint",
        "apatch_spec_plan_register",
        "apatch_spec_plan_diff",
        "apatch_spec_adherence",
    ):
        out["agent_hint"] = "Full spec playbook: apatch_doctor.spec_run + protocol_contract"
    elif tool_name == "apatch_session_start" and out.get("ok"):
        arts = (out.get("session") or {}).get("artifacts") or []
        out["artifact_anchored_intent"] = {
            "bound_artifacts": arts,
            "next_after_mutations": hint_trustchain_coverage(target_dir),
            "playbook_ref": "apatch_doctor",
        }
    elif tool_name == "apatch_session_state":
        arts = (out.get("session") or {}).get("artifacts") or []
        if arts:
            out["artifact_anchored_intent"] = {
                "bound_artifacts": arts,
                "coverage_check": hint_trustchain_coverage(target_dir),
                "playbook_ref": "apatch_doctor",
            }
    elif tool_name == "apatch_attest" and out.get("ok"):
        out["artifact_anchored_intent"] = {
            "committed_artifacts": out.get("artifacts")
            or _session_artifacts(target_dir),
            "next": hint_trustchain_coverage(target_dir),
            "playbook_ref": "apatch_doctor",
        }
    elif tool_name in ("apatch_trustchain_coverage", "apatch_trustchain_history"):
        out.setdefault("artifact_anchored_intent", {"playbook_ref": "apatch_doctor"})
        if tool_name == "apatch_trustchain_coverage" and out.get("artifact"):
            cov = out.get("coverage") or {}
            out["artifact_anchored_intent"]["coverage_complete"] = bool(cov.get("complete"))
    return out


_guidance_emitted_sessions: set = set()


def _session_guidance_first_emit(target_dir: str = ".") -> bool:
    """True the first time heavy guidance is emitted for a session (RFP-027 U27-G)."""
    try:
        from apatch.session_state import load_session_state

        sid = (load_session_state(target_dir) or {}).get("session_id")
    except Exception:
        sid = None
    if not sid:
        return True  # no session → never dedupe
    if sid in _guidance_emitted_sessions:
        return False
    _guidance_emitted_sessions.add(sid)
    return True


_TOOL_TOKEN = re.compile(r"apatch_[a-z0-9_]+")


def unavailable_tool_names(text: Any) -> List[str]:
    """Tools named in one piece of guidance that the active MCP profile does not serve."""
    from apatch.mcp.profiles import allowed_tools

    allowed = allowed_tools()
    if allowed is None:
        return []
    return sorted({name for name in _TOOL_TOKEN.findall(str(text)) if name not in allowed})


def split_by_availability(names: Any) -> Any:
    """Split named tools into what the active profile serves and what needs an upgrade.

    An entry that is not an ``apatch_*`` tool -- a CLI hint or prose -- is never filtered.
    """
    from apatch.mcp.profiles import allowed_tools

    allowed = allowed_tools()
    available: List[Any] = []
    upgrades: List[Any] = []
    for name in names:
        head = str(name).split()[0] if str(name).strip() else ""
        if allowed is None or not head.startswith("apatch_") or head in allowed:
            available.append(name)
        else:
            upgrades.append(name)
    return available, upgrades


def _apply_profile_truthfulness(out: Dict[str, Any]) -> None:
    """Recommend only what the active profile serves, and name the upgrade (R15).

    A recommendation that calls a tool the profile does not expose costs the agent a
    turn and pushes it toward the shell workarounds the sandbox then blocks. Reference
    text that explains the model is left alone; only the do-this-next surfaces move.
    """
    upgrades: Dict[str, Any] = {}

    for key in ("recommended_workflow", "governed_workflow_with_artifacts"):
        steps = out.get(key)
        if not isinstance(steps, list):
            continue
        kept: List[Any] = []
        for step in steps:
            missing = unavailable_tool_names(step)
            if missing:
                row = upgrades.setdefault(key, {"guidance": [], "requires": []})
                row["guidance"].append(step)
                row["requires"] = sorted(set(row["requires"]) | set(missing))
            else:
                kept.append(step)
        out[key] = kept

    protocol = out.get("agent_protocol")
    if isinstance(protocol, dict):
        for key in list(protocol):
            value = protocol[key]
            if not isinstance(value, str):
                continue
            missing = unavailable_tool_names(value)
            if missing:
                upgrades[key] = {"guidance": protocol.pop(key), "requires": missing}
        if upgrades:
            protocol["requires_profile_upgrade"] = upgrades


def attach_artifact_guidance(
    out: Dict[str, Any],
    tool_name: str,
    *,
    target_dir: str = ".",
) -> Dict[str, Any]:
    """Add ``artifact_anchored_intent`` to MCP responses agents actually read."""
    if not isinstance(out, dict):
        return out
    if mcp_guidance_mode(target_dir) == "doctor_only" and tool_name != "apatch_doctor":
        return _attach_slim_guidance(out, tool_name, target_dir=target_dir)
    if tool_name != "apatch_doctor" and not _session_guidance_first_emit(target_dir):
        out["guidance_deduped"] = True
        return _attach_slim_guidance(out, tool_name, target_dir=target_dir)
    playbook = artifact_anchored_intent_playbook()
    if tool_name == "apatch_doctor":
        out["mcp_guidance_mode"] = mcp_guidance_mode(target_dir)
        out["artifact_anchored_intent"] = playbook
        out["governed_workflow_with_artifacts"] = governed_workflow_steps(with_artifacts=True)
        _apply_profile_truthfulness(out)
        out["sandbox_agent_protocol"] = sandbox_agent_protocol()
        out["spec_authoring"] = spec_authoring_requirements()
        out["spec_execution"] = spec_execution_playbook()
        out["spec_run"] = spec_run_playbook()
        out["agent_onboarding"] = agent_onboarding_playbook()
        out["protocol_contract"] = protocol_contract()
        out["runtime_hygiene"] = runtime_hygiene_playbook()
        out["tool_usage"] = tool_usage_playbook()
        out["mcp_resources"] = mcp_playbook_index()
    elif tool_name in (
        "apatch_spec_lint",
        "apatch_spec_needles_scaffold",
        "apatch_spec_status",
        "apatch_spec_next",
    ):
        out["spec_authoring"] = spec_authoring_requirements()
        out["spec_execution"] = spec_execution_playbook()
        if tool_name == "apatch_spec_lint" and not out.get("passed"):
            out["fix_hint"] = (
                "Read spec_authoring.rules; fix SPEC.md; re-run apatch_spec_lint before spec_run"
            )
        if tool_name == "apatch_spec_needles_scaffold" and out.get("ok"):
            out["autonomy_boundary"] = autonomy_boundary()
            out["templates_vs_needles"] = (
                "needle_templates[] = structural examples; plan_scaffold.execution_plan.{Rk}.needles "
                "stays empty until YOU fill — templates are never auto-applied"
            )
            out["recommended_tool"] = "apatch_execute_next"
        out["protocol_contract"] = protocol_contract()
        if tool_name == "apatch_spec_next" and out.get("next"):
            remaining = out.get("remaining") or []
            if len(remaining) >= 2:
                out["recommended_tool"] = "apatch_spec_run"
                out["recommended_workflow"] = (
                    "§3K RFP-009: apatch_spec_run(spec=…, dry_run=true) → "
                    "requirements={Rk: {needles}} → repeat while continue"
                )
                out["anti_pattern"] = (
                    "Do NOT loop N× (spec_next → hand-staged patches.jsonl). "
                    "spec_run batches all Rk internally."
                )
            else:
                out["recommended_tool"] = "apatch_execute_next"
                out["recommended_workflow"] = (
                    "§3J: apatch_execute_next(needles=[mutation dicts]) OR §3I manual cycle"
                )
    elif tool_name in (
        "apatch_spec_run",
        "apatch_spec_run_manifest_lint",
        "apatch_execute_next",
    ):
        out["spec_authoring"] = spec_authoring_requirements()
        out["spec_run"] = spec_run_playbook()
        out["protocol_contract"] = protocol_contract()
        if tool_name == "apatch_execute_next":
            out["spec_execution"] = spec_execution_playbook()
    elif tool_name == "apatch_simulate" and out.get("needles_hints"):
        out["templates_vs_needles"] = (
            "needles_hints[] are advisory partial dicts from simulate drift — "
            "re-read source, fix find_text, then apply_session"
        )
        out["recommended_workflow"] = (
            "Review needles_hints for drift; author literal find_text from source; "
            "do not auto-apply hints"
        )
    elif tool_name in (
        "apatch_generate",
        "apatch_generate_batch",
        "apatch_plan",
        "apatch_plan_batch",
    ):
        out["protocol_contract"] = protocol_contract()
        out["if_spec_task"] = (
            "STOP: for SPEC-* work use apatch_spec_run (whole spec, RFP-009) or "
            "apatch_execute_next(needles=…) — not standalone generate→jsonl staging"
        )
        out["allowed_when"] = (
            "§3B mass refactor with active session; or internal call from execute_next/spec_run"
        )
    elif tool_name == "apatch_session_start" and out.get("ok"):
        arts = (out.get("session") or {}).get("artifacts") or []
        out["artifact_anchored_intent"] = {
            **playbook,
            "bound_artifacts": arts,
            "next_after_mutations": hint_trustchain_coverage(target_dir),
        }
    elif tool_name == "apatch_session_state":
        arts = (out.get("session") or {}).get("artifacts") or []
        if arts:
            out["artifact_anchored_intent"] = {
                "bound_artifacts": arts,
                "coverage_check": hint_trustchain_coverage(target_dir),
            }
    elif tool_name == "apatch_attest" and out.get("ok"):
        out["artifact_anchored_intent"] = {
            "committed_artifacts": out.get("artifacts")
            or _session_artifacts(target_dir),
            "next": hint_trustchain_coverage(target_dir),
        }
    elif tool_name in ("apatch_trustchain_coverage", "apatch_trustchain_history"):
        out.setdefault("artifact_anchored_intent", {"playbook_ref": "apatch_doctor"})
        if tool_name == "apatch_trustchain_coverage" and out.get("artifact"):
            cov = out.get("coverage") or {}
            out["artifact_anchored_intent"]["coverage_complete"] = bool(cov.get("complete"))
    return out


def _session_artifacts(target_dir: str) -> List[Dict[str, Any]]:
    try:
        from apatch.session_state import load_session_state

        return list(load_session_state(target_dir).get("artifacts") or [])
    except Exception:
        return []


# Back-compat: RFP-023 enriched MCP tools import enrich_tool_response from here.
from apatch.session_state import enrich_tool_response  # noqa: F401
