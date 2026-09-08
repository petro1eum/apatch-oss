"""Lane context — multiple specs on one catalog."""

from __future__ import annotations

import json

from apatch.lane import resolve_lane
from apatch.lane_context import (
    active_lane_ids,
    bind_lane_from_kwargs,
    parse_spec_id,
    register_active_lane,
    unregister_lane,
)


def test_parse_spec_from_requirement():
    assert parse_spec_id(requirement="SPEC-UI-ROLES-1#R2") == "SPEC-UI-ROLES-1"
    assert parse_spec_id(spec="SPEC-ONPREM-1") == "SPEC-ONPREM-1"


def test_explicit_lane_beats_multiple_active_lanes(tmp_path):
    root = str(tmp_path)
    register_active_lane(root, "SPEC-A", session_id="s1")
    register_active_lane(root, "SPEC-B", session_id="s2")

    bind_lane_from_kwargs({"target_dir": root, "lane": "enterprise-yellow"})

    lane = resolve_lane(root)
    assert lane.lane_id == "enterprise-yellow"
    assert lane.source == "explicit"


def test_lane_artifact_routes_older_mcp_registration(tmp_path):
    root = str(tmp_path)
    register_active_lane(root, "SPEC-A", session_id="s1")
    register_active_lane(root, "SPEC-B", session_id="s2")

    bind_lane_from_kwargs(
        {"target_dir": root, "artifacts": ["lane:enterprise-yellow", "bug:123"]}
    )

    lane = resolve_lane(root)
    assert lane.lane_id == "enterprise-yellow"
    assert lane.source == "explicit"


def test_two_specs_two_lanes(tmp_path):
    root = str(tmp_path)
    bind_lane_from_kwargs({"requirement": "SPEC-A#R1"})
    assert resolve_lane(root).lane_id == "SPEC-A"
    register_active_lane(root, "SPEC-A", session_id="s1")

    bind_lane_from_kwargs({"requirement": "SPEC-B#R1"})
    assert resolve_lane(root).lane_id == "SPEC-B"
    register_active_lane(root, "SPEC-B", session_id="s2")

    assert active_lane_ids(root) == ["SPEC-A", "SPEC-B"]

    unregister_lane(root, "SPEC-A")
    bind_lane_from_kwargs({})
    assert resolve_lane(root).lane_id == "SPEC-B"
