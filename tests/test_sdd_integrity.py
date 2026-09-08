from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest


def _api():
    return importlib.import_module("apatch.sdd_integrity")


def _brief_request() -> dict:
    return {
        "author": "owner:local",
        "source": "typed",
        "objective": "Keep the agent inside the approved task",
        "outcomes": ["Unauthorized effects are denied"],
        "constraints": ["Frozen tests cannot change"],
        "non_goals": ["No generic remote shell"],
        "created_at": "2026-09-01T12:00:00.000000Z",
    }


def _obligation() -> dict:
    return {
        "acceptance_id": "AC-1",
        "test_id": "tests/test_scope.py::test_denied",
        "oracle": "A denied effect performs no external mutation",
        "perspectives": ["positive", "negative", "boundary", "regression"],
        "asset_hashes": ["sha256:" + "a" * 64],
        "judge_assets": [{"path": "tests/test_scope.py", "sha256": "sha256:" + "a" * 64}],
        "command": ["python3", "-m", "pytest", "tests/test_scope.py::test_denied", "-q"],
        "command_hash": "sha256:" + "b" * 64,
        "baseline": {"kind": "observed_red", "result_hash": "sha256:" + "c" * 64},
        "falsification": {
            "kind": "reversible_seed",
            "expected": "red",
            "target_hash": "sha256:" + "d" * 64,
            "target_path": "apatch/scope.py",
        },
        "approver": "owner:local",
        "material": True,
    }


def _freeze_request() -> dict:
    return {
        "brief_hash": "sha256:" + "1" * 64,
        "rfp_hash": "sha256:" + "2" * 64,
        "spec_hash": "sha256:" + "3" * 64,
        "plan_hash": "sha256:" + "4" * 64,
        "baseline_hash": "sha256:" + "5" * 64,
        "coverage": {"complete": True, "missing": [], "ambiguous": [], "waivers": []},
        "obligations": [_obligation()],
        "authority": {"actor_id": "owner:local", "role": "authority"},
        "source_mutation_count": 0,
        "frozen_at": "2026-09-01T12:01:00.000000Z",
    }


def _envelope(contract_hash: str | None = None) -> dict:
    return {
        "schema": "apatch.sdd.task-envelope.v1",
        "requirement": "SPEC-X#R1",
        "contract_hash": contract_hash or "sha256:" + "6" * 64,
        "baseline_hash": "sha256:" + "5" * 64,
        "allowed_reads": ["src/**", "tests/fixtures/public/**"],
        "allowed_writes": ["src/feature.py"],
        "allowed_symbols": ["feature.apply"],
        "forbidden_paths": ["docs/RFP-*.md", "docs/specs/**", "tests/**", "schemas/**"],
        "tools": ["apatch_execute_next", "apatch_verify_run"],
        "commands": [["python3", "-m", "pytest", "tests/test_scope.py::test_denied", "-q"]],
        "network": [],
        "remote": [],
        "services": [],
        "budgets": {"files": 1, "insertions": 80, "deletions": 20, "seconds": 900},
        "checks": ["AC-1"],
        "rollback_owner": "session:self",
        "containment": "mediated_only",
    }


def test_existing_runtime_remains_canonical_and_opt_in(tmp_path: Path) -> None:
    api = _api()
    assert api.profile_status(tmp_path)["enabled"] is False
    assert api.profile_status(tmp_path)["legacy_behavior"] == "unchanged"
    assert api.OWNERSHIP["session"] == "apatch.runtime.session"
    assert api.OWNERSHIP["mutation"] == "apatch.apply_session"
    assert api.OWNERSHIP["evidence"] == "apatch TrustChain ledger"


def test_sdd_documents_are_canonical_content_safe_and_versioned() -> None:
    api = _api()
    first = api.build_brief(_brief_request())
    second = api.build_brief({**_brief_request(), "objective": "A different approved outcome"})
    assert first["schema"] == "apatch.sdd.brief.v1"
    assert first["revision"] == 1
    assert first["document_hash"].startswith("sha256:")
    assert first["document_hash"] != second["document_hash"]
    serialized = json.dumps(first)
    assert "/Users/" not in serialized and "secret" not in serialized.lower()
    assert api.canonical_hash({"b": 2, "a": 1}) == api.canonical_hash({"a": 1, "b": 2})


def test_contract_freeze_rejects_incomplete_or_post_mutation_judges() -> None:
    api = _api()
    frozen = api.freeze_contract(_freeze_request())
    assert frozen["status"] == "frozen"
    assert frozen["implementation_allowed"] is True
    assert frozen["source_mutation_count_at_freeze"] == 0

    incomplete = _freeze_request()
    incomplete["coverage"] = {"complete": False, "missing": ["AC-2"], "ambiguous": []}
    with pytest.raises(api.SddContractError, match="coverage"):
        api.freeze_contract(incomplete)

    weak = _freeze_request()
    weak["obligations"][0]["perspectives"] = ["positive"]
    with pytest.raises(api.SddContractError, match="perspectives"):
        api.freeze_contract(weak)

    late = _freeze_request()
    late["source_mutation_count"] = 1
    with pytest.raises(api.SddContractError, match="before source mutation"):
        api.freeze_contract(late)

    # A contract the executor could never run must not be freezable: it would
    # otherwise pass freeze and fail at implementation time, when the freeze is
    # immutable and the authority has already signed off.
    unlocatable = _freeze_request()
    del unlocatable["obligations"][0]["judge_assets"]
    with pytest.raises(api.SddContractError, match="judge_assets"):
        api.freeze_contract(unlocatable)

    partial = _freeze_request()
    partial["obligations"][0]["asset_hashes"].append("sha256:" + "e" * 64)
    with pytest.raises(api.SddContractError, match="exactly the frozen asset_hashes"):
        api.freeze_contract(partial)

    repeated = _freeze_request()
    obligation = repeated["obligations"][0]
    obligation["asset_hashes"] = ["sha256:" + "a" * 64] * 2
    obligation["judge_assets"] = [
        {"path": "tests/test_scope.py", "sha256": "sha256:" + "a" * 64}
    ] * 2
    with pytest.raises(api.SddContractError, match="repeat a path"):
        api.freeze_contract(repeated)

    escaping = _freeze_request()
    escaping["obligations"][0]["judge_assets"][0]["path"] = "../outside/test_scope.py"
    with pytest.raises(api.SddContractError, match="workspace-relative"):
        api.freeze_contract(escaping)

    unfalsifiable = _freeze_request()
    del unfalsifiable["obligations"][0]["falsification"]["target_path"]
    with pytest.raises(api.SddContractError, match="falsification target_path"):
        api.freeze_contract(unfalsifiable)


def test_capabilities_separate_implementation_verifier_and_authority(tmp_path: Path) -> None:
    api = _api()
    frozen = api.freeze_contract(_freeze_request())
    envelope = api.validate_task_envelope(_envelope(frozen["document_hash"]))
    implementation = api.issue_capability(
        frozen,
        envelope,
        actor_id="agent:codex",
        role="implementation",
    )
    verifier = api.issue_capability(
        frozen,
        envelope,
        actor_id="verifier:harness",
        role="verifier",
    )
    assert implementation["role"] == "implementation"
    assert verifier["role"] == "verifier"
    assert implementation["capability_hash"] != verifier["capability_hash"]

    denied_judge = api.admit_effect(
        implementation,
        {"surface": "mutation", "effect": "write", "path": "tests/test_scope.py"},
    )
    denied_source = api.admit_effect(
        verifier,
        {"surface": "mutation", "effect": "write", "path": "src/feature.py"},
    )
    assert denied_judge["decision"] == denied_source["decision"] == "denied"

    from apatch.runtime.session import start_session
    from apatch.session_state import load_session_state

    started = start_session(
        str(tmp_path),
        "Implement exact R1",
        artifacts=["spec:SPEC-X#R1"],
        sdd_contract=frozen,
        task_envelope=envelope,
        actor={"actor_id": "agent:codex", "role": "implementation"},
    )
    assert started["ok"] is True
    state = load_session_state(str(tmp_path))
    assert state["sdd"]["contract_hash"] == frozen["document_hash"]
    assert state["sdd"]["envelope_hash"] == envelope["document_hash"]


def test_task_envelope_is_exact_non_widening_and_hash_bound() -> None:
    api = _api()
    envelope = api.validate_task_envelope(_envelope())
    assert envelope["document_hash"].startswith("sha256:")
    assert envelope["allowed_writes"] == ["src/feature.py"]
    assert envelope["containment"] == "mediated_only"

    widened = dict(envelope)
    widened["allowed_writes"] = ["src/**"]
    decision = api.compare_envelopes(envelope, widened)
    assert decision["non_widening"] is False
    assert decision["added"]["allowed_writes"] == ["src/**"]


def test_an_allowed_write_is_admitted_when_apply_resolved_it_absolutely(tmp_path):
    """An envelope states paths relative to the workspace; apply resolves them.

    Before this, the two were compared in different forms, so a strict profile
    denied every write it had explicitly allowed and reported it as the author
    reaching outside their scope.
    """

    from apatch.sdd_integrity import _path_matches, _workspace_relative

    root = str(tmp_path)
    resolved = str(tmp_path / "apatch_studio" / "app.py")

    assert _path_matches(_workspace_relative(resolved, root), "apatch_studio/app.py")
    assert _path_matches(_workspace_relative(resolved, root), "apatch_studio/**")


def test_a_path_outside_the_workspace_is_still_refused(tmp_path):
    """Relativising must not become a way to climb out of the workspace."""

    from apatch.sdd_integrity import _path_matches, _workspace_relative

    outside = str(tmp_path.parent / "elsewhere" / "app.py")
    stated = _workspace_relative(outside, str(tmp_path))

    # It stays outside rather than being restated as a path within the
    # workspace, so no scoped pattern can now reach it. A pattern that already
    # allowed everything still allows everything; that is a separate question
    # about what "**" should mean, and this change does not touch it.
    assert stated.startswith("/") or stated.startswith(".."), stated
    assert not _path_matches(stated, "apatch_studio/**")
    assert not _path_matches(stated, "apatch/**")
    assert not _path_matches(stated, "app.py")


def test_a_relative_path_is_left_exactly_as_it_was(tmp_path):
    from apatch.sdd_integrity import _workspace_relative

    assert _workspace_relative("apatch_studio/app.py", str(tmp_path)) == "apatch_studio/app.py"
    assert _workspace_relative("", str(tmp_path)) == ""


def test_supported_effect_surfaces_use_one_fail_closed_admission() -> None:
    api = _api()
    frozen = api.freeze_contract(_freeze_request())
    envelope = api.validate_task_envelope(_envelope(frozen["document_hash"]))
    capability = api.issue_capability(
        frozen, envelope, actor_id="agent:codex", role="implementation"
    )
    surfaces = ("mutation", "mcp_tool", "local_runner", "sandbox", "network", "remote", "service")
    decisions = {
        api.admit_effect(
            capability,
            {"surface": surface, "effect": "unsupported", "path": "outside/scope"},
        )["decision"]
        for surface in surfaces
    }
    assert decisions == {"denied"}

    from apatch.apply_session import run_apply_session
    from apatch.remote.orchestrator import remote_task_run
    from apatch.remote.services import execute_service_action
    from apatch.runtime.runtime import MutationRuntime
    from apatch.sandbox import acquire_lease

    for entrypoint in (
        MutationRuntime._ensure_mutation,
        run_apply_session,
        acquire_lease,
        remote_task_run,
        execute_service_action,
    ):
        assert "admit_session_effect" in inspect.getsource(entrypoint)


def test_plan_and_scope_denial_block_proof_without_foreign_mutation() -> None:
    api = _api()
    frozen = api.freeze_contract(_freeze_request())
    envelope = api.validate_task_envelope(_envelope(frozen["document_hash"]))
    capability = api.issue_capability(
        frozen, envelope, actor_id="agent:codex", role="implementation"
    )
    mismatch = api.admit_effect(
        capability,
        {
            "surface": "mutation",
            "effect": "write",
            "path": "src/feature.py",
            "plan_match": False,
            "governed_session_id": "apatch_sess_exact",
        },
    )
    assert mismatch["decision"] == "denied"
    assert mismatch["blocks_attestation"] is True
    assert mismatch["mutation_performed"] is False
    assert mismatch["rollback_scope"] == "session:self"


def test_verifier_quality_rejects_false_green_results() -> None:
    api = _api()
    obligation = _obligation()
    good = {
        "capability_role": "verifier",
        "mutated": False,
        "collected": 4,
        "executed": 4,
        "passed": 4,
        "failed": 0,
        "skipped": 0,
        "perspectives": ["positive", "negative", "boundary", "regression"],
        "asset_drift": False,
        "tautological": False,
        "falsification": {"observed_red": True, "restored_green": True},
    }
    assert api.evaluate_verification(obligation, good)["accepted"] is True

    cases = [
        {**good, "collected": 0, "executed": 0},
        {**good, "executed": 0, "skipped": 4},
        {**good, "asset_drift": True},
        {**good, "tautological": True},
        {**good, "perspectives": ["positive"]},
        {**good, "falsification": {"observed_red": False, "restored_green": True}},
        {**good, "mutated": True},
    ]
    assert all(api.evaluate_verification(obligation, case)["accepted"] is False for case in cases)


def test_attestation_evidence_binds_the_complete_causal_contract() -> None:
    api = _api()
    evidence = api.build_attestation_evidence(
        contract_hash="sha256:" + "1" * 64,
        envelope_hash="sha256:" + "2" * 64,
        actor={"actor_id": "agent:codex", "role": "implementation"},
        mutation_ids=["op:1"],
        adherence={"status": "compliant"},
        verification={
            "collected": 4,
            "executed": 4,
            "passed": 4,
            "failed": 0,
            "skipped": 0,
            "result_hash": "sha256:" + "3" * 64,
        },
        falsification={"observed_red": True, "restored_green": True},
        environment_hash="sha256:" + "4" * 64,
        checkpoint_refs=["checkpoint:1"],
        ledger_refs=["ledger:1"],
    )
    assert evidence["schema"] == "apatch.sdd.attestation-evidence.v1"
    assert evidence["document_hash"].startswith("sha256:")
    assert evidence["verification"]["collected"] == 4
    assert evidence["checkpoint_refs"] == ["checkpoint:1"]
    assert evidence["ledger_refs"] == ["ledger:1"]


def test_amendment_preserves_history_and_invalidates_only_affected_work() -> None:
    api = _api()
    frozen = api.freeze_contract(_freeze_request())
    amended = api.amend_contract(
        frozen,
        reason="Boundary oracle omitted one failure mode",
        authority={"actor_id": "owner:local", "role": "authority"},
        changed_acceptance_ids=["AC-1"],
        affected_requirements=["R4"],
        all_requirements=["R1", "R2", "R3", "R4"],
    )
    assert amended["schema"] == "apatch.sdd.contract-amendment.v1"
    assert amended["supersedes_hash"] == frozen["document_hash"]
    assert amended["invalidated_requirement_ids"] == ["R4"]
    assert amended["preserved_requirement_ids"] == ["R1", "R2", "R3"]
    assert amended["implementation_actor_may_approve"] is False


def test_edition_floor_and_containment_claims_are_honest(tmp_path: Path) -> None:
    api = _api()
    assert api.integrity_floor("oss") == api.integrity_floor("pro")
    assert {
        "strict_envelope",
        "locked_judge",
        "meaningful_verification",
        "falsification",
    } <= api.integrity_floor("oss")
    assert api.containment_claim("mediated_only", direct_shell_available=True) == {
        "level": "mediated_only",
        "contained": False,
        "unsupported_channels": ["direct_shell"],
    }
    assert api.profile_status(tmp_path)["legacy_behavior"] == "unchanged"
