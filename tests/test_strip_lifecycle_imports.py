"""P0: governed lifecycle verify→attest and hook-strip parent import prune."""

import json

import pytest

import apatch.enforcement as enforcement_mod
from apatch.enforcement import register_enforcement_policy_hook
from apatch.imports_resolver import prune_unused_imports
from apatch.trustchain_helper import TrustChainHelper
from apatch.runtime.domain import LIFECYCLE_COMMITTED, LIFECYCLE_VERIFYING, derive_lifecycle
from apatch.runtime.session import start_session
from apatch.runtime.state_machine import OP_ATTEST, assert_operation
from apatch.session_state import enrich_tool_response, load_session_state


def _catalog_parent_after_hook_strip() -> str:
    return """import React, { useState, useMemo } from 'react';
import { useSelector, useDispatch } from 'react-redux';
import { RootState, AppDispatch } from '@/store';
import {
  addProductAsync, updateProductAsync, deleteProductAsync,
  addServiceAsync, updateServiceAsync, deleteServiceAsync
} from '@/store/salesSlice';
import PageHeader from '@/components/common/PageHeader';
import { Product, Service } from '@/types/sales';
import { Plus, Edit2, Trash2, X } from 'lucide-react';
import { useCatalogPage } from '@/pages/hooks/useCatalogPage';

const Catalog: React.FC = () => {
  const { activeTab, handleAdd, sortedProducts } = useCatalogPage();

  return (
    <div className="px-2 relative space-y-6">
      <PageHeader title="Product & Service Catalog" subTitle="Manage pricing" />
      <button type="button" onClick={handleAdd}><Plus className="w-4 h-4" /></button>
      <Edit2 className="w-3.5 h-3.5" />
      <Trash2 className="w-3.5 h-3.5" />
      <X className="w-3 h-3" />
      <span>{activeTab}</span>
      <span>Product Name</span>
      {sortedProducts.map((record) => (
        <span key={record.id}>{record.name}</span>
      ))}
    </div>
  );
};

export default Catalog;
"""


def test_prune_unused_imports_after_hook_strip():
    pruned = prune_unused_imports(_catalog_parent_after_hook_strip())
    assert "salesSlice" not in pruned
    assert "useDispatch" not in pruned
    assert "useSelector" not in pruned
    assert "AppDispatch" not in pruned
    assert "useState" not in pruned
    assert "useMemo" not in pruned
    assert "Product, Service" not in pruned
    assert "import React from 'react';" in pruned
    assert "useCatalogPage" in pruned
    assert "PageHeader" in pruned
    assert "lucide-react" in pruned


def test_derive_lifecycle_committed_allows_attest_not_trustchain_checkpoint():
    assert derive_lifecycle("complete", attested=False) == LIFECYCLE_COMMITTED
    assert derive_lifecycle("complete", attested=True) != LIFECYCLE_COMMITTED


def test_verify_run_ok_transitions_to_complete_phase(tmp_path):
    start_session(str(tmp_path), "hook strip verify attest")
    enrich_tool_response(
        "apatch_strip",
        {"ok": True, "checkpoint": "ck-strip", "trustchain": {"active": True}},
        target_dir=str(tmp_path),
    )
    enrich_tool_response(
        "apatch_verify_run",
        {"ok": True, "verify_command": "npm test"},
        target_dir=str(tmp_path),
    )
    state = load_session_state(str(tmp_path))
    assert state["phase"] == "complete"
    assert state.get("attested") is not True


def test_attest_allowed_after_verify_without_manual_session_patch(tmp_path):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "enforcement.json").write_text(json.dumps({"mode": "strict"}), encoding="utf-8")

    start_session(str(tmp_path), "verify then attest")
    enrich_tool_response(
        "apatch_verify_run",
        {"ok": True, "verify_command": "npm test"},
        target_dir=str(tmp_path),
    )
    # Must not raise — phase complete → lifecycle committed
    assert_operation(str(tmp_path), OP_ATTEST)


def test_attest_allowed_in_verifying_lifecycle_fallback(tmp_path):
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "enforcement.json").write_text(json.dumps({"mode": "strict"}), encoding="utf-8")

    start_session(str(tmp_path), "still verifying")
    enrich_tool_response(
        "apatch_strip",
        {"ok": True, "checkpoint": "ck"},
        target_dir=str(tmp_path),
    )
    state = load_session_state(str(tmp_path))
    assert state["phase"] == "verify"
    assert derive_lifecycle(state["phase"]) == LIFECYCLE_VERIFYING
    assert_operation(str(tmp_path), OP_ATTEST)


def test_enforcement_policy_allows_apatch_attest_ledger_commit(tmp_path, monkeypatch):
    """Regression: attest used tool_id apatch_attest, blocked by _require_apatch_notarization."""
    pytest.importorskip("trustchain")  # asserts has_trustchain(); needs the lib
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()
    (apatch_dir / "enforcement.json").write_text(json.dumps({"mode": "strict"}), encoding="utf-8")

    enforcement_mod._APATCH_POLICY_REGISTERED = False
    register_enforcement_policy_hook()
    helper = TrustChainHelper(str(tmp_path), auto_init=True)
    assert helper.has_trustchain()

    payload = {
        "intent": "verify then attest",
        "session_id": "apatch_sess_test",
        "message": "test attest",
    }
    assert helper.commit_action("apatch_attest", payload) is True


def test_attest_success_sets_attested_flag(tmp_path):
    start_session(str(tmp_path), "attest flag")
    enrich_tool_response(
        "apatch_verify_run",
        {"ok": True},
        target_dir=str(tmp_path),
    )
    enrich_tool_response(
        "apatch_attest",
        {"ok": True, "committed": True},
        target_dir=str(tmp_path),
    )
    state = load_session_state(str(tmp_path))
    assert state.get("attested") is True
    assert derive_lifecycle(state["phase"], attested=True) != LIFECYCLE_COMMITTED
