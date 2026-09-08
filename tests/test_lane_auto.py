"""Auto lane + worktree routing (RFP-019)."""

from __future__ import annotations

import json
import os

from apatch.lane import lane_info, lane_state_path, resolve_lane
from apatch.worktree_lane import load_worktree_manifest, prepare_execution_root


def test_lane_default_without_spec(tmp_path, monkeypatch):
    monkeypatch.delenv("APATCH_LANE", raising=False)
    monkeypatch.setenv("APATCH_LANE", "auto")
    ctx = resolve_lane(str(tmp_path))
    assert ctx.source == "shared"
    assert lane_state_path(str(tmp_path), "session_state.json").endswith(
        os.path.join(".apatch", "session_state.json")
    )


def test_lane_from_lane_json(tmp_path):
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    (apatch / "lane.json").write_text(
        json.dumps({"lane_id": "ui-roles"}),
        encoding="utf-8",
    )
    ctx = resolve_lane(str(tmp_path))
    assert ctx.lane_id == "ui-roles"
    assert ctx.source == "manifest"


def test_prepare_execution_no_manifest(tmp_path):
    prep = prepare_execution_root(str(tmp_path), spec="SPEC-FOO")
    assert prep["path"] == os.path.abspath(str(tmp_path))
    assert prep["retargeted"] is False


def test_load_platform_worktree_manifest():
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "TrustChain_Platform")
    )
    if not os.path.isdir(os.path.join(root, "manifests", "worktrees.yaml")):
        return
    manifest = load_worktree_manifest(root)
    assert manifest is not None
    assert manifest.lanes
