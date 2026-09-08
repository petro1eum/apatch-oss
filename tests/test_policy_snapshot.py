"""Policy snapshot parity and MCP enrich hot-path performance."""

from __future__ import annotations

import time

from apatch.doctor import build_policy_snapshot, run_doctor
from apatch.runtime.session import build_session_view
from apatch.session_state import enrich_tool_response


def _policy_from_doctor(doctor: dict) -> dict:
    tc = doctor.get("trustchain") or {}
    return {
        "trustchain_mode": tc.get("mode"),
        "enforcement": (doctor.get("enforcement") or {}).get("active"),
        "sandbox_mode": (doctor.get("sandbox") or {}).get("mode"),
    }


def test_policy_snapshot_matches_doctor_policy(tmp_path):
    doctor = run_doctor(str(tmp_path))
    snap = build_policy_snapshot(str(tmp_path))
    view = build_session_view(str(tmp_path))

    expected = _policy_from_doctor(doctor)
    from_snap = {
        "trustchain_mode": (snap["trustchain"] or {}).get("mode"),
        "enforcement": snap["enforcement"]["active"],
        "sandbox_mode": (snap["sandbox"] or {}).get("mode"),
    }
    assert from_snap == expected
    assert view["policy"] == expected


def test_build_session_view_stays_fast(tmp_path):
    t0 = time.perf_counter()
    for _ in range(20):
        build_session_view(str(tmp_path))
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"build_session_view too slow: {elapsed:.2f}s for 20 calls"


def test_enrich_tool_response_stays_fast(tmp_path):
    t0 = time.perf_counter()
    for _ in range(10):
        enrich_tool_response(
            "apatch_session_state",
            {"ok": True},
            target_dir=str(tmp_path),
        )
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"enrich_tool_response too slow: {elapsed:.2f}s for 10 calls"
