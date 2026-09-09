"""Auto-rebind file_drift-stale requirements (shared-file spec DX)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from apatch.cli import cli
from apatch.spec_rebind import rebind_stale_requirements


class _FakeRuntime:
    instances: list = []

    def __init__(self, target_dir):
        self.calls: list = []
        _FakeRuntime.instances.append(self)

    def open_session(self, intent, artifacts=None):
        self.calls.append(("open", intent, tuple(artifacts or [])))
        return {"ok": True}

    def verify_run(self, verify=None, skip_transition_check=False):
        self.calls.append(("verify", verify))
        return {"ok": "RED" not in str(verify)}

    def noop_attest(self, covered_by, message=None):
        self.calls.append(("noop", message))
        return {"ok": True, "committed": True}

    def close_session(self):
        self.calls.append(("close",))
        return {"ok": True}


def _fake_resolve(root, token, **k):
    rid = token.split("#")[1]
    verify = "pytest RED" if rid == "R2" else "pytest {}".format(rid)
    return {
        "ok": True,
        "intent": token,
        "artifact": "spec:{}@h".format(token),
        "verify": verify,
    }


def test_rebind_only_file_drift_green(monkeypatch):
    _FakeRuntime.instances.clear()
    statuses = iter(
        [
            {
                "ok": True,
                "spec": "SPEC-X",
                "requirements": [
                    {"id": "R1", "stale": True, "stale_reason": "file_drift"},
                    {"id": "R2", "stale": True, "stale_reason": "file_drift"},
                    {"id": "R3", "stale": True, "stale_reason": "spec_text_changed"},
                    {"id": "R4", "stale": False, "stale_reason": None},
                ],
                "summary": {"stale": 3},
            },
            {"ok": True, "spec": "SPEC-X", "requirements": [], "summary": {"stale": 1}},
        ]
    )
    monkeypatch.setattr(
        "apatch.spec_coverage.spec_status_with_coverage",
        lambda *a, **k: next(statuses),
    )
    monkeypatch.setattr("apatch.spec.resolve_requirement", _fake_resolve)
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", _FakeRuntime)

    out = rebind_stale_requirements(".", spec="SPEC-X")

    assert out["ok"] is True
    # only file_drift + green re-anchors; file_drift + red is reported, not faked
    assert out["rebound"] == ["R1"]
    assert [s["requirement"] for s in out["skipped_red"]] == ["R2"]
    # spec_text_changed (R3) and non-stale (R4) are never touched
    assert len(_FakeRuntime.instances) == 2
    assert [c[0] for c in _FakeRuntime.instances[0].calls] == [
        "open",
        "verify",
        "noop",
        "close",
    ]
    # red path stops before noop_attest
    assert [c[0] for c in _FakeRuntime.instances[1].calls] == ["open", "verify", "close"]


def test_rebind_propagates_status_error(monkeypatch):
    monkeypatch.setattr(
        "apatch.spec_coverage.spec_status_with_coverage",
        lambda *a, **k: {"ok": False, "error": "no spec"},
    )
    out = rebind_stale_requirements(".", spec="SPEC-MISSING")
    assert out["ok"] is False
    assert out["error"] == "no spec"


def test_rebind_stale_cli_json(monkeypatch, tmp_path):
    def fake_rebind(*args, **kwargs):
        return {
            "ok": True,
            "spec": kwargs["spec"],
            "rebound": ["R1"],
            "skipped_red": [],
            "errors": [],
            "summary": {"stale": 0},
        }

    monkeypatch.setattr("apatch.spec_rebind.rebind_stale_requirements", fake_rebind)

    result = CliRunner().invoke(
        cli,
        ["spec", "rebind-stale", "--spec", "SPEC-X", "--target-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["spec"] == "SPEC-X"
    assert payload["rebound"] == ["R1"]
    assert payload["errors"] == []


class _RejectOpenRuntime(_FakeRuntime):
    def open_session(self, intent, artifacts=None):
        self.calls.append(("open", intent, tuple(artifacts or [])))
        return {
            "ok": False,
            "error": "foreign session active",
            "error_type": "SESSION_CONFLICT",
        }


def test_rebind_does_not_touch_foreign_active_session(monkeypatch):
    _FakeRuntime.instances.clear()
    statuses = iter(
        [
            {
                "ok": True,
                "spec": "SPEC-X",
                "requirements": [
                    {"id": "R1", "stale": True, "stale_reason": "file_drift"},
                ],
                "summary": {"stale": 1},
            },
            {
                "ok": True,
                "spec": "SPEC-X",
                "requirements": [
                    {"id": "R1", "stale": True, "stale_reason": "file_drift"},
                ],
                "summary": {"stale": 1},
            },
        ]
    )
    monkeypatch.setattr(
        "apatch.spec_coverage.spec_status_with_coverage",
        lambda *a, **k: next(statuses),
    )
    monkeypatch.setattr("apatch.spec.resolve_requirement", _fake_resolve)
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", _RejectOpenRuntime)

    out = rebind_stale_requirements(".", spec="SPEC-X")

    assert out["rebound"] == []
    assert out["errors"][0]["requirement"] == "R1"
    assert len(_FakeRuntime.instances) == 1
    assert [call[0] for call in _FakeRuntime.instances[0].calls] == ["open"]


def test_rebind_selects_and_excludes_exact_requirements(monkeypatch):
    _FakeRuntime.instances.clear()
    status = {
        "ok": True,
        "spec": "SPEC-X",
        "requirements": [
            {"id": "R1", "stale": True, "stale_reason": "file_drift"},
            {"id": "R2", "stale": True, "stale_reason": "file_drift"},
            {"id": "R3", "stale": True, "stale_reason": "file_drift"},
        ],
        "summary": {"stale": 3},
    }
    final = dict(status, requirements=[
        dict(row, state="attested", stale=False) if row["id"] == "R1" else row
        for row in status["requirements"]
    ])
    statuses = iter([status, final])
    monkeypatch.setattr("apatch.spec_coverage.spec_status_with_coverage", lambda *a, **k: next(statuses))
    monkeypatch.setattr("apatch.spec.resolve_requirement", _fake_resolve)
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", _FakeRuntime)

    out = rebind_stale_requirements(
        ".",
        spec="SPEC-X",
        requirement_ids=["R1", "SPEC-X#R2"],
        exclude_requirement_ids=["R2"],
    )

    assert out["selected_requirement_ids"] == ["R1"]
    assert out["excluded_requirement_ids"] == ["R2"]
    assert out["rebound"] == ["R1"]
    assert len(_FakeRuntime.instances) == 1


def test_rebind_rejects_unknown_filter_before_opening_session(monkeypatch):
    _FakeRuntime.instances.clear()
    monkeypatch.setattr(
        "apatch.spec_coverage.spec_status_with_coverage",
        lambda *a, **k: {
            "ok": True,
            "spec": "SPEC-X",
            "requirements": [{"id": "R1", "stale": True, "stale_reason": "file_drift"}],
        },
    )
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", _FakeRuntime)

    out = rebind_stale_requirements(".", spec="SPEC-X", requirement_ids=["SPEC-Y#R1"])

    assert out["ok"] is False
    assert out["error_type"] == "INVALID_REQUIREMENT_FILTER"
    assert _FakeRuntime.instances == []
