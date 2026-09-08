"""Unified differential probe (apatch/probe.py) — falsify / regress / ratify are one
primitive: perturb, re-measure, judge the delta against a polarity (RFP-005)."""
import sys

import pytest

from apatch.probe import (
    POLARITY_DIVERGE,
    POLARITY_HOLD,
    differential_probe,
    falsify,
    ratify,
    regress,
)

# a command that emits one pytest-style FAILED line and exits red
FAILCMD = r"printf 'FAILED tests/test_x.py::test_y - boom\n'; exit 1"


# ── the unified core ──────────────────────────────────────────────────────────────
def test_core_diverge_passes_when_perturbation_degrades():
    seq = iter([{"ok": True}, {"ok": False}])  # before green, after red
    restored = {"v": False}
    res = differential_probe(
        measure=lambda: next(seq), polarity=POLARITY_DIVERGE,
        perturb=lambda: (lambda: restored.__setitem__("v", True)),
    )
    assert res["passed"] is True and res["degraded"] is True
    assert restored["v"] is True  # restore always runs


def test_core_diverge_fails_when_gate_survives():
    seq = iter([{"ok": True}, {"ok": True}])  # perturbation left it green
    res = differential_probe(
        measure=lambda: next(seq), polarity=POLARITY_DIVERGE,
        perturb=lambda: (lambda: None),
    )
    assert res["passed"] is False and res["degraded"] is False


def test_core_diverge_baseline_red_guard():
    res = differential_probe(
        measure=lambda: {"ok": True}, polarity=POLARITY_DIVERGE,
        baseline={"ok": False, "rc": 1},
    )
    assert res["verdict"] == "baseline_red" and res["passed"] is False


def test_core_hold_flags_new_failures_only():
    res = differential_probe(
        measure=lambda: {"ok": False, "failures": {"a", "b"}},
        polarity=POLARITY_HOLD, baseline={"ok": False, "failures": {"a"}},
    )
    assert res["passed"] is False and res["new_failures"] == ["b"]


def test_core_hold_is_baseline_aware():
    # a pre-existing baseline failure does NOT count as a regression
    res = differential_probe(
        measure=lambda: {"ok": False, "failures": {"a"}},
        polarity=POLARITY_HOLD, baseline={"ok": False, "failures": {"a"}},
    )
    assert res["passed"] is True and res["new_failures"] == []


def test_core_restore_runs_even_when_measure_raises():
    state = {"n": 0, "restored": False}

    def measure():
        state["n"] += 1
        if state["n"] == 1:
            return {"ok": True}
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        differential_probe(
            measure=measure, polarity=POLARITY_DIVERGE,
            perturb=lambda: (lambda: state.__setitem__("restored", True)),
        )
    assert state["restored"] is True


# ── driver: falsify (must_diverge) ────────────────────────────────────────────────
def _mod(tmp_path, test_body):
    (tmp_path / "mod.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    (tmp_path / "t_x.py").write_text(test_body, encoding="utf-8")


def test_falsify_real_gate_restores(tmp_path):
    _mod(tmp_path, "from mod import f\n\ndef test_f():\n    assert f() == 42\n")
    res = falsify(str(tmp_path), f"{sys.executable} -m pytest t_x.py -q -p no:cacheprovider", ["mod.py"])
    assert res["verdict"] == "real" and res["killed"] is True and res["restored_ok"] is True
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "def f():\n    return 42\n"


def test_falsify_records_gate_quality_in_active_session(tmp_path, monkeypatch):
    _mod(tmp_path, "from mod import f\n\ndef test_f():\n    assert f() == 42\n")
    committed = []

    monkeypatch.setattr(
        "apatch.session_state.load_session_state",
        lambda _root: {"session_id": "session-1", "ended_at": None},
    )

    class Recorder:
        def __init__(self, _root):
            pass

        def commit_action(self, tool_id, payload):
            committed.append((tool_id, payload))
            return True

    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", Recorder)
    result = falsify(
        str(tmp_path),
        f"{sys.executable} -m pytest t_x.py -q -p no:cacheprovider",
        ["mod.py"],
        record=True,
    )

    assert result["recorded"] is True
    assert result["gate_quality"] == "falsified"
    assert committed[0][0] == "apatch_probe"
    assert committed[0][1]["gate_quality"] == "falsified"
    assert "verify" not in committed[0][1]
    assert len(committed[0][1]["verify_sha256"]) == 64


def test_falsify_false_gate(tmp_path):
    _mod(tmp_path, "def test_ok():\n    assert 1 == 1\n")
    res = falsify(str(tmp_path), f"{sys.executable} -m pytest t_x.py -q -p no:cacheprovider", ["mod.py"])
    assert res["verdict"] == "false" and res["killed"] is False


def test_falsify_kills_a_presence_gate_in_a_document(tmp_path):
    """A gate asserting a row is present cannot be broken by adding text to the file.

    Every RFP traceability gate is such a check. With the prepend mutant alone the probe
    called all four remote SPEC gates false while removing one row really did turn them
    red -- the tool built to catch a false gate was crying wolf on real ones.
    """
    doc = tmp_path / "contract.md"
    original = "| A-1 | R1 | covered |\n"
    doc.write_text(original, encoding="utf-8")
    verify = (
        "%s -c \"from pathlib import Path; "
        "assert '| A-1 | R' in Path('contract.md').read_text()\"" % sys.executable
    )

    res = falsify(str(tmp_path), verify, ["contract.md"])

    assert res["verdict"] == "real" and res["killed"] is True
    assert res["mutation"] == "replace"
    assert res["restored_ok"] is True
    assert doc.read_text(encoding="utf-8") == original


def test_falsify_prefers_the_addition_that_already_works(tmp_path):
    """Removal is the fallback, not the default: a gate killed by addition stays so."""
    doc = tmp_path / "contract.md"
    doc.write_text("clean\n", encoding="utf-8")
    verify = (
        "%s -c \"from pathlib import Path; "
        "assert Path('contract.md').read_text() == 'clean\\n'\"" % sys.executable
    )

    res = falsify(str(tmp_path), verify, ["contract.md"])

    assert res["verdict"] == "real" and res["mutation"] == "prepend"


def test_falsify_no_files():
    assert falsify(".", "true", [])["verdict"] == "no_files"


# ── driver: ratify (must_hold — attested gate must still pass) ────────────────────
def test_ratify_still_green(tmp_path):
    assert ratify(str(tmp_path), "true")["verdict"] == "ratified"


def test_ratify_now_red_is_stale(tmp_path):
    assert ratify(str(tmp_path), "false")["verdict"] == "stale"


# ── driver: regress (must_hold — no new failures vs a recorded corpus) ────────────
def test_regress_stable_when_green(tmp_path):
    assert regress(str(tmp_path), "true")["verdict"] == "stable"


def test_regress_detects_new_failure(tmp_path):
    res = regress(str(tmp_path), FAILCMD)
    assert res["verdict"] == "regressed"
    assert res["new_failures"] == ["tests/test_x.py::test_y"]


def test_regress_baseline_aware(tmp_path):
    res = regress(str(tmp_path), FAILCMD, baseline_failures=["tests/test_x.py::test_y"])
    assert res["verdict"] == "stable" and res["new_failures"] == []


def test_regress_allowed_failures_tolerated(tmp_path):
    res = regress(str(tmp_path), FAILCMD, allowed_failures=["test_y"])
    assert res["verdict"] == "stable"
    assert res["allowed_skipped"] == ["tests/test_x.py::test_y"]


def test_falsify_requirement_backcompat_delegates(tmp_path):
    # conformance.falsify_requirement must remain a thin shim over probe.falsify
    from apatch.conformance import falsify_requirement
    _mod(tmp_path, "from mod import f\n\ndef test_f():\n    assert f() == 42\n")
    res = falsify_requirement(str(tmp_path), f"{sys.executable} -m pytest t_x.py -q -p no:cacheprovider", ["mod.py"])
    assert res["verdict"] == "real" and res["killed"] is True
