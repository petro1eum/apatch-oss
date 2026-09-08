"""Direct strip_pipeline integration (no CLI)."""

import json

from apatch.strip import StripSpec, load_strip_manifest
from apatch.strip_pipeline import StripPipelineConfig, run_strip_pipeline
from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX


def test_strip_pipeline_warns_when_to_module_without_out_dir(tmp_path):
    page = tmp_path / "App.tsx"
    page.write_text(
        "export function App() {\n"
        "  const handleOpen = () => {};\n"
        "  return <button onClick={handleOpen}>x</button>;\n"
        "}\n",
        encoding="utf-8",
    )
    specs = [
        StripSpec(
            start="const handleOpen",
            until="return <button",
            replace="  // stub\n",
            module_kind="hook",
        )
    ]
    cfg = StripPipelineConfig(
        file_path=str(page),
        specs=specs,
        dry_run=True,
        to_module="hook",
        no_trustchain=True,
    )
    result = run_strip_pipeline(cfg)
    assert result.ok
    assert any("defaulted out_dir" in w for w in result.pipeline_warnings)


def test_run_strip_pipeline_module_conversion(tmp_path):
    page = tmp_path / "src" / "Planning.tsx"
    page.parent.mkdir(parents=True)
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")
    specs = load_strip_manifest(manifest)
    out = tmp_path / "extracted"
    hooks = tmp_path / "src" / "hooks"
    hooks.mkdir(parents=True)

    cfg = StripPipelineConfig(
        file_path=str(page),
        specs=specs,
        dry_run=False,
        out_dir=str(out),
        to_module="hook",
        module_out_dir=str(hooks),
        no_trustchain=True,
        strict_overlap=True,
    )
    result = run_strip_pipeline(cfg)
    assert result.ok, result.errors
    assert (hooks / "usePlanningHandlers.ts").exists()
    assert result.report_path and json.loads(open(result.report_path).read())["extracted_blocks"]
