"""MCP Resources for static agent playbooks (RFP-019 L1-10)."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict


def _json_resource(factory: Callable[[], Dict[str, Any]]) -> str:
    return json.dumps(factory(), ensure_ascii=False, indent=2)


def register_mcp_resources(mcp: Any) -> None:
    """Expose agent playbooks as MCP resources (fetch once per session)."""
    if mcp is None:
        return

    from apatch.agent_guidance import (
        protocol_contract,
        sandbox_agent_protocol,
        spec_authoring_requirements,
        spec_execution_playbook,
        spec_run_playbook,
    )
    from apatch.agent_playbooks import (
        diagnose_playbook,
        doc_outline_playbook,
        mcp_playbook_index,
        runtime_hygiene_playbook,
        tool_usage_playbook,
    )

    playbooks = (
        (
            "apatch://playbook/protocol_contract",
            "protocol_contract",
            "Agent protocol: task routing, invariant, never-do list.",
            protocol_contract,
        ),
        (
            "apatch://playbook/runtime_hygiene",
            "runtime_hygiene",
            "EPHEMERAL JSONL routing, session_end, apatch_gc, inference sunset.",
            runtime_hygiene_playbook,
        ),
        (
            "apatch://playbook/sandbox_protocol",
            "sandbox_protocol",
            "Sandbox enforce: never pip/shell writes; human-only MCP restart.",
            sandbox_agent_protocol,
        ),
        (
            "apatch://playbook/spec_authoring",
            "spec_authoring",
            "SPEC.md format — apatch_spec_lint rules.",
            spec_authoring_requirements,
        ),
        (
            "apatch://playbook/tool_usage",
            "tool_usage",
            "Which apatch_* MCP tool when — decision tree.",
            tool_usage_playbook,
        ),
        (
            "apatch://playbook/spec_run",
            "spec_run",
            "RFP-009 batch spec workflow (§3K).",
            spec_run_playbook,
        ),
        (
            "apatch://playbook/spec_execution",
            "spec_execution",
            "RFP-007 per-requirement loop (§3I–§3J).",
            spec_execution_playbook,
        ),
        (
            "apatch://playbook/diagnose",
            "diagnose",
            "Verify failures: diagnostics[] + fix_forward loop (SPEC-DIAGNOSTIC-GRAPH-1).",
            diagnose_playbook,
        ),
        (
            "apatch://playbook/doc_outline",
            "doc_outline",
            "Numbered markdown outline: insert_section/shift_outline workflow (SPEC-DOC-OUTLINE-1).",
            doc_outline_playbook,
        ),
        (
            "apatch://playbook/index",
            "playbook_index",
            "Catalog of all apatch://playbook/* resources and read order.",
            mcp_playbook_index,
        ),
    )

    for uri, name, description, factory in playbooks:
        _register_playbook(mcp, uri, name, description, factory)


def _register_playbook(
    mcp: Any,
    uri: str,
    name: str,
    description: str,
    factory: Callable[[], Dict[str, Any]],
) -> None:
    def _make_handler(fn: Callable[[], Dict[str, Any]]):
        @mcp.resource(uri, name=name, description=description, mime_type="application/json")
        def _handler() -> str:
            return _json_resource(fn)

        _handler.__name__ = f"_playbook_{name}"
        return _handler

    _make_handler(factory)
