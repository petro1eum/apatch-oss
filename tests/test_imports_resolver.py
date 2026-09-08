import json

from apatch.import_paths import find_semantic_rules_path, resolve_consumer_root, workspace_ts_import
from apatch.imports_resolver import (
    build_symbol_type_map,
    filter_imports_for_content,
    infer_param_types,
    lookup_imported_symbol_type,
    parse_exported_symbol_type,
    parse_import_bindings,
    resolve_path_alias,
    resolve_parent_imports,
)


def test_parse_import_bindings():
    bindings = parse_import_bindings("import { doWork, Foo as Bar } from '@/lib/work';")
    locals = {b[0] for b in bindings}
    assert "doWork" in locals
    assert "Bar" in locals
    assert all(b[1] == "@/lib/work" for b in bindings)


def test_resolve_path_alias(tmp_path):
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {"@/*": ["src/*"]},
                }
            }
        ),
        encoding="utf-8",
    )
    src = tmp_path / "src" / "pages" / "Planning.tsx"
    src.parent.mkdir(parents=True)
    src.write_text("// x", encoding="utf-8")
    resolved = resolve_path_alias("@/lib/foo", str(src))
    assert resolved.replace("\\", "/") == "src/lib/foo"


def test_infer_param_types_use_state():
    parent = """
import React, { useState } from 'react';
const [drawerOpen, setDrawerOpen] = useState<boolean>(false);
const dispatch = useAppDispatch();
"""
    types = infer_param_types(["setDrawerOpen", "dispatch"], parent, "Planning.tsx")
    assert "SetStateAction" in types["setDrawerOpen"] or "boolean" in types["setDrawerOpen"]


def test_filter_imports_for_content():
    imports = [
        "import { doWork } from '@/lib/work';",
        "import React from 'react';",
    ]
    removed = "const handleClick = () => { doWork(); };"
    out = filter_imports_for_content(imports, removed, "", "x.tsx")
    assert any("doWork" in line for line in out)


def test_tree_sitter_imports_tsx():
    content = """import type { Deal } from '@/types/deal';
import { useState } from 'react';
"""
    lines = resolve_parent_imports("page.tsx", content)
    assert any("import type" in l for l in lines)
    assert any("useState" in l for l in lines)


def test_resolve_consumer_root_monorepo_layout(tmp_path):
    repo = tmp_path / "Risk_development_2"
    platform = repo / "platform"
    radar = platform / "src" / "components" / "radar" / "MilitaryRadar.jsx"
    radar.parent.mkdir(parents=True)
    (repo / ".git").mkdir()
    (platform / "src").mkdir(parents=True, exist_ok=True)
    (platform / "package.json").write_text("{}", encoding="utf-8")
    radar.write_text("'use client'\n", encoding="utf-8")
    assert resolve_consumer_root(str(radar)) == str(platform)


def test_find_semantic_rules_path_prefers_consumer_manifests(tmp_path):
    repo = tmp_path / "Risk_development_2"
    platform = repo / "platform"
    radar = platform / "src" / "components" / "radar" / "MilitaryRadar.jsx"
    radar.parent.mkdir(parents=True)
    (repo / "manifests").mkdir()
    (repo / "manifests" / "semantic-verify.yaml").write_text("routes: {}\n", encoding="utf-8")
    (platform / "src").mkdir(parents=True, exist_ok=True)
    (platform / "package.json").write_text("{}", encoding="utf-8")
    (platform / "manifests").mkdir()
    rules = platform / "manifests" / "semantic-verify.yaml"
    rules.write_text("version: 1\nroutes:\n  patterns: []\n", encoding="utf-8")
    radar.write_text("'use client'\n", encoding="utf-8")
    assert find_semantic_rules_path(str(radar)) == str(rules)


def test_parse_exported_symbol_type_l35_api_function():
    module = """
export type L35AutopilotState = { enabled?: boolean };
export async function patchAutopilotState(body: L35AutopilotState) {
  return safeFetch<L35AutopilotState>(`/api/autopilot`, { method: 'PATCH', body });
}
"""
    fn_type = parse_exported_symbol_type(module, "patchAutopilotState")
    assert fn_type is not None
    assert "body: L35AutopilotState" in fn_type
    assert "Promise<L35AutopilotState>" in fn_type


def test_parse_exported_symbol_type_type_alias():
    module = "export type L35DataSource = { id: string; name: string };\n"
    assert parse_exported_symbol_type(module, "L35DataSource") == "{ id: string; name: string }"


def test_infer_param_types_from_imported_api_module(tmp_path):
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {"@/*": ["src/*"]},
                }
            }
        ),
        encoding="utf-8",
    )
    api = tmp_path / "src" / "services" / "l35Api.ts"
    api.parent.mkdir(parents=True)
    api.write_text(
        """
export type L35AutopilotState = { enabled?: boolean };
export async function patchAutopilotState(body: L35AutopilotState) {
  return safeFetch<L35AutopilotState>('/autopilot', { method: 'PATCH' });
}
""",
        encoding="utf-8",
    )
    radar = tmp_path / "src" / "components" / "radar" / "MilitaryRadar.jsx"
    radar.parent.mkdir(parents=True)
    parent = """
import { patchAutopilotState } from '@/services/l35Api';
const apply = () => patchAutopilotState({ enabled: true });
"""
    radar.write_text(parent, encoding="utf-8")
    types = infer_param_types(["patchAutopilotState"], parent, str(radar))
    assert "L35AutopilotState" in types["patchAutopilotState"]
    assert "Promise<L35AutopilotState>" in types["patchAutopilotState"]
    assert lookup_imported_symbol_type(
        "patchAutopilotState",
        "src/services/l35Api",
        str(radar),
    ) == types["patchAutopilotState"]


def test_infer_param_types_radar_store_and_refs():
    parent = """
import { useRef, useState } from 'react';
import { useL35DataStore } from '@/stores/l35DataStore';
const [mode, setMode] = useState('overview');
const [scanAngle, setScanAngle] = useState(0);
const prevProjectIdRef = useRef(null);
const { loadAll, activeProjectId } = useL35DataStore();
"""
    types = infer_param_types(
        ["mode", "setScanAngle", "prevProjectIdRef", "loadAll", "activeProjectId"],
        parent,
        "MilitaryRadar.jsx",
    )
    assert types["mode"] == "string"
    assert "number" in types["setScanAngle"]
    assert types["prevProjectIdRef"] == "React.MutableRefObject<string | null>"
    assert types["loadAll"] == "() => Promise<void>"
    assert types["activeProjectId"] == "string | null"


def test_workspace_ts_import_from_monorepo_consumer(tmp_path):
    repo = tmp_path / "Risk_development_2"
    platform = repo / "platform"
    mod = platform / "src" / "features" / "radar" / "useMeta.ts"
    mod.parent.mkdir(parents=True)
    mod.write_text("export function useMeta() {}\n", encoding="utf-8")
    line = workspace_ts_import(str(mod), str(platform), "useMeta")
    assert line == "import { useMeta } from '@/features/radar/useMeta';"
