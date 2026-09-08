import json

from apatch.orchestrate import run_orchestrate


def _write_log(path, n=2):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            (path.parent / f"f{i}.py").write_text(f"x{i} = 1\n", encoding="utf-8")
            step = {
                "step_index": i + 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": f"f{i}.py",
                        "TargetContent": f"x{i} = 1",
                        "ReplacementContent": f"x{i} = 2",
                    },
                }],
            }
            f.write(json.dumps(step) + "\n")


def test_orchestrate_dry_run(tmp_path):
    log = tmp_path / "patches.jsonl"
    _write_log(log, n=2)
    manifest = tmp_path / "pipe.json"
    manifest.write_text(
        json.dumps({
            "kind": "engineering-pipeline",
            "patches_jsonl": "patches.jsonl",
            "phases": [{"action": "plan"}, {"action": "apply"}],
        }),
        encoding="utf-8",
    )
    result = run_orchestrate(str(manifest), str(tmp_path), dry_run=True)
    assert result["ok"] is True
    assert result["graph"]["ok"] is True
    assert result["execution"]["dry_run"] is True
