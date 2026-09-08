"""SPEC-APPLY-OBS-1#R3: deferred verify must not be demoted to per-step on the last chunk."""

from apatch.apply_session import run_apply_session
from apatch.workflows import generate_patch_jsonl_batch


def _gen(tmp_path):
    log = tmp_path / "p.jsonl"
    gen = generate_patch_jsonl_batch(
        needles=[
            {"action": "create", "target_file": "pkg_mod.py", "content": "VALUE = 1\n"},
            {"action": "create", "target_file": "pkg_mod_check.py", "content": "import pkg_mod\n"},
        ],
        target_dir=str(tmp_path),
        out_path=str(log),
    )
    assert gen["ok"], gen
    return log


def test_interdependent_creates_pass_with_deferred_verify(tmp_path):
    """Two creates in one (last) chunk where verify needs BOTH files.

    Before the fix run_apply_session forced verify_deferred=False on the last
    chunk, so verify ran after EACH step against a half-applied chunk and
    rolled everything back (the SPEC-COVERAGE-1 R1 failure mode).
    """
    log = _gen(tmp_path)
    res = run_apply_session(
        str(log),
        str(tmp_path),
        verify="test -f pkg_mod.py && test -f pkg_mod_check.py",
        verify_deferred=True,
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert res["ok"] is True, res.get("verify_output")
    assert res["chunk_result"]["applied"] == 2
    assert res["chunk_result"]["verify_rollback"] is False
    assert (tmp_path / "pkg_mod.py").exists()
    assert (tmp_path / "pkg_mod_check.py").exists()


def test_deferred_verify_waits_for_final_chunk(tmp_path):
    """A repository verify must not run against a partially applied session."""
    log = _gen(tmp_path)
    verify = "test -f pkg_mod.py && test -f pkg_mod_check.py"

    first = run_apply_session(
        str(log),
        str(tmp_path),
        verify=verify,
        verify_deferred=True,
        chunk_max_files=1,
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert first["ok"] is True, first.get("verify_output")
    assert first["continue"] is True
    assert (tmp_path / "pkg_mod.py").exists()
    assert not (tmp_path / "pkg_mod_check.py").exists()

    final = run_apply_session(
        str(log),
        str(tmp_path),
        verify=verify,
        verify_deferred=True,
        chunk_max_files=1,
        no_trustchain=True,
        quiet=True,
    )
    assert final["ok"] is True, final.get("verify_output")
    assert final["continue"] is False
    assert (tmp_path / "pkg_mod.py").exists()
    assert (tmp_path / "pkg_mod_check.py").exists()


def test_deferred_verify_failure_still_rolls_back_chunk(tmp_path):
    """Deferred verify failure on the last chunk must roll the chunk back."""
    log = _gen(tmp_path)
    res = run_apply_session(
        str(log),
        str(tmp_path),
        verify="exit 1",
        verify_deferred=True,
        no_trustchain=True,
        reset=True,
        quiet=True,
    )
    assert res["ok"] is False
    assert not (tmp_path / "pkg_mod.py").exists()
    assert not (tmp_path / "pkg_mod_check.py").exists()
    # R1 invariant: failed session state is cleared for a clean retry.
    assert not (tmp_path / ".apatch" / "apply_session.json").exists()