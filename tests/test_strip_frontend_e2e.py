"""E2E: TS/React strip + phase run + --to-module hook (production path)."""

import json

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX, TSCONFIG


@pytest.fixture
def planning_project(tmp_path):
    src = tmp_path / "src" / "pages"
    hooks = tmp_path / "src" / "hooks"
    extracted = tmp_path / "src" / "features" / "extracted"
    src.mkdir(parents=True)
    hooks.mkdir(parents=True)
    extracted.mkdir(parents=True)

    page = src / "Planning.tsx"
    page.write_text(PLANNING_TSX, encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(json.dumps(TSCONFIG), encoding="utf-8")

    manifest = tmp_path / "manifests" / "phase.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")
    return {
        "root": tmp_path,
        "page": page,
        "manifest": manifest,
        "extracted": extracted,
        "hooks": hooks,
    }


def test_phase_run_frontend_to_module_hook(planning_project):
    proj = planning_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "phase",
            "run",
            "--profile",
            "frontend",
            "--manifest",
            str(proj["manifest"]),
            "--file",
            str(proj["page"]),
            "--out-dir",
            str(proj["extracted"]),
            "--module-out-dir",
            str(proj["hooks"]),
            "--to-module",
            "hook",
            "--verify",
            "echo ok",
            "--emit-wiring",
            str(proj["root"] / "wiring.md"),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    hook_path = proj["hooks"] / "usePlanningHandlers.ts"
    assert hook_path.exists(), "generated hook module missing"
    hook_src = hook_path.read_text(encoding="utf-8")
    assert "export function usePlanningHandlers" in hook_src
    assert "return { handleOpenDrawer, handleCloseDrawer }" in hook_src
    assert "setDrawerOpen" in hook_src
    assert "import { doWork }" in hook_src
    assert "UsePlanningHandlersParams" in hook_src

    parent = proj["page"].read_text(encoding="utf-8")
    assert "handleOpenDrawer = ()" not in parent
    assert "Stubbed: usePlanningHandlers" in parent

    report_path = proj["extracted"] / "extraction_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["profile"] == "typescript"
    block = report["extracted_blocks"][0]
    assert block["integration_hints"]["target_module"] == "src/hooks/usePlanningHandlers.ts"
    assert block["integration_hints"]["route_from"] == "/legacy/planning"
    assert block["wiring_hints"]["route_redirect"] == "/legacy/planning → /account-planning"
    assert "dangling_references" in report
    assert any(d["name"] == "handleOpenDrawer" for d in report["dangling_references"])

    wiring = (proj["root"] / "wiring.md").read_text(encoding="utf-8")
    assert "Route redirect" in wiring
    assert "usePlanningHandlers" in wiring

    fragment = proj["extracted"] / "planning_handlers.fragment.txt"
    assert fragment.exists()
    assert fragment.suffix == ".txt"


def test_strip_json_dry_run_preview(planning_project):
    proj = planning_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "-n",
            "--json",
            "--strict-overlap",
            "--file",
            str(proj["page"]),
            "--manifest",
            str(proj["manifest"]),
            "--out-dir",
            str(proj["extracted"]),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["exported_meta"]
    assert payload["dangling_references"]
    assert payload["exported_meta"][0]["integration_hints"]["module_kind"] == "hook"
