import json
import os

from click.testing import CliRunner

from apatch.cli import cli
from apatch.discovery import discover_transcripts, count_candidates


def _write_transcript(path):
    step = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": "main.cpp",
                "TargetContent": "int a = 1;",
                "ReplacementContent": "int a = 2;",
            },
        }],
    }
    path.write_text(json.dumps(step) + "\n", encoding="utf-8")


def test_discover_finds_extra_path_and_counts(tmp_path, monkeypatch):
    # Isolate HOME so the known Cursor/Claude/Gemini globs don't scan the real
    # machine (which can be huge and slow).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    t1 = logs_dir / "a.jsonl"
    t2 = logs_dir / "b.jsonl"
    _write_transcript(t1)
    _write_transcript(t2)

    found = discover_transcripts(extra_paths=[str(logs_dir)], include_cwd=False)
    paths = {f.path for f in found}
    assert os.path.realpath(str(t1)) in paths
    assert os.path.realpath(str(t2)) in paths

    assert count_candidates(str(t1)) == 1


def test_scan_command_json(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    _write_transcript(logs_dir / "a.jsonl")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["scan", "--path", str(logs_dir), "--no-cwd", "--json"], env={"HOME": str(home)}
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(entry["candidate_count"] == 1 for entry in data)
    assert all("modified" in entry for entry in data)


def test_scan_command_table_empty(tmp_path):
    runner = CliRunner()
    empty = tmp_path / "empty"
    empty.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    # Point HOME at an empty dir so the known Cursor/Claude/Gemini globs resolve
    # to nothing and we get the deterministic "no transcripts" path.
    result = runner.invoke(
        cli, ["scan", "--path", str(empty), "--no-cwd"], env={"HOME": str(home)}
    )
    assert result.exit_code == 0
    assert "No agent transcripts" in result.output
