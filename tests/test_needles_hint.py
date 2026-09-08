"""Tests for needles_hint v2 + scaffold clarity (SPEC-NEEDLES-HINT-1)."""

from __future__ import annotations

import json
import os

from apatch.agent_guidance import enrich_tool_response
from apatch.simulate import needles_hints_from_plan, simulate_from_logs
from apatch.spec_needles_scaffold import spec_needles_scaffold_workspace
from apatch.workflows import plan_from_logs


def _write_drift_log(path, target_file: str, old: str, new: str):
    step = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": target_file,
                "TargetContent": old,
                "ReplacementContent": new,
            },
        }],
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(step) + "\n")


def test_r1_recovered_drift_hint(tmp_path):
    target = "drift.py"
    live = "hello  world\n"
    (tmp_path / target).write_text(live, encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_drift_log(log, target, "hello world", "hello earth")
    plan = plan_from_logs(str(log), str(tmp_path))
    from apatch.ingestor import LogIngestor
    from apatch.workflows import filter_patch_candidates

    candidates = filter_patch_candidates(LogIngestor(str(log)).parse())
    hints = needles_hints_from_plan(plan, candidates, str(tmp_path))
    assert len(hints) == 1
    assert hints[0]["drift_kind"] == "recovered"
    assert hints[0]["needles_hint"]["partial"] is True
    assert hints[0]["needles_hint"]["advisory"] is True


def test_r2_simulate_embeds_hints(tmp_path):
    target = "drift.py"
    (tmp_path / target).write_text("hello  world\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    _write_drift_log(log, target, "hello world", "hello earth")
    result = simulate_from_logs(str(log), str(tmp_path))
    assert result["ok"] is True
    assert result["needles_hints"]
    assert result["needles_hint_note"]


def test_r3_scaffold_templates_note():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out = spec_needles_scaffold_workspace(root, spec="SPEC-NEEDLES-HINT-1")
    assert out["ok"] is True
    assert out.get("scaffold_vs_templates")
    assert "needle_templates" in out["scaffold_vs_templates"]
    req = (out.get("requirements") or [])[0]
    assert req.get("needle_templates_note")


def test_r4_agent_guidance_scaffold_simulate(tmp_path):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    scaffold = spec_needles_scaffold_workspace(root, spec="SPEC-NEEDLES-HINT-1")
    enriched = enrich_tool_response(
        "apatch_spec_needles_scaffold", scaffold, target_dir=str(tmp_path)
    )
    assert enriched.get("templates_vs_needles")
    assert enriched.get("autonomy_boundary")
    sim = enrich_tool_response(
        "apatch_simulate",
        {"ok": True, "needles_hints": [{"drift_kind": "recovered"}]},
        target_dir=str(tmp_path),
    )
    assert sim.get("templates_vs_needles")
    assert sim.get("recommended_workflow")