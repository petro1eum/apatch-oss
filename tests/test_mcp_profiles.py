from __future__ import annotations

import os
from unittest import mock

from apatch.mcp.profiles import PROFILE_COMPACT, allowed_tools, mcp_profile_name


def test_compact_profile_is_bounded_and_carries_read_only_orientation():
    assert 10 <= len(PROFILE_COMPACT) <= 17
    assert {
        "apatch_doctor",
        "apatch_spec_run",
        "apatch_recover",
        "apatch_session_end",
        "apatch_verify_run",
    } <= PROFILE_COMPACT
    assert {"apatch_impact", "apatch_build_diagnose"} <= PROFILE_COMPACT
    assert "apatch_generate_batch" not in PROFILE_COMPACT


def test_compact_profile_carries_no_unguarded_source_writer():
    # apatch_refactor_run applies through apply_from_logs and honours a
    # manifest-supplied no_trustchain flag, so the default surface must exclude it
    # and every other tool that writes source outside a governed session.
    assert PROFILE_COMPACT.isdisjoint({
        "apatch_refactor_run",
        "apatch_apply",
        "apatch_apply_session",
        "apatch_strip",
        "apatch_phase_run",
        "apatch_governed_phase_run",
        "apatch_pipeline_run",
        "apatch_db_run",
        "apatch_execute_graph",
        "apatch_orchestrate",
    })


def test_compact_is_default_and_full_remains_opt_in():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert mcp_profile_name() == "compact"
        assert allowed_tools() == PROFILE_COMPACT
    assert allowed_tools("full") is None


def test_recommended_mcp_config_uses_compact():
    from apatch.mcp_health import recommended_mcp_server_block

    assert recommended_mcp_server_block()["env"]["APATCH_MCP_PROFILE"] == "compact"


def test_compact_doctor_and_playbooks_only_name_available_hot_path_tools(tmp_path):
    from apatch.agent_playbooks import runtime_hygiene_playbook, tool_usage_playbook
    from apatch.doctor import run_doctor

    with mock.patch.dict(os.environ, {"APATCH_MCP_PROFILE": "compact"}, clear=False):
        doctor = run_doctor(str(tmp_path))
        workflow = " ".join(doctor["recommended_workflow"])
        mass = doctor["agent_protocol"]["mass_refactor"]
        hygiene = " ".join(runtime_hygiene_playbook()["mass_refactor_cycle"])
        tools = tool_usage_playbook()["by_intent"]["mass_refactor_not_spec"]["tools"]

    assert doctor["mcp_profile"] == "compact"
    assert "apatch_generate" not in workflow
    assert "apatch_apply_session" not in workflow
    assert "apatch_simulate" not in mass
    assert "apatch_apply_session" not in hygiene
    assert set(tools) <= PROFILE_COMPACT

    # A recommendation naming a tool the profile does not expose costs the agent a turn
    # and pushes it toward the shell the sandbox then blocks. No do-this-next surface
    # may name one, and every upgrade must be stated rather than implied.
    from apatch.agent_guidance import unavailable_tool_names

    with mock.patch.dict(os.environ, {"APATCH_MCP_PROFILE": "compact"}, clear=False):
        report = run_doctor(str(tmp_path))
        by_intent = tool_usage_playbook()["by_intent"]

    protocol = report["agent_protocol"]
    upgrades = protocol.get("requires_profile_upgrade") or {}
    for key, value in protocol.items():
        if key == "requires_profile_upgrade" or not isinstance(value, str):
            continue
        assert not unavailable_tool_names(value), f"agent_protocol[{key}] names an absent tool"
    for key in ("recommended_workflow", "governed_workflow_with_artifacts"):
        for step in report.get(key) or []:
            assert not unavailable_tool_names(step), f"{key} names an absent tool: {step}"
    assert upgrades, "a compact profile hides real tools; say which and where"

    for intent, entry in by_intent.items():
        named = list(entry.get("tools") or [])
        if isinstance(entry.get("tool"), str):
            named.append(entry["tool"])
        for item in named:
            head = str(item).split()[0]
            if head.startswith("apatch_"):
                assert head in PROFILE_COMPACT, f"{intent} names absent {head}"

    # the orientation the compact profile gained is actually pointed at
    assert "apatch_impact" in by_intent["orient_before_change"]["tools"]
    assert "apatch_build_diagnose" in by_intent["fix_verify_failure"]["tools"]
