"""Add File byte fidelity: created content round-trips exactly.

Regression for a real-world failure: `create` needles ending with a
trailing newline produced files WITHOUT it (generator dropped it via
splitlines, parser reassembled with plain join), so the next needle's
find_text anchored at EOF never matched. The fix uses the unified-diff
"\\ No newline at end of file" marker so both shapes round-trip.
"""

from apatch.generate import _format_apply_patch_add_file
from apatch.ingestor import parse_apply_patch


def _roundtrip(content: str) -> str:
    envelope = _format_apply_patch_add_file("pkg/mod.py", content)
    records = parse_apply_patch(envelope)
    assert len(records) == 1
    rec = records[0]
    assert rec["action"] == "CREATE"
    assert rec["path"] == "pkg/mod.py"
    return rec["new"]


def test_add_file_preserves_trailing_newline():
    content = "line1\nline2\n"
    assert _roundtrip(content) == content


def test_add_file_preserves_missing_trailing_newline():
    content = "line1\nline2"
    assert _roundtrip(content) == content


def test_add_file_marker_present_only_without_newline():
    with_nl = _format_apply_patch_add_file("a.py", "x\n")
    without_nl = _format_apply_patch_add_file("a.py", "x")
    assert "No newline at end of file" not in with_nl
    assert "No newline at end of file" in without_nl


def test_add_file_empty_content_stays_empty():
    assert _roundtrip("") == ""


def test_add_file_single_line_with_newline():
    assert _roundtrip("only\n") == "only\n"
