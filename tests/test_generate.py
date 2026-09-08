import json
from apatch.generate import generate_patches, write_jsonl


def test_generate_patches(tmp_path):
    f = tmp_path / "models.py"
    f.write_text("x = old\ny = old\n", encoding="utf-8")
    patches = generate_patches(
        find="old",
        replace="new",
        target_dir=str(tmp_path),
        glob_pattern="*.py",
    )
    assert len(patches) == 1
    assert patches[0]["tool_calls"][0]["arguments"]["AllowMultiple"] is True


def test_write_jsonl(tmp_path):
    patches = [{"step_index": 1, "tool_calls": []}]
    out = tmp_path / "p.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        n = write_jsonl(patches, fh)
    assert n == 1
    data = json.loads(out.read_text(encoding="utf-8").strip())
    assert data["step_index"] == 1
