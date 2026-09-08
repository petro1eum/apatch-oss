"""Tests for R20–R30 recommendation implementations."""

import json
import os

import pytest

from apatch.auto_wire import apply_auto_wire
from apatch.barrel_export import emit_barrel_exports
from apatch.consumer_profiles import get_profile, list_profiles
from apatch.doctor import init_consumer, run_doctor
from apatch.generate import find_whitespace_spans, generate_patches
from apatch.manifest_bundle import load_manifest_bundle
from apatch.toolchain import detect_toolchain, recommend_verify
from apatch.workflows import phase_run_bundle, rollback_workspace


def test_toolchain_detects_alembic_marker(tmp_path):
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    data = detect_toolchain(str(tmp_path))
    assert "sqlalchemy" in data["detected_profiles"]
    assert "pytest" in data["recommended_verify"] or "alembic" in data["recommended_verify"]


def test_init_consumer_sqlalchemy_profile(tmp_path):
    created = init_consumer(str(tmp_path), profile="sqlalchemy")
    assert any("apatch.sqlalchemy.example.json" in p for p in created)
    assert (tmp_path / "manifests" / "PROFILE.sqlalchemy.md").is_file()
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "sqlalchemy" in agents.lower()


def test_init_consumer_with_ci(tmp_path):
    created = init_consumer(str(tmp_path), with_ci=True)
    ci = tmp_path / ".github" / "workflows" / "apatch.yml"
    assert ci.is_file()
    content = ci.read_text(encoding="utf-8")
    assert "apatch plan" in content
    assert "Governance policy + portable proof gate" in content
    assert "block_commit_without_proof" in content
    assert "verify-inclusion" in content
    assert "manifests/apatch-inclusion.jsonl" in content
    assert "apatch sandbox ci-gate" not in content
    assert str(ci) in created


def test_generate_whitespace_match(tmp_path):
    f = tmp_path / "models.py"
    f.write_text("x = Column(default = datetime.utcnow)\n", encoding="utf-8")
    spans = find_whitespace_spans(f.read_text(encoding="utf-8"), "default=datetime.utcnow")
    assert spans
    patches = generate_patches(
        find="default=datetime.utcnow",
        replace="server_default=func.now()",
        target_dir=str(tmp_path),
        glob_pattern="*.py",
        match_mode="whitespace",
    )
    assert len(patches) == 1
    assert "default = datetime.utcnow" in patches[0]["tool_calls"][0]["arguments"]["TargetContent"]


def test_generate_regex_match(tmp_path):
    (tmp_path / "a.py").write_text("foo_old_bar\n", encoding="utf-8")
    patches = generate_patches(
        find="unused",
        replace="NEW",
        target_dir=str(tmp_path),
        glob_pattern="*.py",
        match_mode="regex",
        find_pattern=r"foo_\w+_bar",
    )
    assert len(patches) == 1


def test_auto_wire_inserts_import_and_replaces_stub(tmp_path):
    parent = tmp_path / "App.tsx"
    parent.write_text(
        "import React from 'react';\n"
        "export function App() {\n"
        "  // Stubbed: useExampleHandlers hook\n"
        "  return <button onClick={handleOpen}>x</button>;\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "stub_replacement": "  // Stubbed: useExampleHandlers hook\n",
        "integration_hints": {
            "parent_import": "import { useExampleHandlers } from '@/hooks/useExampleHandlers';",
            "parent_wire": "const { handleOpen } = useExampleHandlers({});",
        },
        "wiring_hints": {},
    }]
    result = apply_auto_wire(str(parent), meta, specs_replace=[meta[0]["stub_replacement"]])
    text = parent.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert "useExampleHandlers" in text
    assert "Stubbed" not in text
    assert "handleOpen" in text


def test_emit_barrel_exports(tmp_path):
    barrel = tmp_path / "hooks" / "index.ts"
    meta = [{
        "module_path": str(tmp_path / "hooks" / "useFoo.ts"),
        "wiring_hints": {
            "exports": ["useFoo"],
            "barrel_export": "export { useFoo } from './useFoo';",
        },
    }]
    result = emit_barrel_exports(str(barrel), meta)
    assert result["added"]
    assert "useFoo" in barrel.read_text(encoding="utf-8")


def test_multi_file_manifest_bundle(tmp_path):
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    src = tmp_path / "src"
    src.mkdir()
    (src / "A.tsx").write_text(PLANNING_TSX, encoding="utf-8")
    (src / "B.tsx").write_text(PLANNING_TSX, encoding="utf-8")
    manifest = {
        "verify_command": "echo ok",
        "files": [
            {"path": "src/A.tsx", "strips": PLANNING_MANIFEST["strips"]},
            {"path": "src/B.tsx", "strips": PLANNING_MANIFEST["strips"]},
        ],
    }
    mpath = tmp_path / "multi.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    bundle = load_manifest_bundle(mpath)
    assert bundle.is_multi
    assert len(bundle.files) == 2


def test_phase_run_bundle_multi_file(tmp_path):
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    src = tmp_path / "src"
    hooks = tmp_path / "src" / "hooks"
    extracted = tmp_path / "extracted"
    src.mkdir()
    hooks.mkdir(parents=True)
    extracted.mkdir()
    (src / "A.tsx").write_text(PLANNING_TSX, encoding="utf-8")
    (src / "B.tsx").write_text(PLANNING_TSX, encoding="utf-8")
    manifest = {
        "verify_command": "echo ok",
        "files": [
            {"path": "src/A.tsx", "strips": PLANNING_MANIFEST["strips"]},
            {"path": "src/B.tsx", "strips": PLANNING_MANIFEST["strips"]},
        ],
    }
    mpath = tmp_path / "multi.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    result = phase_run_bundle(
        str(mpath),
        profile="frontend",
        out_dir="extracted",
        module_out_dir="src/hooks",
        to_module="hook",
        target_dir=str(tmp_path),
        no_trustchain=True,
    )
    assert result["ok"] is True, result
    assert len(result["files"]) == 2
    assert (hooks / "usePlanningHandlers.ts").exists()


def test_doctor_includes_toolchain(tmp_path):
    info = run_doctor(str(tmp_path))
    assert "toolchain" in info
    assert "recommended_verify" in info
    assert "consumer_profiles" in info
    assert "sqlalchemy" in info["consumer_profiles"]


def test_mcp_apply_verify_rollback(tmp_path):
    pytest.importorskip("mcp")
    from apatch.mcp.server import apatch_apply

    src = tmp_path / "value.py"
    src.write_text("KEEP = 1\n", encoding="utf-8")
    log = tmp_path / "p.jsonl"
    log.write_text(
        json.dumps({
            "step_index": 1,
            "tool_calls": [{
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "value.py",
                    "TargetContent": "KEEP = 1",
                    "ReplacementContent": "KEEP = 2",
                },
            }],
        }) + "\n",
        encoding="utf-8",
    )
    result = apatch_apply(
        logs_path=str(log),
        target_dir=str(tmp_path),
        verify="exit 1",
        no_trustchain=True,
    )
    assert result["ok"] is False
    assert result.get("verify_rollback") is True
    assert src.read_text(encoding="utf-8") == "KEEP = 1\n"


def test_profiles_list():
    assert "frontend" in list_profiles()
    assert get_profile("sqlalchemy")["verify"]
