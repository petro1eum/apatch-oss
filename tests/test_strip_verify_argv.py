"""apatch_strip.verify accepts an argv list (consistency with verify_run/apply_session)."""
import pytest

pytest.importorskip("mcp")


def test_apatch_strip_verify_accepts_argv_schema():
    from apatch.mcp import server as S

    tm = getattr(S.mcp, "_tool_manager", None)
    assert tm is not None and hasattr(tm, "_tools")
    strip = tm._tools["apatch_strip"]
    props = (strip.parameters or {}).get("properties", {})
    blob = str(props.get("verify", {})).lower()
    # verify now allows an argv array (previously string-only → pydantic rejected lists)
    assert "array" in blob
    assert "string" in blob