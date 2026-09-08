"""Baseline-aware verify + argv verify (feedback: pre-existing red must not block)."""

from __future__ import annotations

import sys

from apatch.runtime.runtime import MutationRuntime
from apatch.verify_baseline import (
    compare_failures,
    load_baseline,
    parse_failed_tests,
    save_baseline,
)


def test_parse_failed_tests_dedup_and_errors():
    out = (
        "FAILED tests/test_old.py::test_legacy - AssertionError: boom\n"
        "FAILED tests/test_old.py::test_legacy - AssertionError: boom\n"
        "ERROR tests/test_setup.py::test_import\n"
        "=== 2 failed, 1 error in 0.5s ===\n"
    )
    assert parse_failed_tests(out) == [
        "tests/test_old.py::test_legacy",
        "tests/test_setup.py::test_import",
    ]


def test_compare_failures_buckets():
    report = compare_failures(
        ["a.py::t1", "b.py::t2", "c.py::t3"],
        baseline=["a.py::t1"],
        allowed_failures=["t2"],
    )
    assert report["pre_existing_failures"] == ["a.py::t1"]
    assert report["allowed_failures_matched"] == ["b.py::t2"]
    assert report["new_failures"] == ["c.py::t3"]


def test_save_and_load_baseline_roundtrip(tmp_path):
    save_baseline(str(tmp_path), verify_command="pytest", failures=["x.py::t"])
    data = load_baseline(str(tmp_path))
    assert data["failures"] == ["x.py::t"]
    assert data["verify_command"] == "pytest"


def _verify_cmd_failing(nodes):
    lines = "; ".join(f"print('FAILED {n} - boom')" for n in nodes)
    return f'{sys.executable} -c "{lines}; import sys; sys.exit(1)"'


def test_verify_run_baseline_capture_then_compare_pre_existing(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    cmd = _verify_cmd_failing(["tests/test_old.py::test_legacy"])
    cap = rt.verify_run(verify=cmd, baseline="capture", skip_transition_check=True)
    assert cap["ok"] is True
    assert cap["baseline"]["failures"] == ["tests/test_old.py::test_legacy"]

    cmp_res = rt.verify_run(verify=cmd, baseline="compare", skip_transition_check=True)
    assert cmp_res["ok"] is True
    assert cmp_res["pre_existing_only"] is True
    assert cmp_res["baseline"]["new_failures"] == []


def test_verify_run_baseline_compare_flags_new_failure(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    rt.verify_run(
        verify=_verify_cmd_failing(["tests/test_old.py::test_legacy"]),
        baseline="capture",
        skip_transition_check=True,
    )
    res = rt.verify_run(
        verify=_verify_cmd_failing(
            ["tests/test_old.py::test_legacy", "tests/test_new.py::test_mine"]
        ),
        baseline="compare",
        skip_transition_check=True,
    )
    assert res["ok"] is False
    assert res["baseline"]["new_failures"] == ["tests/test_new.py::test_mine"]
    assert res["baseline"]["pre_existing_failures"] == ["tests/test_old.py::test_legacy"]


def test_verify_run_allowed_failures_without_baseline(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    res = rt.verify_run(
        verify=_verify_cmd_failing(["tests/test_flaky.py::test_known"]),
        allowed_failures=["test_flaky.py::test_known"],
        skip_transition_check=True,
    )
    assert res["ok"] is True
    assert res["pre_existing_only"] is True


def test_verify_run_rejects_bad_baseline_mode(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    res = rt.verify_run(
        verify="true", baseline="bogus", skip_transition_check=True
    )
    assert res["ok"] is False
    assert "baseline" in res["error"]


def test_verify_run_accepts_argv_list_with_spaces(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    res = rt.verify_run(
        verify=[sys.executable, "-c", "assert 'not x' == 'not x'"],
        skip_transition_check=True,
    )
    assert res["ok"] is True
    assert isinstance(res["verify_command"], list)


def test_verify_run_argv_list_failure_reported(tmp_path):
    rt = MutationRuntime(str(tmp_path))
    res = rt.verify_run(
        verify=[sys.executable, "-c", "import sys; sys.exit(3)"],
        skip_transition_check=True,
    )
    assert res["ok"] is False
    assert "exit 3" in res["error"]