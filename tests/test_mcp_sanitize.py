import json

from apatch.mcp.sanitize import (
    compact_apply_result,
    sanitize_for_json,
    sanitize_text,
    sanitize_tool_result,
)


def test_sanitize_text_replaces_invalid_utf8():
    bad = "ok \udcff bad"
    clean = sanitize_text(bad)
    assert "\udcff" not in clean
    json.dumps(clean)


def test_sanitize_for_json_nested():
    raw = {"diff": "line\n\xff", "n": 1, "items": list(range(50))}
    clean = sanitize_for_json(raw, max_list=5)
    assert clean["n"] == 1
    assert len(clean["items"]) == 6
    assert "truncated" in clean["items"][-1]
    json.dumps(clean, ensure_ascii=False)


def test_sanitize_tool_result_roundtrip():
    payload = {"msg": "тест \xff", "ok": True}
    out = sanitize_tool_result(payload)
    json.dumps(out, ensure_ascii=False)


def test_compact_apply_result_truncates_entries():
    entries = [{"step_index": i} for i in range(100)]
    result = compact_apply_result(
        {"ok": True, "entries": entries, "report_path": ".apatch/r.json"},
        max_entries=10,
    )
    assert len(result["entries"]) == 10
    assert result["entries_truncated"] is True
    assert result["entries_total"] == 100
