"""Exercise the actual MCP tool wrapper with a disposable tool registry."""
import os
from types import SimpleNamespace

import pytest

from apatch.mcp import server as mcp_server
from apatch.trust_identity import active_workspace_identity_scope, load_local_identity
from tests.test_workspace_signer_scope import project


def wrapper_fixture(fn, monkeypatch):
    tool = SimpleNamespace(fn=fn, name="fixture_scope_projection")
    registry = SimpleNamespace(_tool_manager=SimpleNamespace(_tools={"fixture": tool}))
    monkeypatch.setattr(mcp_server, "mcp", registry)
    mcp_server._wrap_mcp_tools()
    return tool.fn


@pytest.mark.parametrize(
    "requested,expected",
    [("@verified-fixture", "project-fixture"), (".", "caller-agent-fixture")],
)
def test_only_successfully_verified_roaming_alias_receives_project_scope(
    project, monkeypatch, requested, expected,
):
    from apatch.mcp import bound_workspace, lifecycle
    from apatch import lane_context

    before = dict(os.environ)

    def verified_route(kwargs):
        assert kwargs["target_dir"] in {"@verified-fixture", "."}
        kwargs["target_dir"] = str(project)
        return kwargs

    monkeypatch.setattr(bound_workspace, "resolve_target_dir_kwargs", verified_route)
    monkeypatch.setattr(lifecycle, "touch_workspace", lambda *args: None)
    monkeypatch.setattr(lane_context, "bind_lane_from_kwargs", lambda *args: None)

    def native_call(target_dir=".", actor_id="untrusted-role-text"):
        return {
            "identity": load_local_identity(target_dir).agent_id,
            "state_update": {"phase": "idle"},
        }

    call = wrapper_fixture(native_call, monkeypatch)
    assert call(target_dir=requested, actor_id="authority-spoof")["identity"] == expected
    assert active_workspace_identity_scope() is None
    assert dict(os.environ) == before


def test_scoped_wrapper_restores_context_after_tool_exception(project, monkeypatch):
    from apatch.mcp import bound_workspace, lifecycle
    from apatch import lane_context

    monkeypatch.setattr(
        bound_workspace, "resolve_target_dir_kwargs",
        lambda kwargs: kwargs.update(target_dir=str(project)),
    )
    monkeypatch.setattr(lifecycle, "touch_workspace", lambda *args: None)
    monkeypatch.setattr(lane_context, "bind_lane_from_kwargs", lambda *args: None)

    def fail(target_dir="."):
        assert load_local_identity(target_dir).agent_id == "project-fixture"
        raise RuntimeError("controlled-tool-fixture")

    with pytest.raises(RuntimeError, match="controlled-tool-fixture"):
        wrapper_fixture(fail, monkeypatch)(target_dir="@verified-fixture")
    assert active_workspace_identity_scope() is None
    assert load_local_identity(str(project)).agent_id == "caller-agent-fixture"
