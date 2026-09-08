"""Agent guidance embedded in MCP responses (executable specs discovery)."""

import os

from apatch.agent_guidance import (
    attach_artifact_guidance,
    mcp_guidance_mode,
    protocol_contract,
    spec_authoring_requirements,
    spec_execution_playbook,
    spec_run_playbook,
)
from apatch.mcp_health import is_apatch_source_workspace, recommended_mcp_server_block
from apatch.doctor import init_consumer, run_doctor
from apatch.session_state import enrich_tool_response
from apatch.spec import spec_lint_enriched, spec_next_enriched


def test_spec_authoring_has_rules():
    auth = spec_authoring_requirements()
    assert "rules" in auth
    assert len(auth["rules"]) >= 5
    assert "apatch_spec_lint" in auth["lint_tool"]


def test_run_doctor_embeds_spec_playbooks(tmp_path):
    info = run_doctor(str(tmp_path))
    assert "spec_authoring" in info
    assert "spec_execution" in info
    assert "spec_run" in info
    assert "protocol_contract" in info
    assert info["spec_authoring"]["rules"]
    assert "apatch_spec_run" in info["protocol_contract"]["task_routing"]["implement_whole_spec"]
    assert "executable_specs_entrypoint" in info["agent_protocol"]


def test_generate_batch_response_warns_on_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "full")
    out = enrich_tool_response(
        "apatch_generate_batch",
        {"ok": True, "count": 1},
        target_dir=str(tmp_path),
    )
    assert out.get("if_spec_task")
    assert out.get("protocol_contract")


def test_spec_next_recommends_spec_run_for_multiple_rk(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-MULTI-1.md").write_text(
        "# SPEC-MULTI-1 — Multi\n\n> **apatch artifact:** `spec:SPEC-MULTI-1`\n\n"
        "## R1 One\n(verify: python3 -c \"pass\")\n"
        "## R2 Two\n(verify: python3 -c \"pass\")\n",
        encoding="utf-8",
    )
    out = spec_next_enriched(str(tmp_path), spec="SPEC-MULTI-1")
    assert out.get("recommended_tool") == "apatch_spec_run"
    assert "anti_pattern" in out


def test_spec_lint_enriched_includes_authoring(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "full")
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-TEST-1.md").write_text(
        "# SPEC-TEST-1 — Test\n\n> **apatch artifact:** `spec:SPEC-TEST-1`\n\n"
        "## R1 One\n(verify: python3 -c \"pass\")\n",
        encoding="utf-8",
    )
    out = spec_lint_enriched(str(tmp_path), spec="SPEC-TEST-1")
    assert out.get("spec_authoring")
    assert out["spec_authoring"]["rules"]


def test_spec_lint_doctor_only_passed_is_slim(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "doctor_only")
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-TEST-1.md").write_text(
        "# SPEC-TEST-1 — Test\n\n> **apatch artifact:** `spec:SPEC-TEST-1`\n\n"
        "## R1 One\n(verify: python3 -c \"pass\")\n",
        encoding="utf-8",
    )
    out = spec_lint_enriched(str(tmp_path), spec="SPEC-TEST-1")
    assert out.get("guidance_mode") == "doctor_only"
    assert out.get("guidance_ref")
    assert "spec_authoring" not in out


def test_init_consumer_scaffolds_specs_and_cursor_rule(tmp_path):
    created = init_consumer(str(tmp_path), with_mcp=True)
    assert (tmp_path / "docs" / "specs" / "README.md").exists()
    assert (tmp_path / "docs" / "specs" / "SPEC-TEMPLATE.md").exists()
    assert (tmp_path / ".cursor" / "rules" / "apatch-executable-specs.mdc").exists()
    assert (tmp_path / ".cursor" / "rules" / "apatch-protocol.mdc").exists()
    assert any("SPEC-TEMPLATE" in p for p in created)


def test_protocol_contract_covers_manual_jsonl_antipattern():
    pc = protocol_contract()
    assert any("patches.jsonl" in n for n in pc["never"])
    assert any("driver" in n.lower() for n in pc["never"])
    assert "rfp_009_summary" in pc
    assert "manifest_path" in pc["rfp_009_summary"]


def test_protocol_contract_covers_remote_source_handoff_antipattern():
    pc = protocol_contract()
    remote = pc["remote_workspaces"]
    joined = " ".join(remote["never"] + pc["never"]).lower()
    assert "apatch_remote_source_handoff" in remote["source_handoff"]
    assert "tar | ssh" in joined
    assert "scp" in joined
    assert "credentials" in remote["source_handoff"]
    assert "raw shell" in remote["if_tool_missing"]


def test_spec_execution_references_authoring():
    ex = spec_execution_playbook()
    assert ex["authoring"] == spec_authoring_requirements()
    assert "workflow_choice" in ex
    assert "whole_spec_large" in ex["workflow_choice"]


def test_spec_run_playbook_needles():
    run = spec_run_playbook()
    assert "needles" in run
    assert "verify_deferred" in run
    assert "manifest_path" in run.get("large_spec_path", "")


def test_mcp_guidance_mode_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "doctor_only")
    assert mcp_guidance_mode(str(tmp_path)) == "doctor_only"
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "full")
    assert mcp_guidance_mode(str(tmp_path)) == "full"


def test_mcp_guidance_mode_apatch_source_defaults_full(monkeypatch):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.delenv("APATCH_MCP_GUIDANCE", raising=False)
    assert is_apatch_source_workspace(root)
    assert mcp_guidance_mode(root) == "full"


def test_doctor_only_slims_generate_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "doctor_only")
    out = attach_artifact_guidance(
        {"ok": True, "count": 1},
        "apatch_generate_batch",
        target_dir=str(tmp_path),
    )
    assert out.get("guidance_mode") == "doctor_only"
    assert out.get("guidance_ref")
    assert out.get("if_spec_task")
    assert "protocol_contract" not in out


def test_doctor_exposes_guidance_mode(monkeypatch):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.delenv("APATCH_MCP_GUIDANCE", raising=False)
    out = attach_artifact_guidance({}, "apatch_doctor", target_dir=root)
    assert out.get("mcp_guidance_mode") == "full"
    assert out.get("protocol_contract")


def test_recommended_mcp_config_sets_doctor_only_guidance():
    cfg = recommended_mcp_server_block()
    assert cfg["env"]["APATCH_MCP_GUIDANCE"] == "doctor_only"
