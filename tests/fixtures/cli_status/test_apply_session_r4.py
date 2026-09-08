
def test_apply_session_human_output(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    import json

    rec = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": "a.py",
                "TargetContent": "x = 1",
                "ReplacementContent": "x = 2",
            },
        }],
    }
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "apply-session",
            "--logs",
            str(log),
            "--target-dir",
            str(tmp_path),
            "--no-trustchain",
            "--reset",
        ],
    )
    assert result.exit_code == 0
    assert "chunk" in result.output.lower()
    assert '"ok"' not in result.output
