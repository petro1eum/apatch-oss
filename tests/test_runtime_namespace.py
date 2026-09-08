from __future__ import annotations

from apatch.artifact_governance import load_registry, register_ephemeral_logs, resolve_ephemeral_logs_path
from apatch.runtime.namespace import runtime_namespace
from apatch.runtime.session import start_session


def test_requirement_namespace_contains_lane_hash_rk_and_session(monkeypatch, tmp_path):
    root = str(tmp_path)
    monkeypatch.setenv("APATCH_LANE", "feature-lane")
    opened = start_session(
        root,
        "implement R1",
        artifacts=["spec:SPEC-X#R1@sha256:abcdef123456"],
    )
    session_id = opened["session"]["session_id"]
    namespace = runtime_namespace(root, session_id=session_id)
    assert namespace["workspace_id"]
    assert namespace["lane"] == "feature-lane"
    assert namespace["requirement"] == "R1"
    assert namespace["spec_hash"] == "sha256-abcdef123456"
    assert namespace["session_id"] == session_id

    absolute, relative = resolve_ephemeral_logs_path(root, "patches.jsonl")
    assert "/feature-lane/sha256-abcdef123456/R1/" in relative
    assert relative.endswith(f"/{session_id}/patches.jsonl")
    tmp_path.joinpath(relative).parent.mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath(relative).write_text("{}\n", encoding="utf-8")
    register_ephemeral_logs(
        root,
        relative,
        created_by_tool="test",
        governed_session_id=session_id,
    )
    entry = load_registry(root)[relative]
    recorded = entry.lineage["runtime_namespace"]
    assert recorded["workspace_id"] == namespace["workspace_id"]
    assert recorded["requirement"] == "R1"
    assert absolute == str(tmp_path / relative)

