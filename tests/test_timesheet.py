"""Tests for apatch.timesheet (SPEC-CONTRIB-TIMESHEET-1 R4-R8)."""
import json

from apatch import contribution as C
from apatch import timesheet as T


def _ev(key_id="k1", proj="p1", dur=3600.0, ops=2, files=1, ins=10, dele=2,
        spec="spec:SPEC-X#R1", trust="attested",
        ended="2026-06-18T10:00:00+00:00", sid="s1", eid="e1"):
    return {
        "schema_version": 1, "kind": "contribution", "event_id": eid,
        "source": "apatch", "trust_level": trust, "idempotency_key": eid,
        "identity": {"key_id": key_id, "ca": "platform", "trust_level": trust, "agent_id": "a"},
        "project": {"id": proj, "name": proj, "remote": None},
        "session": {"session_id": sid, "intent": "x", "artifacts": [spec],
                    "started_at": ended, "ended_at": ended,
                    "duration_sec": dur, "active_sec": None},
        "volume": {"ops": ops, "files_touched": files, "insertions": ins, "deletions": dele},
        "signature": "sig", "attestation": {"op_ids": ["op_1", "op_2"], "head": "op_2"},
    }


def _store(tmp_path, events):
    for ev in events:
        d = tmp_path / ev["identity"]["key_id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / (ev["event_id"] + ".json")).write_text(json.dumps(ev), encoding="utf-8")
    return str(tmp_path)


def test_r4_cross_project_aggregate(tmp_path):
    store = _store(tmp_path, [
        _ev(key_id="k1", proj="p1", dur=3600, sid="s1", eid="e1"),
        _ev(key_id="k1", proj="p2", dur=1800, sid="s2", eid="e2"),
    ])
    by_id = T.aggregate(T.load_events(store), ("identity",))
    assert len(by_id) == 1
    assert by_id[0]["duration_sec"] == 5400.0
    assert by_id[0]["hours"] == 1.5
    assert by_id[0]["ops"] == 4
    by_proj = T.aggregate(T.load_events(store), ("project",))
    assert len(by_proj) == 2


def test_r5_cli_group_by(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from apatch.cli import cli

    store = _store(tmp_path, [_ev(key_id="k1", proj="p1", sid="s1", eid="e1")])
    monkeypatch.setenv("APATCH_CONTRIB_STORE", store)
    res = CliRunner().invoke(cli, ["timesheet", "--by", "project", "--format", "json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["by"] == ["project"]
    assert data["groups"][0]["key"] == ["p1"]


def test_r6_verify_detects_tamper(monkeypatch):
    monkeypatch.setattr(T, "_ledger_rows_for_session", lambda *a, **k: [
        {"id": "op_1", "payload": {"files": {"a": {}}, "insertions": 10, "deletions": 2}},
        {"id": "op_2", "payload": {"files": {"a": {}}}},
    ])
    res = T.verify_events([_ev(ops=5)], ".")
    assert res["ok"] is False
    assert res["drift"][0]["claimed"]["ops"] == 5
    assert res["drift"][0]["rederived"]["ops"] == 2


def test_r7_multi_user_privacy(tmp_path):
    store = _store(tmp_path, [
        _ev(key_id="k1", eid="e1", sid="s1"),
        _ev(key_id="k2", eid="e2", sid="s2"),
    ])
    evs = T.load_events(store)
    assert len(T.aggregate(evs, ("identity",))) == 2
    for ev in evs:
        assert set(ev.keys()) <= T._ALLOWED_EVENT_KEYS  # no code/secret leak


def test_r8_idle_gap_split():
    base = 1_000_000.0
    ts = [base, base + 60, base + 60 + 1800, base + 60 + 1800 + 60]
    # 10-min threshold excludes the 30-min gap -> 60 + 60 active
    assert T.idle_gap_split(ts, gap_min=10) == 120.0


def test_r8b_estimate_active_seconds_git_hours():
    base = 1_000_000.0
    # a single op = one ramp-up (work happened before it), not zero
    assert T.estimate_active_seconds([base], ramp_up_min=15) == 900.0
    # an unbroken burst within idle_gap = its span + one ramp-up
    assert T.estimate_active_seconds([base, base + 600], idle_gap_min=30,
                                     ramp_up_min=15) == 600.0 + 900.0
    # a gap > idle_gap starts a new work block -> a second ramp-up, gap excluded
    assert T.estimate_active_seconds([base, base + 3600], idle_gap_min=30,
                                     ramp_up_min=15) == 1800.0
    # no timestamps -> zero
    assert T.estimate_active_seconds([]) == 0.0


def test_r8c_hours_are_honest_for_zero_duration_bursts(tmp_path):
    # Governed ops record duration_sec=0 (sub-second ceremony). The honest hours
    # must come from op spacing, not the (zero) recorded duration.
    day = "2026-06-20T"
    evs = []
    for i, hhmm in enumerate(["09:00:00", "09:20:00", "11:00:00", "11:10:00"]):
        evs.append(_ev(dur=0.0, ended=f"{day}{hhmm}+00:00",
                       sid=f"s{i}", eid=f"e{i}"))
    store = _store(tmp_path, evs)
    row = T.aggregate(T.load_events(store), ("identity",))[0]
    assert row["duration_sec"] == 0.0          # recorded ceremony time is ~0
    assert row["estimated_sec"] > 0            # spacing estimate is non-zero
    # block1 09:00->09:20 (1200s) + ramp; break at 09:20->11:00 (>30m);
    # block2 11:00->11:10 (600s) + ramp. = 1200 + 600 + 2*900 = 3600s = 1.0h
    assert row["active_sec"] == 3600.0
    assert row["hours"] == 1.0


def test_r8d_hours_take_the_larger_of_recorded_and_estimate(tmp_path):
    # When a real duration IS recorded, hours never drop below it (never under-count).
    store = _store(tmp_path, [_ev(dur=3600.0, ended="2026-06-18T10:00:00+00:00",
                                  sid="s1", eid="e1")])
    row = T.aggregate(T.load_events(store), ("identity",))[0]
    assert row["active_sec"] == max(3600.0, row["estimated_sec"])
    assert row["hours"] == 1.0