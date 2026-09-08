"""Tests for parent auto-wiring after strip + to-module."""

from apatch.auto_wire import (
    apply_auto_wire,
    extract_jsx_wire_from_replace,
    stub_already_wires_symbol,
)
from apatch.import_paths import normalize_ts_import_line, workspace_ts_import


def test_workspace_ts_import_uses_alias(tmp_path):
    ws = tmp_path / "repo"
    mod = ws / "src" / "features" / "planning" / "components" / "MethodologyNote.tsx"
    mod.parent.mkdir(parents=True)
    mod.write_text("export const X = 1;\n", encoding="utf-8")
    line = workspace_ts_import(str(mod), str(ws), "MethodologyNote")
    assert line == "import { MethodologyNote } from '@/features/planning/components/MethodologyNote';"


def test_normalize_absolute_import(tmp_path):
    ws = tmp_path / "repo"
    (ws / "src" / "hooks").mkdir(parents=True)
    abs_path = str((ws / "src" / "hooks" / "useFoo.ts").resolve())
    raw = f"import {{ useFoo }} from '{abs_path}';"
    assert normalize_ts_import_line(raw, str(ws)) == "import { useFoo } from '@/hooks/useFoo';"


def test_auto_wire_inserts_component_before_until_marker(tmp_path):
    ws = tmp_path / "repo"
    src = ws / "src"
    src.mkdir(parents=True)
    parent = src / "Panel.tsx"
    parent.write_text(
        "import React from 'react';\n"
        "export function Panel() {\n"
        "  return (\n"
        "    <div>\n"
        "      <QuotaEditDialog plan={null} />\n"
        "    </div>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )
    mod = src / "features" / "MethodologyNote.tsx"
    mod.parent.mkdir(parents=True, exist_ok=True)
    mod.write_text("export const MethodologyNote = () => null;\n", encoding="utf-8")

    meta = [{
        "label": "methodology_note",
        "stub_replacement": "",
        "until_marker": "<QuotaEditDialog",
        "module_path": str(mod),
        "wiring_hints": {
            "import_line": workspace_ts_import(str(mod), str(ws), "MethodologyNote"),
            "parent_wire": "<MethodologyNote />",
            "export_symbol": "MethodologyNote",
            "module_kind": "component",
        },
        "integration_hints": {"module_kind": "component"},
    }]
    result = apply_auto_wire(str(parent), meta, workspace=str(ws))
    text = parent.read_text(encoding="utf-8")
    assert result["ok"] is True, result
    assert result["changed"] is True
    assert "MethodologyNote" in text
    assert text.index("MethodologyNote") < text.index("QuotaEditDialog")


def test_auto_wire_import_after_multiline_import_block(tmp_path):
    parent = tmp_path / "Panel.tsx"
    parent.write_text(
        "import React from 'react';\n"
        "import {\n"
        "  Table,\n"
        "  TableRow,\n"
        "} from '@/components/ui/table';\n"
        "import { Button } from '@/components/ui/button';\n"
        "\n"
        "export function Panel() {\n"
        "  return <QuotaEditDialog />;\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "until_marker": "<QuotaEditDialog",
        "module_path": str(tmp_path / "MethodologyNote.tsx"),
        "wiring_hints": {
            "import_line": "import { MethodologyNote } from '@/features/MethodologyNote';",
            "parent_wire": "<MethodologyNote />",
            "export_symbol": "MethodologyNote",
            "module_kind": "component",
        },
        "integration_hints": {"module_kind": "component"},
    }]
    (tmp_path / "MethodologyNote.tsx").write_text("export const MethodologyNote = () => null;\n")
    result = apply_auto_wire(str(parent), meta, workspace=str(tmp_path))
    text = parent.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "import { MethodologyNote }" in text
    method_idx = text.index("import { MethodologyNote }")
    table_idx = text.index("} from '@/components/ui/table'")
    button_idx = text.index("import { Button }")
    assert method_idx > button_idx, "import must come after all existing imports"


def test_extract_jsx_wire_from_multiline_replace():
    replace = (
        "{subTab === 'quarters' ? (\n"
        "            <QuarterlyQuotasTable\n"
        "              quarterStats={quarterStats}\n"
        "              year={year}\n"
        "            />\n"
        "          ) : ("
    )
    wire = extract_jsx_wire_from_replace(replace, "QuarterlyQuotasTable")
    assert "quarterStats={quarterStats}" in wire
    assert "year={year}" in wire


def test_stub_already_wires_symbol_detects_props():
    stub = "<Foo bar={bar} baz={baz} />"
    assert stub_already_wires_symbol(stub, "Foo") is True
    assert stub_already_wires_symbol("<Foo />", "Foo") is False


def test_auto_wire_preserves_manifest_stub_with_props(tmp_path):
    parent = tmp_path / "Panel.tsx"
    stub = (
        "          {subTab === 'quarters' ? (\n"
        "            <QuarterlyQuotasTable\n"
        "              quarterStats={quarterStats}\n"
        "              year={year}\n"
        "            />\n"
        "          ) : ("
    )
    parent.write_text(
        "import React from 'react';\n"
        "export function Panel() {\n"
        "  return (\n"
        f"{stub}\n"
        "            <div>months</div>\n"
        "          )}\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "label": "quarterly_quotas_table",
        "stub_replacement": stub,
        "wiring_hints": {
            "import_line": "import { QuarterlyQuotasTable } from '@/features/QuarterlyQuotasTable';",
            "parent_wire": "<QuarterlyQuotasTable />",
            "export_symbol": "QuarterlyQuotasTable",
            "module_kind": "component",
        },
        "integration_hints": {"module_kind": "component"},
    }]
    result = apply_auto_wire(str(parent), meta, specs_replace=[stub], workspace=str(tmp_path))
    text = parent.read_text(encoding="utf-8")
    assert result["ok"] is True, result
    assert "quarterStats={quarterStats}" in text
    assert "<QuarterlyQuotasTable />" not in text
    assert "import { QuarterlyQuotasTable }" in text


def test_auto_wire_skips_until_insert_for_inner_component(tmp_path):
    parent = tmp_path / "Panel.tsx"
    parent.write_text(
        "import React from 'react';\n"
        "export function Panel() {\n"
        "  return (\n"
        "    <div className=\"space-y-4\">\n"
        "      <table />\n"
        "    </div>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "label": "stage_progress",
        "stub_replacement": "",
        "strip_shape": "inner_component",
        "until_marker": "<div className=\"space-y-4\">",
        "module_path": str(tmp_path / "StageBar.tsx"),
        "wiring_hints": {
            "import_line": "import { StageBar } from '@/StageBar';",
            "parent_wire": "<StageBar deal={record} />",
            "export_symbol": "StageBar",
            "module_kind": "component",
        },
        "integration_hints": {"module_kind": "component"},
    }]
    (tmp_path / "StageBar.tsx").write_text("export const StageBar = () => null;\n")
    result = apply_auto_wire(str(parent), meta, workspace=str(tmp_path))
    text = parent.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "StageBar" in text  # import added
    assert "<StageBar" not in text  # no bogus until insert
    assert "skipped until-marker wire" in " ".join(result["changes"])


def test_auto_wire_hook_preserves_guard_and_shell(tmp_path):
    stub = (
        "  const { profile, handleSave } = useContactProfile({ contactId });\n\n"
        "  if (!profile) {\n"
        "    return (\n"
        "      <div>not found</div>\n"
        "    );\n"
        "  }\n\n"
        "  return (\n"
        "    <div className=\"space-y-4\">\n"
    )
    parent = tmp_path / "Page.tsx"
    parent.write_text(
        "import React from 'react';\n"
        "export function Page({ contactId }) {\n"
        f"{stub}"
        "      <span>body</span>\n"
        "    </div>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "stub_replacement": stub,
        "integration_hints": {
            "parent_import": "import { useContactProfile } from '@/hooks/useContactProfile';",
            "parent_wire": "const { profile, handleSave } = useContactProfile({ contactId });",
            "module_kind": "hook",
        },
        "wiring_hints": {},
    }]
    result = apply_auto_wire(str(parent), meta, specs_replace=[stub])
    text = parent.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert "useContactProfile" in text
    assert "if (!profile)" in text
    assert 'className="space-y-4"' in text
    assert "<span>body</span>" in text


def test_auto_wire_hook_stub_replace(tmp_path):
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
            "module_kind": "hook",
        },
        "wiring_hints": {},
    }]
    result = apply_auto_wire(str(parent), meta, specs_replace=[meta[0]["stub_replacement"]])
    text = parent.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert "useExampleHandlers" in text
    assert "Stubbed" not in text


def test_auto_wire_preserves_multiline_hook_destructure(tmp_path):
    """Dogfood: MilitaryRadar handlers manifest — do not collapse to parent_wire one-liner."""
    stub = (
        "  const {\n"
        "    stats,\n"
        "    getFutureThetaWithForces,\n"
        "    forcesRisk,\n"
        "  } = useMilitaryRadarHandlers({\n"
        "    risks,\n"
        "    setRisks,\n"
        "    persistRiskPart,\n"
        "  });\n\n"
    )
    parent = tmp_path / "MilitaryRadar.jsx"
    parent.write_text(
        "'use client'\n\n"
        "import React from 'react';\n"
        "export function MilitaryRadar() {\n"
        f"{stub}"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "stub_replacement": stub,
        "integration_hints": {
            "parent_import": "import { useMilitaryRadarHandlers } from '@/features/radar/useMilitaryRadarHandlers';",
            "parent_wire": "useMilitaryRadarHandlers({ risks, setRisks, persistRiskPart })",
            "module_kind": "hook",
        },
        "wiring_hints": {},
    }]
    result = apply_auto_wire(str(parent), meta, specs_replace=[stub], workspace=str(tmp_path))
    text = parent.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "const {" in text
    assert "stats," in text
    assert "useMilitaryRadarHandlers({" in text
    assert "useMilitaryRadarHandlers({ risks" not in text.replace("\n", " ").replace("  ", " ")


def test_auto_wire_adds_import_when_parent_wire_empty_but_stub_has_hook(tmp_path):
    """Regression: import must not be skipped when parent_wire is missing."""
    stub = "  const { user, name } = useProfilePage();\n\n"
    parent = tmp_path / "Profile.tsx"
    parent.write_text(
        "import React from 'react';\n"
        "export function Profile() {\n"
        f"{stub}"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "stub_replacement": stub,
        "module_path": "src/pages/hooks/useProfilePage.ts",
        "integration_hints": {
            "parent_import": "import { useProfilePage } from '@/pages/hooks/useProfilePage';",
            "parent_wire": "",
            "module_kind": "hook",
            "target_module": "src/pages/hooks/useProfilePage.ts",
        },
        "wiring_hints": {},
    }]
    result = apply_auto_wire(str(parent), meta, specs_replace=[stub], workspace=str(tmp_path))
    text = parent.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "import { useProfilePage }" in text
    assert "useProfilePage()" in text


def test_auto_wire_import_after_use_client_directive(tmp_path):
    parent = tmp_path / "Radar.jsx"
    parent.write_text(
        "'use client'\n"
        "\n"
        "import React from 'react';\n"
        "export function Radar() {\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    meta = [{
        "stub_replacement": "",
        "integration_hints": {
            "parent_import": "import { useRadarTheme } from '@/features/radar/useRadarTheme';",
            "parent_wire": "useRadarTheme();",
            "module_kind": "hook",
        },
        "wiring_hints": {},
    }]
    result = apply_auto_wire(str(parent), meta, workspace=str(tmp_path))
    text = parent.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert text.splitlines()[0].strip() == "'use client'"
    assert text.index("useRadarTheme") > text.index("'use client'")
