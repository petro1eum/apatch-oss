"""Session isolation regression: a prior SDD binding is never current authority."""
from copy import deepcopy

from apatch.runtime.session import end_session, start_session
from apatch.session_state import load_session_state, save_session_state
from tests.test_sdd_mandatory_profile import _frozen_pair, _install_manifest

ACTOR = {"actor_id": "agent:current", "role": "implementation"}


def _previous(root):
    _api, contract, envelope = _frozen_pair()
    started = start_session(
        str(root), "Previous SDD session", sdd_contract=contract, task_envelope=envelope,
        actor={"actor_id": "agent:previous", "role": "implementation"},
    )
    assert started["ok"] is True
    state = load_session_state(str(root))
    state["sdd"]["verification"] = {"decision": {"accepted": True}, "previous_only": True}
    state["sdd"]["falsification"] = {"observed_red": True, "previous_only": True}
    state["sdd"]["verification_run_id"] = "previous-verification"
    state["sdd"]["adherence"] = {"status": "blocked", "violations": [{"previous_only": True}]}
    save_session_state(str(root), state)
    ended = end_session(str(root), expected_session_id=state["session_id"],
                        session_token=started["session_token"], require_binding=True)
    assert ended["ok"] is True
    return state, contract, envelope


def test_completed_sdd_does_not_leak_into_next_legacy_session(tmp_path):
    previous, _contract, _envelope = _previous(tmp_path)
    started = start_session(str(tmp_path), "Independent maintenance without a profile")
    assert started["ok"] is True
    current = load_session_state(str(tmp_path))
    assert current["session_id"] != previous["session_id"]
    assert not current.get("sdd"), "The new session inherited the old judge and accepted verification"
    from apatch.sdd_integrity import admit_session_effect
    admitted = admit_session_effect(str(tmp_path), {
        "surface": "mutation", "effect": "write", "path": "tests/new_contract_test.py",
    })
    assert admitted["decision"] == "allowed"
    assert admitted["profile_enabled"] is False


def test_strict_workspace_cannot_use_previous_binding_to_omit_new_authority(tmp_path):
    _previous_state, contract, _envelope = _previous(tmp_path)
    _install_manifest(tmp_path, contract)
    before = deepcopy(load_session_state(str(tmp_path)))
    denied = start_session(str(tmp_path), "No current SDD binding")
    assert denied["ok"] is False
    assert denied["error_type"] == "SDD_CONTRACT_REQUIRED"
    assert load_session_state(str(tmp_path)) == before


def test_new_sdd_session_uses_only_new_actor_and_fresh_evidence(tmp_path):
    previous, contract, envelope = _previous(tmp_path)
    _install_manifest(tmp_path, contract)
    started = start_session(str(tmp_path), "New task under the same contract",
                            sdd_contract=contract, task_envelope=envelope, actor=ACTOR)
    assert started["ok"] is True
    current = load_session_state(str(tmp_path))
    assert current["session_id"] != previous["session_id"]
    assert current["sdd"]["actor"] == ACTOR
    assert current["sdd"]["contract_hash"] == contract["document_hash"]
    assert current["sdd"]["envelope_hash"] == envelope["document_hash"]
    assert not current["sdd"].get("verification")
    assert not current["sdd"].get("falsification")
    assert not current["sdd"].get("verification_run_id")
    assert not current["sdd"]["adherence"]["violations"]
    from apatch.sdd_integrity import SddAdmissionError, admit_session_effect
    import pytest
    with pytest.raises(SddAdmissionError) as error:
        admit_session_effect(str(tmp_path), {
            "surface": "mutation", "effect": "write", "path": "tests/test_scope.py",
        })
    assert "frozen_judge" in error.value.decision["reasons"]


def test_incomplete_new_binding_is_rejected_without_rewriting_previous_state(tmp_path):
    _state, contract, _envelope = _previous(tmp_path)
    before = deepcopy(load_session_state(str(tmp_path)))
    denied = start_session(str(tmp_path), "Incomplete SDD input", sdd_contract=contract)
    assert denied["ok"] is False
    assert denied["error_type"] == "SDD_CONTRACT_INCOMPLETE"
    assert load_session_state(str(tmp_path)) == before


def test_fresh_workspace_still_starts_without_a_profile(tmp_path):
    started = start_session(str(tmp_path), "Legacy governed work")
    assert started["ok"] is True
    assert not load_session_state(str(tmp_path)).get("sdd")
