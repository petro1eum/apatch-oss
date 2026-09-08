import json

from apatch.ingestor import PatchCandidate
from apatch.tui import InteractiveTUI


def _cand(old, new, target="main.cpp"):
    return PatchCandidate(
        step_index=1,
        tool_name="replace_file_content",
        target_file=target,
        old_content=old,
        new_content=new,
    )


def test_only_drifted_skips_exact(tmp_path):
    src = tmp_path / "main.cpp"
    original = "int a = 1;\n"
    src.write_text(original, encoding="utf-8")
    report = tmp_path / "rep.json"

    tui = InteractiveTUI(
        [_cand("int a = 1;", "int a = 2;")],
        str(tmp_path),
        non_interactive=True,
        no_trustchain=True,
        only_drifted=True,
        report_path=str(report),
    )
    tui.run_apply_loop()

    # Exact match is not "drifted" -> skipped, file untouched.
    assert src.read_text(encoding="utf-8") == original
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["entries"][0]["outcome"] == "skipped"
    assert data["entries"][0]["reason"] == "only-drifted"


def test_min_confidence_blocks_low_confidence(tmp_path):
    src = tmp_path / "main.cpp"
    original = "int   a   =   1;\n"  # whitespace-drifted vs the proposed old_content
    src.write_text(original, encoding="utf-8")

    tui = InteractiveTUI(
        [_cand("int a = 1;", "int a = 2;")],
        str(tmp_path),
        non_interactive=True,
        no_trustchain=True,
        min_confidence=0.9,  # whitespace-fuzzy is 0.85 -> blocked
    )
    tui.run_apply_loop()
    assert src.read_text(encoding="utf-8") == original  # unchanged


def test_min_confidence_allows_when_above(tmp_path):
    src = tmp_path / "main.cpp"
    src.write_text("int   a   =   1;\n", encoding="utf-8")

    tui = InteractiveTUI(
        [_cand("int a = 1;", "int a = 2;")],
        str(tmp_path),
        non_interactive=True,
        no_trustchain=True,
        min_confidence=0.8,  # 0.85 >= 0.8 -> applied
    )
    tui.run_apply_loop()
    assert "int a = 2;" in src.read_text(encoding="utf-8")
