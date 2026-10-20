"""Exact evidence selection with historical foreign signers; crypto is a double."""
import json
from types import SimpleNamespace
import pytest
from apatch import governed_work as G, governed_work_mcp as M


@pytest.fixture
def case(tmp_path, monkeypatch):
    binding = 'tcpsb_'+'1'*32
    root = G.governed_work_root(str(tmp_path))
    (root/'evidence').mkdir(parents=True)
    (root/'timesheets').mkdir()
    sheet = {'timesheet_id': 'aptime_'+'2'*32, 'claimed_active_seconds': 1,
             'project_source_binding_id': binding}
    selected = {'bundle_id': 'apweb_'+'7'*32, 'project_source_binding_id': binding, 'signer': 'selected',
        'timesheet_ref': {**sheet, 'timesheet_hash': G.document_hash(sheet)},
        'change_id': 'apchg_'+'3'*32}
    change = {'tenant_id': 'test-only', 'project_group_id': 'tcpg_'+'4'*32}
    selected['change_hash'] = G.document_hash(change)
    (root/'timesheets'/f"{sheet['timesheet_id']}.json").write_text(json.dumps(sheet))
    (root/'evidence/00-historical.json').write_text(json.dumps({
        'bundle_id': 'apweb_'+'8'*32, 'project_source_binding_id': 'tcpsb_'+'9'*32, 'signer': 'foreign'}))
    path = root/'evidence/01-selected.json';path.write_text(json.dumps(selected))
    provider = SimpleNamespace(get_public_key=lambda: b'x'*32)
    monkeypatch.setattr(G, '_load_local_provider', lambda *_: provider)
    monkeypatch.setattr(G, 'signer_key_id', lambda _: 'selected-test-key')
    validated = []
    def validate(document, *, trusted_actor_keys):
        validated.append(document['signer'])
        if document['signer'] != 'selected':raise G.GovernedWorkError('untrusted signing key')
        return document
    monkeypatch.setattr(G, 'validate_work_evidence_bundle', validate)
    monkeypatch.setattr(G, 'validate_timesheet_draft', lambda document, **_: document)
    monkeypatch.setattr(G, 'load_change', lambda *_: change)
    return tmp_path, binding, path, selected, sheet, change, validated


def test_unrelated_historical_key_does_not_block_selected_evidence(case):
    root, binding, path, selected, sheet, change, validated = case
    assert M._publication_documents(str(root), binding) == (change, selected, sheet)
    assert validated == ['selected']


def test_selected_untrusted_signature_is_still_refused(case):
    root, binding, path, selected, sheet, change, validated = case
    path.write_text(json.dumps({**selected, 'signer': 'foreign'}))
    with pytest.raises(G.GovernedWorkError, match='untrusted signing key'):
        M._publication_documents(str(root), binding)
    assert validated == ['foreign']


def test_ambiguous_selected_binding_is_still_refused(case):
    root, binding, path, selected, sheet, change, validated = case
    path.with_name('02-duplicate.json').write_text(json.dumps(selected))
    with pytest.raises(G.GovernedWorkError, match='exactly one'):
        M._publication_documents(str(root), binding)


def test_plan_selection_also_ignores_unrelated_historical_key(case):
    root, binding, path, selected, sheet, change, validated = case
    plan = {'evidence_bundle_ref': {'bundle_id': selected['bundle_id']}}
    assert M._publication_documents_for_plan(str(root), plan) == (change, selected, sheet)
    assert validated == ['selected', 'selected']


def test_plan_selection_does_not_accept_an_untrusted_selected_key(case):
    root, binding, path, selected, sheet, change, validated = case
    path.write_text(json.dumps({**selected, 'signer': 'foreign'}))
    plan = {'evidence_bundle_ref': {'bundle_id': selected['bundle_id']}}
    with pytest.raises(G.GovernedWorkError, match='untrusted signing key'):
        M._publication_documents_for_plan(str(root), plan)


def test_plan_selection_still_refuses_duplicate_selected_id(case):
    root, binding, path, selected, sheet, change, validated = case
    path.with_name('02-duplicate.json').write_text(json.dumps(selected))
    plan = {'evidence_bundle_ref': {'bundle_id': selected['bundle_id']}}
    with pytest.raises(G.GovernedWorkError, match='exactly one'):
        M._publication_documents_for_plan(str(root), plan)
