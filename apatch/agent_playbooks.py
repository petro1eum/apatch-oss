"""Static MCP Resource playbooks (RFP-019 L1-10) — self-contained agent guidance."""

from __future__ import annotations

from typing import Any, Dict


def mcp_playbook_index() -> Dict[str, Any]:
    """Catalog of MCP resources — read once per agent session."""
    return {
        "first_call": "apatch_doctor(target_dir='.') OR fetch all resources below",
        "read_order": [
            "apatch://playbook/protocol_contract",
            "apatch://playbook/runtime_hygiene",
            "apatch://playbook/sandbox_protocol",
            "apatch://playbook/spec_authoring",
            "apatch://playbook/tool_usage",
            "apatch://playbook/doc_outline (if editing numbered markdown)",
            "apatch://playbook/diagnose (if verify failed)",
            "apatch://playbook/spec_run (if implementing a SPEC)",
            "apatch://playbook/spec_execution (if per-Rk inspection / §3I)",
        ],
        "resources": {
            "apatch://playbook/protocol_contract": (
                "Task routing, invariant, never-do list, spec vs mass-refactor choice"
            ),
            "apatch://playbook/runtime_hygiene": (
                "EPHEMERAL JSONL routing, session_end, apatch_gc modes, inference sunset"
            ),
            "apatch://playbook/sandbox_protocol": (
                "What agents must never run in shell; human-only pip/MCP restart"
            ),
            "apatch://playbook/spec_authoring": "SPEC.md format enforced by apatch_spec_lint",
            "apatch://playbook/tool_usage": "Which MCP tool when — decision tree",
            "apatch://playbook/doc_outline": "Numbered markdown: insert_section/shift_outline workflow (SPEC-DOC-OUTLINE-1)",
            "apatch://playbook/diagnose": "Verify failures: diagnostics[] + fix_forward loop (SPEC-DIAGNOSTIC-GRAPH-1)",
            "apatch://playbook/spec_run": "RFP-009 whole-spec batch workflow (§3K)",
            "apatch://playbook/spec_execution": "RFP-007 per-requirement loop (§3I–§3J)",
        },
        "doctor_only_mode": (
            "Consumer default APATCH_MCP_GUIDANCE=doctor_only: hot-path tools return "
            "guidance_ref pointing here; full playbooks on apatch_doctor or MCP resources."
        ),
    }


def runtime_hygiene_playbook() -> Dict[str, Any]:
    """RFP-016 artifact lifecycle — how patch JSONL and .apatch/ hygiene work."""
    from apatch.mcp.profiles import mcp_profile_name

    compact = mcp_profile_name() == "compact"
    return {
        "principle": "Patch JSONL is EPHEMERAL staging — not source code, not for git commit.",
        "ephemeral_routing": {
            "default_out": "patches.jsonl or patches-*.jsonl requested at repo root",
            "resolved_to": ".apatch/tmp/<session_id>/<basename> when governed session active",
            "implementation": "resolve_ephemeral_logs_path in generate_batch / spec_run / execute_next",
            "use_logs_path_from": (
                "generate_batch response out_path — same path for simulate and apply_session"
            ),
            "never": [
                "Hand-create patches-*.jsonl in repo root",
                "Commit patch JSONL to git (consumer .gitignore excludes them)",
                "Pass repo-root logical name to simulate after generate routed to .apatch/tmp/",
            ],
        },
        "mass_refactor_cycle": ([
            "Compact profile governs executable SPEC work through apatch_execute_next or apatch_spec_run",
            "Non-SPEC mass refactors require APATCH_MCP_PROFILE=core",
        ] if compact else [
            "apatch_session_start(intent='<why>', artifacts=[...])",
            "apatch_generate_batch(needles=[{action, target_file, ...}]) — markdown outline: shift_outline|insert_section (SPEC-DOC-OUTLINE-1)",
            "apatch_simulate(logs_path=<out_path from generate response>)",
            "apatch_apply_session(logs_path=<same out_path>, verify_deferred=true) until continue=false",
            "apatch_verify_run(target_dir='.')",
            "apatch_attest(target_dir='.')",
            "apatch_session_end(target_dir='.')  # required: purges tmp JSONL, releases leases",
        ]),
        "spec_cycles": {
            "whole_spec": (
                "apatch_spec_run — per-Rk: session → generate → apply → verify → attest → session_end"
            ),
            "one_rk": "apatch_execute_next(needles=[...]) — same tmp routing internally",
            "never": "Hand-stage patches.jsonl for SPEC workflows",
        },
        "doctor_hygiene_fields": {
            "status": "clean | degraded | critical",
            "orphan_count": "EPHEMERAL on disk with gc_allowed — apatch_gc(mode='safe')",
            "inferred_count": "On disk but not in registry — apatch_gc(mode='reconcile')",
            "unclassified_count": "Unknown .apatch files — see gc report",
            "governed_ops": "Toward inference sunset (block at 100 ops if inferred_count>0)",
        },
        "gc_modes": {
            "report": "apatch_gc(mode='report') — classification only",
            "reconcile": "Register inferred into registry; fixes inference sunset",
            "safe": "Delete EPHEMERAL/GARBAGE where gc_allowed",
            "rotate": "safe + prune HISTORY + stale DEBUG",
        },
        "inference_sunset": {
            "blocks": "apply_session when inferred_count>0 and governed_ops>=100",
            "remediation": "apatch_gc(mode='reconcile') then retry",
            "auto_reconcile": "Execution auto-reconciles inferred once before blocking",
        },
    }


def _profile_truthful_intents(by_intent: Dict[str, Any]) -> Dict[str, Any]:
    """Name only tools the active profile serves, and say what an upgrade adds (R15)."""
    from apatch.agent_guidance import split_by_availability

    out: Dict[str, Any] = {}
    for intent, spec in by_intent.items():
        entry = dict(spec)
        upgrades: list = []
        names = entry.get("tools")
        if isinstance(names, list):
            available, missing = split_by_availability(names)
            upgrades.extend(missing)
            if available:
                entry["tools"] = available
            else:
                entry.pop("tools", None)
        single = entry.get("tool")
        if isinstance(single, str):
            _available, missing = split_by_availability([single])
            if missing:
                upgrades.extend(missing)
                entry.pop("tool", None)
        if upgrades:
            entry["requires_profile_upgrade"] = upgrades
        out[intent] = entry
    return out


def tool_usage_playbook() -> Dict[str, Any]:
    """Which MCP tool when — decision tree for common agent tasks."""
    from apatch.mcp.profiles import mcp_profile_name

    compact = mcp_profile_name() == "compact"
    playbook: Dict[str, Any] = {
        "always_first": "apatch_doctor",
        "by_intent": {
            "implement_whole_spec": {
                "tool": "apatch_spec_run",
                "when": "All pending Rk have needles ready; user says implement/continue whole spec",
                "not": "spec_run when MANIFEST_GAP (needles missing — use execute_next per Rk first)",
            },
            "one_spec_requirement": {
                "tool": "apatch_execute_next",
                "when": "One Rk at a time while discovering needles (typical 21-Rk spec); needles as inline dicts",
            },
            "mass_refactor_not_spec": {
                "tools": (["apatch_execute_next", "apatch_spec_run"] if compact else [
                    "apatch_session_start",
                    "apatch_generate_batch",
                    "apatch_simulate",
                    "apatch_apply_session",
                    "apatch_session_end",
                ]),
                "required_profile": "core" if compact else "core_or_full",
            },
            "orient_before_change": {
                "tools": ["apatch_impact"],
                "when": (
                    "Before choosing where to write — which files and tests a file or "
                    "symbol change reaches"
                ),
            },
            "fix_verify_failure": {
                "resource": "apatch://playbook/diagnose",
                "when": "verify_run ok:false — read diagnostics[]",
                "tools": [
                    "apatch_build_diagnose",
                    "apatch_generate_batch",
                    "apatch_apply_session",
                    "apatch_verify_run",
                ],
            },
            "numbered_markdown_outline": {
                "resource": "apatch://playbook/doc_outline",
                "when": "Insert §N or renumber ## N. / ### N.M in vision/spec .md",
                "tools": [
                    "apatch_session_start",
                    "apatch_generate_batch",
                    "apatch_simulate",
                    "apatch_apply_session",
                    "apatch_verify_run",
                    "apatch_attest",
                    "apatch_session_end",
                ],
            },
            "inspect_spec_progress": {
                "tools": ["apatch_spec_status", "apatch_spec_next", "apatch_spec_coverage"],
            },
            "author_spec_from_contract": {
                "tool": "apatch_spec_scaffold (MCP) / apatch spec scaffold --from-contract (CLI) — RFP-023",
                "when": "Starting a SPEC governed by an RFP/contract — do NOT hand-author the Rk checklist + traceability",
                "how": "Emits one Rk stub per RFP acceptance row + a pre-filled R0 traceability gate; contract-complete by construction (0 coverage gaps). You only fill each (verify:).",
                "then": "apatch_spec_lint(spec=...) to fill verify commands + gate, then implement",
            },
            "check_contract_conformance": {
                "tool": "apatch conformance gate (CLI) — does the WHOLE contract still hold (RFP-035)",
                "when": "Opt-in via .apatch/conformance.json — inert if absent. Classifies every gated spec at once.",
                "buckets": "conformant | drifted (verify RED now -> real regression) | stale (verify GREEN, attestation old -> re-attest, NOT a bug) | unproven (no verify)",
                "drifted_vs_stale": "Block only on drifted (live red verify). A stale attestation with a green verify is bookkeeping -> apatch_rebind_stale(spec=...). Never conflate them (cry wolf).",
                "scoped_slug_gate": "For one slug/spec, use --spec SPEC-X. For enrollment/static checks inside a spec with live_verify=true, add --no-live to avoid recursive self-verify.",
                "policy": "mode / block_on / ci_safe are the project owner's choice. Never flip blocking yourself. --base scopes live verify to changed specs.",
            },
            "verify_gate_quality": {
                "tool": "apatch_probe (MCP) / apatch probe (CLI) — RFP-005 differential probe: perturb, re-measure, judge the delta vs a polarity",
                "when": "Prove a green verify is trustworthy — does it catch breakage, did anything regress, is an old attestation still green",
                "modes": {
                    "falsify": "must_diverge — corrupt the files a gate guards (files=...); verify MUST go RED. Green = FALSE gate (the test does not test what it guards). verdict real|false",
                    "regress": "must_hold — no NEW failures vs a recorded corpus (baseline_failures or .apatch/verify_baseline.json). verdict stable|regressed",
                    "ratify": "must_hold — an attested gate must STILL be green now; red now = STALE attestation. verdict ratified|stale",
                },
                "verify_is": "any shell command (pytest / script / linter) — domain-agnostic",
            },
            "track_observed_reality": {
                "tool": "apatch_reality (MCP) / apatch reality (CLI) — RFP-005 reality ledger: reality is the source of truth, requirements discharge records",
                "when": "A bug/incident/feedback/finding is observed, or you want to see what observed reality is still uncovered",
                "actions": {
                    "add": "append an observed fact (summary, source, kind) — undischarged DEBT by construction until a requirement claims it via a (discharges: REC-…) line",
                    "status": "coverage — covered (discharged by an attested green gate) | pending (claimed, gate not attested) | uncovered (derived debt)",
                },
                "model": "reality stands above the spec; the spec is accountable to it (does not replace internal invariants)",
            },
            "cleanup_workspace": {
                "tools": ["apatch_gc", "apatch_mcp_hygiene"],
                "note": "apatch_gc = artifacts; apatch_mcp_hygiene = ghost MCP PIDs / leases",
            },
        },
        "logs_path_rule": (
            "After apatch_generate_batch use response out_path for simulate and apply_session."
        ),
        "session_rules": [
            "apatch_session_start before first mutation",
            "apatch_session_end after attest or rollback",
            "Do not leave governed session open after mass refactor",
        ],
    }
    playbook["by_intent"] = _profile_truthful_intents(playbook["by_intent"])
    return playbook


def diagnose_playbook() -> Dict[str, Any]:
    """Unified verify failure diagnostics (SPEC-DIAGNOSTIC-GRAPH-1 / RFP-022)."""
    return {
        "when": "verify_run or apply_session fails — read structured diagnostics, not stderr",
        "resource": "apatch://playbook/diagnose",
        "spec": "docs/specs/SPEC-DIAGNOSTIC-GRAPH-1.md",
        "workflow": [
            "apatch_verify_run fails → read result diagnostics[] (not verify_output parsing)",
            "Check diagnostics[0].recommended_action (fix_forward vs rollback)",
            "Read diagnostics[].suggestions — advisory only",
            "apatch_session_start(intent='fix …') → apatch_generate_batch(needles=[…])",
            "apatch_apply_session → apatch_verify_run → attest → session_end",
        ],
        "fields": {
            "source": "clang|pytest|spec|trust|sandbox",
            "type": "missing_member|test_failure|spec_violation|…",
            "recommended_action": "fix_forward|rollback|reduce_scope|retry_chunk",
        },
        "never": [
            "Parse clang/pytest stderr manually when diagnostics[] present",
            "Auto-apply suggestions without new governed session intent",
        ],
    }


def doc_outline_playbook() -> Dict[str, Any]:
    """Numbered markdown outline — unfamiliar agent workflow (SPEC-DOC-OUTLINE-1)."""
    return {
        "when": (
            "Insert or renumber ## N. / ### N.M sections in vision/spec .md — "
            "not N× replace on full heading lines"
        ),
        "resource": "apatch://playbook/doc_outline",
        "spec": "docs/specs/SPEC-DOC-OUTLINE-1.md",
        "workflow": [
            "apatch_doctor(target_dir='.') — spec_run.needles + lessons[0] in response",
            "apatch_session_start(intent='<why>', artifacts=[...])",
            "Large content: write needles array to .apatch/needles.json",
            "apatch_generate_batch(needles_path='.apatch/needles.json') — actions in tool docstring",
            "apatch_simulate(logs_path=<out_path from generate>) → apatch_apply_session(same out_path, verify_deferred=true)",
            "apatch_verify_run(verify=\"grep -q '## 3.' path/to/file.md\") — grep for .md, not pytest",
            "apatch_attest(target_dir='.') → apatch_session_end(target_dir='.')",
        ],
        "actions": {
            "shift_outline": {
                "fields": "target_file, after, levels, delta",
                "use": "Renumber headings from anchor onward (3→4, 3.1→4.1)",
            },
            "insert_before": {
                "fields": "target_file, before, content",
                "use": "Insert block before anchor without renumbering",
            },
            "insert_section": {
                "fields": "target_file, before, content, shift_following?",
                "use": "Typical «new §N» — shift following headings then insert",
            },
        },
        "example_needle": {
            "action": "insert_section",
            "target_file": "VISION.md",
            "before": "## 3.",
            "content": "## 3. Entity Model\n\n…",
            "shift_following": {"levels": [2, 3], "delta": 1},
        },
        "anchor_rule": "before/after = substring in heading line (e.g. \"## 3.\") — not the full title",
        "needles_path": "Multi-line content → JSON array at .apatch/needles.json (avoids MCP escaping)",
        "never": [
            "N× replace needles on full heading lines (fragile: Cyrillic titles, long lines)",
            "Default pytest verify for pure .md outline edits (use grep or spec (verify:))",
            "Hand-create patches.jsonl in repo root",
        ],
    }
