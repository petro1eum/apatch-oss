"""Tests for SPEC-PROJECT-STATUS-1."""

from __future__ import annotations

from apatch.plan_adherence import spec_adherence_workspace
from apatch.project_status import project_status_workspace


def _write_spec(tmp_path, spec_id: str, n_req: int = 1):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {spec_id} — Title",
        f"> **apatch artifact:** `spec:{spec_id}`",
        "",
    ]
    for i in range(1, n_req + 1):
        lines += [f"## R{i} Req {i}", "(verify: true)", ""]
    (d / f"{spec_id}.md").write_text("\n".join(lines), encoding="utf-8")


def test_project_status_dto_shape(tmp_path):
    _write_spec(tmp_path, "SPEC-A")
    out = project_status_workspace(str(tmp_path))
    assert out["ok"] is True
    assert out["schema_version"] == 1
    for key in (
        "specs",
        "summary",
        "conflicts",
        "active_session",
        "hygiene",
        "policy",
        "activity",
    ):
        assert key in out
    assert len(out["specs"]) >= 1
    assert out["conflicts"] is None


def test_multi_spec_summary(tmp_path):
    _write_spec(tmp_path, "SPEC-A", 2)
    _write_spec(tmp_path, "SPEC-B", 2)
    out = project_status_workspace(str(tmp_path))
    assert out["summary"]["spec_count"] == 2
    assert out["summary"]["total_requirements"] == 4
    assert out["conflicts"] is not None
    assert "safe_order" in out["conflicts"]
    assert "risk_score" in out["conflicts"]


def test_adherence_uses_ledger(tmp_path, monkeypatch):
    import json

    spec_id = "SPEC-ADH-LED"
    _write_spec(tmp_path, spec_id, 1)
    plan_dir = tmp_path / ".apatch" / "plans"
    plan_dir.mkdir(parents=True)
    plan = {
        "schema_version": 1,
        "spec": spec_id,
        "decision_plan": {"chosen_strategy": "test"},
        "execution_plan": {"R1": {"files": ["a.py"], "needles": []}},
    }
    (plan_dir / f"{spec_id}.v1.json").write_text(json.dumps(plan), encoding="utf-8")
    entries = [
        {
            "tool_id": "apatch",
            "id": "op-m1",
            "payload": {
                "action": "apply",
                "files": {"a.py": {}, "extra.py": {}},
                "artifacts": [{"kind": "spec", "id": f"{spec_id}#R1"}],
            },
        },
    ]
    monkeypatch.setattr(
        "apatch.spec._ledger_entries",
        lambda _td: (entries, True),
    )
    out = spec_adherence_workspace(str(tmp_path), spec=spec_id)
    assert out["ok"] is True
    r1 = out["requirements"][0]
    assert "extra.py" in r1["unplanned"]