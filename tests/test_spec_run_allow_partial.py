"""Subset batches are a legal delivery (allow_partial, RFP-009 UX).

Regression for a real-world friction: an agent supplied needles for a
ready subset of pending Rk (R3-R6 of a 14-Rk spec) and spec_run refused
the whole batch with MANIFEST_GAP, forcing N x execute_next round-trips.
With ``allow_partial=true`` the inline requirements map IS the work list:
covered Rk run and attest, the rest stay pending and are reported in
``skipped_pending`` — never silently, never fatally. The default remains
strict (no behavior change for existing callers).
"""

from __future__ import annotations

from apatch.spec_run import (
    ERROR_MANIFEST_GAP,
    lint_run_manifest_workspace,
    spec_run_enriched,
)
from tests.test_spec_run import (  # noqa: F401  (shared fixtures)
    SPEC_TWO,
    _FakeTrustChain,
    _manifest_two_rk,
    _write_spec,
)


def _subset_manifest(tmp_path):
    manifest = _manifest_two_rk(tmp_path)
    del manifest["requirements"]["R2"]
    return manifest


def test_lint_default_stays_strict(tmp_path):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    out = lint_run_manifest_workspace(
        str(tmp_path), manifest=_subset_manifest(tmp_path))
    assert out["ok"] is False
    assert out["error_type"] == ERROR_MANIFEST_GAP


def test_lint_allow_partial_reports_skips(tmp_path):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    out = lint_run_manifest_workspace(
        str(tmp_path), manifest=_subset_manifest(tmp_path), allow_partial=True)
    assert out["ok"] is True
    assert out["skipped_pending"] == ["R2"]
    assert any("partial run" in w for w in out.get("warnings") or [])


def test_spec_run_allow_partial_runs_subset(tmp_path, monkeypatch):
    _write_spec(tmp_path, "SPEC-TWO", SPEC_TWO)
    monkeypatch.setattr(
        "apatch.trustchain_helper.TrustChainHelper",
        lambda *_a, **_k: _FakeTrustChain(),
    )
    reqs = _subset_manifest(tmp_path)["requirements"]

    strict = spec_run_enriched(
        str(tmp_path), spec="SPEC-TWO", requirements=reqs, reset=True)
    assert strict["ok"] is False
    assert strict["error_type"] == ERROR_MANIFEST_GAP

    out = spec_run_enriched(
        str(tmp_path), spec="SPEC-TWO", requirements=reqs, reset=True,
        allow_partial=True)
    assert out["ok"] is True
    # the covered Rk really ran: R1's marker exists
    assert (tmp_path / "marker-r1.txt").read_text(encoding="utf-8") == "BEFORE\n"
    # the uncovered Rk is reported, and the run does not claim completion
    assert out["skipped_pending"] == ["R2"]
    assert out.get("done") is not True
