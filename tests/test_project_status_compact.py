"""project_status compact view: token-bounded DTO (RFP-020 DX)."""

from __future__ import annotations

from apatch.project_status import _compact_view


def test_compact_view_drops_heavy_fields_keeps_actionable():
    full = {
        "ok": True,
        "specs": [
            {
                "id": "SPEC-X",
                "title": "X",
                "summary": {"total": 3, "attested": 1, "stale": 1, "pending": 1},
                "done": False,
                "requirements": [
                    {"id": "R1", "state": "attested", "stale": False,
                     "file_hashes": {"a": "b"}},
                    {"id": "R2", "state": "stale", "stale": True,
                     "attested_hashes": ["x"]},
                    {"id": "R3", "state": "pending", "stale": False},
                ],
            }
        ],
        "conflicts": {
            "graph": {"SPEC-X": ["SPEC-Y"]},
            "conflicts": [{"a": 1}, {"b": 2}],
            "summary": {"total_conflicts": 2},
            "risk_score": 0.2,
            "safe_order": ["SPEC-X"],
        },
        "hygiene": {"status": "clean"},
        "asset_summary": {"big": "x" * 200},
        "activity": [{"i": i} for i in range(20)],
    }

    out = _compact_view(full)

    assert out["view"] == "compact"
    spec = out["specs"][0]
    # heavy requirement rows dropped
    assert "requirements" not in spec
    # actionable ids kept
    assert spec["stale"] == ["R2"]
    assert spec["pending"] == ["R3"]
    assert spec["summary"]["attested"] == 1
    # conflicts trimmed: no full graph / per-edge list, counts kept
    assert "graph" not in out["conflicts"]
    assert "conflicts" not in out["conflicts"]
    assert out["conflicts"]["summary"]["total_conflicts"] == 2
    assert out["conflicts"]["risk_score"] == 0.2
    # untouched blocks preserved verbatim
    assert out["hygiene"]["status"] == "clean"
    # secondary heavy blocks trimmed in compact
    assert out["asset_summary"] == {"omitted_in_compact": True}
    assert len(out["activity"]) == 5
    assert out["activity_truncated"] == 20