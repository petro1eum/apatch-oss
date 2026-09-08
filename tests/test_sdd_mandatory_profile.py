from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tests.test_sdd_integrity import _envelope, _freeze_request


def _frozen_pair():
    from apatch import sdd_integrity as api

    frozen = api.freeze_contract(_freeze_request())
    envelope_request = _envelope(frozen["document_hash"])
    envelope = api.validate_task_envelope(envelope_request)
    return api, frozen, envelope


def _install_manifest(root: Path, contract: dict) -> Path:
    path = root / ".apatch" / "sdd_verification_contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_profile_manifest_requires_exact_binding_before_session_creation(tmp_path: Path) -> None:
    api, frozen, envelope = _frozen_pair()
    _install_manifest(tmp_path, frozen)

    from apatch.runtime.session import start_session
    from apatch.session_state import load_session_state

    denied = start_session(str(tmp_path), "Attempt legacy mutation")
    assert denied["ok"] is False
    assert denied["error_type"] == "SDD_CONTRACT_REQUIRED"
    assert denied["recommended_action"] == "start_sdd_session"
    assert not load_session_state(str(tmp_path)).get("session_id")

    different_request = _freeze_request()
    different_request["plan_hash"] = "sha256:" + "9" * 64
    different = api.freeze_contract(different_request)
    different_envelope_request = _envelope(different["document_hash"])
    different_envelope = api.validate_task_envelope(different_envelope_request)
    mismatch = start_session(
        str(tmp_path),
        "Attempt mismatched contract",
        sdd_contract=different,
        task_envelope=different_envelope,
        actor={"actor_id": "agent:codex", "role": "implementation"},
    )
    assert mismatch["ok"] is False
    assert mismatch["error_type"] == "SDD_CONTRACT_MISMATCH"

    started = start_session(
        str(tmp_path),
        "Implement the exact frozen task",
        sdd_contract=frozen,
        task_envelope=envelope,
        actor={"actor_id": "agent:codex", "role": "implementation"},
    )
    assert started["ok"] is True


def test_existing_legacy_session_is_denied_after_profile_activation(tmp_path: Path) -> None:
    api, frozen, _envelope_document = _frozen_pair()

    from apatch.runtime.session import start_session

    started = start_session(str(tmp_path), "Legacy work before owner freeze")
    assert started["ok"] is True
    _install_manifest(tmp_path, frozen)

    with pytest.raises(api.SddAdmissionError) as exc_info:
        api.admit_session_effect(
            str(tmp_path),
            {"surface": "mutation", "effect": "write", "path": "src/feature.py"},
        )
    assert exc_info.value.decision["reasons"] == ["sdd_contract_required"]
    assert exc_info.value.decision["mutation_performed"] is False


def test_task_envelope_must_bind_the_exact_frozen_contract() -> None:
    api, frozen, envelope = _frozen_pair()
    assert api.issue_capability(
        frozen,
        envelope,
        actor_id="agent:codex",
        role="implementation",
    )["contract_hash"] == frozen["document_hash"]

    wrong = dict(envelope)
    wrong["contract_hash"] = "sha256:" + "8" * 64
    wrong.pop("document_hash", None)
    with pytest.raises(api.SddContractError, match="contract_hash"):
        api.issue_capability(
            frozen,
            wrong,
            actor_id="agent:codex",
            role="implementation",
        )


def test_mcp_start_exposes_only_fixed_implementation_binding(tmp_path: Path) -> None:
    _api, frozen, envelope = _frozen_pair()
    _install_manifest(tmp_path, frozen)

    from apatch.mcp.server import apatch_session_start
    from apatch.session_state import load_session_state

    parameters = inspect.signature(apatch_session_start).parameters
    assert {"sdd_contract", "task_envelope", "actor_id"} <= set(parameters)
    assert "actor" not in parameters
    assert "role" not in parameters

    started = apatch_session_start(
        target_dir=str(tmp_path),
        intent="Implement exact approved task",
        sdd_contract=frozen,
        task_envelope=envelope,
        actor_id="agent:codex",
    )
    assert started["ok"] is True
    assert load_session_state(str(tmp_path))["sdd"]["actor"] == {
        "actor_id": "agent:codex",
        "role": "implementation",
    }


def test_workspace_without_profile_keeps_legacy_session_and_admission(tmp_path: Path) -> None:
    from apatch.runtime.session import start_session
    from apatch.sdd_integrity import admit_session_effect

    started = start_session(str(tmp_path), "Ordinary governed maintenance")
    assert started["ok"] is True
    decision = admit_session_effect(
        str(tmp_path),
        {"surface": "mutation", "effect": "write", "path": "src/feature.py"},
    )
    assert decision["decision"] == "allowed"
    assert decision["profile_enabled"] is False
    assert decision["legacy_behavior"] == "unchanged"
