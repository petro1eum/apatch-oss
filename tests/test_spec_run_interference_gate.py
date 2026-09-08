"""SPEC-INTERFERENCE-3 R4 — spec_run execution ordering gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apatch.spec_run import ERROR_SPEC_RUN_ORDER_BLOCKED, spec_run_workspace


def _setup_specs(tmp_path: Path) -> None:
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    for sid in ("SPEC-A", "SPEC-B"):
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n> **apatch artifact:** `spec:{sid}`\n## R1 t\n(verify: true)\n",
            encoding="utf-8",
        )


def _fake_interference(*_a, **_k):
    return {
        "ok": True,
        "has_cycle": False,
        "safe_order": ["SPEC-A", "SPEC-B"],
        "validity": "current",
        "conflicts": [],
    }


def test_blocks_until_predecessor_attested(tmp_path, monkeypatch):
    _setup_specs(tmp_path)
    monkeypatch.setattr(
        "apatch.spec_interference.spec_interference_workspace",
        _fake_interference,
    )
    req_b = {"R1": {"needles": [{"action": "create", "target_file": "b.txt", "content": "b\n"}]}}
    out = spec_run_workspace(
        str(tmp_path),
        spec="SPEC-B",
        peer_specs=["SPEC-A"],
        requirements=req_b,
    )
    assert out["ok"] is False
    assert out["error_type"] == ERROR_SPEC_RUN_ORDER_BLOCKED
    assert out["recommended_action"] == "complete_predecessor_first"
    assert "SPEC-A" in (out.get("blocked_by") or [])


def test_mcp_gate_default_with_peers(tmp_path, monkeypatch):
    _setup_specs(tmp_path)
    monkeypatch.setattr(
        "apatch.spec_interference.spec_interference_workspace",
        _fake_interference,
    )
    out = spec_run_workspace(
        str(tmp_path),
        spec="SPEC-B",
        peer_specs=["SPEC-A"],
        interference_check=False,
        requirements={"R1": {"needles": [{"action": "create", "target_file": "b.txt", "content": "b\n"}]}},
    )
    assert out["ok"] is False
    assert out["error_type"] == ERROR_SPEC_RUN_ORDER_BLOCKED
