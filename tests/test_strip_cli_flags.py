"""CLI integration: strip --verify rollback, --strict-dangling, default export ext."""

import json

from click.testing import CliRunner

from apatch.cli import cli
from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX


def test_strip_verify_failure_rolls_back(tmp_path):
    page = tmp_path / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    original = page.read_text(encoding="utf-8")

    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")
    out = tmp_path / "extracted"
    out.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file",
            str(page),
            "--manifest",
            str(manifest),
            "--out-dir",
            str(out),
            "--no-trustchain",
            "--verify",
            "exit 1",
        ],
    )
    assert result.exit_code != 0
    assert page.read_text(encoding="utf-8") == original


def test_strip_strict_dangling_fails(tmp_path):
    page = tmp_path / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")

    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")
    out = tmp_path / "extracted"
    out.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file",
            str(page),
            "--manifest",
            str(manifest),
            "--out-dir",
            str(out),
            "--no-trustchain",
            "--strict-dangling",
        ],
    )
    assert result.exit_code != 0
    assert "dangling" in result.output.lower()


def test_default_export_ext_is_fragment_txt_for_tsx(tmp_path):
    from apatch.strip import default_export_ext

    assert default_export_ext("foo.tsx") == ".fragment.txt"
    assert default_export_ext("eval.cpp") == ".extracted.cpp"
