"""Regression: sales funnel hook strip naming and closure params."""

from apatch.converters.ts_module import convert_hook

SALES_FUNNEL_PARENT = """
import { useDispatch } from 'react-redux';
import { useNavigate } from 'react-router-dom';
import { AppDispatch } from '@/store';

const SalesFunnel = () => {
  const dispatch: AppDispatch = useDispatch();
  const navigate = useNavigate();
  const deals = [];
  const blueSheets = [];
  const greenSheets = [];
  const dealContexts = [];
"""

REMOVED = """  const [editDealId, setEditDealId] = useState<string | null>(null);
  const handleDeleteClick = (dealId: string) => {
    dispatch(deleteDeal({ id: dealId }));
  };
"""

REPLACE = (
    "  const { editDealId, setEditDealId, handleDeleteClick } = "
    "useSalesFunnel({ deals, blueSheets, greenSheets, dealContexts, dispatch, navigate });"
)


def test_convert_sales_funnel_hook_name_and_params(tmp_path):
    out = tmp_path / "useSalesFunnel.ts"
    result = convert_hook(
        removed_content=REMOVED,
        label="sales_funnel_hook",
        parent_imports=[
            "import { useState } from 'react';",
            "import { useDispatch } from 'react-redux';",
            "import { deleteDeal } from '@/store/salesSlice';",
        ],
        out_path=str(out),
        parent_content=SALES_FUNNEL_PARENT,
        file_path=str(tmp_path / "SalesFunnel.tsx"),
        manifest_replace=REPLACE,
    )
    text = out.read_text(encoding="utf-8")
    assert "export function useSalesFunnel" in text
    assert "useSalesFunnelHook" not in text
    assert "UseSalesFunnelParams" in text
    assert "dispatch: AppDispatch" in text
    assert "deals" in result["closure_params"]
    assert result["export_name"] == "useSalesFunnel"
