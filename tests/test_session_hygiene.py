"""SPEC-SESSION-LIFECYCLE-1 — session end & RUN_STATE leases."""

from __future__ import annotations

import json
import os

from apatch.apply_session import save_session as save_apply_session
from apatch.artifact_governance import (
    collect_registered_orphan_ephemeral,
    gc_delete_allowed,
    load_registry,
    register_on_write,
)
from apatch.doctor import run_doctor
from apatch.runtime.session import end_session
from apatch.session_state import save_session_state
from apatch.spec_run import clear_spec_run_state, save_spec_run_state


def test_apply_session_lease(tmp_path):
    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    session_path = str(apatch / "apply_session.json")

    save_apply_session(
        session_path,
        {
            "chunks": [[1, 2]],
            "chunk_index": 0,
            "last_checkpoint": "apatch_sess_ckpt1",
            "session_id": "apatch_sess_1",
        },
    )
    reg = load_registry(ws)
    entry = reg[".apatch/apply_session.json"]
    assert entry.run_lease_id == "lease_apatch_sess_1_apatch_sess_ckpt1"
    assert entry.gc_allowed is False
    assert entry.replay_critical is True
    assert not gc_delete_allowed(entry, entry.path, reg)

    save_apply_session(
        session_path,
        {
            "chunks": [[1, 2]],
            "chunk_index": 1,
            "last_checkpoint": "apatch_sess_ckpt1",
            "session_id": "apatch_sess_1",
        },
    )
    reg = load_registry(ws)
    entry = reg[".apatch/apply_session.json"]
    assert entry.run_lease_id is None
    assert entry.gc_allowed is True
    assert entry.replay_critical is False
    assert gc_delete_allowed(entry, entry.path, reg)


def test_apply_session_inherits_lane_session_id(tmp_path):
    ws = str(tmp_path)
    lane = tmp_path / ".apatch" / "lanes" / "spec-x"
    lane.mkdir(parents=True)
    (lane / "session_state.json").write_text(
        json.dumps({"session_id": "apatch_sess_lane", "ended_at": None}),
        encoding="utf-8",
    )

    apply_path = lane / "apply_session.json"
    save_apply_session(
        str(apply_path),
        {"chunks": [[1]], "chunk_index": 0, "last_checkpoint": "ckpt1"},
    )

    persisted = json.loads(apply_path.read_text(encoding="utf-8"))
    assert persisted["session_id"] == "apatch_sess_lane"
    entry = load_registry(ws)[".apatch/lanes/spec-x/apply_session.json"]
    assert entry.lineage["governed_session_id"] == "apatch_sess_lane"
    assert entry.run_lease_id == "lease_apatch_sess_lane_ckpt1"


def test_spec_run_lease(tmp_path):
    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    state_path = str(apatch / "spec_run.json")

    save_spec_run_state(
        state_path,
        {
            "spec": "SPEC-X",
            "spec_run_id": "spec_run_99",
            "session_id": "apatch_sess_sr",
            "rk_index": 0,
        },
    )
    reg = load_registry(ws)
    entry = reg[".apatch/spec_run.json"]
    assert entry.run_lease_id == "spec_run_99"
    assert entry.gc_allowed is False
    assert not gc_delete_allowed(entry, entry.path, reg)

    patches = tmp_path / "patches-spec-x-r1.jsonl"
    patches.write_text("{}\n", encoding="utf-8")
    register_on_write(
        ws,
        str(patches.relative_to(tmp_path)),
        class_name="EPHEMERAL",
        created_by_tool="apatch_generate",
        reason="spec run rk log",
        governed_session_id="apatch_sess_sr",
        spec_run_id="spec_run_99",
    )

    clear_spec_run_state(state_path)
    assert not os.path.isfile(state_path)
    reg = load_registry(ws)
    patch_entry = reg["patches-spec-x-r1.jsonl"]
    assert patch_entry.gc_allowed is True
    assert patch_entry.run_lease_id is None


def test_session_end_registry_cleanup(tmp_path):
    ws = str(tmp_path)
    apatch = tmp_path / ".apatch"
    apatch.mkdir()

    save_session_state(
        ws,
        {
            "session_id": "apatch_sess_end",
            "intent": "test cleanup",
            "phase": "apply",
        },
    )
    register_on_write(
        ws,
        "patches.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_generate",
        reason="session patches",
        governed_session_id="apatch_sess_end",
        run_lease_id="lease_ephemeral",
        gc_allowed=False,
        replay_critical=True,
    )
    register_on_write(
        ws,
        ".apatch/write_lease.json",
        class_name="EPHEMERAL",
        created_by_tool="apatch_apply_session",
        reason="sandbox write lease",
        governed_session_id="apatch_sess_end",
        run_lease_id="lease_write",
        gc_allowed=False,
    )
    (apatch / "write_lease.json").write_text("{}\n", encoding="utf-8")

    result = end_session(ws)
    assert result["ok"] is True

    reg = load_registry(ws)
    patch = reg["patches.jsonl"]
    assert patch.run_lease_id is None
    assert patch.gc_allowed is True
    assert patch.replay_critical is False
    lease = reg[".apatch/write_lease.json"]
    assert lease.run_lease_id is None
    assert lease.gc_allowed is True


def test_orphan_ephemeral_doctor(tmp_path):
    ws = str(tmp_path)
    (tmp_path / "patches-leftover.jsonl").write_text("{}\n", encoding="utf-8")
    register_on_write(
        ws,
        "patches-leftover.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_generate",
        reason="left after session",
        governed_session_id="apatch_sess_old",
        gc_allowed=True,
        replay_critical=False,
    )

    orphans = collect_registered_orphan_ephemeral(ws)
    assert "patches-leftover.jsonl" in orphans

    doc = run_doctor(ws)
    hygiene = doc["hygiene"]
    assert hygiene["status"] == "degraded"
    issue_types = {i["type"] for i in hygiene.get("issues") or []}
    assert "ORPHAN_EPHEMERAL" in issue_types


def test_generate_routes_tmp_and_purges_on_session_end(tmp_path):
    ws = str(tmp_path)
    (tmp_path / ".apatch").mkdir()
    save_session_state(
        ws,
        {
            "session_id": "apatch_sess_tmp",
            "intent": "ephemeral routing test",
            "phase": "plan",
        },
    )
    marker = tmp_path / "tests" / "markers"
    marker.mkdir(parents=True)
    from apatch.workflows import generate_patch_jsonl_batch

    result = generate_patch_jsonl_batch(
        needles=[
            {
                "action": "create",
                "target_file": "tests/markers/ephemeral-route-test.txt",
                "content": "route test\n",
            }
        ],
        target_dir=ws,
        out_path="patches-route-test.jsonl",
    )
    assert result["ok"]
    rel = result["out_path_rel"]
    assert rel == ".apatch/tmp/apatch_sess_tmp/patches-route-test.jsonl"
    assert os.path.isfile(tmp_path / rel)
    lock_path = tmp_path / f"{rel}.lock"
    assert lock_path.is_file()
    reg = load_registry(ws)
    entry = reg[rel]
    assert entry.class_name == "EPHEMERAL"
    assert entry.gc_allowed is False

    end_session(ws)
    assert not os.path.isfile(tmp_path / rel)
    assert not lock_path.exists()
