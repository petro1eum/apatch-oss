"""Multi-file batches must never silently drop a file's patches.

Regression: an execute_next/spec_run batch that touches module + runtime +
test in one Rk could apply only some files' patches (stale patch log from a
reused session, or a lingering prior partial apply) and then fail verify
with a confusing "no tests ran" — the dropped patch was the test file's.
plan_chunks_for_logs / run_apply_session now surface every log step that
will NOT apply, so a partial apply is loud, not silent.
"""

from __future__ import annotations

import json
from pathlib import Path

from apatch.apply_session import dropped_plan_steps, plan_chunks_for_logs
from apatch.generate import generate_patches_batch, write_jsonl


def _write_batch(tmp_path, needles):
    patches = generate_patches_batch(needles, target_dir=str(tmp_path))
    jl = tmp_path / "p.jsonl"
    with open(jl, "w", encoding="utf-8") as f:
        write_jsonl(patches, f)
    return jl


def test_all_would_apply_no_drops(tmp_path):
    (tmp_path / "a.py").write_text("AAA\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("BBB\n", encoding="utf-8")
    jl = _write_batch(tmp_path, [
        {"action": "replace", "target_file": "a.py",
         "find_text": "AAA", "replace_text": "AAA2"},
        {"action": "replace", "target_file": "b.py",
         "find_text": "BBB", "replace_text": "BBB2"},
    ])
    layout = plan_chunks_for_logs(str(jl), str(tmp_path))
    assert layout["would_apply"] == 2
    assert layout["dropped_steps"] == []
    # both files land in the (single) chunk
    flat = [s for c in layout["chunks"] for s in c]
    assert len(flat) == 2


def test_stale_anchor_is_reported_not_dropped_silently(tmp_path):
    """One file's anchor no longer matches (already applied / drifted) →
    that step is surfaced in dropped_steps, not silently omitted."""
    (tmp_path / "a.py").write_text("AAA\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("BBB\n", encoding="utf-8")
    jl = _write_batch(tmp_path, [
        {"action": "replace", "target_file": "a.py",
         "find_text": "AAA", "replace_text": "AAA2"},
        {"action": "replace", "target_file": "b.py",
         "find_text": "BBB", "replace_text": "BBB2"},
    ])
    # simulate a prior partial apply: b.py already changed, so its anchor
    # will not be found this run
    (tmp_path / "b.py").write_text("BBB2\n", encoding="utf-8")

    layout = plan_chunks_for_logs(str(jl), str(tmp_path))
    dropped = layout["dropped_steps"]
    assert any(str(d["target_file"]).endswith("b.py") for d in dropped), dropped
    # a.py still applies; b.py is loudly reported, not silently gone
    flat = [s for c in layout["chunks"] for s in c]
    assert len(flat) == 1


def test_seven_files_are_consumed_across_all_chunks(tmp_path):
    from apatch.apply_session import run_apply_session

    jl = _write_batch(
        tmp_path,
        [
            {
                "action": "create",
                "target_file": f"f{i}.txt",
                "content": str(i),
            }
            for i in range(7)
        ],
    )

    result = run_apply_session(
        str(jl),
        str(tmp_path),
        chunk_max_files=5,
        no_trustchain=True,
        quiet=True,
    )
    assert result["continue"] is True
    while result["continue"]:
        result = run_apply_session(
            str(jl),
            str(tmp_path),
            chunk_max_files=5,
            no_trustchain=True,
            quiet=True,
        )

    assert result["ok"] is True
    assert result["progress"] == {
        "chunks_done": 2,
        "chunks_total": 2,
        "percent": 100,
    }
    assert sorted(path.name for path in tmp_path.glob("f*.txt")) == [
        f"f{i}.txt" for i in range(7)
    ]


def test_dropped_plan_steps_helper():
    entries = [
        {"step_index": 1, "target_file": "a.py", "would_apply": True},
        {"step_index": 2, "target_file": "b.py", "would_apply": False,
         "strategy": "exact", "confidence": 0.0},
    ]
    dropped = dropped_plan_steps(entries)
    assert len(dropped) == 1
    assert dropped[0]["step_index"] == 2
    assert dropped[0]["target_file"] == "b.py"
    assert dropped[0]["reason"]
