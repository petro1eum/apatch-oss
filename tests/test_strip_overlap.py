from click.testing import CliRunner
from apatch.cli import cli


def test_strip_strict_overlap_fails(tmp_path):
    mono = tmp_path / "mono.cpp"
    mono.write_text(
        "// --- A ---\n"
        "code a\n"
        "// --- B ---\n"
        "code b\n"
        "// --- END ---\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "bad.json"
    manifest.write_text(
        """[
  {"start": "// --- A ---", "until": "// --- END ---", "label": "a"},
  {"start": "// --- B ---", "until": "// --- END ---", "label": "b"}
]""",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file",
            str(mono),
            "--manifest",
            str(manifest),
            "-n",
            "--strict-overlap",
            "--out-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    assert "overlap" in result.output.lower()
