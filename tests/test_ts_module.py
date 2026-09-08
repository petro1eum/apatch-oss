from apatch.converters.ts_module import (
    classify_strip_shape,
    collect_scope_bindings,
    convert_component,
    convert_hook,
    convert_util,
    extract_arrow_function_return_jsx,
    extract_closure_params,
    extract_component_jsx_body,
    extract_root_jsx_element,
    extract_defined_names,
    extract_hook_call_params,
    extract_hook_destructure_exports,
    hook_call_has_no_args,
    extract_tab_entry_children,
    extract_top_level_hook_exports,
    resolve_hook_export_name,
)


PARENT = """
import React, { useState } from 'react';
import { doWork } from '@/lib/work';

export function Planning() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  // --- handlers ---
  const handleOpenDrawer = () => { setDrawerOpen(true); doWork(); };
  const handleCloseDrawer = () => { setDrawerOpen(false); };
  // --- end handlers ---
  return <div onClick={handleOpenDrawer} />;
}
"""

REMOVED = """  const handleOpenDrawer = () => { setDrawerOpen(true); doWork(); };
  const handleCloseDrawer = () => { setDrawerOpen(false); };
"""


def test_extract_defined_names():
    names = extract_defined_names(REMOVED)
    assert "handleOpenDrawer" in names
    assert "handleCloseDrawer" in names


def test_extract_defined_names_use_state_tuple():
    block = "  const [view, setView] = useState<'company' | 'reps'>('company');\n"
    names = extract_defined_names(block)
    assert names == ["view", "setView"]


def test_extract_top_level_hook_exports_excludes_nested_locals():
    block = """  const [view, setView] = useState(false);
  const handleEditPeriod = (row: { quarter?: string }) => {
    const plan = plans.find((p) => p.quarter === row.quarter);
    setEditingPlan(plan);
  };
  const titlePrefix = 'Company';
"""
    exports = extract_top_level_hook_exports(block)
    assert "view" in exports
    assert "setView" in exports
    assert "handleEditPeriod" in exports
    assert "titlePrefix" in exports
    assert "plan" not in exports
    assert "row" not in exports


def test_collect_scope_bindings_includes_callback_params():
    block = "plans.find((p) => p.year === year);\n"
    bindings = collect_scope_bindings(block)
    assert "p" in bindings


def test_resolve_hook_export_name_prefers_manifest_and_strips_hook_suffix():
    assert (
        resolve_hook_export_name(
            label="sales_funnel_hook",
            target_module="src/features/sales-funnel/useSalesFunnel.ts",
            manifest_replace="} = useSalesFunnel({ deals, dispatch });",
        )
        == "useSalesFunnel"
    )
    assert resolve_hook_export_name(label="account_hub_overview_hook") == "useAccountHubOverview"


def test_classify_strip_shape():
    assert classify_strip_shape("  const Foo = () => <div />") == "inner_component"
    assert classify_strip_shape("  <div className='x'>") == "jsx"
    assert classify_strip_shape("  const [x, setX] = useState(0);") == "logic"


def test_sanitize_hook_body_strips_jsx_guards():
    from apatch.converters.ts_module import _sanitize_hook_body

    body = """  if (!profile) {
    return (
      <div>Contact not found</div>
    );
  }
  const handleSave = () => {};
"""
    cleaned = _sanitize_hook_body(body)
    assert "return (" not in cleaned
    assert "handleSave" in cleaned


def test_convert_hook_full(tmp_path):
    out = tmp_path / "usePlanningHandlers.ts"
    result = convert_hook(
        removed_content=REMOVED,
        label="planning_handlers",
        parent_imports=[
            "import { doWork } from '@/lib/work';",
            "import React, { useState } from 'react';",
        ],
        out_path=str(out),
        parent_content=PARENT,
        file_path=str(tmp_path / "Planning.tsx"),
    )
    text = out.read_text(encoding="utf-8")
    assert "export function usePlanningHandlers" in text
    assert "UsePlanningHandlersParams" in text
    assert "return { handleOpenDrawer, handleCloseDrawer }" in text
    assert "setDrawerOpen" in text
    assert "const { setDrawerOpen" in text
    assert "import { doWork }" in text
    assert "setDrawerOpen" in text
    assert "doWork" not in text.split("Params")[1].split("}")[0]  # doWork via import, not param
    assert result["exports"] == ["handleOpenDrawer", "handleCloseDrawer"]
    assert "setDrawerOpen" in result["closure_params"]


def test_extract_closure_params():
    removed = "const x = foo + bar;\n"
    params = extract_closure_params(removed, PARENT)
    assert "foo" in params or "setDrawerOpen" not in params


COMPANY_TARGETS_PARENT = """
import React, { useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { SALES_PLANNING_YEAR } from '@/config/app';

const CompanyTargetsPanel = ({ year = SALES_PLANNING_YEAR }) => {
  const dispatch = useDispatch();
  const [view, setView] = useState('company');
  return <div>{year}</div>;
};
"""

COMPANY_TARGETS_REMOVED = """  const dispatch = useDispatch();
  const { plans, goldSheets } = useSelector((s) => s.sales);
  const [view, setView] = useState('company');
  const [subTab, setSubTab] = useState('quarters');
  const repNames = useMemo(() => collectRepNames(deals, plans, goldSheets, year), [year]);
  const handleEditPeriod = (row: { quarter?: string }) => {
    const plan = plans.find((p) => p.year === year && p.quarter === row.quarter) || {
      year,
      targetSoftware: 0,
      targetTotal: 0,
    };
    setEditingPlan(plan);
  };
  const titlePrefix = view === 'company' ? 'Company' : 'Seller';
"""

COMPANY_TARGETS_REPLACE = """  const {
    view,
    setView,
    subTab,
    setSubTab,
    repNames,
    titlePrefix,
    handleEditPeriod,
  } = useCompanyTargetsPanel({ year });

"""


def test_hook_manifest_replace_drives_closure_and_exports():
    hook = "useCompanyTargetsPanel"
    assert extract_hook_call_params(COMPANY_TARGETS_REPLACE, hook) == ["year"]
    exports = extract_hook_destructure_exports(COMPANY_TARGETS_REPLACE, hook)
    assert "view" in exports
    assert "setView" in exports
    assert "handleEditPeriod" in exports


def test_hook_call_has_no_args_detects_empty_call():
    replace = "  const { user } = useProfilePage();\n"
    assert hook_call_has_no_args(replace, "useProfilePage")
    assert not hook_call_has_no_args("  useProfilePage({ year });\n", "useProfilePage")


def test_convert_hook_with_useeffect_keeps_manifest_exports(tmp_path):
    replace = """  const {
    user,
    name,
    setName,
    handleProfileSave,
  } = useProfilePage();

"""
    removed = """
  const { user } = useAccess();
  const [name, setName] = useState('');
  const handleProfileSave = async (e: React.FormEvent) => { e.preventDefault(); };
  useEffect(() => {
    if (user) setName(user.name);
  }, [user]);
"""
    out = tmp_path / "useProfilePage.ts"
    convert_hook(
        removed_content=removed,
        label="profile_page_hook",
        parent_imports=[
            "import { useEffect, useState } from 'react';",
            "import { useAccess } from '@/hooks/useAccess';",
        ],
        out_path=str(out),
        manifest_replace=replace,
    )
    text = out.read_text(encoding="utf-8")
    assert "export function useProfilePage()" in text
    assert "return { user, name, setName, handleProfileSave }" in text


def test_convert_company_targets_style_hook(tmp_path):
    out = tmp_path / "useCompanyTargetsPanel.ts"
    result = convert_hook(
        removed_content=COMPANY_TARGETS_REMOVED,
        label="company_targets_panel",
        parent_imports=[
            "import { useMemo, useState } from 'react';",
            "import { useDispatch, useSelector } from 'react-redux';",
            "import { collectRepNames } from '@/domain/planning/quotaAnalytics';",
        ],
        out_path=str(out),
        parent_content=COMPANY_TARGETS_PARENT,
        file_path=str(tmp_path / "CompanyTargetsPanel.tsx"),
        manifest_replace=COMPANY_TARGETS_REPLACE,
    )
    text = out.read_text(encoding="utf-8")
    assert "export function useCompanyTargetsPanel" in text
    assert "year: number" in text or "year:" in text
    assert "const { year } = params" in text
    assert "return { view, setView, subTab, setSubTab, repNames, titlePrefix, handleEditPeriod }" in text
    assert "const { can, dispatch" not in text
    assert "const plan =" in text
    assert result["closure_params"] == ["year"]
    assert "setView" in result["exports"]
    assert "plan" not in result["exports"]


COMPONENT_REMOVED = """  return (
    <div onClick={handleOpenDrawer}>
      <span>{drawerOpen ? 'open' : 'closed'}</span>
    </div>
  );
"""


def test_convert_component_strips_ternary_prefix(tmp_path):
    out = tmp_path / "QuarterlyQuotasTable.tsx"
    removed = (
        "{subTab === 'quarters' ? (\n"
        "            <Card><span>Q1</span></Card>\n"
        "          ) : ("
    )
    convert_component(
        removed_content=removed,
        label="quarterly_quotas_table",
        parent_imports=["import React from 'react';", "import { Card } from '@/components/ui/card';"],
        out_path=str(out),
    )
    text = out.read_text(encoding="utf-8")
    assert "subTab === 'quarters'" not in text
    assert "<Card>" in text


TAB_ENTRY_REMOVED = """      key: 'concept',
      label: '1. Objective & Concept',
      children: (
        <div className="flex flex-col gap-5">
          <Card className="shadow-sm">
            <CardContent>
              <textarea value={sheet.title} onChange={(e) => updateField('title', e.target.value)} />
            </CardContent>
          </Card>
        </div>
      )
    },
    {
"""

RENDER_FN_REMOVED = """  const renderQuestionTable = (
    type: 'confirmation' | 'newInfo',
    title: string,
  ) => {
    return (
      <Card className="shadow-sm">
        <CardContent>{title}</CardContent>
      </Card>
    );
  };
"""


def test_extract_tab_entry_children():
    inner = extract_tab_entry_children(TAB_ENTRY_REMOVED)
    assert inner is not None
    assert inner.startswith("<div")
    assert "sheet.title" in inner
    assert "key: 'concept'" not in inner


def test_extract_arrow_function_return_jsx():
    inner = extract_arrow_function_return_jsx(RENDER_FN_REMOVED)
    assert inner is not None
    assert "<Card" in inner
    assert "renderQuestionTable" not in inner


def test_extract_component_jsx_body_prefers_tab_entry():
    body = extract_component_jsx_body(TAB_ENTRY_REMOVED)
    assert body.startswith("<div")


SALES_FUNNEL_TABLE_REMOVED = """      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="overflow-x-auto w-full">
          <table className="w-full text-left border-collapse min-w-[1300px]">
            <thead className="bg-muted/40 border-b border-border">
              <tr><th>Client Name</th></tr>
            </thead>
            <TableBody>
              {paginatedDeals.map((record, index) => {
                const isExpanded = selectedDealId === record.id;
                return (
                  <React.Fragment key={record.id}>
                    <TableRow>
                      <TableCell>{record.clientName}</TableCell>
                    </TableRow>
                  </React.Fragment>
                );
              })}
            </TableBody>
          </table>
        </div>
        {totalPages > 1 && (
          <div className="p-3.5 bg-muted/20 border-t border-border">
            <span>Page {currentPage}</span>
          </div>
        )}
      </div>
"""


def test_extract_root_jsx_element_returns_outer_wrapper():
    root = extract_root_jsx_element(SALES_FUNNEL_TABLE_REMOVED)
    assert root is not None
    assert root.startswith('<div className="border')
    assert "paginatedDeals.map" in root
    assert "<table" in root
    assert root.endswith("</div>")


def test_extract_component_jsx_body_prefers_root_over_map_return():
    body = extract_component_jsx_body(SALES_FUNNEL_TABLE_REMOVED)
    assert body.startswith('<div className="border')
    assert "paginatedDeals.map" in body
    assert "<React.Fragment" in body
    assert body.count("<div") >= 2


def test_convert_component_from_root_jsx_with_map(tmp_path):
    out = tmp_path / "SalesFunnelDealsTable.tsx"
    replace = (
        "      <SalesFunnelDealsTable\n"
        "        paginatedDeals={paginatedDeals}\n"
        "        selectedDealId={selectedDealId}\n"
        "        onSelectedDealIdChange={setSelectedDealId}\n"
        "        currentPage={currentPage}\n"
        "        totalPages={totalPages}\n"
        "      />\n"
    )
    result = convert_component(
        removed_content=SALES_FUNNEL_TABLE_REMOVED,
        label="sales_funnel_deals_table",
        parent_imports=[
            "import React from 'react';",
            "import { TableBody, TableRow, TableCell } from '@/components/ui/table';",
        ],
        out_path=str(out),
        parent_content="const x = 1;",
        file_path=str(tmp_path / "SalesFunnel.tsx"),
        manifest_replace=replace,
    )
    text = out.read_text(encoding="utf-8")
    assert "export const SalesFunnelDealsTable" in text
    assert "paginatedDeals.map" in text
    assert "<table" in text
    assert "record.clientName" in text
    assert "paginatedDeals" in result["closure_params"]
    assert "selectedDealId" in result["closure_params"]


def test_convert_component_from_tab_entry(tmp_path):
    out = tmp_path / "GreenConceptTab.tsx"
    replace = "        <GreenConceptTab sheet={sheet} updateField={updateField} />\n"
    parent = """
import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { GreenSheet } from '@/types/sales';

const Page = () => {
  const [sheet, setSheet] = useState<GreenSheet | null>(null);
  if (!sheet) return null;
  const updateField = (field: keyof GreenSheet, value: unknown) => {
    setSheet((prev) => prev ? { ...prev, [field]: value } : null);
  };
  const tabItems = [
"""
    result = convert_component(
        removed_content=TAB_ENTRY_REMOVED,
        label="green_concept_tab",
        parent_imports=[
            "import React, { useState } from 'react';",
            "import { Card, CardContent } from '@/components/ui/card';",
            "import { GreenSheet } from '@/types/sales';",
        ],
        out_path=str(out),
        parent_content=parent,
        file_path=str(tmp_path / "Page.tsx"),
        manifest_replace=replace,
    )
    text = out.read_text(encoding="utf-8")
    assert "export const GreenConceptTab" in text
    assert "key: 'concept'" not in text
    assert "sheet.title" in text
    assert "GreenSheet" in text
    assert "updateField" in text
    assert "sheet:" in text
    assert result["closure_params"] == ["sheet", "updateField"]


def test_convert_component(tmp_path):
    out = tmp_path / "PlanningPanel.tsx"
    result = convert_component(
        removed_content=COMPONENT_REMOVED,
        label="planning_panel",
        parent_imports=["import React, { useState } from 'react';"],
        out_path=str(out),
        parent_content=PARENT,
        file_path=str(tmp_path / "Planning.tsx"),
    )
    text = out.read_text(encoding="utf-8")
    assert "export const PlanningPanel" in text
    assert "PlanningPanelProps" in text
    assert "drawerOpen" in text
    assert result["module_kind"] == "component"
    assert "handleOpenDrawer" in result["parent_wire"]


UTIL_REMOVED = """  const total = drawerOpen ? 1 : 0;
  return total;
"""


META_EFFECT_REMOVED = """  // apatch: radar-meta-sync
  useEffect(() => {
    if (appliedMeta) return;
    if (apiDataSources.length) setDataSources(apiDataSources);
    setAppliedMeta(true);
"""


OVERVIEW_SCAN_REMOVED = """  // apatch: radar-overview-scan
  useEffect(() => {
    if (mode === 'overview') {
      const interval = setInterval(() => setScanAngle(prev => (prev + 1.5) % 360), 40);
      return () => clearInterval(interval);
"""


def test_convert_hook_side_effect_wire_no_spurious_exports(tmp_path):
    out = tmp_path / "useMilitaryRadarOverviewScan.ts"
    convert_hook(
        removed_content=OVERVIEW_SCAN_REMOVED,
        label="radar_overview_scan",
        parent_imports=["import { useEffect } from 'react';"],
        out_path=str(out),
        parent_content="const [mode, setMode] = useState('overview');",
        file_path=str(tmp_path / "Radar.jsx"),
        manifest_replace="  useMilitaryRadarOverviewScan({ mode, setScanAngle });\n",
        parent_wire="useMilitaryRadarOverviewScan({ mode, setScanAngle });",
        until_marker="  }, [mode]);",
    )
    text = out.read_text(encoding="utf-8")
    assert "return { interval }" not in text
    assert "return {}" not in text


def test_convert_hook_use_effect_until_closure(tmp_path):
    out = tmp_path / "useMilitaryRadarMetaSync.ts"
    convert_hook(
        removed_content=META_EFFECT_REMOVED,
        label="radar_meta_sync",
        parent_imports=["import { useEffect } from 'react';"],
        out_path=str(out),
        parent_content="const x = 1;",
        file_path=str(tmp_path / "Radar.jsx"),
        until_marker="  }, [apiDataSources, apiActionTemplates, appliedMeta]);",
    )
    text = out.read_text(encoding="utf-8")
    assert "}, [apiDataSources, apiActionTemplates, appliedMeta]);" in text
    assert "return {}" not in text
    assert "// apatch:" not in text


def test_convert_util(tmp_path):
    out = tmp_path / "planningTotals.ts"
    result = convert_util(
        removed_content=UTIL_REMOVED,
        label="planning_totals",
        parent_imports=[],
        out_path=str(out),
        parent_content=PARENT,
        file_path=str(tmp_path / "Planning.tsx"),
    )
    text = out.read_text(encoding="utf-8")
    assert "export function planningTotals" in text
    assert "drawerOpen" in text
    assert result["module_kind"] == "util"
    assert "import" in result["parent_wire"]
