"""Gate falsification (apatch spec falsify) — a green gate must be able to go RED."""
import os
import sys

import pytest


def _mod_and_test(tmp_path, test_body):
    (tmp_path / "mod.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    (tmp_path / "t_x.py").write_text(test_body, encoding="utf-8")


def test_falsify_real_gate(tmp_path):
    # The verify imports & tests mod.py, so corrupting mod.py makes verify RED:
    # the gate KILLS the mutant -> verdict "real".
    _mod_and_test(tmp_path, "from mod import f\n\ndef test_f():\n    assert f() == 42\n")
    from apatch.conformance import falsify_requirement

    res = falsify_requirement(
        str(tmp_path), f"{sys.executable} -m pytest t_x.py -q -p no:cacheprovider", ["mod.py"]
    )
    assert res["verdict"] == "real" and res["killed"] is True
    assert res["restored_ok"] is True
    # the guarded file is restored byte-for-byte
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "def f():\n    return 42\n"


def test_falsify_false_gate(tmp_path):
    # The verify does NOT exercise mod.py, so corrupting it stays GREEN: the gate
    # SURVIVES the mutant -> verdict "false" (the test does not test the requirement).
    _mod_and_test(tmp_path, "def test_ok():\n    assert 1 == 1\n")
    from apatch.conformance import falsify_requirement

    res = falsify_requirement(
        str(tmp_path), f"{sys.executable} -m pytest t_x.py -q -p no:cacheprovider", ["mod.py"]
    )
    assert res["verdict"] == "false" and res["killed"] is False
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "def f():\n    return 42\n"


def test_falsify_no_files(tmp_path):
    from apatch.conformance import falsify_requirement

    res = falsify_requirement(str(tmp_path), "true", [])
    assert res["verdict"] == "no_files"
