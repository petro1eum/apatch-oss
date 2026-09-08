"""Concurrent lanes must fail closed unless the caller supplies an exact binding."""
from apatch.lane import resolve_lane
from apatch.lane_context import (
    load_registry,
    register_active_lane,
    resolve_active_lane,
    save_registry,
)


def test_ambiguous_lanes_fail_closed(tmp_path):
    root = str(tmp_path)
    register_active_lane(root, "SPEC-OLD-1", session_id="s1")
    register_active_lane(root, "SPEC-NEW-1", session_id="s2")
    # Timestamps cannot confer session ownership.
    data = load_registry(root)
    data["lanes"]["SPEC-OLD-1"]["updated_at"] = "2020-01-01T00:00:00+00:00"
    data["lanes"]["SPEC-NEW-1"]["updated_at"] = "2030-01-01T00:00:00+00:00"
    save_registry(root, data)

    lane, err = resolve_active_lane(root)
    assert lane is None
    assert err and "Ambiguous active lanes" in err
    assert resolve_lane(root).lane_id == "ambiguous"


def test_single_active_lane_unchanged(tmp_path):
    root = str(tmp_path)
    register_active_lane(root, "SPEC-ONE-1", session_id="s1")
    assert resolve_active_lane(root) == ("SPEC-ONE-1", None)


def test_no_active_lane_is_default(tmp_path):
    assert resolve_active_lane(str(tmp_path)) == (None, None)
