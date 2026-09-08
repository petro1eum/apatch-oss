"""Gate for REC-7855: apatch_reality(status) reporting debt is a valid result, NOT a
tool failure. enrich_tool_response flags any ok:false as a failure, so the status branch
must surface the coverage signal as `clean` and keep operational `ok` true."""
import pytest

pytest.importorskip("mcp")

from apatch.mcp import server as mcp_server


def _reality():
    return mcp_server.mcp._tool_manager._tools["apatch_reality"].fn


def test_status_with_debt_is_not_a_tool_failure(tmp_path):
    reality = _reality()
    reality(action="add", summary="an observed bug", source="dogfood", kind="bug",
            status="open", rec_id="", spec="", target_dir=str(tmp_path))
    res = reality(action="status", summary="", source="", kind="observation",
                  status="open", rec_id="", spec="", target_dir=str(tmp_path))
    # debt exists, but the STATUS QUERY itself succeeded
    assert res.get("ok") is True
    assert res.get("clean") is False
    assert len(res.get("uncovered") or []) == 1
    # enrich must NOT have turned "debt exists" into an operational failure
    assert res.get("failure") in (None, {})
    assert res.get("error_type") is None
    assert res.get("recommended_action") != "rollback"
    assert (res.get("state_update") or {}).get("phase") != "blocked"


def test_status_clean_when_no_debt(tmp_path):
    res = _reality()(action="status", summary="", source="", kind="observation",
                     status="open", rec_id="", spec="", target_dir=str(tmp_path))
    assert res.get("ok") is True and res.get("clean") is True
    assert not res.get("uncovered")
