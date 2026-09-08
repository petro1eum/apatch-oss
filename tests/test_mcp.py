import os

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from apatch.mcp import server as mcp_server


def test_mcp_tools_registered():
    assert mcp_server.mcp is not None
    # FastMCP exposes tool manager
    tools = getattr(mcp_server.mcp, "_tool_manager", None)
    if tools is None:
        pytest.skip("FastMCP tool manager API unavailable")
    names = set(tools._tools.keys()) if hasattr(tools, "_tools") else set()
    expected = {
        "apatch_plan",
        "apatch_plan_batch",
        "apatch_apply",
        "apatch_apply_session",
        "apatch_scan",
        "apatch_view",
        "apatch_strip_dry_run",
        "apatch_strip",
        "apatch_phase_run",
        "apatch_governed_phase_run",
        "apatch_rollback",
        "apatch_generate",
        "apatch_generate_batch",
        "apatch_doctor",
        "apatch_workspace_list",
        "apatch_workspace_inspect",
        "apatch_extension_list",
        "apatch_extension_inspect",
        "apatch_extension_validate",
        "apatch_extension_run",
        "apatch_remote_task_run",
        "apatch_remote_source_handoff",
        "apatch_remote_service_action",
        "apatch_gc",
        "apatch_mcp_hygiene",
        "apatch_build_diagnose",
        "apatch_init_consumer",
        "apatch_natives_check",
        "apatch_compile",
        "apatch_suggest_until",
        "apatch_arch_check",
        "apatch_impact",
        "apatch_db_check",
        "apatch_db_revision",
        "apatch_db_safety",
        "apatch_db_run",
        "apatch_refactor_run",
        "apatch_verify_semantic",
        "apatch_verify_notarization",
        "apatch_commit_attested",
        "apatch_verify_anchor",
        "apatch_verify_inclusion",
        "apatch_trust_enroll",
        "apatch_policy_sign",
        "apatch_policy_verify",
        "apatch_project_status",
        "apatch_knowledge_graph",
        "apatch_session_state",
        "apatch_session_start",
        "apatch_session_end",
        "apatch_verify_run",
        "apatch_attestation_export",
        "apatch_events_tail",
        "apatch_attestation_show",
        "apatch_attest",
        "apatch_verify_status",
        "apatch_simulate",
        "apatch_index_build",
        "apatch_index_query",
        "apatch_trustchain_history",
        "apatch_trustchain_coverage",
        "apatch_pipeline_run",
        "apatch_plan_graph",
        "apatch_execute_graph",
        "apatch_orchestrate",
        "apatch_replay",
        "apatch_sandbox_status",
        "apatch_sandbox_audit",
        "apatch_sandbox_ci_gate",
        "apatch_spec_lint",
        "apatch_rfp_lint",
        "apatch_rfp_spec_coverage",
        "apatch_spec_needles_scaffold",
        "apatch_spec_status",
        "apatch_spec_coverage",
        "apatch_spec_interference",
        "apatch_spec_schedule",
        "apatch_spec_cross_verify",
        "apatch_spec_run_multi",
        "apatch_spec_next",
        "apatch_execute_next",
        "apatch_spec_run",
        "apatch_spec_adherence",
        "apatch_spec_plan_diff",
        "apatch_spec_plan_register",
        "apatch_spec_plan_lint",
        "apatch_spec_plan_show",
        "apatch_spec_run_manifest_lint",
        "apatch_probe",
        "apatch_reality",
        "apatch_slug_intake",
        "apatch_slug_cockpit",
        "apatch_spec_scaffold",
        "apatch_scip",
    }
    assert expected.issubset(names)


def test_session_start_exposes_explicit_lane():
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None or not hasattr(tm, "_tools"):
        pytest.skip("FastMCP tool manager API unavailable")
    props = tm._tools["apatch_session_start"].parameters["properties"]
    assert "lane" in props
    assert props["lane"].get("description")


def test_mcp_tool_params_have_descriptions():
    """Every registered tool param must carry a schema description (agent UX)."""
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None or not hasattr(tm, "_tools"):
        pytest.skip("FastMCP tool manager API unavailable")
    missing = []
    for tool in tm._tools.values():
        props = (tool.parameters or {}).get("properties") or {}
        for name, spec in props.items():
            if not (isinstance(spec, dict) and spec.get("description")):
                missing.append(f"{tool.name}.{name}")
    assert not missing, f"params without description: {missing}"


def test_apatch_plan_tool(tmp_path):
    from apatch.mcp.server import apatch_plan

    f = tmp_path / "a.py"
    f.write_text("old_value = 1\n", encoding="utf-8")
    result = apatch_plan(
        target_file="a.py",
        old_content="old_value",
        new_content="new_value",
        target_dir=str(tmp_path),
    )
    assert result["success"] is True
    assert result["strategy"] == "exact"


def test_apatch_apply_tool(tmp_path):
    import json

    from apatch.mcp.server import apatch_apply

    src = tmp_path / "main.cpp"
    src.write_text("int a = 1;\n", encoding="utf-8")
    log = tmp_path / "session.jsonl"
    step = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": "main.cpp",
                "TargetContent": "int a = 1;",
                "ReplacementContent": "int a = 2;",
            },
        }],
    }
    log.write_text(json.dumps(step) + "\n", encoding="utf-8")

    result = apatch_apply(logs_path=str(log), target_dir=str(tmp_path))
    assert result["ok"] is True
    assert result["applied"] == 1
    assert result["failed"] == 0
    assert "int a = 2" in src.read_text(encoding="utf-8")
    assert result["report_path"] and (tmp_path / ".apatch" / "mcp_apply_report.json").exists()


def test_apatch_strip_dry_run_tool(tmp_path):
    import json

    from apatch.mcp.server import apatch_strip_dry_run
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    page = tmp_path / "src" / "Planning.tsx"
    page.parent.mkdir(parents=True)
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")

    result = apatch_strip_dry_run(
        file_path="src/Planning.tsx",
        manifest_path=str(manifest),
        out_dir="extracted",
        target_dir=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["exported_meta"]
    assert result["exported_meta"][0]["integration_hints"]["module_kind"] == "hook"
    assert any(d["name"] == "handleOpenDrawer" for d in result["dangling_references"])


def test_apatch_doctor_tool(tmp_path):
    from apatch.mcp.server import apatch_doctor

    result = apatch_doctor(target_dir=str(tmp_path))
    assert result["version"]
    assert result["workspace"] == str(tmp_path.resolve())
    assert isinstance(result["tree_sitter_grammars"], list)
    assert result["tree_sitter_grammars"]
    assert "apatch:doctor" in result["npm_scripts"]


def test_apatch_generate_tool(tmp_path):
    import json

    from apatch.mcp.server import apatch_generate

    (tmp_path / "alpha.py").write_text("OLD_MARKER = 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("no match here\n", encoding="utf-8")
    out_path = tmp_path / "generated.jsonl"

    result = apatch_generate(
        find_text="OLD_MARKER",
        replace_text="NEW_MARKER = 1",
        target_dir=str(tmp_path),
        glob_pattern="*.py",
        out_path=str(out_path),
    )
    assert result["count"] == 1
    assert out_path.is_file()
    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    step = json.loads(lines[0])
    assert step["tool_calls"][0]["arguments"]["TargetFile"] == "alpha.py"


def test_apatch_rollback_tool(tmp_path):
    from apatch.backup import BackupManager
    from apatch.mcp.server import apatch_rollback

    target = tmp_path / "app.py"
    target.write_text("original\n", encoding="utf-8")
    mgr = BackupManager(str(tmp_path), session_id="mcp_rollback_test")
    mgr.create_backup(str(target))
    target.write_text("mutated\n", encoding="utf-8")

    result = apatch_rollback(target_dir=str(tmp_path), session_id="mcp_rollback_test")
    assert result["ok"] is True
    assert result["count"] >= 1
    assert result["restored"]
    assert "trustchain" in result
    assert target.read_text(encoding="utf-8") == "original\n"
    assert str(target.resolve()) in [os.path.abspath(p) for p in result["restored"]]


def test_apatch_suggest_until_tool(tmp_path):
    from apatch.mcp.server import apatch_suggest_until
    from tests.fixtures.planning_tsx import PLANNING_TSX

    src = tmp_path / "src"
    src.mkdir()
    page = src / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")

    result = apatch_suggest_until(
        file_path="src/Planning.tsx",
        start_marker="// --- HANDLERS START ---",
        target_dir=str(tmp_path),
    )
    assert result
    assert all("line" in item and "text" in item for item in result)
    assert all("score" in item and "confidence" in item for item in result)
    assert any("HANDLERS END" in item["text"] for item in result)


def test_apatch_phase_run_tool(tmp_path):
    import json

    from apatch.mcp.server import apatch_phase_run
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    src = tmp_path / "src" / "pages"
    hooks = tmp_path / "src" / "hooks"
    extracted = tmp_path / "extracted"
    src.mkdir(parents=True)
    hooks.mkdir(parents=True)
    extracted.mkdir(parents=True)

    page = src / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")

    result = apatch_phase_run(
        file_path=str(page),
        manifest_path=str(manifest),
        profile="frontend",
        out_dir="extracted",
        module_out_dir="src/hooks",
        to_module="hook",
        verify="echo ok",
        target_dir=str(tmp_path),
    )
    assert result["ok"] is True, result.get("errors")
    hook = hooks / "usePlanningHandlers.ts"
    assert hook.exists(), result
    assert "export function usePlanningHandlers" in hook.read_text(encoding="utf-8")
    parent = page.read_text(encoding="utf-8")
    assert "handleOpenDrawer = ()" not in parent
    assert (extracted / "planning_handlers.fragment.txt").exists()


def test_apatch_plan_batch_tool(tmp_path):
    import json

    from apatch.mcp.server import apatch_plan_batch

    src = tmp_path / "main.py"
    src.write_text("val = 1\n", encoding="utf-8")
    log = tmp_path / "log.jsonl"
    log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": "main.py",
                        "TargetContent": "val = 1",
                        "ReplacementContent": "val = 2",
                    },
                }],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = apatch_plan_batch(str(log), str(tmp_path))
    assert result["would_apply"] == 1


def test_apatch_scan_and_view_tools(tmp_path):
    import json

    from apatch.mcp.server import apatch_scan, apatch_view

    log = tmp_path / "agent.jsonl"
    log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": "a.py",
                        "TargetContent": "old",
                        "ReplacementContent": "new",
                    },
                }],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    scan = apatch_scan(extra_paths=[str(tmp_path)], include_cwd=False, count_candidates=False)
    assert scan["count"] >= 1
    view = apatch_view(str(log))
    assert view["count"] == 1


def test_apatch_strip_apply_tool(tmp_path):
    import json

    from apatch.mcp.server import apatch_strip
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    page = tmp_path / "src" / "Planning.tsx"
    page.parent.mkdir(parents=True)
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")
    out = tmp_path / "extracted"
    hooks = tmp_path / "src" / "hooks"
    hooks.mkdir(parents=True)

    result = apatch_strip(
        file_path="src/Planning.tsx",
        target_dir=str(tmp_path),
        manifest_path=str(manifest),
        out_dir="extracted",
        module_out_dir="src/hooks",
        to_module="hook",
        strict_overlap=True,
        no_trustchain=True,
        verify="echo ok",
    )
    assert result["ok"] is True, result.get("errors")
    assert (hooks / "usePlanningHandlers.ts").exists()
    assert "handleOpenDrawer = ()" not in page.read_text(encoding="utf-8")
    assert (out / "planning_handlers.fragment.txt").exists()


def test_apatch_init_consumer_tool(tmp_path):
    from apatch.mcp.server import apatch_init_consumer

    result = apatch_init_consumer(str(tmp_path))
    assert result["count"] >= 1
    assert (tmp_path / "manifests" / "apatch.example.json").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert "apatch_strip_dry_run" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_apatch_init_consumer_with_arch_rules(tmp_path):
    from apatch.mcp.server import apatch_init_consumer

    result = apatch_init_consumer(str(tmp_path), with_arch_rules=True)
    assert result["count"] >= 3
    assert (tmp_path / "manifests" / "arch-rules.yaml").exists()
    assert (tmp_path / "manifests" / "engineering-pipeline.example.json").exists()


def test_apatch_natives_check_tool(tmp_path):
    from apatch.mcp.server import apatch_natives_check

    cpp = tmp_path / "a.cpp"
    cpp.write_text('register_native("foo");\n', encoding="utf-8")
    ok = apatch_natives_check(str(tmp_path))
    assert ok["ok"] is True


def test_apatch_compile_dry_run(tmp_path):
    from apatch.mcp.server import apatch_compile

    md = tmp_path / "doc.md"
    md.write_text("# Title\n\nParagraph.\n", encoding="utf-8")
    result = apatch_compile(str(md), target_dir=str(tmp_path), dry_run=True)
    assert result["ok"] is True
    assert result["blocks_count"] >= 1


def test_apatch_apply_dry_run_and_filters(tmp_path):
    import json

    from apatch.mcp.server import apatch_apply

    src = tmp_path / "f.py"
    src.write_text("x = 1\n", encoding="utf-8")
    log = tmp_path / "l.jsonl"
    log.write_text(
        json.dumps(
            {
                "step_index": 1,
                "tool_calls": [{
                    "name": "replace_file_content",
                    "arguments": {
                        "TargetFile": "f.py",
                        "TargetContent": "x = 1",
                        "ReplacementContent": "x = 2",
                    },
                }],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = apatch_apply(
        str(log),
        str(tmp_path),
        dry_run=True,
        no_trustchain=True,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert src.read_text(encoding="utf-8") == "x = 1\n"


def test_apatch_apply_rejects_mass_without_session(tmp_path):
    import json

    from apatch.mcp.server import apatch_apply

    log = tmp_path / "big.jsonl"
    for i in range(20):
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        step = {
            "step_index": i + 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": f"f{i}.py",
                    "TargetContent": "x = 1",
                    "ReplacementContent": "x = 2",
                },
            }],
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(step) + "\n")

    result = apatch_apply(logs_path=str(log), target_dir=str(tmp_path), no_trustchain=True)
    assert result["ok"] is False
    assert result["use_tool"] == "apatch_apply_session"


def test_apatch_apply_mcp_quiet_stdout(tmp_path, capsys):
    import json

    from apatch.mcp.server import apatch_apply

    src = tmp_path / "quiet.py"
    src.write_text("v = 1\n", encoding="utf-8")
    log = tmp_path / "session.jsonl"
    step = {
        "step_index": 1,
        "tool_calls": [{
            "name": "replace_file_content",
            "arguments": {
                "TargetFile": "quiet.py",
                "TargetContent": "v = 1",
                "ReplacementContent": "v = 2",
            },
        }],
    }
    log.write_text(json.dumps(step) + "\n", encoding="utf-8")

    result = apatch_apply(logs_path=str(log), target_dir=str(tmp_path), no_trustchain=True)
    captured = capsys.readouterr()

    assert result["ok"] is True
    assert "TrustChain" not in captured.out
    assert "Applying Step" not in captured.out
    assert "v = 2" in src.read_text(encoding="utf-8")


def test_mcp_tool_wrapper_sanitizes_returns(tmp_path):
    import json

    from apatch.mcp import server as mcp_server
    from apatch.mcp.server import apatch_plan

    mcp_server._wrap_mcp_tools()
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    tool = mcp_server.mcp._tool_manager._tools["apatch_plan"]
    result = tool.fn(
        target_file="a.py",
        old_content="x = 1",
        new_content="x = 2",
        target_dir=str(tmp_path),
    )
    json.dumps(result, ensure_ascii=False)


def test_apatch_probe_mcp_tool(tmp_path):
    """apatch_probe wraps the differential-probe drivers (RFP-005)."""
    import sys as _sys

    (tmp_path / "mod.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    (tmp_path / "t_x.py").write_text(
        "from mod import f\n\ndef test_f():\n    assert f() == 42\n", encoding="utf-8")
    probe = mcp_server.mcp._tool_manager._tools["apatch_probe"].fn
    pf = f"{_sys.executable} -m pytest t_x.py -q -p no:cacheprovider"
    # pass every param explicitly (direct .fn call bypasses MCP Field-default resolution)
    real = probe(mode="falsify", verify=pf, files="mod.py",
                 baseline_failures="", allowed_failures="", target_dir=str(tmp_path))
    assert real["verdict"] == "real" and real["killed"] is True
    rat = probe(mode="ratify", verify="true", files="",
                baseline_failures="", allowed_failures="", target_dir=str(tmp_path))
    assert rat["verdict"] == "ratified"
    stale = probe(mode="ratify", verify="false", files="",
                  baseline_failures="", allowed_failures="", target_dir=str(tmp_path))
    assert stale["verdict"] == "stale"
    bad = probe(mode="bogus", verify="true", files="",
                baseline_failures="", allowed_failures="", target_dir=str(tmp_path))
    assert bad.get("error")


def test_apatch_reality_mcp_tool(tmp_path):
    """apatch_reality add/status over the observed-reality ledger (RFP-005)."""
    reality = mcp_server.mcp._tool_manager._tools["apatch_reality"].fn
    add = reality(action="add", summary="login 500 on empty password", source="sentry",
                  kind="bug", status="open", rec_id="", spec="", target_dir=str(tmp_path))
    assert add["ok"] is True and add["id"].startswith("REC-")
    st = reality(action="status", summary="", source="", kind="observation",
                 status="open", rec_id="", spec="", target_dir=str(tmp_path))
    # status is operationally ok; debt shows as clean=False (not a tool failure — REC-7855)
    assert add["id"] in st["uncovered"] and st["clean"] is False and st["ok"] is True
    empty = reality(action="add", summary="", source="", kind="observation",
                    status="open", rec_id="", spec="", target_dir=str(tmp_path))
    assert empty["ok"] is False


def test_an_undeclared_argument_is_refused_not_silently_dropped():
    """A dropped argument lets a caller report work the tool never did.

    ``apatch_execute_next`` takes ``verify_override``; a caller passing ``verify``
    used to get ``ok`` back with the argument discarded, so the requirement's own
    bound check ran alone and the caller believed it had ordered another one.
    """
    tools = getattr(mcp_server.mcp, "_tool_manager", None)
    if tools is None or not hasattr(tools, "_tools"):
        pytest.skip("FastMCP tool manager API unavailable")
    mcp_server._forbid_unknown_arguments()

    tool = tools._tools["apatch_execute_next"]
    model = tool.fn_metadata.arg_model
    with pytest.raises(Exception) as refused:
        model.model_validate({"spec": "SPEC-X", "verify": ["pytest"]})
    assert "verify" in str(refused.value)

    # the declared call still works, and the published schema says extras are out
    model.model_validate({"spec": "SPEC-X", "requirement": "SPEC-X#R1"})
    assert tool.parameters["additionalProperties"] is False

    # no tool may opt out: none of them accepts arbitrary keywords
    for name, other in tools._tools.items():
        assert other.parameters.get("additionalProperties") is False, name
