"""Environment diagnostics for apatch consumers.

``run_doctor`` returns workspace health including ``trustchain.mode``:

- ``audit_pending`` — no ``.trustchain/`` yet (created on first apply/strip)
- ``audit`` — ledger active, enforcement off (soft; ``no_trustchain`` allowed)
- ``enforce`` — ``.apatch/enforcement.json`` active (strict notarization)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from apatch import __version__
from apatch.consumer_profiles import get_profile, list_profiles, manifest_content_for_profile
from apatch.matcher import _LANG_MODULES, load_language
from apatch.toolchain import detect_toolchain
from apatch.trustchain_helper import TrustChainHelper

def _trustchain_python_package_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("trustchain") is not None


def build_trustchain_status(
    workspace: str,
    *,
    tc_active: bool,
    enforcement_on: bool,
) -> Dict[str, Any]:
    """Summarize TrustChain mode for ``apatch doctor`` / ``apatch_doctor``.

    Returns ``mode``, ``summary``, ``behaviors`` (per-flag semantics), ``tooling``,
  and ``upgrade_to_enforce`` when still in audit.
    """
    from apatch.enforcement import load_enforcement_config, notarized_index_path

    cfg = load_enforcement_config(workspace) if enforcement_on else {}
    incremental = bool(cfg.get("incremental_notarization", True)) if enforcement_on else False
    notary_path = notarized_index_path(workspace)
    notary_exists = os.path.isfile(notary_path)

    if enforcement_on:
        mode = "enforce"
        summary = (
            "Strict enforcement: per-apply-chunk batch notarization with one O(1) "
            "commit receipt, rollback on failed Ed25519 commit, no_trustchain "
            "blocked, verify_notarization required before git commit."
        )
    elif tc_active:
        mode = "audit"
        summary = (
            "Audit-only: checkpoints and ledger commits on apply/strip; "
            "failed notarization does not rollback; no_trustchain allowed."
        )
    else:
        mode = "audit_pending"
        summary = (
            "Audit pending: .trustchain/ not created yet — first apatch_apply_session or strip "
            "auto-inits ledger (soft mode until enforcement is enabled)."
        )

    return {
        "active": tc_active,
        "path": TrustChainHelper(workspace, auto_init=False).trustchain_dir if tc_active else None,
        "mode": mode,
        "summary": summary,
        "enforcement_active": enforcement_on,
        "behaviors": {
            "auto_init_on_first_mutate": not tc_active,
            "checkpoint_and_backup": True,
            "ledger_commit_on_apply": True,
            "incremental_notarization": incremental,
            "notarization_granularity": "apply_chunk",
            "full_ledger_scan": "explicit_audit_or_recovery",
            "rollback_on_notarization_failure": enforcement_on,
            "no_trustchain_allowed": not enforcement_on,
            "verify_notarization_before_commit": enforcement_on,
            "notarized_index_present": notary_exists,
        },
        "tooling": {
            "tc_cli_on_path": bool(shutil.which("tc")),
            "trustchain_python_package": _trustchain_python_package_available(),
        },
        "upgrade_to_enforce": (
            None
            if enforcement_on
            else "apatch init-consumer --with-enforcement (writes .apatch/enforcement.json)"
        ),
    }


def build_policy_snapshot(workspace: str) -> Dict[str, Any]:
    """Lightweight trustchain/sandbox/enforcement view for session MCP responses.

    Same semantics as the corresponding blocks in ``run_doctor`` — without toolchain
    scans, tree-sitter probes, or MCP health subprocess checks.
    """
    from apatch.enforcement import is_enforcement_enabled
    from apatch.sandbox import sandbox_status_workspace

    workspace = os.path.abspath(workspace)
    tc = TrustChainHelper(workspace, auto_init=False)
    tc_active = tc.has_trustchain()
    enforcement_on = is_enforcement_enabled(workspace)
    return {
        "trustchain": build_trustchain_status(
            workspace,
            tc_active=tc_active,
            enforcement_on=enforcement_on,
        ),
        "enforcement": {"active": enforcement_on},
        "sandbox": sandbox_status_workspace(workspace),
    }


APATCH_NPM_SCRIPTS = {
    "apatch:doctor": "apatch doctor",
    "apatch:plan": "apatch plan --logs patches.jsonl --target-dir . --json",
    "apatch:apply": "apatch apply --logs patches.jsonl --target-dir . --all -y --verify-deferred",
    "apatch:strip-dry": "apatch strip -n --strict-overlap --json",
    "apatch:phase": "apatch phase run --profile frontend --verify npm run build",
}


def resolve_mcp_command() -> Optional[str]:
    from apatch.mcp_health import resolve_mcp_command as _resolve

    return _resolve()


def _governed_workflow_for_agent() -> List[str]:
    from apatch.agent_guidance import governed_workflow_steps

    return governed_workflow_steps(with_artifacts=True)


def run_doctor(target_dir: str = ".") -> Dict[str, Any]:
    """Diagnostics JSON for CLI ``apatch doctor`` and MCP ``apatch_doctor``.

    Key blocks: ``trustchain`` (mode audit_pending|audit|enforce), ``enforcement``,
    ``sandbox``, ``toolchain``, ``agent_protocol``, ``warnings``.
    """
    workspace = os.path.abspath(target_dir)
    tc = TrustChainHelper(workspace, auto_init=False)
    grammars: List[Dict[str, Any]] = []
    for name in sorted(_LANG_MODULES):
        lang = load_language(name)
        grammars.append({"name": name, "available": lang is not None})

    apatch_bin = shutil.which("apatch")
    toolchain = detect_toolchain(workspace)
    tc_active = tc.has_trustchain()
    warnings: List[str] = []
    from apatch.enforcement import (
        is_enforcement_enabled,
        load_enforcement_config,
        resolve_governed_mode,
    )

    enforcement_on = is_enforcement_enabled(workspace)
    governed_mode = resolve_governed_mode(workspace)
    if not tc_active:
        warnings.append(
            "TrustChain not initialized — first apatch_apply_session will auto-create .trustchain/"
        )
    if enforcement_on and not tc_active:
        warnings.append(
            "ENFORCEMENT ACTIVE: unnotarized file changes will block git commit and may rollback apply"
        )
    from apatch.sandbox import sandbox_status_workspace

    sandbox_info = sandbox_status_workspace(workspace)
    if sandbox_info.get("mode") == "enforce" and not sandbox_info.get("cursor_hooks_installed"):
        warnings.append(
            "SANDBOX ENFORCE: install Cursor hooks (.cursor/hooks.json) — apatch init-consumer --with-sandbox"
        )
    trustchain_status = build_trustchain_status(
        workspace,
        tc_active=tc_active,
        enforcement_on=enforcement_on,
    )
    from apatch.trust_identity import anchor_status

    trust_anchor = anchor_status(workspace)
    if enforcement_on and not trust_anchor.get("secure"):
        warnings.append(
            "TRUST ANCHOR EPHEMERAL: ledger signed with a self-signed dev key. "
            "Enroll apatch as a TrustChain agent and set APATCH_AGENT_ID + "
            "APATCH_AGENT_KEY so signatures chain to the root CA (RFP-005 §5.2)."
        )
    from apatch.inclusion import inclusion_status
    from apatch.policy_lock import policy_status

    policy = policy_status(workspace)
    inclusion = inclusion_status(workspace)
    if (
        inclusion.get("anchored")
        and inclusion.get("uncovered")
        and inclusion["uncovered"] > 0
    ):
        warnings.append(
            f"INCLUSION GAP: {inclusion['uncovered']} locally signed ledger "
            f"entries lack external anchor records — run governed sessions with "
            "APATCH_PLATFORM_URL set so pushes are recorded (RFP-005 §5.10)."
        )
    if policy.get("signed") and policy.get("has_drift"):
        warnings.append(
            "POLICY DRIFT: monitor config changed since it was signed — the "
            "signed policy.lock no longer matches sandbox/enforcement/hooks. "
            "Review the change, then re-sign with `apatch policy sign` (RFP-005 §0.3)."
        )
    elif policy.get("signed") and not policy.get("signature_ok"):
        warnings.append(
            "POLICY SIGNATURE INVALID: .apatch/policy.lock.json failed verification "
            "(possible tampering). Investigate before trusting the monitor config."
        )
    from apatch.mcp_health import build_mcp_health, repair_mcp_configs_if_needed

    mcp_repaired = repair_mcp_configs_if_needed(workspace)
    mcp_health = build_mcp_health(workspace)
    if mcp_repaired:
        mcp_health["auto_repaired"] = mcp_repaired
    warnings.extend(mcp_health.get("warnings") or [])

    from apatch.avatar_delivery import avatar_runtime_compatibility

    avatar_contract = avatar_runtime_compatibility()
    if not avatar_contract.get("ok") and avatar_contract.get("installed_version"):
        missing = ", ".join(avatar_contract.get("missing_symbols") or []) or "unknown"
        warnings.append(
            "AVATAR CONTRACT INCOMPATIBLE: interpreter {} loads avatar-contract {} "
            "from {}; missing symbols: {}. Replace it with the APatch-pinned "
            "avatar-contract build and restart MCP.".format(
                avatar_contract.get("python_executable") or sys.executable,
                avatar_contract.get("installed_version") or "unknown",
                avatar_contract.get("module_path") or "unresolved",
                missing,
            )
        )

    from apatch.agent_guidance import attach_artifact_guidance

    from apatch.lane import lane_info

    from apatch.mcp.bound_workspace import bound_mcp_workspace
    from apatch.workflows import workspace_roaming_status_workspace
    from apatch.mcp.profiles import mcp_profile_name

    mcp_bound = bound_mcp_workspace()
    mcp_profile = mcp_profile_name()
    writer_protocol = mcp_health.get("writer_protocol") or {}
    result: Dict[str, Any] = {
        "version": __version__,
        "python": sys.version.split()[0],
        "python_executable": os.path.realpath(sys.executable),
        "apatch_executable": apatch_bin,
        "mcp_command": mcp_health.get("mcp_command_path"),
        "mcp_health": mcp_health,
        "mcp_profile": mcp_profile,
        "avatar_contract": avatar_contract,
        "writer_protocol": writer_protocol,
        "workspace": workspace,
        "mcp_bound_workspace": mcp_bound,
        "roaming": workspace_roaming_status_workspace(
            workspace,
            bound_workspace=mcp_bound,
        ),
        "lane": lane_info(workspace),
        "trustchain": trustchain_status,
        "trust_anchor": trust_anchor,
        "policy": policy,
        "inclusion": inclusion,
        "recommended_workflow": _governed_workflow_for_agent(),
        "agent_protocol": {
            "governed_lifecycle": "Artifact → Intent → Session → Mutation → Verification → Attestation | Rollback",
            "artifact_session_start": (
                "apatch_session_start(intent='<why>', artifacts=['spec:<ID>@sha256:…'])"
            ),
            "artifact_coverage": "apatch_trustchain_coverage(artifact='kind:id') after apatch_attest",
            "artifact_reverse": "apatch_trustchain_coverage(op_id='<ledger op_id>')",
            "session_start_tool": "apatch_session_start",
            "session_end_tool": "apatch_session_end",
            "mass_refactor": (
                "apatch_execute_next / apatch_spec_run; switch APATCH_MCP_PROFILE=core for non-SPEC refactors"
                if mcp_profile == "compact"
                else "apatch_simulate → apatch_apply_session (chunked; repeat until continue=false)"
            ),
            "unified_orchestrator": "apatch_orchestrate(manifest_path=…) — simulate → graph → execute",
            "preflight_tool": "apatch_simulate",
            "replay_tool": "apatch_replay(session_id=checkpoint)",
            "never_bulk_regex": "No sed/awk/search_replace for mass type/import refactors",
            "never_shell_apatch_when_mcp": "Use MCP tools, not terminal apatch — avoids permission spam and UI hangs",
            "never_pip_or_reinstall_apatch": (
                "Do not run pip install / python -m pip — sandbox blocks it; human reinstalls apatch[mcp]"
            ),
            "never_restart_mcp_server": (
                "Agent cannot start/restart MCP; human restarts user-apatch in Cursor after apatch upgrade"
            ),
            "if_mcp_health_not_ok": "Stop and ask human to pip install -e apatch[mcp] + restart MCP — do not shell-retry",
            "checkpoint_field": "checkpoint",
            "rollback_tool": "apatch_rollback",
            "checkpoint_after_every_chunk": True,
            "mass_apply_guard": 15,
            "enforcement_config": ".apatch/enforcement.json",
            "verify_notarization_before_commit": "apatch_verify_notarization(staged=true)",
            "unnotarized_changes_blocked": enforcement_on,
            "session_state_file": ".apatch/session_state.json",
            "read_state_tool": "apatch_session_state",
            "state_machine": "Every MCP tool returns state_update (phase, next_action, risk_level)",
            "failure_taxonomy": "error_type, recoverable, recommended_action on ok:false",
            "sandbox_config": ".apatch/sandbox.json",
            "sandbox_status_tool": "apatch_sandbox_status",
            "protected_surface": ", ".join(
                sandbox_info.get("protected_globs") or []
            ),
            "executable_specs_entrypoint": (
                "apatch_doctor → read protocol_contract + spec_run + spec_execution; "
                "whole spec = apatch_spec_run (RFP-009); no hand-staged jsonl"
            ),
            "remote_source_handoff": (
                "Remote source copy is brokered by apatch_remote_source_handoff; "
                "never use raw ssh/scp/rsync/tar pipes to work around remote GitHub credentials"
            ),
            "protocol_contract_ref": "apatch_doctor.protocol_contract — anti-patterns humans correct",
        },
        "warnings": warnings,
        "sandbox": sandbox_info,
        "enforcement": {
            "active": enforcement_on,
            "governed_mode": governed_mode,
            "config_path": os.path.join(workspace, ".apatch", "enforcement.json"),
            "config": load_enforcement_config(workspace) if enforcement_on else None,
        },
        "tree_sitter_grammars": grammars,
        "toolchain": toolchain,
        "recommended_verify": toolchain.get("recommended_verify", ""),
        "recommended_verify_resolved": toolchain.get("recommended_verify_resolved", ""),
        "detected_profiles": toolchain.get("detected_profiles", []),
        "consumer_profiles": list_profiles(),
        "npm_scripts": APATCH_NPM_SCRIPTS,
        "example_commands": [
            "apatch scan --limit 5",
            "apatch plan --logs patches.jsonl --target-dir . --json",
            "apatch apply --logs patches.jsonl --target-dir . --all -y --verify pytest",
            "apatch strip -n --manifest manifests/phase.json --file src/Mono.tsx",
            "apatch phase run --profile frontend --manifest manifests/phase.json --file src/Mono.tsx --verify 'npm run build'",
            "apatch doctor",
            "apatch init-consumer --target-dir .",
            "npm run apatch:strip-dry -- --file src/pages/Planning.tsx --manifest manifests/phase.json --out-dir extracted",
        ],
    }
    from apatch.artifact_governance import build_doctor_hygiene

    result["hygiene"] = build_doctor_hygiene(workspace)
    result = attach_artifact_guidance(result, "apatch_doctor", target_dir=workspace)
    refresh = (result.get("mcp_health") or {}).get("tooling_refresh") or {}
    if refresh.get("applies"):
        result["tooling_refresh"] = refresh
        result["agent_protocol"]["after_apatch_source_change"] = (
            "Tell user tooling_refresh.human_steps; wait for MCP restart — do not pip install"
        )
    return result


CONSUMER_MANIFEST_EXAMPLE = """{
  "strips": [
    {
      "label": "example_handlers",
      "start": "// --- Example block start ---",
      "until": "// --- Example block end ---",
      "replace": "// Stubbed: see extracted/example_handlers.fragment.txt",
      "export": "example_handlers.fragment.txt",
      "target_module": "src/features/hooks/useExampleHandlers.ts",
      "module_kind": "hook",
      "parent_import": "import { useExampleHandlers } from '@/features/hooks/useExampleHandlers';",
      "route_from": "/legacy/planning",
      "route_to": "/account-planning",
      "verify_command": "npm run build"
    }
  ]
}
"""

CONSUMER_SPECS_README = """# Executable specifications (RFP-007)

Specs live here as `SPEC-<ID>.md`. **Fresh agent:** run `apatch_doctor` and read
`spec_authoring`, `spec_execution`, `spec_run` in the JSON response — format rules are
embedded there; do not guess.

1. Copy `SPEC-TEMPLATE.md` → `SPEC-<YOUR-ID>.md`
2. `apatch_spec_lint(spec='SPEC-<YOUR-ID>')` — fix until `passed: true`
3. Track: `apatch_spec_next` / `apatch_execute_next` / `apatch_spec_run`

Authoring standard: apatch `docs/spec-authoring.md` (apatch repo). Consumer playbook: `AGENTS.md` §3I–§3K.
"""

CONSUMER_SPEC_TEMPLATE = """# SPEC-<ID> — <Human title>

> **Status:** Draft v1 · **Owner:** <team>
> **apatch artifact:** `spec:SPEC-<ID>`   <!-- MUST equal the H1 token above -->

## 0. Motivation
<why this exists>

## R1 <short requirement title>
<what must be true when done>

(verify: pytest tests/test_<id>_r1.py)

## R2 <short requirement title>
<...>

(verify: <requirement-specific command — not the same global build on every Rk>)

## Non-goals
- <out of scope>
"""

CURSOR_PROTOCOL_RULE = """---
description: apatch governed protocol — spec_run (RFP-009), no manual jsonl staging
alwaysApply: true
---

# apatch protocol (MCP only)

**First tool:** `apatch_doctor` → read `protocol_contract`, `spec_run`, `spec_execution`.

## Executable spec tasks

| User intent | Tool |
|-------------|------|
| Implement / continue **whole** SPEC-X | `apatch_spec_run` (RFP-009) — inline `requirements={Rk: {needles}}` |
| One requirement / hotfix | `apatch_execute_next(needles=[mutation dicts])` |
| Inspection / dogfood one Rk | §3I: `spec_next` → `session_start(requirement)` → `generate_batch` → `apply_session` → verify from spec → `attest` |

## Never (humans keep correcting this)

- Hand-write or stage `patches.jsonl` / `manifests/*.run.json` for SPEC workflows
- `apatch_generate` → save jsonl → `plan_batch` as primary spec path
- `Write`/`StrReplace` on protected paths (`src/**`, `e2e/**`, `server/**`, `scripts/**`, `docs/specs/**`, …)
- “Quick fix” bypass in `scripts/*.mjs`, `e2e/*.spec.ts`, or `docs/specs/*.md`
- `attest` outside the same session as mutations for that Rk
- N× `execute_next` when `spec_run` can batch the spec

Needles = `{action, target_file, find_text/replace_text | content}` via MCP, not prose.
`verify_deferred=true` on mutate (default in spec_run).

RFP-009 is embedded in MCP `spec_run` field — not a file in consumer repos.
"""

CURSOR_SPEC_RULE = """---
description: Executable specifications (RFP-007) — format and MCP workflow for apatch agents
globs: docs/specs/**/*.md
alwaysApply: false
---

# Executable specs in this repo

**Discover requirements:** `apatch_doctor` → fields `spec_authoring`, `spec_execution`, `spec_run`.
**Do not guess** SPEC.md format.

## Authoring (enforced by `apatch_spec_lint`)

- H1: `# SPEC-<ID> — Title` — first token is the id
- Block: `> **apatch artifact:** \\`spec:SPEC-<ID>\\`` — same id as H1
- Requirements: `## R1 <title>`, `## R2`, …
- Each Rk: `(verify: <cmd>)` — requirement-specific pytest/node, not one global `npm run build`

## Workflows

| Scope | MCP |
|-------|-----|
| Lint | `apatch_spec_lint(spec='SPEC-<ID>')` |
| One Rk | `apatch_execute_next(needles=[mutation dicts])` |
| Whole spec | `apatch_spec_run(requirements={Rk: {needles: [...]}})` |

Needles = `{action, target_file, find_text/replace_text | content}` — not prose hints.
`verify_deferred=true` on mutate (default) — else chunk verify rolls back patches.
"""

CONSUMER_README = """# apatch manifests (consumer project)

Run apatch from your venv or PATH:

```bash
apatch doctor
npm run apatch:strip-dry -- --file src/pages/YourPage.tsx \\
  --manifest manifests/apatch.example.json --out-dir src/features/extracted
npm run apatch:phase -- \\
  --manifest manifests/apatch.example.json --file src/pages/YourPage.tsx \\
  --module-out-dir src/features/hooks --to-module hook
```

Agent playbook: `AGENTS.md` (from apatch `docs/AGENTS.template.md`).  
Refresh: `apatch init-consumer --refresh-agents` (preserves `<!-- apatch:project:* -->`).
"""


def _merge_npm_scripts(package_json_path: str) -> bool:
    """Add apatch npm scripts to package.json if missing. Returns True if modified."""
    try:
        with open(package_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    scripts = data.setdefault("scripts", {})
    changed = False
    for key, cmd in APATCH_NPM_SCRIPTS.items():
        if key not in scripts:
            scripts[key] = cmd
            changed = True
    if changed:
        with open(package_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return changed


def _consumer_resource_path(relative: str) -> Any:
    """Return the canonical checkout asset or its wheel-bundled Traversable."""
    from importlib.resources import files

    checkout = Path(__file__).resolve().parent.parent
    bundled = files("apatch").joinpath("_consumer_assets")
    if not bundled.is_dir() and (checkout / "pyproject.toml").is_file():
        return checkout / relative
    resource = bundled
    for part in relative.split("/"):
        resource = resource.joinpath(part)
    return resource


def _require_consumer_resources(
    pack: Dict[str, Any], *, with_ci: bool, with_arch_rules: bool,
    with_enforcement: bool, with_sandbox: bool, with_devcontainer: bool,
    profile: str,
) -> None:
    """Reject incomplete installations before creating any consumer files."""
    required = [_agents_template_path(), _design_partner_playbook_source()]
    profile_doc = pack.get("profile_doc")
    if profile_doc:
        required.append(_consumer_resource_path(profile_doc))
    if with_ci:
        required.extend([
            _ci_template_path(),
            _consumer_resource_path("scripts/ci/apatch-sandbox-gate.sh"),
        ])
    if with_arch_rules:
        required.extend(_docs_manifest_path(source) for source, _ in _ARCH_RULES_PACK)
    if profile == "frontend":
        required.append(_docs_manifest_path("semantic-verify.example.yaml"))
    if with_enforcement:
        required.append(_consumer_resource_path("scripts/hooks/pre-commit-trustchain.sh"))
    if with_sandbox:
        required.extend(_consumer_resource_path("scripts/cursor-hooks/" + name)
                        for name in (
                            "apatch-deny-direct-edit.sh", "apatch-deny-shell-mutate.sh",
                            "apatch-deny-mcp-mutate.sh", "hooks.json",
                        ))
    if with_devcontainer:
        required.append(_devcontainer_template_path())
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "APatch installation is missing required consumer resources: "
            + ", ".join(missing)
        )


def _agents_template_path() -> Any:
    return _consumer_resource_path("docs/AGENTS.template.md")


_APATCH_STACK_START = "<!-- apatch:stack:start -->"
_APATCH_STACK_END = "<!-- apatch:stack:end -->"
_APATCH_PROJECT_START = "<!-- apatch:project:start -->"
_APATCH_PROJECT_END = "<!-- apatch:project:end -->"


def _extract_marked_block(text: str, start_marker: str, end_marker: str) -> Optional[str]:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + len(end_marker)]


def _replace_marked_block(text: str, start_marker: str, end_marker: str, new_block: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return text
    return text[:start] + new_block + text[end + len(end_marker) :]


def compose_agents_md(
    template_body: str,
    *,
    stack_section: str = "",
    existing_agents: Optional[str] = None,
) -> str:
    """Build consumer AGENTS.md from template, optional stack slice, preserve project block on refresh."""
    body = template_body
    if existing_agents:
        project = _extract_marked_block(existing_agents, _APATCH_PROJECT_START, _APATCH_PROJECT_END)
        if project:
            body = _replace_marked_block(body, _APATCH_PROJECT_START, _APATCH_PROJECT_END, project)
    if stack_section.strip():
        stack_block = f"{_APATCH_STACK_START}\n{stack_section.strip()}\n{_APATCH_STACK_END}"
    else:
        stack_block = f"{_APATCH_STACK_START}\n{_APATCH_STACK_END}"
    return _replace_marked_block(body, _APATCH_STACK_START, _APATCH_STACK_END, stack_block)


def _ci_template_path() -> Any:
    return _consumer_resource_path("docs/ci/github-action-apatch.yml")


def _devcontainer_template_path() -> Any:
    return _consumer_resource_path("docs/devcontainer/devcontainer.json")


def _docs_manifest_path(name: str) -> Any:
    return _consumer_resource_path("docs/manifests/" + name)


def _profile_doc_source(pack: Dict[str, Any]) -> Optional[Any]:
    """Resolve apatch repo profile markdown for copying into consumer manifests/."""
    rel = pack.get("profile_doc") or ""
    if not rel.startswith("docs/"):
        return None
    path = _consumer_resource_path(rel)
    return path if path.is_file() else None


_ARCH_RULES_PACK = (
    ("arch-rules.example.yaml", "arch-rules.yaml"),
    ("semantic-verify.example.yaml", "semantic-verify.yaml"),
    ("engineering-pipeline.example.json", "engineering-pipeline.example.json"),
)

_APATCH_STRIP_ARTIFACTS_GITIGNORE = (
    "# apatch strip artifacts (local only)\n"
    "extracted/\n"
    "**/extraction_report.json\n"
)

_APATCH_GITIGNORE_TRACKED = (
    "# apatch runtime (policy configs committed)\n"
    ".apatch/*\n"
    "!.apatch/sandbox.json\n"
    "!.apatch/enforcement.json\n"
    "# apatch ephemeral patch logs (generated)\n"
    "patches-*.jsonl\n"
    "patches.jsonl\n"
    + _APATCH_STRIP_ARTIFACTS_GITIGNORE
)


def _ensure_apatch_gitignore(path: str, *, track_policy: bool) -> bool:
    """Ensure consumer .gitignore ignores apatch runtime but keeps policy JSON when requested."""
    block = (
        _APATCH_GITIGNORE_TRACKED
        if track_policy
        else ".apatch/\n" + _APATCH_STRIP_ARTIFACTS_GITIGNORE
    )
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if track_policy and "!.apatch/sandbox.json" in content:
            return False
        if not track_policy and (".apatch/" in content or ".apatch\n" in content):
            return False
        if track_policy and ".apatch/" in content and "!.apatch/sandbox.json" not in content:
            content = content.replace(".apatch/\n", _APATCH_GITIGNORE_TRACKED)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content if content.endswith("\n") else content + "\n")
            return True
        if ".apatch/" not in content and ".apatch\n" not in content:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n" + block)
            return True
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(block)
    return True


def _design_partner_playbook_source() -> Any:
    return _consumer_resource_path("docs/design-partner-playbook.md")


def _copy_design_partner_playbook(root: str) -> Optional[str]:
    src = _design_partner_playbook_source()
    dst = os.path.join(root, "docs", "design-partner-playbook.md")
    if not src.is_file() or os.path.exists(dst):
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    Path(dst).write_bytes(src.read_bytes())
    return dst

def _copy_docs_manifest(manifests_dir: str, src_name: str, dst_name: str) -> Optional[str]:
    src = _docs_manifest_path(src_name)
    dst = os.path.join(manifests_dir, dst_name)
    if not src.is_file() or os.path.exists(dst):
        return None
    body = src.read_text(encoding="utf-8")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(body)
    return dst


def _scaffold_executable_specs(root: str) -> List[str]:
    """Create docs/specs/ template + Cursor rule so fresh agents see format requirements."""
    created: List[str] = []
    specs_dir = os.path.join(root, "docs", "specs")
    os.makedirs(specs_dir, exist_ok=True)
    readme = os.path.join(specs_dir, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(CONSUMER_SPECS_README.strip() + "\n")
        created.append(readme)
    template = os.path.join(specs_dir, "SPEC-TEMPLATE.md")
    if not os.path.exists(template):
        with open(template, "w", encoding="utf-8") as f:
            f.write(CONSUMER_SPEC_TEMPLATE.strip() + "\n")
        created.append(template)
    rules_dir = os.path.join(root, ".cursor", "rules")
    os.makedirs(rules_dir, exist_ok=True)
    rule_path = os.path.join(rules_dir, "apatch-executable-specs.mdc")
    if not os.path.exists(rule_path):
        with open(rule_path, "w", encoding="utf-8") as f:
            f.write(CURSOR_SPEC_RULE.strip() + "\n")
        created.append(rule_path)
    protocol_rule = os.path.join(rules_dir, "apatch-protocol.mdc")
    if not os.path.exists(protocol_rule):
        with open(protocol_rule, "w", encoding="utf-8") as f:
            f.write(CURSOR_PROTOCOL_RULE.strip() + "\n")
        created.append(protocol_rule)
    return created


def init_consumer(
    target_dir: str,
    *,
    profile: str = "default",
    with_ci: bool = False,
    with_arch_rules: bool = False,
    with_enforcement: bool = False,
    governed_mode: str = "auto_session",
    with_sandbox: bool = False,
    with_devcontainer: bool = False,
    with_mcp: bool = True,
    refresh_agents: bool = False,
) -> List[str]:
    """Write starter templates; returns paths created or updated."""
    root = os.path.abspath(target_dir)
    pack = get_profile(profile)
    _require_consumer_resources(
        pack, profile=profile, with_ci=with_ci, with_arch_rules=with_arch_rules,
        with_enforcement=with_enforcement, with_sandbox=with_sandbox,
        with_devcontainer=with_devcontainer,
    )
    os.makedirs(root, exist_ok=True)
    created: List[str] = []
    agents_md = os.path.join(root, "AGENTS.md")
    template = _agents_template_path()
    stack_section = pack.get("agents_section") or ""
    if template.is_file() and (refresh_agents or not os.path.exists(agents_md)):
        template_body = template.read_text(encoding="utf-8")
        existing = Path(agents_md).read_text(encoding="utf-8") if refresh_agents and os.path.exists(agents_md) else None
        body = compose_agents_md(
            template_body,
            stack_section=stack_section,
            existing_agents=existing,
        )
        with open(agents_md, "w", encoding="utf-8") as dst:
            dst.write(body if body.endswith("\n") else body + "\n")
        created.append(agents_md)
    elif stack_section:
        snippet_path = os.path.join(root, "manifests", f"AGENTS.{profile}.snippet.md")
        os.makedirs(os.path.dirname(snippet_path), exist_ok=True)
        if not os.path.exists(snippet_path):
            with open(snippet_path, "w", encoding="utf-8") as f:
                f.write(pack["agents_section"].strip() + "\n")
            created.append(snippet_path)
    manifests = os.path.join(root, "manifests")
    os.makedirs(manifests, exist_ok=True)
    manifest_name = pack.get("manifest_name", "apatch.example.json")
    example = os.path.join(manifests, manifest_name)
    legacy_example = os.path.join(manifests, "apatch.example.json")
    if not os.path.exists(example):
        with open(example, "w", encoding="utf-8") as f:
            f.write(manifest_content_for_profile(profile))
        created.append(example)
    if profile == "default" and not os.path.exists(legacy_example):
        with open(legacy_example, "w", encoding="utf-8") as f:
            f.write(CONSUMER_MANIFEST_EXAMPLE.strip() + "\n")
        created.append(legacy_example)
    readme = os.path.join(manifests, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(CONSUMER_README)
        created.append(readme)
    gitignore = os.path.join(root, ".gitignore")
    if _ensure_apatch_gitignore(gitignore, track_policy=with_sandbox or with_enforcement):
        created.append(gitignore)
    scripts = dict(APATCH_NPM_SCRIPTS)
    scripts.update(pack.get("npm_scripts") or {})
    pkg = os.path.join(root, "package.json")
    if os.path.isfile(pkg):
        if _merge_custom_npm_scripts(pkg, scripts):
            created.append(pkg)
    else:
        snippet = os.path.join(manifests, "package.scripts.snippet.json")
        if not os.path.exists(snippet):
            with open(snippet, "w", encoding="utf-8") as f:
                json.dump({"scripts": scripts}, f, indent=2)
                f.write("\n")
            created.append(snippet)
    profile_readme = os.path.join(manifests, f"PROFILE.{profile}.md")
    if not os.path.exists(profile_readme):
        src = _profile_doc_source(pack)
        if src is not None:
            body = src.read_text(encoding="utf-8")
        else:
            body = (
                f"# apatch profile: {pack.get('label', profile)}\n\n"
                f"Suggested verify: `{pack.get('verify', 'pytest')}`\n"
            )
        with open(profile_readme, "w", encoding="utf-8") as f:
            f.write(body if body.endswith("\n") else body + "\n")
        created.append(profile_readme)
    if with_ci:
        ci_src = _ci_template_path()
        gh_dir = os.path.join(root, ".github", "workflows")
        os.makedirs(gh_dir, exist_ok=True)
        ci_dst = os.path.join(gh_dir, "apatch.yml")
        if ci_src.is_file() and not os.path.exists(ci_dst):
            Path(ci_dst).write_text(ci_src.read_text(encoding="utf-8"), encoding="utf-8")
            created.append(ci_dst)
        ci_gate_src = _consumer_resource_path("scripts/ci/apatch-sandbox-gate.sh")
        ci_gate_dst = os.path.join(root, "scripts", "ci", "apatch-sandbox-gate.sh")
        if ci_gate_src.is_file() and not os.path.exists(ci_gate_dst):
            os.makedirs(os.path.dirname(ci_gate_dst), exist_ok=True)
            Path(ci_gate_dst).write_bytes(ci_gate_src.read_bytes())
            os.chmod(ci_gate_dst, 0o755)
            created.append(ci_gate_dst)
    if profile == "frontend":
        copied = _copy_docs_manifest(manifests, "semantic-verify.example.yaml", "semantic-verify.yaml")
        if copied:
            created.append(copied)
    if with_arch_rules:
        for src_name, dst_name in _ARCH_RULES_PACK:
            copied = _copy_docs_manifest(manifests, src_name, dst_name)
            if copied:
                created.append(copied)
    if with_enforcement:
        from apatch.enforcement import GOVERNED_MODE_OFF, write_enforcement_config

        gm = governed_mode if governed_mode else GOVERNED_MODE_OFF
        created.append(write_enforcement_config(root, governed_mode=gm))
        hooks_dir = os.path.join(root, "scripts", "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        hook_src = _consumer_resource_path("scripts/hooks/pre-commit-trustchain.sh")
        hook_dst = os.path.join(hooks_dir, "pre-commit-trustchain.sh")
        if hook_src.is_file() and not os.path.exists(hook_dst):
            Path(hook_dst).write_bytes(hook_src.read_bytes())
            os.chmod(hook_dst, 0o755)
            created.append(hook_dst)
        enforce_readme = os.path.join(manifests, "ENFORCEMENT.md")
        if not os.path.exists(enforce_readme):
            with open(enforce_readme, "w", encoding="utf-8") as f:
                f.write(
                    "# TrustChain enforcement\n\n"
                    "`.apatch/enforcement.json` is active. All code changes must go through "
                    "`apatch_apply_session` (MCP) so TrustChain records Ed25519 proofs.\n\n"
                    "Install git hook:\n\n"
                    "```bash\n"
                    "git config core.hooksPath scripts/hooks\n"
                    "# or: ln -sf ../../scripts/hooks/pre-commit-trustchain.sh .git/hooks/pre-commit\n"
                    "```\n\n"
                    "Verify before commit: `apatch verify notarization --staged`\n\n"
                    "Check mode: `apatch doctor --json` → `trustchain.mode` should be `enforce`.\n"
                )
            created.append(enforce_readme)
    if with_sandbox:
        from apatch.sandbox import (
            DEFAULT_ALLOW_GLOBS,
            DEFAULT_PROTECTED_GLOBS,
            write_sandbox_config,
        )

        protected_globs = list(DEFAULT_PROTECTED_GLOBS)
        for pattern in pack.get("sandbox_protected_globs") or []:
            if pattern not in protected_globs:
                protected_globs.append(pattern)
        forbidden_allow = set(pack.get("sandbox_forbidden_allow_globs") or [])
        allow_globs = [
            pattern for pattern in DEFAULT_ALLOW_GLOBS if pattern not in forbidden_allow
        ]
        created.append(
            write_sandbox_config(
                root,
                protected_globs=protected_globs,
                allow_globs=allow_globs,
            )
        )
        cursor_dir = os.path.join(root, ".cursor", "hooks")
        os.makedirs(cursor_dir, exist_ok=True)
        hooks_src = _consumer_resource_path("scripts/cursor-hooks")
        for name in (
            "apatch-deny-direct-edit.sh",
            "apatch-deny-shell-mutate.sh",
            "apatch-deny-mcp-mutate.sh",
        ):
            src = hooks_src.joinpath(name)
            dst = os.path.join(cursor_dir, name)
            if src.is_file() and not os.path.exists(dst):
                Path(dst).write_bytes(src.read_bytes())
                os.chmod(dst, 0o755)
                created.append(dst)
        hooks_json_dst = os.path.join(root, ".cursor", "hooks.json")
        hooks_json_src = hooks_src.joinpath("hooks.json")
        if hooks_json_src.is_file() and not os.path.exists(hooks_json_dst):
            Path(hooks_json_dst).write_bytes(hooks_json_src.read_bytes())
            created.append(hooks_json_dst)
        sandbox_readme = os.path.join(manifests, "SANDBOX.md")
        if not os.path.exists(sandbox_readme):
            with open(sandbox_readme, "w", encoding="utf-8") as f:
                f.write(
                    "# Write sandbox (v1)\n\n"
                    "Protected: `src/**`, `app/**`, `services/**`, `packages/**`, "
                    "`e2e/**`, `server/**`, `server.js`, `scripts/**`, `docs/specs/**`, "
                    "`playwright.config.ts`, `vitest.config.ts`.\n\n"
                    "Allow: `manifests/**`, `tests/**`, `docs/*.md` (narrative, not specs), "
                    "root `*.md`.\n\n"
                    "Direct IDE edits and shell mutations are blocked in enforce mode.\n"
                    "Use `apatch_apply_session` / `apatch_execute_next` (MCP) for code and SPEC changes.\n\n"
                    "Default `watcher: revert` — unleased protected writes are auto-reverted.\n\n"
                    "Visual inspection: `cursor-ide-browser` MCP is allowed by default "
                    "(navigate/snapshot — read-only).\n\n"
                    "Status: `apatch sandbox status --json`\n\n"
                    "Local audit: `apatch sandbox ci-gate` (requires this workspace's lease/ledger).\n\n"
                    "Fresh-checkout CI: `.github/workflows/apatch.yml` validates the committed policy "
                    "and any tracked portable inclusion manifest; it cannot infer a local lease.\n"
                )
            created.append(sandbox_readme)
    if with_devcontainer:
        dc_src = _devcontainer_template_path()
        dc_dir = os.path.join(root, ".devcontainer")
        os.makedirs(dc_dir, exist_ok=True)
        dc_dst = os.path.join(dc_dir, "devcontainer.json")
        if dc_src.is_file() and not os.path.exists(dc_dst):
            Path(dc_dst).write_bytes(dc_src.read_bytes())
            created.append(dc_dst)
    if with_mcp:
        from apatch.mcp_health import sync_mcp_configs

        try:
            for written in sync_mcp_configs(root, overwrite=False, auto_ide=True):
                if written not in created:
                    created.append(written)
        except FileExistsError:
            pass
        for p in _scaffold_executable_specs(root):
            if p not in created:
                created.append(p)
    copied_playbook = _copy_design_partner_playbook(root)
    if copied_playbook and copied_playbook not in created:
        created.append(copied_playbook)
    return created


def _merge_custom_npm_scripts(package_json_path: str, scripts_map: Dict[str, str]) -> bool:
    try:
        with open(package_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    scripts = data.setdefault("scripts", {})
    changed = False
    for key, cmd in scripts_map.items():
        if key not in scripts:
            scripts[key] = cmd
            changed = True
    if changed:
        with open(package_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return changed
