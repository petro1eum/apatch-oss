import json

from click.testing import CliRunner

from apatch.cli import cli


def _make_log(tmp_path, target_rel, old, new):
    log = tmp_path / "t.jsonl"
    step = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": target_rel,
                "TargetContent": old,
                "ReplacementContent": new,
            },
        }],
    }
    log.write_text(json.dumps(step) + "\n", encoding="utf-8")
    return log


def test_plan_json_exact(tmp_path):
    src = tmp_path / "main.cpp"
    src.write_text("int a = 1;\n", encoding="utf-8")
    log = _make_log(tmp_path, "main.cpp", "int a = 1;", "int a = 2;")

    runner = CliRunner()
    result = runner.invoke(cli, ["plan", "--logs", str(log), "--target-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["strategy"] == "exact"
    assert data[0]["confidence"] == 1.0
    assert data[0]["would_apply"] is True


def test_plan_json_unresolved_path(tmp_path):
    log = _make_log(tmp_path, "does_not_exist.cpp", "int a = 1;", "int a = 2;")

    runner = CliRunner()
    result = runner.invoke(cli, ["plan", "--logs", str(log), "--target-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["would_apply"] is False
    assert data[0]["resolved_path"] is None


def test_plan_does_not_modify_file(tmp_path):
    src = tmp_path / "main.cpp"
    original = "int a = 1;\n"
    src.write_text(original, encoding="utf-8")
    log = _make_log(tmp_path, "main.cpp", "int a = 1;", "int a = 2;")

    runner = CliRunner()
    runner.invoke(cli, ["plan", "--logs", str(log), "--target-dir", str(tmp_path)])
    assert src.read_text(encoding="utf-8") == original
