import json
import copy

import pytest

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


@pytest.mark.parametrize("state", ["failed", "denied", "blocked", "unknown", "rolled_back"])
def test_compact_keeps_late_refusal_reason_action_and_full_report(state):
    raw = {"ok": False, "report_path": ".apatch/отчёт.json",
           "entries": [{"outcome": "applied", "step_index": i} for i in range(30)] + [{
               "outcome": state, "step_index": 30, "reason": 'Нет доступа: "проект 😀"',
               "next_action": "correct_binding", "evidence_id": "ev_complete_identifier"}]}
    original = copy.deepcopy(raw)
    compact = sanitize_tool_result(compact_apply_result(raw))
    assert compact["entries"][0] == raw["entries"][-1]
    assert compact["entries_retained_indices"][0] == 30
    assert compact["report_path"] == raw["report_path"]
    assert compact["ok"] is False
    assert len(compact["entries"]) == 25
    assert compact["entries_omitted"] == 6
    assert compact["entries_critical_omitted"] == 0
    if state == "unknown":
        assert compact["do_not_auto_retry"] is True
    assert raw == original
    assert json.loads(json.dumps(compact, ensure_ascii=False)) == compact


def test_compact_prioritizes_multiple_errors_and_marks_overflow_without_retry():
    entries = [{"outcome": "applied"} for _ in range(30)] + [
        {"outcome": "failed", "reason": f"refusal-{i}", "recommended_action": "inspect_report"}
        for i in range(4)]
    result = compact_apply_result({"ok": False, "entries": entries, "report_path": ".apatch/report.json"}, max_entries=2)
    assert [item["reason"] for item in result["entries"]] == ["refusal-0", "refusal-1"]
    assert result["entries_critical_total"] == 4
    assert result["entries_critical_omitted"] == 2
    assert result["summary_incomplete"] is True
    assert result["do_not_auto_retry"] is True


def test_compact_unknown_entries_do_not_invent_success_or_a_report():
    result = compact_apply_result({"ok": False, "entries": ["unrecognized"] * 30}, max_entries=1)
    assert result["ok"] is False
    assert "report_path" not in result
    assert "Do not repeat" in result["entries_note"]
    assert result["summary_incomplete"] is True and result["do_not_auto_retry"] is True
    assert result["recommended_action"] == "inspect_saved_result"


def test_repeated_compaction_preserves_original_counts_positions_and_incompleteness():
    raw = {"ok": False, "report_path": ".apatch/report.json",
           "entries": [{"outcome": "applied"} for _ in range(30)] + [
               {"outcome": "failed", "reason": f"denied-{i}", "next_action": "inspect_report"}
               for i in range(5)]}
    first = compact_apply_result(raw, max_entries=3)
    again = compact_apply_result(first, max_entries=1)
    assert again["entries_total"] == 35 and again["entries_omitted"] == 34
    assert again["entries_retained_indices"] == [30]
    assert again["entries_critical_total"] == 5 and again["entries_critical_omitted"] == 4
    assert again["summary_incomplete"] is True and again["do_not_auto_retry"] is True


@pytest.mark.parametrize("limit", [0, -1, True])
def test_compact_invalid_entry_budget_fails_explicitly(limit):
    with pytest.raises(ValueError, match="positive integer"):
        compact_apply_result({"entries": [1, 2, 3]}, max_entries=limit)
