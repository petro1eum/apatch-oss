"""Regression: batched needles touching the same region fail loud, not corrupt.

Two needles in one generate batch — an ``insert_before`` anchored at ``def beta``
and a ``replace`` of beta's body — touch overlapping lines. Both are computed
against the original file, so applying them sequentially used to drift the second
patch's context and silently corrupt the file while apply reported success.

``generate_patches_batch`` now detects overlapping same-file needle ranges and
refuses the batch loudly, so nothing is applied and the file is left untouched.
Far-apart same-file needles are unaffected.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from apatch.workflows import generate_patch_jsonl_batch


def test_overlapping_batched_needles_fail_loud_not_corrupt():
    d = tempfile.mkdtemp()
    try:
        src = os.path.join(d, "mod.py")
        original = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(original)

        needles = [
            {
                "action": "replace",
                "target_file": "mod.py",
                "find_text": "def beta():\n    return 2",
                "replace_text": "def beta():\n    return 222",
            },
            {
                "action": "insert_before",
                "target_file": "mod.py",
                "before": "def beta",
                "content": "def inserted():\n    return 99\n\n\n",
            },
        ]
        out = generate_patch_jsonl_batch(
            needles=needles, target_dir=d, out_path=os.path.join(d, "p.jsonl")
        )

        # generate refuses overlapping same-file needles loudly — no patch produced
        assert out["ok"] is False
        assert "overlap" in out["error"].lower()
        # the source file is never touched (no silent corruption)
        assert open(src, encoding="utf-8").read() == original
    finally:
        shutil.rmtree(d)


def test_far_apart_replace_and_insert_before_apply_in_order():
    d = tempfile.mkdtemp()
    try:
        src = os.path.join(d, "mod.py")
        body = "def a():\n    return 1\n" + "\n" * 20 + "def z():\n    return 26\n"
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(body)

        out_path = os.path.join(d, "p.jsonl")
        out = generate_patch_jsonl_batch(
            needles=[
                {"action": "replace", "target_file": "mod.py",
                 "find_text": "    return 1", "replace_text": "    return 111"},
                {"action": "insert_before", "target_file": "mod.py",
                 "before": "def z", "content": "def inserted():\n    return 99\n\n"},
            ],
            target_dir=d,
            out_path=out_path,
        )

        assert out["ok"] is True
        import json
        rows = [json.loads(line) for line in open(out_path, encoding="utf-8")]
        insert_args = rows[1]["tool_calls"][0]["arguments"]
        assert insert_args["TargetContent"] == "def z():"
        assert "def a" not in insert_args["TargetContent"]

        from apatch.workflows import apply_from_logs
        applied = apply_from_logs(out_path, d, no_trustchain=True, quiet=True)
        assert applied["ok"] is True
        final = open(src, encoding="utf-8").read()
        assert "return 111" in final
        assert "def inserted():\n    return 99\n\n" in final
        assert final.index("def inserted") < final.index("def z")
    finally:
        shutil.rmtree(d)


def test_far_apart_same_file_needles_are_allowed():
    d = tempfile.mkdtemp()
    try:
        src = os.path.join(d, "mod.py")
        body = "def a():\n    return 1\n" + "\n" * 20 + "def z():\n    return 26\n"
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(body)

        needles = [
            {"action": "replace", "target_file": "mod.py",
             "find_text": "    return 1", "replace_text": "    return 111"},
            {"action": "replace", "target_file": "mod.py",
             "find_text": "    return 26", "replace_text": "    return 2626"},
        ]
        out = generate_patch_jsonl_batch(
            needles=needles, target_dir=d, out_path=os.path.join(d, "p.jsonl")
        )
        # far-apart needles in the same file are not blocked
        assert out["ok"] is True
        assert out["count"] == 2
    finally:
        shutil.rmtree(d)