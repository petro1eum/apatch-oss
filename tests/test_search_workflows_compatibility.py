"""Frozen v0.8.21 search-workflow compatibility boundary."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path


PUBLIC_TOOLS = {
    "apatch_slug_intake": (
        "slug", "aliases", "manifest_path", "live", "limit", "target_dir",
    ),
    "apatch_slug_close": (
        "slug", "api_url", "triage_path", "approved_path", "size", "timeout",
        "limit", "target_dir",
    ),
    "apatch_slug_cockpit": (
        "slug", "aliases", "manifest_path", "live", "evidence_limit",
        "feedback_limit", "api_url", "triage_path", "approved_path", "size",
        "timeout", "target_dir",
    ),
    "apatch_slug_feedback_lint": (
        "slug", "triage_path", "approved_path", "target_dir",
    ),
    "apatch_slug_ratify": ("slug", "spec", "dry_run", "target_dir"),
}


def test_r6a_public_search_names_and_routing_are_frozen():
    from apatch.cli import slug_group
    from apatch.codex_approval import AUTOPILOT_CODEX_APPROVAL_TOOLS
    from apatch.mcp import server as mcp_server
    from apatch.mcp.profiles import PROFILE_SPEC_EXTRA
    from apatch.session_state import _READ_ONLY_LIFECYCLE_TOOLS

    assert {"intake", "close", "cockpit", "feedback-lint", "ratify"} <= set(
        slug_group.commands
    )
    manager = mcp_server.mcp._tool_manager
    assert set(PUBLIC_TOOLS) <= set(manager._tools)
    for name, expected_parameters in PUBLIC_TOOLS.items():
        assert tuple(inspect.signature(manager._tools[name].fn).parameters) == expected_parameters

    assert {
        "apatch_slug_intake", "apatch_slug_close", "apatch_slug_cockpit",
    } <= PROFILE_SPEC_EXTRA
    assert {
        "apatch_slug_intake", "apatch_slug_cockpit",
        "apatch_slug_feedback_lint",
    } <= _READ_ONLY_LIFECYCLE_TOOLS
    assert "apatch_slug_close" not in _READ_ONLY_LIFECYCLE_TOOLS
    assert "apatch_slug_ratify" not in _READ_ONLY_LIFECYCLE_TOOLS
    assert {
        "apatch_slug_intake", "apatch_slug_close", "apatch_slug_cockpit",
        "apatch_slug_feedback_lint",
    } <= set(AUTOPILOT_CODEX_APPROVAL_TOOLS)
    assert "apatch_slug_ratify" not in AUTOPILOT_CODEX_APPROVAL_TOOLS

    worker = Path("apatch/remote/worker.py").read_text(encoding="utf-8")
    orchestrator = Path("apatch/remote/orchestrator.py").read_text(encoding="utf-8")
    assert "apatch_slug_ratify" in worker
    assert "apatch_slug_ratify" in orchestrator


def test_r6b_open_workflow_modules_keep_historical_import_identity():
    for name in (
        "slug_close", "slug_feedback_lint", "slug_intake", "slug_cockpit",
    ):
        historical = importlib.import_module("apatch." + name)
        bundled = importlib.import_module("apatch_search_workflows." + name)
        assert historical is bundled


def test_r6c_ratify_is_open_and_core_owns_contract_resolution():
    historical = importlib.import_module("apatch.slug_ratify")
    bundled = importlib.import_module("apatch_search_workflows.slug_ratify")
    resolver = importlib.import_module("apatch.spec_contract_resolver")

    assert historical is bundled
    assert bundled._resolve_spec_id is resolver._resolve_spec_id
    assert bundled._load_contract_yaml is resolver._load_contract_yaml
    ownership_source = Path("apatch/spec_ownership.py").read_text(encoding="utf-8")
    assert "from apatch.slug_ratify import" not in ownership_source
    assert "from apatch.spec_contract_resolver import" in ownership_source


def test_r6a_open_leaf_modules_keep_historical_import_identity():
    old_status = importlib.import_module("apatch.feedback_status")
    open_status = importlib.import_module("apatch_search_workflows.feedback_status")
    old_repair = importlib.import_module("apatch.repair_map")
    open_repair = importlib.import_module("apatch_search_workflows.repair_map")

    assert old_status is open_status
    assert old_repair is open_repair
    assert old_status.canonical_status("neighbor_slug") == "other_slug"
    assert old_repair.route(
        {"rules": [{
            "id": "r1",
            "match": {"root_cause": ["catalog_gap"]},
            "action": "edit catalog",
            "edit_surfaces": ["catalog"],
            "forbidden": [],
            "contract_ref": "RFP-040",
        }]},
        {"root_cause": "catalog_gap"},
    )[0]["rule_id"] == "r1"

    package_root = Path(open_status.__file__).parent
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_root.glob("*.py"))
    ).lower()
    assert "edcher_search" not in source
    assert "edcher-search" not in source


def test_r6d_public_examples_do_not_embed_owner_topology():
    marker_fragments = (
        ("yc", "-proxy"),
        ("/home/ubuntu/projects/", "Elasticsearch_2"),
        ("search", "-main"),
        ("kb", "-search"),
    )
    private_markers = tuple("".join(parts) for parts in marker_fragments)
    roots = (Path("apatch"), Path("docs"), Path("tests"))
    source_files = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".md", ".json"}
    ]
    hits = tuple(
        (marker, path.as_posix())
        for path in source_files
        for marker in private_markers
        if marker in path.read_text(encoding="utf-8", errors="ignore")
    )
    assert hits == (), hits
