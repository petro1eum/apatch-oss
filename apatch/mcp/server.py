"""stdio MCP server — thin wrappers over apatch.workflows (SPEC-COVERAGE-1 R3)."""

from __future__ import annotations

import functools
import inspect
import os
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from pydantic import Field

from apatch.mcp.sanitize import compact_apply_result, sanitize_tool_result

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    FastMCP = None  # type: ignore
    _MCP_IMPORT_ERROR = e
else:
    _MCP_IMPORT_ERROR = None

mcp = FastMCP("apatch") if FastMCP else None


def _ensure_mcp():
    if mcp is None:
        raise RuntimeError(
            "MCP SDK not installed. Run: pip install 'apatch[mcp]' or pip install mcp"
        ) from _MCP_IMPORT_ERROR


def _abs_in_workspace(target_dir: str, path: str) -> str:
    base = os.path.abspath(os.path.expanduser(target_dir))
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))


def _resolve_strip_file(
    *,
    file_path: Optional[str] = None,
    file: Optional[str] = None,
    target_dir: str = ".",
) -> str:
    resolved = file_path or file
    if not resolved:
        raise ValueError("file_path (or alias file) is required")
    return _abs_in_workspace(target_dir, resolved)


def _resolve_strip_manifest(
    *,
    manifest_path: Optional[str] = None,
    manifest: Optional[str] = None,
    target_dir: str = ".",
) -> str:
    resolved = manifest_path or manifest
    if not resolved:
        raise ValueError("manifest_path (or alias manifest) is required")
    return _abs_in_workspace(target_dir, resolved)


def _mcp_phase_run(
    target_dir: str,
    *,
    file_path: Optional[str] = None,
    file: Optional[str] = None,
    manifest_path: str = "",
    manifest: Optional[str] = None,
    profile: str = "frontend",
    out_dir: str = "extracted",
    native_out_dir: Optional[str] = None,
    module_out_dir: Optional[str] = None,
    to_module: Optional[str] = None,
    verify: Optional[str] = None,
    strict: bool = False,
    emit_wiring: Optional[str] = None,
    no_trustchain: bool = False,
    auto_wire: bool = False,
    emit_barrel: Optional[str] = None,
    finish_tool: str = "apatch_phase_run",
) -> Dict[str, Any]:
    from apatch.runtime.runtime import MutationRuntime

    resolved_file = file_path or file
    abs_file = _abs_in_workspace(target_dir, resolved_file) if resolved_file else None
    resolved_manifest = manifest_path or manifest or ""
    abs_manifest = resolved_manifest
    if resolved_manifest and not os.path.isabs(resolved_manifest):
        abs_manifest = _abs_in_workspace(target_dir, resolved_manifest)
    abs_out = out_dir if os.path.isabs(out_dir) else _abs_in_workspace(target_dir, out_dir)
    abs_native = None
    if native_out_dir:
        abs_native = (
            native_out_dir
            if os.path.isabs(native_out_dir)
            else _abs_in_workspace(target_dir, native_out_dir)
        )
    abs_module = None
    if module_out_dir:
        abs_module = (
            module_out_dir
            if os.path.isabs(module_out_dir)
            else _abs_in_workspace(target_dir, module_out_dir)
        )
    abs_wiring = None
    if emit_wiring:
        abs_wiring = (
            emit_wiring
            if os.path.isabs(emit_wiring)
            else _abs_in_workspace(target_dir, emit_wiring)
        )
    abs_barrel = None
    if emit_barrel:
        abs_barrel = (
            emit_barrel
            if os.path.isabs(emit_barrel)
            else _abs_in_workspace(target_dir, emit_barrel)
        )
    try:
        return MutationRuntime(target_dir).phase_run(
            abs_file,
            abs_manifest,
            profile=profile,
            out_dir=abs_out,
            native_out_dir=abs_native,
            module_out_dir=abs_module,
            to_module=to_module,
            verify_cmd=verify,
            strict=strict,
            emit_wiring=abs_wiring,
            no_trustchain=no_trustchain,
            auto_wire=auto_wire,
            emit_barrel=abs_barrel,
            finish_tool=finish_tool,
        )
    except Exception as e:
        from apatch.sandbox import SandboxError

        if isinstance(e, SandboxError):
            return {"ok": False, "error": str(e), "error_type": e.error_type}
        raise


def _wrap_mcp_tools() -> None:
    """Sanitize every tool return so stdio JSON-RPC never sees invalid UTF-8."""
    if mcp is None:
        return
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is None:
        return
    tools = getattr(tool_manager, "_tools", None)
    if not tools:
        return
    for tool in tools.values():
        orig = tool.fn
        try:
            _sig = inspect.signature(orig)
            _accepts_td = "target_dir" in _sig.parameters or any(
                p.kind == p.VAR_KEYWORD for p in _sig.parameters.values()
            )
        except (TypeError, ValueError):
            _accepts_td = True

        @functools.wraps(orig)
        def wrapper(
            *args: Any,
            __orig: Callable[..., Any] = orig,
            __tname: str = tool.name,
            __accepts_td: bool = _accepts_td,
            **kwargs: Any,
        ) -> Any:
            try:
                from apatch.mcp.bound_workspace import resolve_target_dir_kwargs
                from apatch.mcp.workspace_registry import WorkspaceRegistryError

                # Resolve a roaming alias before lane/lifecycle side effects select a workspace.
                resolve_target_dir_kwargs(kwargs)
            except WorkspaceRegistryError as exc:
                from apatch.mcp.bound_workspace import bound_mcp_workspace
                from apatch.session_state import enrich_tool_response

                fallback = bound_mcp_workspace() or os.path.abspath(".")
                return sanitize_tool_result(
                    enrich_tool_response(
                        __tname,
                        exc.to_result(),
                        target_dir=fallback,
                    )
                )
            try:
                from apatch.lane_context import bind_lane_from_kwargs
                from apatch.mcp.lifecycle import touch_workspace

                bind_lane_from_kwargs(kwargs)
                touch_workspace(str(kwargs.get("target_dir", ".")))
            except Exception:
                pass
            # only forward target_dir to tools whose signature accepts it;
            # the workspace side-effects above already used it
            call_kwargs = kwargs
            if not __accepts_td and "target_dir" in kwargs:
                call_kwargs = {k: v for k, v in kwargs.items() if k != "target_dir"}
            raw = sanitize_tool_result(__orig(*args, **call_kwargs))
            if isinstance(raw, dict):
                if "state_update" in raw:
                    return raw
                from apatch.session_state import enrich_tool_response

                return enrich_tool_response(
                    __tname,
                    raw,
                    target_dir=str(kwargs.get("target_dir", ".")),
                )
            return raw

        tool.fn = wrapper


PARAM_DOCS: Dict[str, str] = {
    "by": "Group-by dimensions (comma-combinable): identity,project,spec,day.",
    "until": "Upper time bound (ISO-8601 or epoch seconds).",
    "agent": "Filter to one identity key_id.",
    "project": "Filter to one project id.",
    "idle_gap": "Work-block split: op gaps longer than N minutes are breaks (default 30).",
    "ramp_up": "Per-block warm-up minutes before the first op (default 15).",
    "do_verify": "Re-derive each event from the ledger and flag tamper/drift.",
    "include_audit": "Include non-attested (legacy/audit trust-level) events.",
    "covered_by": "Comma-separated Rk ids that satisfy this requirement (noop-attest).",
    # Workspace / targeting
    "target_dir": "Workspace root: '.' uses the MCP-bound repo; '@alias' uses a human-registered local roaming workspace.",
    "alias": "Human-authorized local workspace alias, without the leading @.",
    "include_contract": "Include the full AGENTS.md contract in this read-only response.",
    "extension_id": "Exact reverse-DNS extension id from the explicit lock.",
    "tool_id": "Exact logical extension tool id '<extension-id>/<tool-id>'.",
    "arguments": "Any JSON value validated against the extension tool input schema.",
    "lock_path": "Explicit extension lock path relative to target_dir.",
    "include_disabled": "Include disabled lock entries in list output.",
    "file_path": "Path to the source file, relative to target_dir or absolute.",
    "file": "Alias for file_path.",
    "target_file": "Path to the file being patched, relative to target_dir.",
    "directory": "Directory to scan, relative to target_dir or absolute.",
    "target": "Symbol or path the operation applies to.",
    "extra_paths": "Additional file/dir paths to include in the operation.",
    "include_cwd": "Include the current working directory in the scan.",
    # Patches / logs
    "logs_path": "Path to a JSONL patch log (one find/replace candidate per line).",
    "old_content": "Exact text to find in target_file.",
    "new_content": "Replacement text for old_content.",
    "tool": "Filter candidates by originating tool name.",
    "keyword": "Filter candidates whose content contains this keyword.",
    "steps": "Step selector, e.g. '1,3,5' or '1-4'.",
    "range_str": "Line/candidate range filter, e.g. '10:40'.",
    "show_diff": "Include a unified diff in the response.",
    "workers": "Parallel worker count (0 = auto).",
    "replace_all": "Replace every match of find_text, not only the first.",
    "min_confidence": "Skip candidates below this match-confidence threshold (0.0-1.0).",
    "only_drifted": "Apply only candidates whose anchor text has drifted.",
    "report_path": "Write a JSON apply report to this path.",
    "budget": "Apply budget guard, e.g. max files/insertions ('files=5').",
    "max_files": "Abort if the change touches more than this many files.",
    "max_insertions": "Abort if more than this many lines would be inserted.",
    "max_deletions": "Abort if more than this many lines would be deleted.",
    # Generate (find/replace authoring)
    "find_text": "Literal text to find when generating a patch log.",
    "replace_text": "Replacement text.",
    "find_pattern": "Regex pattern to find (when match_mode='regex').",
    "glob_pattern": "Glob selecting files to scan, e.g. 'apatch/**/*.py'.",
    "match_mode": "Match strategy: literal | whitespace | regex | json.",
    "out_path": "Output path to write the result (e.g. patch log or audit bundle).",
    "append": "Append new patch steps to existing JSONL (continue step_index).",
    "needles": (
        "Ordered mutations for apatch_generate_batch: "
        "[{action: replace|create|delete|rename|chmod|shift_outline|insert_before|insert_section, ...}, …]. "
        "Legacy omit action for find/replace. create: {target_file, content}. "
        "delete: {target_file}. rename: {source_file, target_file}. "
        "chmod: {target_file, mode: '755'} or {target_file, executable: true}. "
        "Markdown outline: shift_outline {target_file, after, levels, delta}; "
        "insert_section {target_file, before, content, shift_following?} — not N× replace on headings. "
        "Passthrough: {tool_calls: [...]}."
    ),
    "after": (
        "shift_outline anchor — substring matching a heading line (e.g. '## 3.'); "
        "renumber from this line onward (inclusive)."
    ),
    "before": (
        "insert_before / insert_section anchor — substring in heading line; "
        "new content inserted immediately before that line."
    ),
    "levels": (
        "ATX header levels to renumber (1=# … 6=######). Default [2,3] for ## and ###."
    ),
    "delta": "Integer added to the first outline segment (3→4, 3.1→4.1). Default 1.",
    "shift_following": (
        "insert_section only: {levels: [2,3], delta: 1} shifts numbered headings at/after "
        "before anchor before inserting new section."
    ),
    "include_anchor": "shift_outline: include anchor line in shift (default true).",
    "needles_path": (
        "Path to a JSON file with the needles array — use instead of inline "
        "needles for large/multi-line payloads (no JSON escaping pain)."
    ),
    # Apply session / chunking
    "chunk_max_files": "Max files per apply chunk (default 5).",
    "verify_deferred": "Defer verify until the whole session completes.",
    "reset": "Reset/discard the in-progress apply session.",
    "abort": "Abort the current apply session and roll back.",
    "session_path": "Path to the apply-session state file.",
    "session_id": "Session/checkpoint id (used for rollback and replay).",
    "governed_session_id": "Exact governed session id returned by apatch_session_start.",
    "session_token": "One-time governed session secret returned only by apatch_session_start.",
    "request_id": "Client-generated stable id (16..256 chars); reuse the same value after a timeout to replay safely.",
    "requirement_ids": "Optional exact Rk ids to include; accepts bare R6 or SPEC-X#R6.",
    "exclude_requirement_ids": "Optional exact Rk ids to leave stale even when included by the broader filter.",
    # Verify / trustchain / attestation
    "verify": (
        "Verify command: shell string ('pytest tests/') or argv list "
        "(['pytest', '-k', 'not slow'] — no shell quoting pitfalls)."
    ),
    "baseline": (
        "Baseline-aware verify: 'capture' snapshots failing tests BEFORE apply to "
        ".apatch/verify_baseline.json; 'compare' passes when no NEW failures vs "
        "the snapshot (pre-existing red does not block). Default 'off'."
    ),
    "allowed_failures": (
        "Failing test node ids (or substrings) that never block verify — manual "
        "allow-list for known/flaky failures."
    ),
    "async_mode": (
        "Start verify as a background job (AR-2); MCP returns in <30s with "
        "verify_job_id — poll apatch_verify_status(job_id=…). Forced when prior "
        "runs exceeded APATCH_VERIFY_SYNC_MAX_SEC even if async_mode=false."
    ),
    "job_id": (
        "Async verify job id from apatch_verify_run (vjob_…); when set, "
        "apatch_verify_status polls job completion instead of session verify state."
    ),
    "no_trustchain": "Skip TrustChain notarization (forbidden under enforce).",
    "semantic": "Run semantic verification (routes/exports/OpenAPI).",
    "notarization": "Verify staged changes against the TrustChain ledger.",
    "pipeline_manifest": "Pipeline manifest path for verify-by-pipeline.",
    "staged": "Operate on git-staged changes only.",
    "working_tree": "Operate on the working tree (unstaged) changes.",
    "event_limit": "Max number of domain events to include.",
    "limit": "Max number of items to return.",
    "message": "Human-readable intent/commit/attestation message.",
    "lane": "Explicit isolated session lane slug; safe when other lanes are active.",
    "intent": "Why this mutation is happening (required to open a session).",
    "artifacts": (
        "Session anchors (RFP-006): ['spec:SPEC-42@sha256:…', 'adr:ADR-001'] or dicts "
        "{kind,id,ref?,content_hash?}. Auto-stamped on TrustChain commits. "
        "After attest: apatch_trustchain_coverage(artifact='kind:id')."
    ),
    "sdd_contract": "Exact owner-frozen SDD verification contract required by a strict workspace profile.",
    "task_envelope": "Exact approved task envelope bound to the frozen SDD contract.",
    "actor_id": "Implementation actor identity; MCP fixes the capability role to implementation.",
    "artifact": (
        "Filter history/coverage by kind:id (spec:SPEC-42). "
        "coverage.complete=true means mutation+attestation both linked."
    ),
    "op_id": "Reverse lookup: apatch_trustchain_coverage(op_id='…') → artifacts[], role.",
    # Executable specifications (RFP-007)
    "spec": "Spec id (e.g. 'SPEC-42'); auto-discovered at docs/specs/<id>.md or remembered path.",
    "spec_path": "Path to the spec markdown file (relative to target_dir or absolute).",
    "rfp": "RFP id (e.g. 'RFP-023'); resolved under docs/RFP-NNN-*.md.",
    "rfp_path": "Path to RFP markdown (relative to target_dir or absolute).",
    "requirement": (
        "Requirement ref to anchor the session to: 'SPEC-42#R3' (or bare 'R3' with "
        "spec_path). Resolves intent + spec:SPEC-42#R3@<hash> from the spec file."
    ),
    # Strip / phase / decomposition
    "manifest_path": "Path to a strip/phase manifest (JSON).",
    "manifest": "Alias for manifest_path.",
    "profile": "Strip profile: frontend | cpp | ... selects grammar and codegen.",
    "out_dir": "Directory for stripped/extracted artifacts.",
    "module_out_dir": "Output directory for generated modules/hooks.",
    "native_out_dir": "Output directory for generated native (C++) code.",
    "to_module": "Convert the stripped block into a module of this kind (e.g. 'hook').",
    "to_native": "Convert the stripped block into native code of this kind.",
    "start_marker": "First line (inclusive) of the block to strip.",
    "until_marker": "Boundary line: strip up to (not including) this line.",
    "export_ext": "File extension for the extracted export.",
    "export_filename": "Filename for the extracted export.",
    "post_hook": "Shell command to run after codegen (e.g. a formatter).",
    "strict": "Fail on any boundary/overlap warning.",
    "strict_overlap": "Fail if stripped ranges overlap.",
    "strict_dangling": "Fail if dangling references remain after strip.",
    "emit_wiring": "Path to emit import/wiring glue for the extracted code.",
    "auto_wire": "Automatically rewrite imports/usages to the extracted module.",
    "emit_barrel": "Path to emit a barrel (index) file re-exporting extracts.",
    "preview": "Return a preview without writing changes.",
    "count_candidates": "Return only the count of candidates.",
    # Orchestration / graph / index
    "graph_path": "Path to a dependency-graph manifest.",
    "rules_path": "Path to arch-rules definition.",
    "skip_simulate": "Skip the preflight simulate step.",
    "query": "Query string for the project index.",
    "rebuild_index": "Rebuild the project index before querying.",
    "include_tests": "Include test files in the analysis.",
    "scan_all": "Scan the whole project rather than changed files only.",
    "kind": "Item kind to filter by.",
    "depth": "Traversal depth for the impact/dependency graph.",
    # DB
    "since": "Git ref/commit to diff from (e.g. 'HEAD~1').",
    # Trust anchor / enrollment (RFP-005 §5.3)
    "root_ca": "Pinned TrustChain root CA: PEM file path or literal (env APATCH_ROOT_CA).",
    "intermediate": "Intermediate CA PEM path/literal (env APATCH_INTERMEDIATE_CA).",
    "leaf_cert": "Enrolled agent leaf certificate PEM path/literal (env APATCH_AGENT_CERT).",
    "signer_key": "Enrolled signing key PEM whose public key must match the leaf (env APATCH_AGENT_KEY).",
    "registry_base": "TrustChain pub registry URL to fetch root/ca/agent cert (env APATCH_PLATFORM_URL).",
    "crl": "Certificate revocation list PEM path/literal (optional, PEM mode).",
    "invitation": "Enrollment invitation token (mint via /api/enroll/invite).",
    "platform_url": "TrustChain enroll base URL (e.g. https://trust-chain.ai).",
    "agent_id": "Enrolled agent common name (CN), e.g. 'apatch-ci' (env APATCH_AGENT_ID).",
    # init-consumer scaffolding
    "with_ci": "Scaffold CI gate workflow.",
    "with_arch_rules": "Scaffold arch-rules config.",
    "with_enforcement": "Scaffold TrustChain enforcement config.",
    "with_sandbox": "Scaffold the write sandbox + Cursor hooks.",
    "with_devcontainer": "Scaffold a devcontainer.",
    "with_mcp": "Scaffold mcp.json for the consumer.",
    "refresh_agents": "Refresh AGENTS.md from the bundled template.",
    "governed_mode": "Session gate: off | auto_session | strict.",
    "sweep_leases": "Remove stale sandbox write_lease.json in target_dir (MCP hygiene).",
    # sandbox
    "auto_revert": "Automatically revert unauthorized writes found in audit.",
    "base": "Merge-base ref (e.g. origin/main); gate the PR diff vs this ref (Ring-2 authority over shared history).",
    "mode": "Operation mode selector.",
    "dry_run": "Plan only; do not write changes.",
    "dry_run_first": "Run a dry-run pass before applying.",
    "finalize": "Run verify → attest → session_end after mutations (skip mutate phase).",
    "skip_lint": "Skip apatch_spec_lint gate before execute_next (not recommended).",
    "check_dependencies": "Gate on upstream SPEC dependencies declared in dependency lines.",
    "specs": "Comma-separated spec ids for multi-spec aggregate (RFP-023), e.g. 'SPEC-A,SPEC-B'.",
}


def _forbid_unknown_arguments() -> None:
    """Refuse an argument no tool declares instead of silently dropping it.

    FastMCP builds one pydantic model per tool and pydantic ignores extra keys by
    default, so a misspelled or unsupported argument used to reach the tool as
    absence: the call returned ``ok`` and the caller believed it had been honoured.
    In a system whose output is proof that is the worst possible failure mode --
    a caller can report a verification it never ordered. No apatch tool accepts
    arbitrary keywords, so every tool fails closed on an undeclared argument and
    the published schema says so.
    """
    if mcp is None:
        return
    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None) if tool_manager else None
    if not tools:
        return
    for tool in tools.values():
        schema = getattr(tool, "parameters", None)
        if isinstance(schema, dict):
            schema["additionalProperties"] = False
        model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
        config = getattr(model, "model_config", None)
        if model is None or config is None:
            continue
        try:
            config["extra"] = "forbid"
            model.model_rebuild(force=True)
        except Exception:  # pragma: no cover - a model we cannot tighten stays permissive
            continue


def _apply_param_docs() -> None:
    """Fill in JSON-schema parameter descriptions from PARAM_DOCS (centralized)."""
    if mcp is None:
        return
    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None) if tool_manager else None
    if not tools:
        return
    for tool in tools.values():
        schema = getattr(tool, "parameters", None)
        if not isinstance(schema, dict):
            continue
        props = schema.get("properties")
        if not isinstance(props, dict):
            continue
        for name, spec in props.items():
            if isinstance(spec, dict) and not spec.get("description"):
                doc = PARAM_DOCS.get(name)
                if doc:
                    spec["description"] = doc


if mcp is not None:

    @mcp.tool()
    def apatch_plan(
        target_file: str,
        old_content: str,
        new_content: str,
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Dry-run a single patch alignment (strategy, confidence, warnings)."""
        from apatch.matcher import ASTMatcher
        from apatch.resolver import resolve_smart_path

        action_type = "CREATE" if not (old_content or "").strip() else "REPLACE"
        resolved = resolve_smart_path(target_dir, target_file, action_type)
        matcher = ASTMatcher(resolved)
        result = matcher.evaluate(old_content, new_content, action_type)
        return {
            "resolved_path": resolved,
            "success": result.success,
            "strategy": result.strategy,
            "confidence": result.confidence,
            "warnings": result.warnings,
        }

    @mcp.tool()
    def apatch_plan_batch(
        logs_path: str,
        target_dir: str = ".",
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        steps: Optional[str] = None,
        range_str: Optional[str] = None,
        show_diff: bool = False,
        workers: int = 0,
    ) -> Dict[str, Any]:
        """Dry-run all patches in a JSONL log (mirrors apatch plan --json)."""
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(target_dir).plan(
            logs_path,
            tool=tool,
            keyword=keyword,
            steps=steps,
            range_str=range_str,
            show_diff=show_diff,
            workers=workers,
        )

    @mcp.tool()
    def apatch_apply(
        logs_path: str,
        target_dir: str = ".",
        replace_all: bool = False,
        min_confidence: Optional[float] = None,
        only_drifted: bool = False,
        verify: Optional[str] = None,
        verify_deferred: bool = False,
        no_trustchain: bool = False,
        dry_run: bool = False,
        dry_run_first: bool = False,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        steps: Optional[str] = None,
        range_str: Optional[str] = None,
        report_path: Optional[str] = None,
        budget: Optional[str] = None,
        max_files: Optional[int] = None,
        max_insertions: Optional[int] = None,
        max_deletions: Optional[int] = None,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Apply patches from JSONL with filters, verify, TrustChain, and rollback.

        Use steps/range_str/min_confidence for selective apply. Set dry_run_first=True
        to run a plan-style dry-run before applying.
        """
        from apatch.apply_session import MASS_APPLY_GUARD, should_require_session
        from apatch.enforcement import assert_can_apply
        from apatch.ingestor import LogIngestor
        from apatch.workflows import WorkflowError, filter_patch_candidates, plan_from_logs

        block = assert_can_apply(target_dir, no_trustchain=no_trustchain)
        if block:
            return {
                "ok": False,
                "rejected": True,
                "error": block,
                "rejection_prompt": block,
                "agent_prompt": block,
                "applied": 0,
                "failed": 0,
                "skipped": 0,
            }

        from apatch.change_budget import resolve_budget

        change_budget = resolve_budget(
            budget,
            max_files=max_files,
            max_insertions=max_insertions,
            max_deletions=max_deletions,
        )

        if not dry_run:
            try:
                candidate_count = len(
                    filter_patch_candidates(
                        LogIngestor(logs_path).parse(),
                        tool=tool,
                        keyword=keyword,
                        steps=steps,
                        range_str=range_str,
                    )
                )
            except (OSError, ValueError, WorkflowError):
                candidate_count = 0
            if should_require_session(
                candidate_count,
                steps=steps,
                range_str=range_str,
                budget=change_budget,
                dry_run=dry_run,
            ):
                return {
                    "ok": False,
                    "error": (
                        f"Refusing mass apply of {candidate_count} candidates "
                        f"(>{MASS_APPLY_GUARD} without budget/steps). "
                        "Use apatch_apply_session for chunked apply with checkpoints."
                    ),
                    "use_tool": "apatch_apply_session",
                    "candidate_count": candidate_count,
                    "agent_next": (
                        "apatch_apply_session(logs_path=..., verify=...) — "
                        "repeat until continue=false; save checkpoint from each response"
                    ),
                }

        if dry_run_first and not dry_run:
            preview = plan_from_logs(
                logs_path,
                target_dir,
                tool=tool,
                keyword=keyword,
                steps=steps,
                range_str=range_str,
            )
            if preview.get("would_apply", 0) == 0 and preview.get("total", 0) > 0:
                return {
                    "ok": False,
                    "error": "dry_run_first: no candidates would apply",
                    "preview": preview,
                    "applied": 0,
                    "failed": 0,
                    "skipped": 0,
                }

        from apatch.runtime.runtime import MutationRuntime

        result = MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
            session_token=session_token or None,
            enforce_binding=True,
        ).apply(
            logs_path,
            tool=tool,
            keyword=keyword,
            steps=steps,
            range_str=range_str,
            replace_all=replace_all,
            min_confidence=min_confidence,
            only_drifted=only_drifted,
            verify=verify,
            verify_deferred=verify_deferred,
            no_trustchain=no_trustchain,
            dry_run=dry_run,
            report_path=report_path,
            change_budget=change_budget,
            quiet=True,
        )
        if not result.get("ok") and result.get("error_type") == "RUNTIME_TRANSITION":
            return result
        if not result.get("ok") and result.get("error") and "applied" not in result:
            return {**result, "applied": 0, "failed": 0, "skipped": 0}
        return compact_apply_result(result)

    @mcp.tool()
    def apatch_apply_session(
        logs_path: str,
        target_dir: str = ".",
        verify: Optional[str] = None,
        session_path: Optional[str] = None,
        chunk_max_files: int = 5,
        replace_all: bool = False,
        only_drifted: bool = False,
        min_confidence: Optional[float] = None,
        verify_deferred: bool = True,
        no_trustchain: bool = False,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        reset: bool = False,
        abort: bool = False,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Chunked mass apply with TrustChain checkpoint after every chunk.

        Call repeatedly until ``continue`` is false. State: ``.apatch/apply_session.json``.
        A **completed** session is auto-discarded when ``logs_path`` changes (no ``reset=true``).
        For an **in-progress** session with a different ``logs_path``, pass ``reset=true`` or ``abort=true``.
        On failure: ``apatch_rollback(session_id=<checkpoint>)`` or ``abort=true``.
        Prefer this over ``apatch_apply`` for large refactors (>15 candidates).
        """
        from apatch.runtime.runtime import MutationRuntime

        try:
            result = MutationRuntime(
                target_dir,
                session_id=governed_session_id or None,
                session_token=session_token or None,
                enforce_binding=True,
            ).apply_session(
                logs_path,
                session_path=session_path,
                verify=verify,
                chunk_max_files=chunk_max_files,
                replace_all=replace_all,
                only_drifted=only_drifted,
                min_confidence=min_confidence,
                verify_deferred=verify_deferred,
                no_trustchain=no_trustchain,
                tool=tool,
                keyword=keyword,
                reset=reset,
                abort=abort,
                quiet=True,
            )
            if not result.get("ok") and result.get("error_type") == "RUNTIME_TRANSITION":
                return {**result, "continue": False}
            return result
        except Exception as e:
            from apatch.sandbox import SandboxError

            if isinstance(e, SandboxError):
                return {
                    "ok": False,
                    "error": str(e),
                    "error_type": e.error_type,
                    "continue": False,
                }
            raise

    @mcp.tool()
    def apatch_scan(
        extra_paths: Optional[List[str]] = None,
        limit: int = 20,
        include_cwd: bool = True,
        count_candidates: bool = True,
    ) -> Dict[str, Any]:
        """Discover agent transcript JSONL files (mirrors apatch scan --json)."""
        from apatch.workflows import scan_transcripts

        rows = scan_transcripts(
            extra_paths=extra_paths,
            limit=limit,
            include_cwd=include_cwd,
            count_candidates=count_candidates,
        )
        return {"count": len(rows), "transcripts": rows}

    @mcp.tool()
    def apatch_view(
        logs_path: str,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        steps: Optional[str] = None,
        range_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List patch candidates in a JSONL log (mirrors apatch view --json)."""
        from apatch.workflows import WorkflowError, view_log_candidates

        try:
            rows = view_log_candidates(
                logs_path,
                tool=tool,
                keyword=keyword,
                steps=steps,
                range_str=range_str,
            )
            return {"count": len(rows), "candidates": rows}
        except WorkflowError as e:
            return {"ok": False, "error": str(e), "candidates": []}

    @mcp.tool()
    def apatch_strip_dry_run(
        file_path: Optional[str] = None,
        manifest_path: Optional[str] = None,
        file: Optional[str] = None,
        manifest: Optional[str] = None,
        out_dir: str = "extracted",
        target_dir: str = ".",
        strict_overlap: bool = True,
    ) -> Dict[str, Any]:
        """Preview strip manifest without writing files."""
        from apatch.workflows import run_strip

        abs_file = _resolve_strip_file(file_path=file_path, file=file, target_dir=target_dir)
        abs_manifest = _resolve_strip_manifest(
            manifest_path=manifest_path,
            manifest=manifest,
            target_dir=target_dir,
        )
        abs_out = _abs_in_workspace(target_dir, out_dir)
        return run_strip(
            abs_file,
            manifest_path=abs_manifest,
            dry_run=True,
            out_dir=abs_out,
            strict_overlap=strict_overlap,
        )

    @mcp.tool()
    def apatch_strip(
        file_path: Optional[str] = None,
        file: Optional[str] = None,
        target_dir: str = ".",
        manifest_path: Optional[str] = None,
        manifest: Optional[str] = None,
        start_marker: Optional[str] = None,
        until_marker: Optional[str] = None,
        replace_text: str = "",
        out_dir: Optional[str] = None,
        export_ext: Optional[str] = None,
        export_filename: Optional[str] = None,
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
        verify: Optional[Union[str, List[str]]] = None,
        auto_wire: bool = False,
        emit_barrel: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply strip pipeline with full CLI flags (TrustChain, verify, rollback)."""
        from apatch.runtime.runtime import MutationRuntime

        abs_file = _resolve_strip_file(file_path=file_path, file=file, target_dir=target_dir)
        resolved_manifest = manifest_path or manifest
        abs_manifest = None
        if resolved_manifest:
            abs_manifest = (
                resolved_manifest
                if os.path.isabs(resolved_manifest)
                else _abs_in_workspace(target_dir, resolved_manifest)
            )
        abs_emit = emit_wiring
        if emit_wiring and not os.path.isabs(emit_wiring):
            abs_emit = _abs_in_workspace(target_dir, emit_wiring)
        resolved_out = out_dir or "extracted"
        abs_out = (
            resolved_out
            if os.path.isabs(resolved_out)
            else _abs_in_workspace(target_dir, resolved_out)
        )
        abs_module = module_out_dir
        if module_out_dir and not os.path.isabs(module_out_dir):
            abs_module = _abs_in_workspace(target_dir, module_out_dir)
        abs_native = native_out_dir
        if native_out_dir and not os.path.isabs(native_out_dir):
            abs_native = _abs_in_workspace(target_dir, native_out_dir)
        abs_barrel = None
        if emit_barrel:
            abs_barrel = (
                emit_barrel
                if os.path.isabs(emit_barrel)
                else _abs_in_workspace(target_dir, emit_barrel)
            )

        try:
            return MutationRuntime(target_dir).strip(
                abs_file,
                manifest_path=abs_manifest,
                start_marker=start_marker,
                until_marker=until_marker,
                replace_text=replace_text,
                dry_run=False,
                out_dir=abs_out,
                export_ext=export_ext,
                export_filename=export_filename,
                no_trustchain=no_trustchain,
                to_native=to_native,
                to_module=to_module,
                module_out_dir=abs_module,
                native_out_dir=abs_native,
                post_hook=post_hook,
                strict=strict,
                strict_overlap=strict_overlap,
                strict_dangling=strict_dangling,
                emit_wiring=abs_emit,
                verify_cmd=verify,
                auto_wire=auto_wire,
                emit_barrel=abs_barrel,
            )
        except Exception as e:
            from apatch.sandbox import SandboxError

            if isinstance(e, SandboxError):
                return {"ok": False, "error": str(e), "error_type": e.error_type}
            return {
                "ok": False,
                "errors": [str(e)],
                "error_type": type(e).__name__,
                "recoverable": True,
                "recommended_action": "rollback",
            }

    @mcp.tool()
    def apatch_phase_run(
        file_path: Optional[str] = None,
        file: Optional[str] = None,
        manifest_path: str = "",
        manifest: Optional[str] = None,
        profile: str = "frontend",
        out_dir: str = "extracted",
        native_out_dir: Optional[str] = None,
        module_out_dir: Optional[str] = None,
        to_module: Optional[str] = None,
        verify: Optional[str] = None,
        target_dir: str = ".",
        strict: bool = False,
        emit_wiring: Optional[str] = None,
        no_trustchain: bool = False,
        auto_wire: bool = False,
        emit_barrel: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run phase strip + native/module conversion + verify via MutationRuntime."""
        return _mcp_phase_run(
            target_dir,
            file_path=file_path,
            file=file,
            manifest_path=manifest_path,
            manifest=manifest,
            profile=profile,
            out_dir=out_dir,
            native_out_dir=native_out_dir,
            module_out_dir=module_out_dir,
            to_module=to_module,
            verify=verify,
            strict=strict,
            emit_wiring=emit_wiring,
            no_trustchain=no_trustchain,
            auto_wire=auto_wire,
            emit_barrel=emit_barrel,
            finish_tool="apatch_phase_run",
        )

    @mcp.tool()
    def apatch_governed_phase_run(
        file_path: Optional[str] = None,
        file: Optional[str] = None,
        manifest_path: str = "",
        manifest: Optional[str] = None,
        profile: str = "frontend",
        out_dir: str = "extracted",
        native_out_dir: Optional[str] = None,
        module_out_dir: Optional[str] = None,
        to_module: Optional[str] = None,
        verify: Optional[str] = None,
        target_dir: str = ".",
        strict: bool = False,
        emit_wiring: Optional[str] = None,
        no_trustchain: bool = False,
        auto_wire: bool = False,
        emit_barrel: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Alias for apatch_phase_run (same MutationRuntime path; finish_tool differs)."""
        return _mcp_phase_run(
            target_dir,
            file_path=file_path,
            file=file,
            manifest_path=manifest_path,
            manifest=manifest,
            profile=profile,
            out_dir=out_dir,
            native_out_dir=native_out_dir,
            module_out_dir=module_out_dir,
            to_module=to_module,
            verify=verify,
            strict=strict,
            emit_wiring=emit_wiring,
            no_trustchain=no_trustchain,
            auto_wire=auto_wire,
            emit_barrel=emit_barrel,
            finish_tool="apatch_governed_phase_run",
        )

    @mcp.tool()
    def apatch_rollback(
        target_dir: str = ".",
        session_id: Optional[str] = None,
        preview: bool = False,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Rollback TrustChain checkpoint and restore files from .apatch/backups/."""
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
            session_token=session_token or None,
            enforce_binding=True,
        ).rollback(session_id, preview=preview)

    @mcp.tool()
    def apatch_generate(
        find_text: str,
        replace_text: str,
        target_dir: str = ".",
        glob_pattern: str = "**/*",
        out_path: str = "patches.jsonl",
        replace_all: bool = True,
        match_mode: str = "literal",
        find_pattern: Optional[str] = None,
        append: bool = False,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Generate patch JSONL from find/replace."""
        from apatch.workflows import generate_patch_jsonl

        from apatch.runtime.errors import SessionBindingError
        from apatch.runtime.session_binding import capture_session_binding
        from apatch.session_state import enrich_tool_response

        try:
            binding = capture_session_binding(
                target_dir,
                expected_session_id=governed_session_id or None,
                session_token=session_token or None,
                require_capability=True,
                operation="generate",
            )
        except SessionBindingError as exc:
            return exc.to_dict()
        abs_out = out_path if os.path.isabs(out_path) else _abs_in_workspace(target_dir, out_path)
        result = generate_patch_jsonl(
            find_text=find_text,
            replace_text=replace_text,
            target_dir=target_dir,
            glob_pattern=glob_pattern,
            out_path=abs_out,
            replace_all=replace_all,
            match_mode=match_mode,
            find_pattern=find_pattern,
            append=append,
            governed_session_id=binding.session_id if binding else None,
        )
        if binding:
            return enrich_tool_response(
                "apatch_generate",
                result,
                target_dir=target_dir,
                expected_session_id=binding.session_id,
                expected_revision=binding.revision,
            )
        return result

    @mcp.tool()
    def apatch_generate_batch(
        needles: Optional[List[Dict[str, Any]]] = None,
        target_dir: str = ".",
        out_path: str = "patches.jsonl",
        append: bool = False,
        glob_pattern: str = "**/*",
        match_mode: str = "literal",
        replace_all: bool = False,
        needles_path: Optional[str] = None,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Generate patch JSONL from mutation needles — **not** the primary SPEC workflow.

        For executable specs use ``apatch_spec_run`` (whole spec, RFP-009) or
        ``apatch_execute_next(needles=…)`` — they call generate_batch internally.
        Do NOT hand-write/stage ``patches.jsonl`` for SPEC-* tasks. Response includes
        ``protocol_contract.if_spec_task``.

        §3B mass refactor only (with session). Actions:
        replace (default) — find_text/replace_text on existing files;
        create — {action, target_file, content} for new files (apply_patch Add File);
        delete — {action, target_file}; rename — {source_file, target_file};
        chmod — {action, target_file, mode: "755"} or {action, target_file, executable: true};
        shift_outline — {target_file, after, levels: [2,3], delta} renumber ##/### from anchor;
        insert_before — {target_file, before, content} insert block, no renumber;
        insert_section — {target_file, before, content, shift_following?} typical «new §N»;
        passthrough — {tool_calls: [...]} for raw apply_patch envelopes.

        **Numbered markdown:** prefer insert_section/shift_outline over N× heading replace
        (SPEC-DOC-OUTLINE-1). Anchor = substring in heading line (e.g. "## 3.").

        **Requires** active ``apatch_session_start`` first. Default ``out_path`` routes to
        ``.apatch/tmp/<session_id>/`` — use response ``out_path`` for simulate/apply_session.

        Large or multi-line needles: write a JSON array to a file and pass
        ``needles_path`` (e.g. '.apatch/tmp/needles.json') instead of inline ``needles``
        — avoids JSON escaping pain in MCP payloads (parity with CLI ``--needles``).
        """
        import json as _json

        from apatch.workflows import generate_patch_jsonl_batch
        from apatch.runtime.errors import SessionBindingError
        from apatch.runtime.session_binding import capture_session_binding
        from apatch.session_state import enrich_tool_response

        try:
            binding = capture_session_binding(
                target_dir,
                expected_session_id=governed_session_id or None,
                session_token=session_token or None,
                require_capability=True,
                operation="generate_batch",
            )
        except SessionBindingError as exc:
            return exc.to_dict()

        if needles_path and needles:
            return {"ok": False, "error": "Pass either needles or needles_path, not both"}
        if needles_path:
            abs_needles = (
                needles_path
                if os.path.isabs(needles_path)
                else _abs_in_workspace(target_dir, needles_path)
            )
            try:
                with open(abs_needles, encoding="utf-8") as fh:
                    needles = _json.load(fh)
            except Exception as e:
                return {"ok": False, "error": f"Failed to read needles_path {needles_path}: {e}"}
            if not isinstance(needles, list):
                return {"ok": False, "error": "needles_path must contain a JSON array of mutation dicts"}
        if not needles:
            return {"ok": False, "error": "Provide needles or needles_path"}

        abs_out = out_path if os.path.isabs(out_path) else _abs_in_workspace(target_dir, out_path)
        result = generate_patch_jsonl_batch(
            needles=needles,
            target_dir=target_dir,
            out_path=abs_out,
            append=append,
            default_glob_pattern=glob_pattern,
            default_match_mode=match_mode,
            default_replace_all=replace_all,
            governed_session_id=binding.session_id if binding else None,
        )
        if binding:
            return enrich_tool_response(
                "apatch_generate_batch",
                result,
                target_dir=target_dir,
                expected_session_id=binding.session_id,
                expected_revision=binding.revision,
            )
        return result

    @mcp.tool()
    def apatch_doctor(target_dir: str = ".") -> Dict[str, Any]:
        """Workspace diagnostics — **always call first** (or fetch ``apatch://playbook/*``).

        Returns ``protocol_contract``, ``runtime_hygiene``, ``tool_usage``, ``mcp_resources``,
        ``spec_run``, ``spec_execution``, ``spec_authoring``, ``sandbox_agent_protocol``, ``hygiene``.

        **Whole spec:** ``apatch_spec_run``. **Mass refactor:** session_start → generate_batch
        → simulate(**out_path**) → apply_session → session_end. **Not:** root ``patches-*.jsonl``.

        MCP resources mirror this payload for ``doctor_only`` mode. Never pip/restart MCP from agent."""
        from apatch.doctor import run_doctor

        return run_doctor(target_dir)

    @mcp.tool()
    def apatch_workspace_list(target_dir: str = ".") -> Dict[str, Any]:
        """List human-authorized local workspace aliases and pin/drift status."""
        from apatch.workflows import workspace_list_workspace

        return workspace_list_workspace(target_dir)

    @mcp.tool()
    def apatch_workspace_inspect(
        alias: str = Field(
            ...,
            description="Human-authorized local workspace alias, without the leading @.",
        ),
        include_contract: bool = Field(
            default=False,
            description="Include the full AGENTS.md contract in this read-only response.",
        ),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Inspect one roaming alias; optionally return its pinned AGENTS.md contract."""
        from apatch.workflows import workspace_inspect_workspace

        return workspace_inspect_workspace(
            target_dir,
            alias=alias,
            include_contract=include_contract,
        )

    @mcp.tool()
    def apatch_extension_list(
        target_dir: str = ".",
        lock_path: str = ".apatch/extensions.lock.json",
        include_disabled: bool = True,
    ) -> Dict[str, Any]:
        """List digest-verified local extensions from the explicit workspace lock."""
        from apatch.workflows import extension_list_workspace
        return extension_list_workspace(
            target_dir, lock_path=lock_path, include_disabled=include_disabled
        )

    @mcp.tool()
    def apatch_extension_inspect(
        extension_id: str,
        target_dir: str = ".",
        lock_path: str = ".apatch/extensions.lock.json",
    ) -> Dict[str, Any]:
        """Inspect one explicitly locked extension without executing it."""
        from apatch.workflows import extension_inspect_workspace
        return extension_inspect_workspace(
            target_dir, extension_id=extension_id, lock_path=lock_path
        )

    @mcp.tool()
    def apatch_extension_validate(
        extension_id: str = "",
        target_dir: str = ".",
        lock_path: str = ".apatch/extensions.lock.json",
    ) -> Dict[str, Any]:
        """Revalidate lock, manifest, and artifact digests."""
        from apatch.workflows import extension_validate_workspace
        return extension_validate_workspace(
            target_dir, extension_id=extension_id, lock_path=lock_path
        )

    @mcp.tool()
    def apatch_extension_run(
        tool_id: str,
        arguments: Any = None,
        target_dir: str = ".",
        lock_path: str = ".apatch/extensions.lock.json",
    ) -> Dict[str, Any]:
        """Run one enabled extension through the bounded JSON protocol."""
        from apatch.workflows import extension_run_workspace
        return extension_run_workspace(
            target_dir,
            tool_id=tool_id,
            arguments={} if arguments is None else arguments,
            lock_path=lock_path,
        )

    @mcp.tool()
    def apatch_remote_task_run(
        remote_target: str = Field(
            ...,
            description="Remote workspace target alias from .apatch/remote.json, ssh://host/path, or host:/path.",
        ),
        intent: str = Field(
            ...,
            description="Human-level task intent approved once before the orchestrated remote run.",
        ),
        plan: Optional[Dict[str, Any]] = Field(
            default=None,
            description=(
                "Optional governed plan: {needles:[...]}, {logs_path:...}, "
                "{spec:'SPEC-X', requirements:{...}} for one remote spec_run, "
                "{execute_next:true, spec:'SPEC-X', requirement:'SPEC-X#Rk', needles:[...]} "
                "for one exact already-attested requirement; add defer_finalize:true when "
                "the service must restart before its live verify, then use finalize_current, "
                "{specs:[...], requirements:{...}} for remote spec_run_multi, "
                "{slug_ratify:true, slug:'slug', spec:'SPEC-X'} for remote verify-once "
                "re-attestation and conformance gating, "
                "{rebind_stale:true, spec:'SPEC-X'} for one remote verify + shared-file "
                "stale rebind without source mutation, "
                "{fix_forward_current:true, needles:[...]} to repair the active failed "
                "session in place, or {rollback_session:'<checkpoint>'}."
            ),
        ),
        verify: Optional[Union[str, List[str], Dict[str, Any]]] = Field(
            default=None,
            description="Optional remote verification command or structured verify request.",
        ),
        dry_run: bool = Field(
            default=True,
            description="When true, return the orchestrated timeline without executing SSH mutations.",
        ),
        target_dir: str = Field(
            default=".",
            description="Local control workspace root containing .apatch/remote.json policy.",
        ),
        python: str = Field(
            default="python3",
            description="Remote Python executable used by the SSH transport when dry_run is false.",
        ),
        ssh_args: Optional[List[str]] = Field(
            default=None,
            description="Extra ssh argv entries, e.g. ['-o','BatchMode=yes']; default uses BatchMode and ConnectTimeout.",
        ),
        timeout_sec: int = Field(
            default=600,
            description=(
                "Per-step SSH timeout in seconds when dry_run is false. The caller may "
                "shorten an alias policy timeout but cannot raise it; the effective value "
                "is capped below the outer MCP call deadline."
            ),
        ),
    ) -> Dict[str, Any]:
        """Intent-level remote autopilot boundary.

        ⚠️ ``dry_run`` defaults to True. A dry-run SIMULATES the whole
        doctor/session/generate/simulate/apply/verify/attest/session_end timeline against a
        fake transport — it returns ``attest`` / ``phase=complete`` but writes NOTHING and
        makes no SSH connection (the response carries ``applied: false`` and ``transport:
        "fake"``). Pass ``dry_run=false`` WITH either the edit in ``plan.needles`` or an
        existing remote patch log in ``plan.logs_path`` to actually apply on the remote over
        SSH (the worker runs apatch_apply_session there). For one SPEC, pass
        ``plan={"spec": "SPEC-X", "requirements": {...}}``; the worker runs
        ``apatch_spec_run`` directly. For cross-SPEC work, pass
        ``plan={"specs": [...], "requirements": {...}}``; the worker runs
        ``apatch_spec_run_multi``. Neither route uses an outer generic session. For a
        completed slug whose conformance enrollment is stale or missing, pass
        ``plan={"slug_ratify": true, "slug": "slug", "spec": "SPEC-X"}``; the remote
        worker runs verify-once, batch re-attestation, and the conformance gate. For shared-file
        staleness, ``plan={"rebind_stale": true, "spec": "SPEC-X"}`` runs every stale Rk
        verify and rebinds green evidence without a source mutation. When an exact Rk changes
        code loaded by a long-running service, add ``"defer_finalize": true``, restart the
        service through its broker, then call ``plan={"finalize_current": true}`` with the
        fresh live verify. After ``VERIFY_FAILED``
        needing corrective needles, use ``plan={"fix_forward_current": true, "needles": [...]}``
        with a fresh ``verify`` command to resume, repair, verify, attest, and close the same
        governed session. A failed apply can be rolled back in a separate governed call with
        ``plan={"rollback_session": "<checkpoint>"}``; the checkpoint comes from the failed
        apply response. The SSH adapter is a separate
        transport layer so agents do not fan out into many user-facing approvals.
        """
        from apatch.remote.orchestrator import remote_task_run
        from apatch.remote.policy import resolve_remote_target
        from apatch.remote.errors import RemoteTaskError
        from apatch.remote.ssh import SshRemoteTransport
        from apatch.remote.transport import FakeRemoteTransport

        if not isinstance(plan, dict):
            plan = None
        if not isinstance(verify, (str, list, dict)):
            verify = None
        if not isinstance(dry_run, bool):
            dry_run = True
        if not isinstance(target_dir, str):
            target_dir = "."
        if not isinstance(python, str):
            python = "python3"
        if not isinstance(ssh_args, list):
            ssh_args = None
        if not isinstance(timeout_sec, int) or isinstance(timeout_sec, bool) or timeout_sec <= 0:
            timeout_sec = 600

        try:
            resolved_target = resolve_remote_target(remote_target, policy_root=target_dir)
        except RemoteTaskError as exc:
            result = {
                "approval_boundary": "single_intent",
                "approval_prompts_expected": 1,
                "target": remote_target,
                "intent": intent,
                "timeline": [],
            }
            result.update(exc.to_result())
            return result

        if dry_run:
            transport = FakeRemoteTransport(
                {
                    "apatch_generate_batch": {
                        "ok": True,
                        "logs_path": ".apatch/remote/dry_run_patches.jsonl",
                    }
                }
            )
            transport_name = "fake"
        else:
            caller_timeout = min(timeout_sec, 240)
            if resolved_target.allow_transport_overrides:
                transport_python = python
                transport_ssh_args = ssh_args
                transport_timeout = caller_timeout
            else:
                transport_python = resolved_target.python or "python3"
                transport_ssh_args = list(resolved_target.ssh_args) if resolved_target.ssh_args else None
                policy_timeout = max(1, int(resolved_target.timeout_sec or 600))
                transport_timeout = min(policy_timeout, caller_timeout)
            transport = SshRemoteTransport(
                python=transport_python, ssh_args=transport_ssh_args, timeout_sec=transport_timeout
            )
            transport_name = "ssh"
        result = remote_task_run(
            resolved_target,
            intent=intent,
            plan=plan,
            verify=verify,
            transport=transport,
            policy_root=target_dir,
        )
        result["dry_run"] = dry_run
        result["transport"] = transport_name
        # Make it unmistakable whether anything was actually written, so a dry-run
        # (which simulates the WHOLE ceremony incl. attest) is never read as a real apply,
        # and a no-needles run (no apply step) is not read as "done".
        apply_ran = any(
            s.get("operation") == "apatch_apply_session"
            and s.get("ok")
            and int(
                (s.get("result") or {}).get("applied")
                or ((s.get("result") or {}).get("chunk_result") or {}).get("applied")
                or 0
            ) > 0
            for s in (result.get("timeline") or [])
        )
        spec_run_ran = any(
            s.get("operation") == "apatch_spec_run"
            and s.get("ok")
            and int((s.get("result") or {}).get("applied") or 0) > 0
            for s in (result.get("timeline") or [])
        )
        execute_next_ran = any(
            s.get("operation") == "apatch_execute_next"
            and s.get("ok")
            and int(
                (s.get("result") or {}).get("applied")
                or ((s.get("result") or {}).get("chunk_result") or {}).get("applied")
                or 0
            ) > 0
            for s in (result.get("timeline") or [])
        )
        multi_run_ran = any(
            s.get("operation") == "apatch_spec_run_multi"
            and s.get("ok")
            and int((s.get("result") or {}).get("applied") or 0) > 0
            for s in (result.get("timeline") or [])
        )
        commit_attested_ran = any(
            s.get("operation") == "apatch_commit_attested"
            and s.get("ok")
            and bool((s.get("result") or {}).get("committed"))
            for s in (result.get("timeline") or [])
        )
        nested_results = [
            s.get("result") or {}
            for s in (result.get("timeline") or [])
            if isinstance(s.get("result"), Mapping)
        ]
        already_satisfied_requirements = list(dict.fromkeys(
            str(rid)
            for nested in nested_results
            for rid in (nested.get("already_satisfied_requirements") or [])
        ))
        explicitly_skipped_requirements = list(dict.fromkeys(
            str(rid)
            for nested in nested_results
            for rid in (nested.get("explicitly_skipped_requirements") or [])
        ))
        if already_satisfied_requirements:
            result["already_satisfied_requirements"] = already_satisfied_requirements
        if explicitly_skipped_requirements:
            result["explicitly_skipped_requirements"] = explicitly_skipped_requirements
        mutation_ran = (
            apply_ran
            or execute_next_ran
            or spec_run_ran
            or multi_run_ran
            or commit_attested_ran
        )
        noop_attest_ran = any(
            s.get("operation") == "apatch_noop_attest" and s.get("ok")
            for s in (result.get("timeline") or [])
        )
        result["applied"] = bool(mutation_ran and not dry_run)
        plan_obj = plan or {}
        uses_existing_logs_path = bool(plan_obj.get("logs_path")) and not any(
            plan_obj.get(key)
            for key in ("needles", "patches", "slug_close", "candidate", "candidates")
        )
        if dry_run:
            if plan_obj.get("rollback_session"):
                result["message"] = (
                    "DRY-RUN: remote rollback was simulated; no SSH ran and no files changed."
                )
                result["next_action"] = (
                    "Re-run the same rollback_session plan with dry_run=false."
                )
            else:
                result["message"] = (
                    "DRY-RUN: nothing was written on the remote. No SSH ran — FakeRemoteTransport "
                    "simulated every step (including attest / phase=complete). Re-run with "
                    "dry_run=false to actually apply."
                )
            if plan_obj.get("rollback_session"):
                pass
            elif uses_existing_logs_path:
                result["next_action"] = (
                    "apatch_remote_task_run(remote_target=..., intent=..., "
                    "plan={'logs_path': '<remote JSONL path>'}, dry_run=false)"
                )
            elif plan_obj.get("specs"):
                result["next_action"] = (
                    "apatch_remote_task_run(remote_target=..., intent=..., "
                    "plan={'specs': [...], 'requirements': {...}}, dry_run=false)"
                )
            else:
                result["next_action"] = (
                    "apatch_remote_task_run(remote_target=..., intent=..., "
                    "plan={'needles': [<the edit>]}, dry_run=false)"
                )
        elif result.get("ok") is False:
            result.setdefault("message", "Remote task failed before a successful apply.")
            result.setdefault("next_action", result.get("recommended_action") or "Inspect failed_step and retry.")
        elif plan_obj.get("commit_attested"):
            commit_result = next(
                (
                    s.get("result") or {}
                    for s in reversed(result.get("timeline") or [])
                    if s.get("operation") == "apatch_commit_attested"
                ),
                {},
            )
            if commit_result.get("dry_run"):
                result["message"] = (
                    "Remote exact attested Git commit VALIDATED; no Git state changed."
                )
                result["next_action"] = (
                    "Re-run the same commit_attested plan with plan.dry_run=false."
                )
            else:
                result["message"] = (
                    "Remote exact attested Git commit COMMITTED"
                    + (" and PUSHED." if commit_result.get("pushed") else ".")
                )
                result["next_action"] = "Continue with the next governed search change."
        elif (plan or {}).get("reset_session"):
            result["message"] = "Remote session reset completed; no mutation was intended."
            result["next_action"] = "Start the next governed remote task with plan.needles, plan.logs_path, or verify."
        elif (plan or {}).get("rollback_session"):
            result["message"] = "Remote checkpoint rolled back and the governed session was closed."
            result["next_action"] = "Fix the failed plan, then start a fresh governed remote task."
        elif result.get("finalize_deferred") or any(
            nested.get("finalize_deferred") for nested in nested_results
        ):
            result["finalize_deferred"] = True
            result["finalized"] = False
            result["message"] = (
                "Remote mutation applied; finalization DEFERRED. "
                "No final verify, attestation, or session closure was performed."
            )
            result["next_action"] = (
                "Keep the same session. After the required service action, use finalize_current "
                "to execute the bound SPEC verification, attest, and close."
            )
        elif (plan or {}).get("fix_forward_current"):
            result["message"] = (
                "Remote session FIX-FORWARDED over SSH: resumed the active session, "
                "applied the corrective mutation, re-ran verify, attested, and closed it."
            )
            result["next_action"] = "Check apatch session status / conformance for the remote workspace."
        elif noop_attest_ran:
            result["message"] = (
                "Remote task NOOP-ATTESTED over SSH under one intent-level approval "
                "boundary; no source mutation was intended."
            )
            result["next_action"] = "Check apatch_spec_status / conformance for the remote workspace."
        elif (plan or {}).get("finalize_current"):
            result["message"] = (
                "Remote session FINALIZED over SSH: resumed the active session, "
                "re-ran verify, attested, and closed it. No source mutation was intended."
            )
            result["next_action"] = "Check apatch session status / conformance for the remote workspace."
        elif (plan or {}).get("slug_ratify"):
            result["message"] = (
                "Remote slug RATIFIED over SSH: verified once, re-attested open "
                "requirements, and evaluated the conformance gate."
            )
            result["next_action"] = "Check scoped wiring and continue with the next slug."
        elif (plan or {}).get("rebind_stale"):
            result["message"] = (
                "Remote stale requirements REBOUND over SSH after green verification; "
                "no source mutation was intended."
            )
            result["next_action"] = "Continue with the next real mutation or runtime defect."
        elif not mutation_ran and already_satisfied_requirements:
            result["message"] = (
                "Remote task was an ALREADY-SATISFIED NOOP: every requested postcondition "
                "was already present, so no source mutation ran."
            )
            result["next_action"] = "No retry is needed; continue with the next real defect."
        elif not mutation_ran and explicitly_skipped_requirements:
            result["message"] = (
                "Remote task was explicitly SKIPPED for already-attested requirements; "
                "no source mutation ran."
            )
            result["next_action"] = "Remove skip_if_attested=true only if a maintenance rerun is intended."
        elif not mutation_ran:
            result["message"] = (
                "Governed run completed but NO mutation step ran — the plan had no "
                "needles/patches/logs_path, so nothing was written. Put the edit in "
                "plan.needles and re-run."
            )
            result["next_action"] = "Add the edit to plan.needles, then re-run with dry_run=false."
        else:
            result["message"] = "Remote task APPLIED over SSH under one intent-level approval boundary."
        return result


    @mcp.tool()
    def apatch_remote_service_action(
        alias: str = Field(
            ...,
            description="Remote alias from local .apatch/remote.json policy.",
        ),
        service: str = Field(
            ...,
            description="Configured service alias under targets.<alias>.services.",
        ),
        action: str = Field(
            ...,
            description=(
                "Service lifecycle action: status, restart, logs, healthcheck, exec, "
                "or a policy-defined command action."
            ),
        ),
        command: str = Field(
            default="",
            description=(
                "Best-effort read-only shell command for action='exec'; returns its stdout. "
                "Ignored for policy-defined command actions."
            ),
        ),
        lines: int = Field(
            default=80,
            description="Log lines for logs action.",
        ),
        target_dir: str = Field(
            default=".",
            description="Local control workspace root containing .apatch/remote.json policy.",
        ),
        execute: bool = Field(
            default=False,
            description="When false, return a redacted policy-approved plan. When true, execute over SSH.",
        ),
    ) -> Dict[str, Any]:
        """Policy-approved remote service lifecycle broker."""
        from apatch.remote.errors import RemoteTaskError
        from apatch.remote.services import execute_service_action, plan_service_action

        if not isinstance(target_dir, str):
            target_dir = "."
        if not isinstance(lines, int):
            lines = 80
        if not isinstance(execute, bool):
            execute = False
        if not isinstance(command, str):
            command = ""
        try:
            if execute:
                result = execute_service_action(
                    alias=alias,
                    service=service,
                    action=action,
                    policy_root=target_dir,
                    lines=lines,
                    command=command or None,
                )
            else:
                result = plan_service_action(
                    alias=alias,
                    service=service,
                    action=action,
                    policy_root=target_dir,
                    lines=lines,
                    command=command or None,
                )
        except RemoteTaskError as exc:
            result = exc.to_result()
        result["approval_boundary"] = "single_service_action"
        result["approval_prompts_expected"] = 1 if execute else 0
        result["dry_run"] = not execute
        next_action = result.get("recommended_action") or "Inspect service action result."
        if result.get("ok") is False:
            result.setdefault(
                "recommended_action",
                "Inspect stdout/stderr/status and retry the service action; no governed mutation ran.",
            )
            next_action = result["recommended_action"]
        result["state_update"] = {
            "phase": "idle",
            "next_action": next_action,
            "risk_level": "low",
            "last_tool": "apatch_remote_service_action",
        }
        return result


    @mcp.tool()
    def apatch_remote_source_handoff(
        alias: str = Field(
            ...,
            description="Remote alias from local .apatch/remote.json policy.",
        ),
        source_dir: str = Field(
            default=".",
            description="Local source directory to hand off through the local broker.",
        ),
        mode: str = Field(
            default="workspace_overlay",
            description="Policy-approved handoff mode, including checksum-verified release_bundle.",
        ),
        release_bundle_path: Optional[str] = Field(
            default=None,
            description="CI-built .tar.gz release artifact; required for release_bundle mode.",
        ),
        checksum_path: Optional[str] = Field(
            default=None,
            description="Matching CI-generated .sha256 sidecar; required for release_bundle mode.",
        ),
        release_sha: Optional[str] = Field(
            default=None,
            description="Exact 40-character commit SHA pinned by every release image; required for release_bundle mode.",
        ),
        target_dir: str = Field(
            default=".",
            description="Local control workspace root containing .apatch/remote.json policy.",
        ),
        execute: bool = Field(
            default=False,
            description=(
                "When false, return a redacted handoff plan. When true, execute internal archive transport. "
                "Use this instead of raw shell scp/rsync/tar|ssh when remote GitHub credentials are unavailable."
            ),
        ),
    ) -> Dict[str, Any]:
        """Opaque local-source handoff for remote aliases; do not expose SSH/archive transport to the agent."""
        from apatch.remote.errors import RemoteTaskError
        from apatch.remote.handoff import (
            execute_release_bundle_handoff,
            execute_source_handoff,
            plan_release_bundle_handoff,
            plan_source_handoff,
        )

        if not isinstance(target_dir, str):
            target_dir = "."
        if not isinstance(source_dir, str):
            source_dir = "."
        if not isinstance(mode, str):
            mode = "workspace_overlay"
        if not isinstance(release_bundle_path, str):
            release_bundle_path = ""
        if not isinstance(checksum_path, str):
            checksum_path = ""
        if not isinstance(release_sha, str):
            release_sha = ""
        if not isinstance(execute, bool):
            execute = False
        try:
            if mode == "release_bundle":
                if execute:
                    result = execute_release_bundle_handoff(
                        alias=alias,
                        bundle_path=release_bundle_path,
                        checksum_path=checksum_path,
                        release_sha=release_sha,
                        policy_root=target_dir,
                    )
                else:
                    result = plan_release_bundle_handoff(
                        alias=alias,
                        bundle_path=release_bundle_path,
                        checksum_path=checksum_path,
                        release_sha=release_sha,
                        policy_root=target_dir,
                    )
            elif execute:
                result = execute_source_handoff(
                    alias=alias,
                    source_dir=source_dir,
                    policy_root=target_dir,
                    mode=mode,
                )
            else:
                result = plan_source_handoff(
                    alias=alias,
                    source_dir=source_dir,
                    policy_root=target_dir,
                    mode=mode,
                )
        except RemoteTaskError as exc:
            result = exc.to_result()
        result["approval_boundary"] = "single_source_handoff"
        result["approval_prompts_expected"] = 1 if execute else 0
        result["dry_run"] = not execute
        return result


    @mcp.tool()
    def apatch_build_diagnose(
        target_dir: str = ".",
        verify: Optional[str] = Field(
            default=None,
            description="Optional shell verify command when log_text is omitted.",
        ),
        log_text: Optional[str] = Field(
            default=None,
            description="Raw compiler or build log text to parse (skips verify when set).",
        ),
        write_artifacts: bool = Field(
            default=True,
            description="Write diagnostics JSON under .apatch/build_diagnose/ (default true).",
        ),
    ) -> Dict[str, Any]:
        """Parse compiler output → structured diagnostics + advisory suggestions (RFP-018)."""
        from apatch.session_state import enrich_tool_response
        from apatch.workflows import build_diagnose_workspace

        return enrich_tool_response(
            "apatch_build_diagnose",
            build_diagnose_workspace(
                target_dir,
                verify=verify,
                log_text=log_text,
                write_artifacts=write_artifacts,
            ),
            target_dir=target_dir,
        )

    @mcp.tool()
    def apatch_rebind_stale(
        spec: str = "",
        spec_path: str = "",
        run_verify: bool = Field(
            default=True,
            description="Re-run each requirement's verify before re-attesting (default true).",
        ),
        requirement_ids: Optional[List[str]] = None,
        exclude_requirement_ids: Optional[List[str]] = None,
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Re-anchor file_drift-stale requirements of a spec in one call.

        Collapses the manual per-Rk session_start -> verify -> noop_attest ->
        session_end rebind loop into one operation: every requirement that is
        stale only because a sibling Rk mutated a shared file is re-verified and,
        when green, noop-attested back to the final file state. Requirements
        stale for other reasons (spec_text_changed) are left untouched."""
        from apatch.session_state import enrich_tool_response
        from apatch.spec_rebind import rebind_stale_requirements

        return enrich_tool_response(
            "apatch_rebind_stale",
            rebind_stale_requirements(
                target_dir,
                spec=spec or None,
                spec_path=spec_path or None,
                run_verify=run_verify,
                requirement_ids=requirement_ids,
                exclude_requirement_ids=exclude_requirement_ids,
            ),
            target_dir=target_dir,
        )

    @mcp.tool()
    def apatch_probe(
        mode: str = Field(description="Probe mode: 'falsify' | 'regress' | 'ratify'."),
        verify: str = Field(description="Gate command (shell): pytest / script / linter — any shell command."),
        files: str = Field(default="", description="falsify: comma-separated files the gate guards (corrupted, then restored)."),
        baseline_failures: str = Field(default="", description="regress: comma-separated known-failing ids (default .apatch/verify_baseline.json)."),
        allowed_failures: str = Field(default="", description="regress: comma-separated failing ids/substrings to tolerate."),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Differential probe (RFP-005) — perturb, re-measure, judge the delta vs a polarity.

        One primitive, three modes:
        * ``falsify`` (must_diverge) — corrupt the ``files`` the gate guards; ``verify``
          MUST go RED. A gate that stays green is a FALSE gate (does not test what it
          guards). verdict: ``real`` | ``false``.
        * ``regress`` (must_hold) — the tree must introduce NO new failures vs a recorded
          corpus. verdict: ``stable`` | ``regressed``.
        * ``ratify`` (must_hold) — an attested gate must STILL be green now; red now = a
          STALE attestation. verdict: ``ratified`` | ``stale``.

        ``verify`` is any shell command, so this is domain-agnostic. Guarded files are
        always restored (the corruption is transient)."""
        from apatch.probe import falsify, ratify, regress
        from apatch.session_state import enrich_tool_response

        flist = [f.strip() for f in (files or "").split(",") if f.strip()]
        bl = [f.strip() for f in (baseline_failures or "").split(",") if f.strip()]
        al = [f.strip() for f in (allowed_failures or "").split(",") if f.strip()]
        if mode == "falsify":
            res = falsify(target_dir, verify, flist, record=True)
        elif mode == "regress":
            res = regress(target_dir, verify, baseline_failures=bl, allowed_failures=al)
        elif mode == "ratify":
            res = ratify(target_dir, verify)
        else:
            res = {"error": f"unknown mode {mode!r}", "modes": ["falsify", "regress", "ratify"]}
        return enrich_tool_response("apatch_probe", res, target_dir=target_dir)

    @mcp.tool()
    def apatch_reality(
        action: str = Field(default="status", description="'add' (append an observed fact) | 'status' (coverage of reality)."),
        summary: str = Field(default="", description="add: what was observed (bug/incident/feedback/finding) — required for add."),
        source: str = Field(default="", description="add: where it came from (tracker/telemetry/support/...) — free-form."),
        kind: str = Field(default="observation", description="add: bug|incident|feedback|finding|observation|... — free-form."),
        status: str = Field(default="open", description="add: open | closed_wontfix (wontfix drops the obligation)."),
        rec_id: str = Field(default="", description="add: stable id (default: content hash of source|summary)."),
        spec: str = Field(default="", description="status: limit to one SPEC id (default: all docs/specs/SPEC-*.md)."),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Observed-reality ledger (RFP-005) — reality is the source of truth; requirements discharge records.

        * ``action='add'`` — append an observed fact (``summary`` required) to
          ``.apatch/reality.jsonl`` (append-only, idempotent by id). A new record is
          undischarged DEBT by construction until a requirement claims it.
        * ``action='status'`` — coverage of observed reality: ``covered`` (discharged by an
          attested green gate), ``pending`` (claimed, gate not yet attested), ``uncovered``
          (derived debt). A requirement claims a record via a ``(discharges: REC-…)`` line.
          Status reports operational ``ok: true`` (the query succeeded); the "is the corpus clean"
          signal is in ``clean`` (true = no debt), so reporting debt is never a tool failure.

        ``source``/``kind`` are free-form strings — domain-agnostic."""
        from apatch.reality import add_reality_record, reality_status_workspace
        from apatch.session_state import enrich_tool_response

        if action == "add":
            if not summary:
                res = {"ok": False, "error": "summary is required for action='add'"}
            else:
                rid = add_reality_record(target_dir, summary=summary, id=(rec_id or None),
                                         source=source, kind=kind, status=status)
                res = {"ok": bool(rid), "id": rid, "summary": summary,
                       "source": source, "kind": kind, "status": status}
        elif action == "status":
            cov = reality_status_workspace(target_dir, spec or None)
            # 'ok' from coverage is a DOMAIN signal (no debt), not an operational
            # result — surface it as 'clean' so enrich_tool_response does not treat debt
            # (ok:false) as a tool failure (REC-7855, SPEC-REALITY-STATUS-1#R1).
            res = dict(cov)
            res["clean"] = bool(cov.get("ok"))
            res["ok"] = True
        else:
            res = {"error": f"unknown action {action!r}", "actions": ["add", "status"]}
        return enrich_tool_response("apatch_reality", res, target_dir=target_dir)

    @mcp.tool()
    def apatch_slug_intake(
        slug: str = Field(
            ...,
            description="Slug/category/problem token to collect intake context for.",
        ),
        aliases: List[str] = Field(
            default_factory=list,
            description="Additional aliases/synonyms for matching specs, reality, and evidence.",
        ),
        manifest_path: str = Field(
            default="",
            description=(
                "Optional path to a slug-intake manifest; default checks "
                ".apatch/slug_intake.json and manifests/slug-intake.json."
            ),
        ),
        live: bool = Field(
            default=False,
            description="When true, run live conformance verify for matching specs; default avoids test execution.",
        ),
        limit: int = Field(
            default=25,
            description="Maximum evidence rows per scanned section.",
        ),
        target_dir: str = Field(
            default=".",
            description="Workspace root. Optional — default '.' resolves to MCP-bound workspace.",
        ),
    ) -> Dict[str, Any]:
        """Slug/category intake cockpit: specs + reality + conformance + query-first/data gates.

        Read-only first step for category/slug work. It does not mutate state and does
        not mark requirements green; it surfaces missing query-first verification,
        uncovered reality records, conformance state, matching specs, and
        data/dictionary evidence.
        """
        from apatch.slug_intake import slug_intake_enriched

        alias_list = aliases if isinstance(aliases, list) else []
        manifest_arg = manifest_path if isinstance(manifest_path, str) and manifest_path else None
        live_arg = live if isinstance(live, bool) else False
        limit_arg = limit if isinstance(limit, int) else 25
        return slug_intake_enriched(
            target_dir,
            slug=slug,
            aliases=alias_list,
            manifest_path=manifest_arg,
            live=live_arg,
            limit=limit_arg,
        )

    @mcp.tool()
    def apatch_slug_close(
        slug: str = Field(
            ...,
            description="Slug/category whose owner feedback triage should be replayed and classified.",
        ),
        api_url: str = Field(
            default="",
            description="Live smart-search API URL; default is http://127.0.0.1:8004/api/os/smart-search.",
        ),
        triage_path: str = Field(
            default="",
            description="Optional path to tests/regressions/<slug>_feedback_triage.tsv.",
        ),
        approved_path: str = Field(
            default="",
            description="Optional path to tests/regressions/feedback_approved_contract.tsv.",
        ),
        size: int = Field(
            default=5,
            description="Live API result size for each replay.",
        ),
        timeout: float = Field(
            default=30.0,
            description="Live API timeout seconds.",
        ),
        limit: int = Field(
            default=0,
            description="Limit rows for a fast smoke run; 0 means all rows.",
        ),
        target_dir: str = Field(
            default=".",
            description="Workspace root. Optional — default '.' resolves to MCP-bound workspace.",
        ),
    ) -> Dict[str, Any]:
        """Replay slug feedback through live API + decision graph and propose closure needles.

        Read-only: it does not mutate files or mark requirements green. It returns
        deterministic suggested statuses (fixed/catalog_gap/other_slug/open_runtime_bug),
        graph-derived root causes, approved-corpus conflicts, and proposed apatch
        needles for the owner TSV.
        """
        from apatch.slug_close import DEFAULT_API_URL, slug_close_enriched

        return slug_close_enriched(
            target_dir,
            slug=slug,
            api_url=api_url if isinstance(api_url, str) and api_url else DEFAULT_API_URL,
            triage_path=triage_path if isinstance(triage_path, str) and triage_path else None,
            approved_path=approved_path if isinstance(approved_path, str) and approved_path else None,
            size=size if isinstance(size, int) else 5,
            timeout=timeout if isinstance(timeout, (int, float)) else 30.0,
            limit=limit if isinstance(limit, int) else 0,
        )

    @mcp.tool()
    def apatch_slug_cockpit(
        slug: str = Field(
            ...,
            description="Slug/category/problem token to summarize as one operational cockpit.",
        ),
        aliases: List[str] = Field(
            default_factory=list,
            description="Additional aliases/synonyms for matching specs, reality, and evidence.",
        ),
        manifest_path: str = Field(
            default="",
            description=(
                "Optional path to a slug-intake manifest; default checks "
                ".apatch/slug_intake.json and manifests/slug-intake.json."
            ),
        ),
        live: bool = Field(
            default=False,
            description="When true, run scoped conformance and the configured operational_status hook.",
        ),
        evidence_limit: int = Field(
            default=25,
            description="Maximum intake evidence rows per scanned section.",
        ),
        feedback_limit: int = Field(
            default=0,
            description="Limit feedback rows for a fast smoke run; 0 means all rows.",
        ),
        api_url: str = Field(
            default="",
            description="Live smart-search API URL; default is http://127.0.0.1:8004/api/os/smart-search.",
        ),
        triage_path: str = Field(
            default="",
            description="Optional path to tests/regressions/<slug>_feedback_triage.tsv.",
        ),
        approved_path: str = Field(
            default="",
            description="Optional path to tests/regressions/feedback_approved_contract.tsv.",
        ),
        size: int = Field(
            default=5,
            description="Live API result size for each feedback replay.",
        ),
        timeout: float = Field(
            default=30.0,
            description="Live API timeout seconds.",
        ),
        target_dir: str = Field(
            default=".",
            description="Workspace root. Optional — default '.' resolves to MCP-bound workspace.",
        ),
    ) -> Dict[str, Any]:
        """One-screen slug cockpit: specs, gates, operational status, feedback, actions.

        Read-only: combines slug intake, live feedback replay, and an optional
        project operational_status JSON hook. Evidence-only debt stays separate
        from runtime reopen reasons. It does not mutate project state.
        """
        from apatch.slug_close import DEFAULT_API_URL
        from apatch.slug_cockpit import slug_cockpit_enriched

        return slug_cockpit_enriched(
            target_dir,
            slug=slug,
            aliases=aliases if isinstance(aliases, list) else [],
            manifest_path=manifest_path if isinstance(manifest_path, str) and manifest_path else None,
            live=live if isinstance(live, bool) else False,
            evidence_limit=evidence_limit if isinstance(evidence_limit, int) else 25,
            feedback_limit=feedback_limit if isinstance(feedback_limit, int) else 0,
            api_url=api_url if isinstance(api_url, str) and api_url else DEFAULT_API_URL,
            triage_path=triage_path if isinstance(triage_path, str) and triage_path else None,
            approved_path=approved_path if isinstance(approved_path, str) and approved_path else None,
            size=size if isinstance(size, int) else 5,
            timeout=timeout if isinstance(timeout, (int, float)) else 30.0,
        )

    @mcp.tool()
    def apatch_slug_feedback_lint(
        slug: str = Field(
            default="",
            description="Optional slug; default lints every tests/regressions/*_feedback_triage.tsv.",
        ),
        triage_path: str = Field(
            default="",
            description="Optional explicit triage TSV path (overrides slug discovery).",
        ),
        approved_path: str = Field(
            default="",
            description="Optional path to tests/regressions/feedback_approved_contract.tsv.",
        ),
        target_dir: str = Field(
            default=".",
            description="Workspace root. Optional — default '.' resolves to MCP-bound workspace.",
        ),
    ) -> Dict[str, Any]:
        """Lint feedback triage/approved statuses against the canonical vocabulary.

        Read-only: reports unknown statuses, legacy aliases (with byte-exact
        normalization needles for the governed apply path), and files missing a
        status column. The canonical vocabulary lives in apatch.feedback_status.
        """
        from apatch.slug_feedback_lint import feedback_lint_enriched

        return feedback_lint_enriched(
            target_dir,
            slug=slug if isinstance(slug, str) and slug else None,
            triage_path=triage_path if isinstance(triage_path, str) and triage_path else None,
            approved_path=approved_path if isinstance(approved_path, str) and approved_path else None,
        )

    @mcp.tool()
    def apatch_slug_ratify(
        slug: str = Field(
            ...,
            description=(
                "Slug/category to ratify in one call (verify once, batch-attest "
                "green pending/stale Rk, gate)."
            ),
        ),
        spec: str = Field(
            default="",
            description=(
                "Explicit SPEC id; overrides contract-yaml/conformance/default resolution. "
                "Resolution is exact-ownership only — never fuzzy."
            ),
        ),
        dry_run: bool = Field(
            default=False,
            description="Resolve + lint only; report the verify commands and stale requirements that would run.",
        ),
        target_dir: str = Field(
            default=".",
            description="Workspace root. Optional — default '.' resolves to MCP-bound workspace.",
        ),
    ) -> Dict[str, Any]:
        """Single-call slug ratification: one verify run batch-attests open Rk and gates conformance.

        Runs resolve → lint → verify → rebind → gate, fail-fast with stage-tagged
        errors. Each unique verify command executes exactly once; the measured
        results batch-attest every green pending/in-progress/stale requirement
        in one governed session and one signed ledger commit, then produce the
        conformance verdict (verify_runner seam) — no marker files, per-Rk
        sessions, or repeated replays.
        Compact DTO: stages, verify counters, reattested Rk, gate verdict/buckets.
        """
        from apatch.slug_ratify import slug_ratify_enriched

        return slug_ratify_enriched(
            target_dir,
            slug=slug,
            spec=spec if isinstance(spec, str) and spec else None,
            dry_run=dry_run if isinstance(dry_run, bool) else False,
        )

    @mcp.tool()
    def apatch_gc(
        target_dir: str = ".",
        mode: str = "report",
    ) -> Dict[str, Any]:
        """Artifact lifecycle GC (RFP-016).

        ``mode``: ``report`` (default) | ``reconcile`` (register inferred — fixes inference sunset)
        | ``safe`` (delete gc_allowed EPHEMERAL/GARBAGE) | ``rotate`` (safe + prune HISTORY/DEBUG).
        Check ``apatch_doctor.hygiene`` first."""
        from apatch.gc import run_gc
        from apatch.session_state import enrich_tool_response

        return enrich_tool_response(
            "apatch_gc",
            run_gc(target_dir, mode=mode),
            target_dir=target_dir,
        )

    @mcp.tool()
    def apatch_mcp_hygiene(
        target_dir: str = ".",
        sweep_leases: bool = Field(
            default=False,
            description="Remove stale write_lease.json in workspace (default: report only).",
        ),
    ) -> Dict[str, Any]:
        """Ghost MCP processes + lease health report (RFP-019 L1-8)."""
        from apatch.mcp.hygiene import run_mcp_hygiene
        from apatch.session_state import enrich_tool_response

        return enrich_tool_response(
            "apatch_mcp_hygiene",
            run_mcp_hygiene(target_dir, sweep_leases=sweep_leases),
            target_dir=target_dir,
        )

    @mcp.tool()
    def apatch_init_consumer(
        target_dir: str = ".",
        profile: str = "default",
        with_ci: bool = False,
        with_arch_rules: bool = False,
        with_enforcement: bool = False,
        governed_mode: str = "auto_session",
        with_sandbox: bool = False,
        with_devcontainer: bool = False,
        with_mcp: bool = True,
        refresh_agents: bool = False,
    ) -> Dict[str, Any]:
        """Scaffold manifests/README/npm scripts for a consumer project."""
        from apatch.doctor import init_consumer

        created = init_consumer(
            target_dir,
            profile=profile,
            with_ci=with_ci,
            with_arch_rules=with_arch_rules,
            with_enforcement=with_enforcement,
            governed_mode=governed_mode if with_enforcement else "off",
            with_sandbox=with_sandbox,
            with_devcontainer=with_devcontainer,
            with_mcp=with_mcp,
            refresh_agents=refresh_agents,
        )
        return {"created": created, "count": len(created), "profile": profile}

    @mcp.tool()
    def apatch_sandbox_status(target_dir: str = ".") -> Dict[str, Any]:
        """Write sandbox: mode, active lease, Cursor hooks installed."""
        from apatch.sandbox import sandbox_status_workspace

        return sandbox_status_workspace(target_dir)

    @mcp.tool()
    def apatch_sandbox_audit(
        target_dir: str = ".",
        auto_revert: bool = False,
    ) -> Dict[str, Any]:
        """One-shot sandbox audit; auto-revert only when flag is set."""
        from apatch.sandbox_watch import run_sandbox_watch_once

        return run_sandbox_watch_once(
            target_dir,
            force=True,
            auto_revert=auto_revert,
            respect_watcher_revert=False,
        )

    @mcp.tool()
    def apatch_sandbox_ci_gate(
        target_dir: str = ".", base: Optional[str] = None
    ) -> Dict[str, Any]:
        """CI/PR gate: sandbox audit + notarization when configured. Pass base (e.g. origin/main) to gate the PR diff (Ring-2 authority over shared history)."""
        from apatch.sandbox_watch import run_sandbox_ci_gate

        return run_sandbox_ci_gate(target_dir, base=base)

    @mcp.tool()
    def apatch_session_state(target_dir: str = ".") -> Dict[str, Any]:
        """Domain session view: lifecycle, intent, artifacts[], invariant (.apatch/session_state.json)."""
        from apatch.runtime.session import build_session_view
        from apatch.session_state import enrich_tool_response

        return enrich_tool_response("apatch_session_state", build_session_view(target_dir), target_dir=target_dir)

    @mcp.tool()
    def apatch_session_start(
        target_dir: str = ".",
        intent: str = "",
        artifacts: Optional[List[Any]] = None,
        requirement: str = "",
        spec_path: str = "",
        lane: str = "",
        request_id: str = "",
        sdd_contract: Optional[Dict[str, Any]] = None,
        task_envelope: Optional[Dict[str, Any]] = None,
        actor_id: str = "",
    ) -> Dict[str, Any]:
        """Open governed session with Intent and optional typed Artifact anchors (RFP-006).

        ``artifacts`` — repeatable engineering decision refs bound to this session:
        strings ``kind:id`` or ``kind:id@content_hash``, or dicts
        ``{kind, id, ref?, content_hash?}`` (``kind`` is open: spec, adr, ticket, …).

        ``requirement`` (RFP-007 Executable Specifications) — shortcut: pass
        ``'SPEC-42#R3'`` (or bare ``'R3'`` with ``spec_path``) and apatch resolves the
        intent + ``spec:SPEC-42#R3@<hash>`` artifact from the spec file, so the session is
        anchored to that requirement and its status flips to ``attested`` after verify+attest.

        The lane argument selects an isolated session lane before opening it. For
        an older MCP process without this parameter, add a routing-only lane:<slug>
        artifact; it is consumed and not persisted as engineering evidence.

        While the session is active, TrustChain commits auto-stamp
        ``governed_session_id``, ``intent``, and ``artifacts`` onto ledger payloads.
        Read back via ``apatch_session_state`` → ``session.artifacts``.

        Strict SDD workspaces additionally require the exact owner-frozen
        ``sdd_contract`` and approved ``task_envelope``. ``actor_id`` identifies
        the implementation actor; this MCP command deliberately fixes its role to
        ``implementation`` and does not expose verifier or authority capabilities.
        """
        from apatch.runtime.runtime import MutationRuntime

        resolved_intent = intent
        resolved_artifacts: Optional[List[Any]] = [
            artifact
            for artifact in (artifacts or [])
            if not (
                isinstance(artifact, str)
                and artifact.lower().startswith("lane:")
            )
            and not (
                isinstance(artifact, dict)
                and str(artifact.get("kind") or "").lower() == "lane"
            )
        ] or None
        if requirement.strip():
            from apatch.spec import resolve_requirement

            res = resolve_requirement(target_dir, requirement, spec_path=spec_path or None)
            if not res.get("ok"):
                return res
            resolved_intent = intent or res["intent"]
            resolved_artifacts = (resolved_artifacts or []) + [res["artifact"]]

        return MutationRuntime(target_dir).open_session(
            resolved_intent,
            artifacts=resolved_artifacts,
            request_id=request_id or None,
            sdd_contract=sdd_contract,
            task_envelope=task_envelope,
            actor=(
                {"actor_id": actor_id.strip(), "role": "implementation"}
                if actor_id.strip()
                else None
            ),
        )

    @mcp.tool()
    def apatch_spec_status(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
    ) -> Dict[str, Any]:
        """RFP-007: per-requirement coverage of a specification, derived from the ledger.

        Parses ``SPEC.md`` (requirements are ``## R1``/``## FR-2``/``## NFR3`` headings) and,
        for each ``spec:<SPEC>#<REQ>`` artifact, derives state from TrustChain coverage:
        ``pending`` | ``in_progress`` | ``attested`` | ``stale`` (spec text changed after
        attestation). Status is never an agent-writable field — it is computed from attested
        mutations, so ``complete`` cannot be faked.

        Pass ``spec`` (id; auto-discovered at ``docs/specs/<id>.md`` or a remembered path) or
        an explicit ``spec_path``.
        """
        from apatch.spec import spec_status_enriched

        return spec_status_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None
        )

    @mcp.tool()
    def apatch_project_status(
        target_dir: str = ".",
        view: str = Field(
            default="compact",
            description="'compact' (default, token-bounded) or 'full' (complete DTO).",
        ),
    ) -> Dict[str, Any]:
        """RFP-020: unified read-only project status DTO (CLI/HTML/MD views).

        ``view='compact'`` (default) returns a token-bounded DTO: per-spec
        summaries with stale/pending requirement ids and conflict counts — full
        requirement rows and the conflict graph are omitted so the response never
        blows the token budget. Pass ``view='full'`` for the complete DTO.
        """
        from apatch.project_status import project_status_enriched

        return project_status_enriched(target_dir, view=view)

    @mcp.tool()
    def apatch_knowledge_graph(
        target_dir: str = ".",
        session_id: str = Field(
            default="",
            description="Governed session or checkpoint id; defaults to active session or latest diagnostics artifact.",
        ),
    ) -> Dict[str, Any]:
        """RFP-022 Phase 3: read-only session knowledge graph (Intent, Diagnostic, File, Requirement).

        Returns ``{schema_version, nodes[], edges[]}`` joined from diagnostics artifacts,
        session state, and apply_session chunk reports.
        """
        from apatch.knowledge_graph import knowledge_graph_enriched

        return knowledge_graph_enriched(target_dir, session_id=session_id)

    @mcp.tool()
    def apatch_spec_coverage(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
    ) -> Dict[str, Any]:
        """RFP-010 (R3): per-Rk coverage matrix with file-drift staleness.

        Returns ``{state, files[], drifted[], attested_at, session_id, op_ids[]}`` per
        requirement plus aggregate ``{total, attested, stale, pending}``. Deterministic
        from ledger file hashes — not agent-writable.
        """
        from apatch.spec_coverage import spec_coverage_enriched

        return spec_coverage_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None
        )

    @mcp.tool()
    def apatch_spec_interference(
        target_dir: str = ".",
        specs: List[str] = Field(
            default_factory=list,
            description="Two or more spec ids to compare for cross-spec conflicts.",
        ),
        level: int = Field(
            default=2,
            description="Max analysis depth: 1 file overlap, 2 patch-level WR/WW (default).",
        ),
        include_planned: bool = Field(
            default=True,
            description="Include planned needles from .apatch/specs/<ID>.json registry.",
        ),
        include_attested: bool = Field(
            default=True,
            description="Include attested mutation data from the TrustChain ledger.",
        ),
    ) -> Dict[str, Any]:
        """RFP-014 Phase 1: cross-spec interference detection (L1/L2, safe_order, risk_score)."""
        from apatch.spec_interference import spec_interference_enriched

        return spec_interference_enriched(
            target_dir,
            specs=list(specs) if specs else None,
            level=level,
            include_planned=include_planned,
            include_attested=include_attested,
        )

    @mcp.tool()
    def apatch_spec_schedule(
        target_dir: str = ".",
        specs: List[str] = Field(
            default_factory=list,
            description="Two or more spec ids; returns safe_order schedule view.",
        ),
        level: int = Field(
            default=2,
            description="Max analysis depth: 1 file overlap, 2 patch-level WR/WW (default).",
        ),
        include_planned: bool = Field(
            default=True,
            description="Include planned needles from .apatch/specs/<ID>.json registry.",
        ),
        include_attested: bool = Field(
            default=True,
            description="Include attested mutation data from the TrustChain ledger.",
        ),
        strategy: str = Field(
            default="safe",
            description="Schedule strategy: safe (topo sort) or risk_first (high-risk specs first for review).",
        ),
    ) -> Dict[str, Any]:
        """RFP-014 Phase 1.5: schedule view (safe_order + risk) over cross-spec interference."""
        from apatch.spec_interference import spec_schedule_enriched

        return spec_schedule_enriched(
            target_dir,
            specs=list(specs) if specs else None,
            level=level,
            include_planned=include_planned,
            include_attested=include_attested,
            strategy=strategy,
        )


    @mcp.tool()
    def apatch_spec_cross_verify(
        target_dir: str = ".",
        specs: List[str] = Field(
            default_factory=list,
            description="Two or more spec ids; empirical apply(source)→verify(victim) pairs.",
        ),
        include_planned: bool = Field(
            default=True,
            description="Apply needles from .apatch/specs/<ID>.json registry (default).",
        ),
        include_attested: bool = Field(
            default=False,
            description="Also load attested ledger needles as apply source (default false).",
        ),
        pairwise: bool = Field(
            default=True,
            description="Test each ordered (source, victim) pair where source != victim.",
        ),
    ) -> Dict[str, Any]:
        """RFP-014 Phase 2: sandbox cross-verify — apply source needles, run victim verify cmds, rollback."""
        from apatch.spec_cross_verify import spec_cross_verify_enriched

        return spec_cross_verify_enriched(
            target_dir,
            specs=list(specs) if specs else None,
            include_planned=include_planned,
            include_attested=include_attested,
            pairwise=pairwise,
        )


    @mcp.tool()
    def apatch_spec_run_multi(
        target_dir: str = ".",
        specs: List[str] = Field(
            default_factory=list,
            description="Two or more spec ids to run in schedule order.",
        ),
        requirements: Optional[Dict[str, Any]] = Field(
            default=None,
            description="Optional map spec_id → {Rk: {needles: [...]}} per spec.",
        ),
        cross_verify: bool = Field(
            default=False,
            description="Run Level-3 cross-verify before each next spec (RFP-014 Phase 3).",
        ),
        re_interference: bool = Field(
            default=True,
            description="Re-run L1/L2 interference on remaining specs after each success.",
        ),
        logs_path: str = Field(
            default="patches-spec-run-multi.jsonl",
            description="Base JSONL path for per-spec apply logs.",
        ),
        verify_deferred: bool = Field(
            default=True,
            description="Defer verify until apply_session completes per Rk.",
        ),
        execution_mode: str = Field(
            default="serial",
            description="serial (default) or shared_maintenance for one partitioned apply.",
        ),
        maintenance_verify: Optional[str] = Field(
            default=None,
            description="Optional aggregate verify command after exact requirement verifies.",
        ),
        verify_jobs: int = Field(
            default=8,
            description="Maximum parallel exact requirement verify jobs.",
        ),
        verify_timeout: float = Field(
            default=120.0,
            description="Timeout in seconds for each exact requirement verify.",
        ),
        maintenance_chunk_max_files: int = Field(
            default=100,
            description="Maximum files per shared-maintenance apply chunk.",
        ),
    ) -> Dict[str, Any]:
        """RFP-014 Phase 3: orchestrate N executable specs in one governed workflow.

        **When to use:** user asks to run/implement **several specs together** in safe
        interference order — not N× hand-rolled ``spec_run`` loops or staged JSONL.

        **Primary path:**

        ```text
        apatch_spec_schedule(specs=[...])   # optional preview
        apatch_spec_run_multi(
          specs=["SPEC-A", "SPEC-B"],
          requirements={"SPEC-A": {Rk: {needles: [...]}}, ...},
          cross_verify=true,
          execution_mode="shared_maintenance",  # exact SPEC#Rk → owned files
        )
        ```

        ``shared_maintenance`` performs one physical apply, runs every native Rk
        acceptance command in parallel, and creates one signed attestation whose
        ``artifact_files`` partition proves which exact files belong to each Rk.
        Each target file must have one owner; shared targets fail closed.

        Internally: ``spec_schedule`` → for each spec in ``order``: optional
        pre-apply ``spec_cross_verify(source, each_later_spec)`` using the exact inline/registry
        work list → ``spec_run`` with ``peer_specs=remaining``. Each explicitly
        supplied per-spec requirements map is a bounded work list, so unrelated open
        Rk are skipped and reported rather than causing ``MANIFEST_GAP``. Stops on
        first failure; rolls back every recorded chunk checkpoint in reverse order.

        **Do not:** pre-write ``manifests/*.run.json`` or ``patches.jsonl`` for
        multi-spec workflows — pass ``requirements`` inline (same as §3K).

        **Returns:** ``completed_specs[]``, ``order``, ``steps[]``, ``failed_at``,
        ``final_interference``. On failure: ``error_type`` (
        ``SPEC_SCHEDULE_BLOCKED`` | ``SPEC_CROSS_VERIFY_FAILED`` | from nested
        ``spec_run``).

        CLI: ``apatch spec run-multi --spec A --spec B``. Consumer playbook: §3L Phase 3.
        """
        from apatch.spec_run_multi import spec_run_multi_enriched

        return spec_run_multi_enriched(
            target_dir,
            specs=list(specs) if specs else None,
            requirements=requirements,
            cross_verify=cross_verify,
            re_interference=re_interference,
            logs_path=logs_path,
            verify_deferred=verify_deferred,
            execution_mode=execution_mode,
            maintenance_verify=maintenance_verify,
            verify_jobs=verify_jobs,
            verify_timeout=verify_timeout,
            maintenance_chunk_max_files=maintenance_chunk_max_files,
        )


    @mcp.tool()
    def apatch_spec_next(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
    ) -> Dict[str, Any]:
        """RFP-007: next open requirement of a spec + its acceptance check.

        Returns the first requirement still ``pending``/``in_progress``/``stale`` (document
        order), its ``verify`` command, and a ready-to-run
        ``apatch_session_start(requirement='SPEC-42#R3')``. Lets an agent resume "continue
        SPEC-42" without re-reading the whole document — it asks what is not yet attested.
        """
        from apatch.spec import spec_next_enriched

        return spec_next_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None
        )

    @mcp.tool()
    def apatch_spec_plan_lint(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        plan: Optional[Dict[str, Any]] = Field(
            default=None,
            description="Inline plan manifest dict (schema_version, spec, requirements).",
        ),
    ) -> Dict[str, Any]:
        """RFP-011: lint inline plan dict against SPEC.md requirements."""
        from apatch.spec_plan import spec_plan_lint_enriched

        return spec_plan_lint_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None, plan=plan
        )

    @mcp.tool()
    def apatch_spec_plan_register(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        plan: Optional[Dict[str, Any]] = Field(
            default=None,
            description="Inline plan manifest to register as plan:SPEC-X@vN.",
        ),
    ) -> Dict[str, Any]:
        """RFP-011: register signed plan artifact plan:SPEC-X@vN."""
        from apatch.spec_plan import register_plan_enriched

        return register_plan_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None, plan=plan
        )

    @mcp.tool()
    def apatch_spec_plan_diff(
        target_dir: str = ".",
        spec: str = "",
        from_version: int = Field(
            default=1,
            description="Older registered plan version number.",
        ),
        to_version: Optional[int] = Field(
            default=None,
            description="Newer version to compare; default latest registered.",
        ),
    ) -> Dict[str, Any]:
        """RFP-011: diff registered plan versions (decision_plan + execution_plan)."""
        from apatch.spec_plan import plan_diff_enriched

        return plan_diff_enriched(
            target_dir,
            spec=spec or None,
            from_version=from_version,
            to_version=to_version,
        )

    @mcp.tool()
    def apatch_spec_plan_show(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        version: str = Field(
            default="latest",
            description="Registered plan ref: latest, v3, or integer version.",
        ),
    ) -> Dict[str, Any]:
        """RFP-011 R6: load registered plan including decision_plan from .apatch/plans/."""
        from apatch.spec_plan import show_plan_enriched

        return show_plan_enriched(
            target_dir,
            spec=spec or None,
            spec_path=spec_path or None,
            version=version,
        )

    @mcp.tool()
    def apatch_spec_adherence(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        plan: str = Field(
            default="latest",
            description="Registered plan ref: latest or integer version number.",
        ),
    ) -> Dict[str, Any]:
        """RFP-012: plan-vs-fact adherence report per Rk."""
        from apatch.plan_adherence import spec_adherence_enriched

        return spec_adherence_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None, plan_ref=plan
        )

    @mcp.tool()
    def apatch_execute_next(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        requirement: str = "",
        dry_run: bool = False,
        finalize: bool = False,
        completion_summary: Optional[str] = Field(
            default=None,
            description="Explicit public summary of what was delivered; private session intent is never substituted.",
        ),
        needles: Optional[List[Dict[str, Any]]] = None,
        logs_path: str = "patches.jsonl",
        verify_deferred: bool = True,
        skip_lint: bool = False,
        check_dependencies: bool = True,
        governed_session_id: str = "",
        session_token: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        """RFP-008: execute the next (or given) spec requirement — governed cycle.

        Unit of work is ``Rk``, not JSONL. Flow: lint → deps → spec_next → session_start
        → optional needles (generate_batch → simulate → apply_session) → finalize
        (verify → attest → session_end). If needles mutate the active SPEC itself,
        execute_next reparses its current verify command after apply and rebinds
        attestation to the fresh requirement hash. Generation/simulation failures
        close sessions opened by that call because no source mutation occurred. Use
        ``dry_run=true`` for execution_plan only.
        """
        from apatch.spec_executor import execute_next_enriched

        return execute_next_enriched(
            target_dir,
            spec=spec or None,
            spec_path=spec_path or None,
            requirement=requirement or None,
            dry_run=dry_run,
            finalize=finalize,
            completion_summary=completion_summary,
            needles=needles,
            logs_path=logs_path,
            verify_deferred=verify_deferred,
            skip_lint=skip_lint,
            check_dependencies=check_dependencies,
            governed_session_id=governed_session_id or None,
            session_token=session_token or None,
            request_id=request_id or None,
        )

    @mcp.tool()
    def apatch_spec_run(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        requirements: Optional[Dict[str, Any]] = Field(
            default=None,
            description="Inline run manifest map Rk → {needles: [...]}; primary path without disk staging.",
        ),
        manifest_path: str = "",
        manifest: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        reset: bool = False,
        abort: bool = False,
        resume: bool = Field(
            default=True,
            description="Load .apatch/spec_run.json and continue from rk_index; use reset=true to discard.",
        ),
        chunk_rk_per_call: int = Field(
            default=0,
            description="Max pending requirements per MCP call; 0 means all pending Rk in one tick.",
        ),
        logs_path: str = "patches-spec-run.jsonl",
        verify_deferred: bool = Field(
            default=True,
            description="Defer verify until apply_session completes per Rk (default true — chunk verify rolls back patches).",
        ),
        skip_lint: bool = False,
        skip_rfp_coverage: bool = Field(
            default=False,
            description="Skip RFP→SPEC coverage gate (not recommended when ## RFP traceability present).",
        ),
        rfp: str = Field(
            default="",
            description="RFP id for coverage gate (default: infer from SPEC anchors).",
        ),
        check_dependencies: bool = True,
        allow_partial: bool = Field(
            default=False,
            description="Treat the inline requirements map as the work list: pending Rk without needles are skipped and reported (no MANIFEST_GAP). Subset batches are legal.",
        ),
        interference_check: bool = Field(
            default=False,
            description="Preflight L2 gate when peer_specs set (default ON if peer_specs non-empty). Blocks cycle, stale snapshot, or unattested predecessor in safe_order.",
        ),
        peer_specs: List[str] = Field(
            default_factory=list,
            description="Other spec ids for R4 ordering gate. Non-empty → predecessors in safe_order must be fully attested (SPEC_RUN_ORDER_BLOCKED).",
        ),
        request_id: str = "",
    ) -> Dict[str, Any]:
        """RFP-009: **run an entire executable spec in one MCP workflow** (SPEC-RUN-1).

        **Default when user says:** implement/continue/dogfood ``SPEC-X``, or ≥2 pending ``Rk``.
        Replaces hand-rolled ``spec_next`` loops + staged ``patches.jsonl``. Response includes
        ``protocol_contract`` and ``spec_run`` (no need to read apatch ``docs/RFP-009*.md``).

        **When to use:** needles known or from ``dry_run`` gap analysis. Prefer over N× ``execute_next``.

        **Primary path:** ``requirements={{Rk: {{needles: [...]}}}}`` inline — no disk
        staging. ``chunk_rk_per_call=0`` (default) runs all pending Rk in one tick;
        repeat while ``continue=true`` in the response.

        **dry_run:** ``pending[]``, ``gaps[]``, ``manifest_template`` in response (not a file).
        **resume / reset:** state in ``.apatch/spec_run.json``; ``MANIFEST_DRIFT`` if
        manifest changes without ``reset=true``.

        **Per Rk internally:** session_start → generate_batch → apply_session →
        verify_run (from SPEC.md) → attest → session_end. Consumer playbook: §3K.

        **Inter-spec gate (RFP-014 R4):** when ``peer_specs`` non-empty, blocks if L2
        cycle (``SPEC_INTERFERENCE_CYCLE``), stale registry (``SPEC_INTERFERENCE_STALE``),
        or predecessor in ``safe_order`` not fully attested (``SPEC_RUN_ORDER_BLOCKED``).
        Gate runs automatically when ``peer_specs`` is set; ``interference_check=true``
        forces gate even without peers.
        """
        from apatch.spec_run import spec_run_enriched

        return spec_run_enriched(
            target_dir,
            spec=spec or None,
            spec_path=spec_path or None,
            requirements=requirements,
            manifest_path=manifest_path or None,
            manifest=manifest,
            dry_run=dry_run,
            reset=reset,
            abort=abort,
            resume=resume,
            chunk_rk_per_call=chunk_rk_per_call,
            logs_path=logs_path,
            verify_deferred=verify_deferred,
            skip_lint=skip_lint,
            skip_rfp_coverage=skip_rfp_coverage,
            rfp=rfp or None,
            check_dependencies=check_dependencies,
            interference_check=interference_check,
            allow_partial=allow_partial,
            peer_specs=list(peer_specs) if peer_specs else None,
            request_id=request_id or None,
        )

    @mcp.tool()
    def apatch_spec_run_manifest_lint(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
        manifest_path: str = "",
        manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Lint a spec run manifest against ``docs/specs/SPEC-*.md``.

        Checks schema_version, spec id match, requirement keys vs SPEC, needle shape,
        and ``MANIFEST_GAP`` for pending Rk with empty ``needles``. Use before CI
        ``manifest_path`` runs or to validate inline ``requirements`` structure.
        """
        from apatch.spec_run import spec_run_manifest_lint_enriched

        return spec_run_manifest_lint_enriched(
            target_dir,
            spec=spec or None,
            spec_path=spec_path or None,
            manifest_path=manifest_path or None,
            manifest=manifest,
        )

    @mcp.tool()
    def apatch_spec_lint(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
    ) -> Dict[str, Any]:
        """Agent onboarding gate — **call this before any SPEC work** (RFP-007/023/024).

        **One call** when ``spec`` is set and ``passed: true``:

        - Format lint: H1 id, ``spec:`` artifact, per-Rk ``(verify:)``
        - ``rfp_lint`` + ``rfp_coverage`` if SPEC has ``## RFP traceability``
        - ``plan_scaffold`` + ``needles_scaffold`` + ``agent_next`` for pending Rk

        Standalone ``apatch_rfp_*`` / ``apatch_spec_needles_scaffold`` exist for CLI;
        IDE may not list them — use this tool. See ``docs/agent-onboarding.md``.

        ``passed`` false → fix errors; re-run. Does not auto-generate mutation needles.
        """
        from apatch.spec import spec_lint_enriched

        return spec_lint_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None
        )

    @mcp.tool()
    def apatch_rfp_lint(
        target_dir: str = ".",
        rfp: str = "",
        rfp_path: str = "",
    ) -> Dict[str, Any]:
        """RFP-023: lint RFP ``## Acceptance`` table (CLI / advanced).

        **Prefer** ``apatch_spec_lint(spec=…)`` — runs this automatically when SPEC
        has ``## RFP traceability``. Standalone tool may be absent in IDE tool list.
        """
        from apatch.rfp_coverage import rfp_lint_enriched

        return rfp_lint_enriched(
            target_dir, rfp=rfp or None, rfp_path=rfp_path or None
        )

    @mcp.tool()
    def apatch_rfp_spec_coverage(
        target_dir: str = ".",
        rfp: str = "",
        spec: str = "",
        specs: str = "",
        rfp_path: str = "",
        spec_path: str = "",
    ) -> Dict[str, Any]:
        """RFP-023: RFP Acceptance vs SPEC traceability (CLI / multi-spec aggregate).

        **Prefer** ``apatch_spec_lint(spec=…)`` for single-spec gate. Use ``specs``
        comma-list here for multi-spec aggregate (e.g. RFP-022 phase SPECs).
        """
        from apatch.rfp_coverage import rfp_spec_coverage_enriched

        return rfp_spec_coverage_enriched(
            target_dir,
            rfp=rfp or None,
            spec=spec or None,
            specs=specs or None,
            rfp_path=rfp_path or None,
            spec_path=spec_path or None,
        )

    @mcp.tool()
    def apatch_spec_needles_scaffold(
        target_dir: str = ".",
        spec: str = "",
        spec_path: str = "",
    ) -> Dict[str, Any]:
        """RFP-024: needles/plan scaffold (CLI / optional).

        **Prefer** ``apatch_spec_lint(spec=…)`` — embeds ``plan_scaffold`` when
        ``passed``. Returns ``target_files``, ``needle_templates``, schema v2 plan.
        Does not LLM-generate ``find_text``/``replace_text``.
        """
        from apatch.spec_needles_scaffold import spec_needles_scaffold_enriched

        return spec_needles_scaffold_enriched(
            target_dir, spec=spec or None, spec_path=spec_path or None
        )

    @mcp.tool()
    def apatch_spec_scaffold(
        target_dir: str = ".",
        rfp: str = Field(default="", description="RFP id (RFP-X) or path whose Acceptance table seeds the requirements."),
        spec: str = Field(default="", description="New SPEC id to scaffold (e.g. SPEC-FOO-1)."),
        out_path: str = Field(default="", description="Write the skeleton here (relative to target_dir); empty = return markdown only."),
        mandatory_only: bool = Field(default=False, description="Only MUST-level acceptance rows."),
    ) -> Dict[str, Any]:
        """RFP-023 (authoring side): scaffold a SPEC.md skeleton from an RFP Acceptance table.

        Emits one ``## Rk`` stub per acceptance row + an ``## R0 RFP traceability gate`` that
        maps every acceptance id to its Rk \u2014 contract-complete by construction (0 coverage
        gaps). You fill the ``(verify:)`` commands instead of hand-authoring the whole checklist
        + traceability. The inverse of ``apatch_rfp_spec_coverage`` (which CHECKS an existing
        spec); use this to START one from the contract."""
        from apatch.rfp_coverage import scaffold_spec_from_rfp_workspace
        from apatch.session_state import enrich_tool_response

        res = scaffold_spec_from_rfp_workspace(
            target_dir, rfp=rfp, spec_id=spec, out_path=out_path or None,
            mandatory_only=mandatory_only)
        return enrich_tool_response("apatch_spec_scaffold", res, target_dir=target_dir)

    @mcp.tool()
    def apatch_session_end(
        target_dir: str = ".",
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Close governed session — **required** after mass refactor.

        Purges session EPHEMERAL JSONL under ``.apatch/tmp/<session_id>/``, releases registry
        leases, clears ``write_lease.json``. Always call after attest or rollback."""
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
            session_token=session_token or None,
            enforce_binding=True,
        ).close_session()

    @mcp.tool()
    def apatch_sdd_verify(
        target_dir: str = ".",
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Execute the exact frozen SDD judge bound to one governed session.

        This fixed-purpose surface accepts no command, result, counts, perspectives
        or caller-selected verifier role. Core loads the hash-bound contract and
        task envelope, executes argv without a shell, performs required reversible
        falsification, and atomically records content-safe evidence.
        """
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
            session_token=session_token or None,
            enforce_binding=True,
        ).sdd_verify()

    @mcp.tool()
    def apatch_verify_run(
        target_dir: str = ".",
        verify: Optional[Union[str, List[str]]] = None,
        semantic: bool = False,
        notarization: bool = False,
        pipeline_manifest: Optional[str] = None,
        dry_run: bool = False,
        rules_path: Optional[str] = None,
        since: str = "HEAD",
        staged: bool = False,
        working_tree: bool = False,
        baseline: str = "off",
        allowed_failures: Optional[List[str]] = None,
        async_mode: bool = False,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Unified verify: shell command (default from doctor), semantic, notarization, or pipeline.

        ``verify`` accepts a shell string OR an argv list — argv runs without a shell,
        so quoting just works: ``["pytest", "-k", "not slow", "--deselect", "tests/x.py::t"]``.
        Baseline-aware verify (pre-existing red must not block): ``baseline='capture'``
        BEFORE apply snapshots failing tests to ``.apatch/verify_baseline.json``;
        ``baseline='compare'`` after apply passes when no NEW failures vs the snapshot.
        ``allowed_failures``: failing node ids (or substrings) that never block.
        ``async_mode=True`` starts a background job; poll ``apatch_verify_status(job_id=…)``.
        Long suites may be forced async via ``APATCH_VERIFY_SYNC_MAX_SEC`` even when ``async_mode=False``.
        """
        from apatch.runtime.runtime import MutationRuntime

        result = MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
            session_token=session_token or None,
            enforce_binding=True,
        ).verify_run(
            verify=verify,
            semantic=semantic,
            notarization=notarization,
            pipeline_manifest=pipeline_manifest,
            dry_run=dry_run,
            rules_path=rules_path,
            since=since,
            staged=staged,
            working_tree=working_tree,
            baseline=baseline,
            allowed_failures=allowed_failures,
            async_mode=async_mode,
        )
        # SCIP Phase 2 advisory (RFP-033 A33-G): attach cross-file reference impact when
        # a .scip index is present \u2014 never blocks or marks anything stale; no-op otherwise.
        if isinstance(result, dict):
            from apatch.scip_producer import scip_impact_workspace
            # scip-only on the verify hot path (allow_native=False) \u2014 the native
            # resolver is on-demand via apatch_scip, not rebuilt on every verify.
            _impact = scip_impact_workspace(target_dir, since=since, allow_native=False)
            if _impact.get("model_present"):
                result["scip_impact"] = _impact
        return result

    @mcp.tool()
    def apatch_scip(
        action: str = Field(default="impact", description="'index' (produce .scip via scip-python) | 'impact' (advisory cross-file reference impact)."),
        since: str = Field(default="HEAD", description="impact: git ref to diff from for changed symbols."),
        spec: str = Field(default="", description="impact: limit attested-requirement anchors to one SPEC id."),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """RFP-033 Phase 2: SCIP cross-file reference impact (advisory, never blocks).

        * ``action='index'`` \u2014 run scip-python out of band to produce
          ``.apatch/scip/index.scip`` (graceful no-op when scip-python is not installed).
        * ``action='impact'`` \u2014 which ATTESTED requirements reference a symbol changed
          since ``since``, via the ingested .scip graph. Advisory only \u2014 a warning,
          never a staleness trigger or block; ``index_present: False`` when no index exists."""
        from apatch.scip_producer import produce_scip_index, scip_impact_workspace
        from apatch.session_state import enrich_tool_response

        if action == "index":
            pr = produce_scip_index(target_dir)
            # 'ok' from the producer is a DOMAIN signal (did indexing succeed), not an
            # operational failure (scip-python absent is a valid outcome) \u2014 keep the tool
            # operationally ok:True so it is never read as a session failure (cf. REC-7855).
            res = {"ok": True, "produced": bool(pr.get("ok")),
                   **{k: v for k, v in pr.items() if k != "ok"}}
        elif action == "impact":
            res = scip_impact_workspace(target_dir, since=since, spec=spec or None)
        else:
            res = {"ok": True, "error": f"unknown action {action!r}", "actions": ["index", "impact"]}
        return enrich_tool_response("apatch_scip", res, target_dir=target_dir)

    @mcp.tool()
    def apatch_attestation_export(
        target_dir: str = ".",
        out_path: str = ".apatch/audit_bundle.json",
        event_limit: int = 500,
    ) -> Dict[str, Any]:
        """Export session + attestation + domain events as audit bundle JSON."""
        from apatch.runtime.runtime import MutationRuntime

        out_abs = _abs_in_workspace(target_dir, out_path)
        return MutationRuntime(target_dir).export_attestation(out_abs, event_limit=event_limit)

    @mcp.tool()
    def apatch_events_tail(target_dir: str = ".", limit: int = 20) -> Dict[str, Any]:
        """Tail append-only domain event stream (.apatch/events.jsonl)."""
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(target_dir).events_tail(limit=limit)

    @mcp.tool()
    def apatch_timesheet(target_dir: str = ".", by: str = "identity",
                         since: str = "", until: str = "", agent: str = "",
                         project: str = "", idle_gap: int = 0,
                         ramp_up: int = 0,
                         do_verify: bool = False,
                         include_audit: bool = False) -> Dict[str, Any]:
        """Per-identity, cross-project contribution timesheet (read-only, RFP-026).

        Aggregates signed ContributionEvent receipts by identity/project/spec/day;
        do_verify re-derives volume from the ledger to flag tamper. Never mutates."""
        from apatch.timesheet import run_timesheet

        out = run_timesheet(
            target_dir=target_dir, by=by, since=since or None, until=until or None,
            agent=agent or None, project=project or None,
            idle_gap=idle_gap or None, ramp_up=ramp_up or None, fmt="json",
            do_verify=do_verify, include_audit=include_audit,
        )
        return out if isinstance(out, dict) else {"report": out}

    @mcp.tool()
    def apatch_resume_session(
        target_dir: str = ".",
        governed_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Recover one exact governed session and rotate its capability.

        When more than one lane is active, governed_session_id is required.
        The returned session_capability supersedes every earlier token.
        """
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(
            target_dir,
            session_id=governed_session_id,
        ).resume_session()

    @mcp.tool()
    def apatch_recover(
        governed_session_id: str,
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Recover one exact session: reconcile, stale owned lease, capability, cleanup.

        Returns ``recovery=resumed|closed|blocked_foreign``. Never steals a live
        foreign lease and never guesses the newest active lane.
        """
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
        ).recover_session()

    @mcp.tool()
    def apatch_noop_attest(target_dir: str = ".", covered_by: str = "",
                           message: str = "") -> Dict[str, Any]:
        """RFP-027: attest a requirement covered by another Rk's mutation (no marker file).

        covered_by: comma-separated Rk ids (e.g. 'R1,R4') that satisfy this requirement."""
        from apatch.runtime.runtime import MutationRuntime

        cov = [c.strip() for c in str(covered_by).split(",") if c.strip()]
        return MutationRuntime(target_dir).noop_attest(cov, message=message or None)

    @mcp.tool()
    def apatch_asset_summary(target_dir: str = ".") -> Dict[str, Any]:
        """Avatar Compiler — deterministic asset_summary over governed work
        (read-only, RFP-025): attested artifacts/specs/methodology. No source, no economics."""
        from apatch.avatar_compiler import build_asset_summary

        return build_asset_summary(target_dir)

    @mcp.tool()
    def apatch_avatar_episodes(
        target_dir: str = ".",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Observed WorkEpisodes and explicit capability-exclusion reasons."""
        from apatch.episode import build_episodes

        episodes = build_episodes(target_dir)
        selected = episodes if limit < 0 else episodes[:limit]
        return {
            "count": len(episodes),
            "returned": len(selected),
            "episodes": selected,
        }

    @mcp.tool()
    def apatch_avatar_capabilities(target_dir: str = ".") -> Dict[str, Any]:
        """Evidence-linked capability estimates with uncertainty intervals."""
        from apatch.capability import build_capabilities

        estimates = build_capabilities(target_dir)
        return {"count": len(estimates), "capability_estimates": estimates}

    @mcp.tool()
    def apatch_avatar_evidence_export(target_dir: str = ".") -> Dict[str, Any]:
        """Build the signed, content-safe CapabilityEvidence v2 distillate."""
        from apatch.avatar_evidence import build_evidence_bundle

        return build_evidence_bundle(target_dir)

    @mcp.tool()
    def apatch_avatar_evidence_sync(
        target_dir: str = ".",
        tracker_url: str = Field(
            default="",
            description="HC Tracker base URL; empty uses APATCH_HC_TRACKER_URL.",
        ),
    ) -> Dict[str, Any]:
        """Queue and deliver Avatar state via a purpose-bound service credential."""
        from apatch.avatar_delivery import sync_avatar_state

        return sync_avatar_state(
            target_dir,
            base_url=tracker_url or None,
        )

    @mcp.tool()
    def apatch_governed_work_configure(
        platform_url: str = Field(
            description="TrustChain signed-service base URL; production uses https://clients.trust-chain.ai.",
        ),
        client_id: str = Field(
            description="Platform subject alias authorized for the ProjectGroup.",
        ),
        binding_authority_keys: Dict[str, str] = Field(
            description="Trusted Platform binding-authority key ids mapped to Ed25519 public keys.",
        ),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Configure TrustChain governed work without accepting or storing a secret."""
        from apatch.governed_work_mcp import configure_governed_work

        return configure_governed_work(
            target_dir,
            platform_url=platform_url,
            client_id=client_id,
            binding_authority_keys=binding_authority_keys,
        )

    @mcp.tool()
    def apatch_governed_work_transition_endpoint(
        expected_platform_url: str = Field(
            description="Exact currently configured signed-service base URL.",
        ),
        platform_url: str = Field(
            description="New signed-service base URL; production uses https://clients.trust-chain.ai.",
        ),
        idempotency_key: str = Field(
            description="Stable 16-200 character transition key for crash-safe replay.",
        ),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """CAS-transition governed-work endpoint without changing identity or authority."""
        from apatch.governed_work_mcp import transition_governed_work_endpoint

        return transition_governed_work_endpoint(
            target_dir,
            expected_platform_url=expected_platform_url,
            platform_url=platform_url,
            idempotency_key=idempotency_key,
        )

    @mcp.tool()
    def apatch_governed_work_prepare_change(
        tenant_id: str = Field(
            description="TrustChain tenant id pinned into the Change.",
        ),
        project_group_id: str = Field(
            description="Exact TrustChain ProjectGroup id owning the work.",
        ),
        work_program_id: str = Field(
            description="Exact immutable TrustChain WorkProgram id.",
        ),
        work_program_hash: str = Field(
            description="Canonical hash of the pinned WorkProgram.",
        ),
        spec_id: str = Field(
            description="APatch SPEC id used as the execution contract.",
        ),
        purpose: str = Field(
            description="Private work intent; only its purpose hash leaves the workspace.",
        ),
        target_dir: str = ".",
        requirement_ids: Optional[List[str]] = None,
        spec_path: Optional[str] = None,
        context_release_id: Optional[str] = Field(
            default=None,
            description="Optional immutable TrustChain ContextRelease id.",
        ),
        context_release_manifest_hash: Optional[str] = Field(
            default=None,
            description="Canonical manifest hash of the optional ContextRelease.",
        ),
        queue_source_binding: bool = Field(
            default=True,
            description="Durably queue a ProjectSourceBinding admission request.",
        ),
    ) -> Dict[str, Any]:
        """Sign an exact Change and durably queue its source-binding request."""
        from apatch.governed_work_mcp import prepare_governed_change

        return prepare_governed_change(
            target_dir,
            tenant_id=tenant_id,
            project_group_id=project_group_id,
            work_program_id=work_program_id,
            work_program_hash=work_program_hash,
            spec_id=spec_id,
            purpose=purpose,
            requirement_ids=requirement_ids,
            spec_path=spec_path,
            context_release_id=context_release_id,
            context_release_manifest_hash=context_release_manifest_hash,
            queue_source_binding=queue_source_binding,
        )

    @mcp.tool()
    def apatch_governed_work_store_binding(
        binding: Dict[str, Any] = Field(
            description="Exact signed trustchain.project-source-binding.v1 document.",
        ),
        target_dir: str = ".",
        status: Optional[Dict[str, Any]] = Field(
            default=None,
            description="Optional exact Platform binding-status projection.",
        ),
    ) -> Dict[str, Any]:
        """Verify and immutably store an exact Platform ProjectSourceBinding."""
        from apatch.governed_work_mcp import store_governed_source_binding

        return store_governed_source_binding(
            target_dir,
            binding=binding,
            status=status,
        )

    @mcp.tool()
    def apatch_governed_work_build_evidence(
        binding_id: str = Field(
            description="Verified local ProjectSourceBinding id for this evidence.",
        ),
        target_dir: str = ".",
        contribution_store_dir: Optional[str] = Field(
            default=None,
            description="Optional contribution store used to resolve referenced facts.",
        ),
        queue_for_admission: bool = Field(
            default=True,
            description="Durably queue the evidence bundle for Platform admission.",
        ),
    ) -> Dict[str, Any]:
        """Build signed source-bound evidence and durably queue its admission."""
        from apatch.governed_work_mcp import build_governed_evidence

        return build_governed_evidence(
            target_dir,
            binding_id=binding_id,
            contribution_store_dir=contribution_store_dir,
            queue_for_admission=queue_for_admission,
        )

    @mcp.tool()
    def apatch_governed_work_sync(
        target_dir: str = ".",
        platform_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deliver governed-work outbox entries with signed service requests."""
        from apatch.governed_work_mcp import sync_governed_work

        return sync_governed_work(target_dir, platform_url=platform_url)

    @mcp.tool()
    def apatch_governed_work_retire_outbox(
        entry_id: str = Field(
            description="Rejected immutable outbox entry to stop retrying.",
        ),
        superseded_by_entry_id: str = Field(
            description="Same-scope replacement entry with a validated Platform ACK.",
        ),
        target_dir: str = ".",
    ) -> Dict[str, Any]:
        """Retire a request only after an acknowledged replacement exists."""
        from apatch.governed_work_mcp import retire_governed_work_outbox

        return retire_governed_work_outbox(
            target_dir,
            entry_id=entry_id,
            superseded_by_entry_id=superseded_by_entry_id,
        )

    @mcp.tool()
    def apatch_governed_work_status(
        target_dir: str = ".",
        tenant_id: str = Field(
            default="",
            description="TrustChain tenant id for an optional Platform status refresh.",
        ),
        project_group_id: str = Field(
            default="",
            description="Exact ProjectGroup id for an optional Platform status refresh.",
        ),
        work_program_id: str = Field(
            default="",
            description="Exact WorkProgram id for an optional Platform status refresh.",
        ),
        refresh_platform: bool = Field(
            default=False,
            description="Fetch the four independent Platform work states when configured.",
        ),
    ) -> Dict[str, Any]:
        """Read independent local, delivery and optional Platform work states."""
        from apatch.governed_work_mcp import governed_work_status

        return governed_work_status(
            target_dir,
            tenant_id=tenant_id,
            project_group_id=project_group_id,
            work_program_id=work_program_id,
            refresh_platform=refresh_platform,
        )

    @mcp.tool()
    def apatch_work_assets(target_dir: str = ".", query: str = "", limit: int = -1) -> Dict[str, Any]:
        """WorkAsset candidates — read-only metadata/proof index (RFP-031)."""
        from apatch.work_assets import list_work_assets

        if not isinstance(limit, int):
            limit = -1
        return list_work_assets(target_dir, query=query or "", limit=None if limit < 0 else limit)

    @mcp.tool()
    def apatch_work_asset_show(
        target_dir: str = ".",
        asset_id: str = Field(default="", description="WorkAsset candidate id to show."),
    ) -> Dict[str, Any]:
        """Show one WorkAsset candidate by id (read-only)."""
        from apatch.work_assets import show_work_asset

        return show_work_asset(target_dir, asset_id=asset_id)

    @mcp.tool()
    def apatch_work_asset_search(target_dir: str = ".", query: str = "", limit: int = 10) -> Dict[str, Any]:
        """Search WorkAsset metadata without exposing source content."""
        from apatch.work_assets import search_work_assets

        if not isinstance(limit, int):
            limit = 10
        return search_work_assets(target_dir, query=query or "", limit=limit)

    @mcp.tool()
    def apatch_work_asset_export(target_dir: str = ".", query: str = "") -> Dict[str, Any]:
        """Export metadata-only WorkAsset bundle for consumer systems."""
        from apatch.work_assets import export_work_assets

        return export_work_assets(target_dir, query=query or "")

    @mcp.tool()
    def apatch_work_asset_export_schema() -> Dict[str, Any]:
        """JSON Schema for apatch WorkAsset export v1."""
        from apatch.work_assets import work_asset_export_schema

        return work_asset_export_schema()

    @mcp.tool()
    def apatch_work_asset_suggest(
        target_dir: str = ".",
        intent: str = Field(default="", description="New task intent to match proven methods against."),
        limit: int = 3,
    ) -> Dict[str, Any]:
        """Avatar Recall (AUC-1 / RFP-031 A31-E): suggest the owner's proven,
        recallable WorkAssets for a new task — explainable reasons + guarded next
        step. Read-only; never mutates outside a normal governed session."""
        from apatch.work_asset_suggest import suggest_work_assets

        if not isinstance(limit, int):
            limit = 3
        return suggest_work_assets(target_dir, intent=intent or "", limit=limit)

    @mcp.tool()
    def apatch_work_asset_recall(
        target_dir: str = ".",
        asset_id: str = Field(default="", description="Recallable WorkAsset id to bundle."),
        intent: str = Field(default="", description="Optional task intent for why_fit reasons."),
    ) -> Dict[str, Any]:
        """RecallBundle (AUC-1 §4): compact money-free distillate of one proven
        method — why_fit / method (integrity-checked against the signed pin) /
        context / evidence / rights / guarded next_action. Read-only."""
        from apatch.work_asset_suggest import build_recall_bundle

        return build_recall_bundle(target_dir, asset_id=asset_id, intent=intent or "")

    @mcp.tool()
    def apatch_work_asset_use(
        target_dir: str = ".",
        spec_id: str = Field(default="", description="Spec id of the used WorkAsset (stable anchor)."),
        verify_command: str = Field(default="", description="Verification command that was run."),
        verify_ok: bool = Field(default=True, description="Verification outcome."),
        adaptation: str = Field(default="unchanged", description="'unchanged' or 'adapted'."),
        proof_ref: str = Field(default="", description="Attestation op id backing this use."),
        session_id: str = Field(default="", description="Governed session id."),
    ) -> Dict[str, Any]:
        """Record a signed WorkAsset use event after verify (AUC-1 learn edge /
        RFP-031 A31-F): reuse counters grow ONLY from these ledger records. The
        only write in the recall family — commits one signed event, mutates no files."""
        from apatch.work_asset_lifecycle import record_work_asset_use

        return record_work_asset_use(
            target_dir, spec_id,
            verification={"command": verify_command, "ok": bool(verify_ok)},
            adaptation=adaptation or "unchanged",
            proof_ref=proof_ref or None,
            session_id=session_id or None,
        )

    @mcp.tool()
    def apatch_attestation_show(target_dir: str = ".") -> Dict[str, Any]:
        """TrustChain attestation: mode, HEAD, recent domain events (not Verification)."""
        from apatch.runtime.attestation import build_attestation_view
        from apatch.session_state import enrich_tool_response

        return enrich_tool_response(
            "apatch_attestation_show", build_attestation_view(target_dir), target_dir=target_dir
        )

    @mcp.tool()
    def apatch_attest(
        target_dir: str = ".",
        message: Optional[str] = None,
        governed_session_id: str = "",
        session_token: str = "",
    ) -> Dict[str, Any]:
        """Commit TrustChain attestation for the governed session (RFP-004).

        Signed payload includes session ``intent``, ``session_id``, and ``artifacts[]``
        when bound at ``apatch_session_start``. Use ``apatch_trustchain_coverage`` after
        attest to verify artifact → mutation → attestation chain.
        """
        from apatch.runtime.runtime import MutationRuntime

        return MutationRuntime(
            target_dir,
            session_id=governed_session_id or None,
            session_token=session_token or None,
            enforce_binding=True,
        ).attest(message=message)

    @mcp.tool()
    def apatch_verify_status(
        target_dir: str = ".",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Unified verification status, or poll an async verify job when ``job_id`` is set."""
        from apatch.runtime.runtime import MutationRuntime
        from apatch.session_state import enrich_tool_response

        rt = MutationRuntime(target_dir)
        if job_id:
            return rt.verify_job_status(job_id)
        return enrich_tool_response(
            "apatch_verify_status",
            rt.verification_status(),
            target_dir=target_dir,
        )

    @mcp.tool()
    def apatch_simulate(
        target_dir: str = ".",
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
        """R58 preflight: risk map, rollback probability, execution graph — no disk writes."""
        from apatch.workflows import WorkflowError, simulate_workspace

        try:
            return simulate_workspace(
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
        except WorkflowError as e:
            return {"ok": False, "dry_run": True, "error": str(e)}

    @mcp.tool()
    def apatch_commit_attested(
        target_dir: str = ".",
        governed_session_id: Optional[str] = None,
        session_ids: Optional[List[str]] = Field(
            default=None,
            description="Exact completed governed session ids whose signed current files may be committed.",
        ),
        message: str = "",
        push: bool = Field(
            default=False,
            description="Push the created exact commit; false leaves it local.",
        ),
        remote: str = Field(
            default="origin",
            description="Remote used only for the first push when the branch has no upstream.",
        ),
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Commit/push only files whose current hashes belong to explicit attested sessions."""
        from apatch.workflows import commit_attested_workspace

        return commit_attested_workspace(
            target_dir,
            governed_session_id=governed_session_id,
            session_ids=session_ids,
            message=message,
            push=push,
            remote=remote,
            dry_run=dry_run,
        )

    @mcp.tool()
    def apatch_verify_notarization(
        target_dir: str = ".",
        staged: bool = True,
        working_tree: bool = False,
        rebuild_index: bool = False,
    ) -> Dict[str, Any]:
        """Block unnotarized changes: staged files must match TrustChain proof index."""
        from apatch.workflows import verify_notarization_workspace

        return verify_notarization_workspace(
            target_dir,
            staged=staged,
            working_tree=working_tree,
            rebuild_index=rebuild_index,
        )

    @mcp.tool()
    def apatch_verify_anchor(
        root_ca: Optional[str] = None,
        intermediate: Optional[str] = None,
        leaf_cert: Optional[str] = None,
        signer_key: Optional[str] = None,
        registry_base: Optional[str] = None,
        agent_id: Optional[str] = None,
        crl: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify apatch's signing identity chains to a pinned TrustChain root (RFP-005 §5.3). PEM paths/literals or registry_base+agent_id; falls back to APATCH_* env."""
        from apatch.trust_identity import verify_anchor

        return verify_anchor(
            root_ca=root_ca,
            intermediate=intermediate,
            leaf_cert=leaf_cert,
            signer_key=signer_key,
            registry_base=registry_base,
            agent_id=agent_id,
            crl=crl,
        )

    @mcp.tool()
    def apatch_verify_inclusion(
        target_dir: str = ".",
        platform_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Prove recorded ledger ops are included in the external append-only log (RFP-005 §5.10). Fetches public Merkle inclusion proofs and verifies the audit path; missing/inconsistent fails. Opt-in (needs APATCH_PLATFORM_URL + recorded pushes)."""
        from apatch.inclusion import verify_inclusion

        return verify_inclusion(target_dir, base_url=platform_url)

    @mcp.tool()
    def apatch_trust_enroll(
        invitation: str,
        platform_url: str,
        out_dir: str = ".apatch/identity",
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Enroll apatch as a TrustChain agent (bridges to `tc cert request`). Saves agent.key/agent.crt/root-ca.pem; returns env vars to export."""
        from apatch.trust_identity import enroll_agent

        return enroll_agent(
            invitation=invitation,
            platform_url=platform_url,
            out_dir=out_dir,
            agent_id=agent_id,
        )

    @mcp.tool()
    def apatch_policy_sign(target_dir: str = ".") -> Dict[str, Any]:
        """Sign the monitor config (sandbox/enforcement/hooks) with the enrolled identity → .apatch/policy.lock.json. Makes out-of-channel tampering detectable (RFP-005 §0.3). Requires APATCH_AGENT_ID + APATCH_AGENT_KEY."""
        from apatch.policy_lock import sign_policy

        return sign_policy(target_dir)

    @mcp.tool()
    def apatch_policy_verify(target_dir: str = ".") -> Dict[str, Any]:
        """Verify the signed policy lock: signature valid and no config drift. Reports drift (added/removed/changed) and whether the signer is root-anchored (RFP-005 §0.3)."""
        from apatch.policy_lock import verify_policy

        return verify_policy(target_dir)

    @mcp.tool()
    def apatch_natives_check(directory: str) -> Dict[str, Any]:
        """Scan C++ directory for duplicate register_native() calls."""
        from apatch.workflows import natives_check

        return natives_check(directory)

    @mcp.tool()
    def apatch_compile(
        file_path: str,
        target_dir: str = ".",
        out_dir: Optional[str] = None,
        dry_run: bool = False,
        no_trustchain: bool = False,
    ) -> Dict[str, Any]:
        """Compile Markdown: inject semantic IDs and export knowledge_map.json."""
        from apatch.workflows import compile_markdown_file

        abs_file = _abs_in_workspace(target_dir, file_path)
        abs_out = None
        if out_dir:
            abs_out = out_dir if os.path.isabs(out_dir) else _abs_in_workspace(target_dir, out_dir)
        return compile_markdown_file(
            abs_file,
            out_dir=abs_out,
            dry_run=dry_run,
            no_trustchain=no_trustchain,
        )

    @mcp.tool()
    def apatch_suggest_until(
        file_path: str,
        start_marker: str,
        target_dir: str = ".",
    ) -> List[Dict[str, Any]]:
        """Suggest until-boundary candidates forward from start marker."""
        from apatch.workflows import suggest_until_for_file

        abs_file = _abs_in_workspace(target_dir, file_path)
        return suggest_until_for_file(abs_file, start_marker)

    @mcp.tool()
    def apatch_arch_check(
        target_dir: str = ".",
        rules_path: Optional[str] = None,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Architecture drift check (forbidden imports, layers, service rules)."""
        from apatch.workflows import arch_check_workspace

        try:
            return arch_check_workspace(target_dir, rules_path=rules_path, since=since)
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            return {"ok": False, "error": str(e), "violations": []}

    @mcp.tool()
    def apatch_impact(
        target: str,
        target_dir: str = ".",
        kind: Optional[str] = None,
        depth: int = 1,
        include_tests: bool = True,
    ) -> Dict[str, Any]:
        """Dependency impact graph for a file or symbol."""
        from apatch.workflows import impact_analysis

        return impact_analysis(
            target,
            target_dir,
            kind=kind,
            depth=depth,
            include_tests=include_tests,
        )

    @mcp.tool()
    def apatch_db_check(
        target_dir: str = ".",
        profile: str = "sqlalchemy",
        since: str = "HEAD",
    ) -> Dict[str, Any]:
        """Check git diff: model files changed but no migration file."""
        from apatch.workflows import db_check_workspace

        try:
            return db_check_workspace(target_dir, profile=profile, since=since)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    @mcp.tool()
    def apatch_db_revision(
        target_dir: str = ".",
        profile: str = "sqlalchemy",
        message: str = "apatch revision",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Draft migration via stack CLI (alembic / makemigrations / prisma)."""
        from apatch.workflows import db_revision_workspace

        return db_revision_workspace(
            target_dir, profile=profile, message=message, dry_run=dry_run
        )

    @mcp.tool()
    def apatch_db_safety(
        target_dir: str = ".",
        profile: str = "sqlalchemy",
        since: Optional[str] = "HEAD",
        scan_all: bool = False,
    ) -> Dict[str, Any]:
        """Static scan migration files for risky SQL/ops."""
        from apatch.workflows import db_safety_workspace

        return db_safety_workspace(
            target_dir,
            profile=profile,
            since=None if scan_all else since,
        )

    @mcp.tool()
    def apatch_db_run(
        manifest_path: str,
        target_dir: str = ".",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Run db-refactor JSON manifest (apply, db check, revision, verify phases)."""
        from apatch.workflows import db_run_manifest

        try:
            return db_run_manifest(manifest_path, target_dir, dry_run=dry_run)
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e), "phases": []}

    @mcp.tool()
    def apatch_refactor_run(
        manifest_path: str,
        target_dir: str = ".",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Run refactor-bundle manifest (rename_symbol: impact → generate → apply)."""
        from apatch.workflows import refactor_run_manifest

        try:
            return refactor_run_manifest(manifest_path, target_dir, dry_run=dry_run)
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e), "phases": []}

    @mcp.tool()
    def apatch_index_build(target_dir: str = ".") -> Dict[str, Any]:
        """Build project memory index (.apatch/project_index.json)."""
        from apatch.workflows import index_build_workspace

        return index_build_workspace(target_dir)

    @mcp.tool()
    def apatch_index_query(target_dir: str, query: str) -> Dict[str, Any]:
        """Query symbols/routes/usages/migrations/ADRs from project index."""
        from apatch.workflows import index_query_workspace

        try:
            return index_query_workspace(target_dir, query)
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}

    @mcp.tool()
    def apatch_trustchain_history(
        target_dir: str = ".",
        query: str = "",
        artifact: str = "",
        limit: int = 50,
    ) -> Dict[str, Any]:
        """List intent/artifact checkpoints from the TrustChain ledger.

        Filter by ``artifact`` (``kind:id``, e.g. ``spec:SPEC-42``) or legacy substring
        ``query`` (intent, adr, manifest text). Entries include ``artifacts[]`` when present.
        """
        from apatch.workflows import trustchain_intent_history_workspace

        return trustchain_intent_history_workspace(
            target_dir,
            query=query or None,
            artifact=artifact or None,
            limit=limit,
        )

    @mcp.tool()
    def apatch_trustchain_coverage(
        target_dir: str = ".",
        artifact: str = "",
        op_id: str = "",
    ) -> Dict[str, Any]:
        """Artifact traceability matrix (RFP-006 §6.2) and op_id reverse lookup.

        With ``artifact`` (``kind:id``): returns intents, mutations, attestations linked to
        that artifact; ``coverage.complete`` is true when both mutation and attestation exist.

        Without ``artifact``: summary for all artifacts seen in the ledger.

        With ``op_id``: reverse map one ledger operation id → ``role``, ``artifacts[]``,
        ``action``, ``signature`` (mutations inherit artifact context from the active session
        window when not stamped directly).
        """
        from apatch.workflows import trustchain_coverage_workspace

        return trustchain_coverage_workspace(
            target_dir,
            artifact=artifact or None,
            op_id=op_id or None,
        )

    @mcp.tool()
    def apatch_pipeline_run(
        manifest_path: str,
        target_dir: str = ".",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Run unified engineering-pipeline manifest (R52)."""
        from apatch.workflows import pipeline_run_manifest

        try:
            return pipeline_run_manifest(manifest_path, target_dir, dry_run=dry_run)
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e), "phases": []}

    @mcp.tool()
    def apatch_plan_graph(
        manifest_path: str,
        target_dir: str = ".",
        graph_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build dependency execution graph from engineering-pipeline manifest."""
        from apatch.workflows import WorkflowError, plan_graph_workspace

        try:
            return plan_graph_workspace(manifest_path, target_dir, graph_path=graph_path)
        except (FileNotFoundError, ValueError, WorkflowError) as e:
            return {"ok": False, "error": str(e)}

    @mcp.tool()
    def apatch_execute_graph(
        target_dir: str = ".",
        manifest_path: Optional[str] = None,
        graph_path: Optional[str] = None,
        dry_run: bool = False,
        chunk_max_files: int = 5,
    ) -> Dict[str, Any]:
        """Execute nodes from execution graph in topological order."""
        from apatch.workflows import WorkflowError, execute_graph_workspace

        try:
            return execute_graph_workspace(
                target_dir,
                manifest_path=manifest_path,
                graph_path=graph_path,
                dry_run=dry_run,
                chunk_max_files=chunk_max_files,
            )
        except (FileNotFoundError, ValueError, WorkflowError) as e:
            return {"ok": False, "error": str(e)}

    @mcp.tool()
    def apatch_orchestrate(
        manifest_path: str,
        target_dir: str = ".",
        dry_run: bool = False,
        skip_simulate: bool = False,
        chunk_max_files: int = 5,
    ) -> Dict[str, Any]:
        """Single entry: simulate → plan graph → execute graph."""
        from apatch.workflows import WorkflowError, orchestrate_workspace

        try:
            return orchestrate_workspace(
                manifest_path,
                target_dir,
                dry_run=dry_run,
                skip_simulate=skip_simulate,
                chunk_max_files=chunk_max_files,
            )
        except (FileNotFoundError, ValueError, WorkflowError) as e:
            return {"ok": False, "error": str(e)}

    @mcp.tool()
    def apatch_replay(
        target_dir: str = ".",
        session_id: Optional[str] = None,
        mode: str = "deterministic",
    ) -> Dict[str, Any]:
        """Replay apply_session chunk timeline from persisted reports."""
        from apatch.workflows import replay_session_workspace

        return replay_session_workspace(target_dir, session_id=session_id, mode=mode)

    @mcp.tool()
    def apatch_verify_semantic(
        target_dir: str = ".",
        rules_path: Optional[str] = None,
        since: str = "HEAD",
    ) -> Dict[str, Any]:
        """Semantic verify: routes/exports/events/OpenAPI not removed in diff."""
        from apatch.workflows import semantic_verify_workspace

        try:
            return semantic_verify_workspace(target_dir, rules_path=rules_path, since=since)
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            return {"ok": False, "error": str(e), "violations": []}

    _apply_param_docs()


def run_mcp_stdio() -> None:
    """Run MCP over stdio (guard must already be active via launcher)."""
    from apatch.mcp.stdio_guard import activate_stdio_guard

    activate_stdio_guard()
    _ensure_mcp()
    from apatch.mcp.resources import register_mcp_resources
    from apatch.mcp.profiles import apply_tool_profile

    register_mcp_resources(mcp)
    apply_tool_profile(mcp)
    _wrap_mcp_tools()
    _forbid_unknown_arguments()
    _apply_param_docs()
    mcp.run(transport="stdio")


def main() -> None:
    from apatch.mcp.launcher import main as launcher_main

    launcher_main()


if __name__ == "__main__":
    main()
