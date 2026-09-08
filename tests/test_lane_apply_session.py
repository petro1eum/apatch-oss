"""Lane-scoped apply_session paths (multi-chat isolation)."""

from __future__ import annotations

import os

from apatch.apply_session import default_session_path
from apatch.lane_context import bind_lane_from_kwargs


def test_default_session_path_uses_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_LANE", "auto")
    bind_lane_from_kwargs({"spec": "SPEC-OMEGA-SENSOR-2"})
    path = default_session_path(str(tmp_path))
    assert path.endswith(
        os.path.join(".apatch", "lanes", "SPEC-OMEGA-SENSOR-2", "apply_session.json")
    )


def test_default_session_path_shared_lane_without_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_LANE", "auto")
    bind_lane_from_kwargs({})
    path = default_session_path(str(tmp_path))
    assert path.endswith(os.path.join(".apatch", "apply_session.json"))