from __future__ import annotations

import json
import shlex
import sys

from apatch.workflows import apply_from_logs


def _patch(path, target="app.py"):
    path.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [
                    {
                        "name": "replace_file_content",
                        "arguments": {
                            "TargetFile": target,
                            "TargetContent": "value = 'old'",
                            "ReplacementContent": "value = 'new'",
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


def test_apply_keeps_change_when_only_preexisting_failure_remains(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    log = tmp_path / "patches.jsonl"
    _patch(log)
    verify = _command(
        "print('FAILED tests/test_old.py::test_legacy - boom'); "
        "raise SystemExit(1)"
    )

    result = apply_from_logs(
        str(log),
        str(tmp_path),
        verify=verify,
        verify_deferred=True,
        no_trustchain=True,
        quiet=True,
    )
    assert result["ok"] is True
    assert result["verify_rollback"] is False
    assert "value = 'new'" in target.read_text(encoding="utf-8")
    assert result["verify_baseline"]["new_failures"] == []


def test_apply_rolls_back_only_new_failure(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    log = tmp_path / "patches.jsonl"
    _patch(log)
    script = (
        "from pathlib import Path; "
        "text=Path('app.py').read_text(); "
        "print('FAILED tests/test_old.py::test_legacy - boom'); "
        "print('FAILED tests/test_new.py::test_regression - boom') if \"'new'\" in text else None; "
        "raise SystemExit(1)"
    )

    result = apply_from_logs(
        str(log),
        str(tmp_path),
        verify=_command(script),
        verify_deferred=True,
        no_trustchain=True,
        quiet=True,
    )
    assert result["ok"] is False
    assert result["verify_rollback"] is True
    assert "value = 'old'" in target.read_text(encoding="utf-8")
    assert result["verify_baseline"]["new_failures"] == [
        "tests/test_new.py::test_regression"
    ]

