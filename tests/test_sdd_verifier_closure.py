from __future__ import annotations

import hashlib
import inspect
import json
import stat
import sys
from pathlib import Path

import pytest

RFP = "docs/RFP-045-sdd-verifier-closure.md"
SPEC = "docs/specs/SPEC-SDD-VERIFIER-CLOSURE-1.md"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    root: Path, *, marker: bool = False, bad_command_hash: bool = False
) -> tuple[dict, dict, Path, Path]:
    from apatch import sdd_integrity as sdd

    source = root / "src" / "feature.py"
    judge = root / "checks" / "check_feature.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    judge.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('VALUE = "green"\n', encoding="utf-8")
    marker_line = 'Path("executed.marker").write_text("yes", encoding="utf-8")\n' if marker else ""
    judge.write_text(
        "from pathlib import Path\n"
        + marker_line
        + "scope = {}\n"
        + 'exec(Path("src/feature.py").read_text(encoding="utf-8"), scope)\n'
        + 'assert scope["VALUE"] == "green"\n',
        encoding="utf-8",
    )
    command = [sys.executable, "checks/check_feature.py"]
    obligation = {
        "acceptance_id": "AC-1",
        "test_id": "checks/check_feature.py",
        "oracle": "The implemented value remains green",
        "perspectives": ["positive", "negative", "boundary", "regression"],
        "asset_hashes": [_sha(judge)],
        "judge_assets": [{"path": "checks/check_feature.py", "sha256": _sha(judge)}],
        "command": command,
        "command_hash": (
            "sha256:" + "0" * 64 if bad_command_hash else sdd.canonical_hash(command)
        ),
        "baseline": {"kind": "observed_red", "result_hash": "sha256:" + "b" * 64},
        "falsification": {
            "kind": "reversible_builtin_mutant",
            "expected": "red",
            "target_hash": sdd.canonical_hash({"path": "src/feature.py"}),
            "target_path": "src/feature.py",
        },
        "approver": "owner:local",
        "material": True,
    }
    request = {
        "brief_hash": "sha256:" + "1" * 64,
        "rfp_hash": "sha256:" + "2" * 64,
        "spec_hash": "sha256:" + "3" * 64,
        "plan_hash": "sha256:" + "4" * 64,
        "baseline_hash": "sha256:" + "5" * 64,
        "coverage": {"complete": True, "missing": [], "ambiguous": [], "waivers": []},
        "obligations": [obligation],
        "authority": {"actor_id": "owner:local", "role": "authority"},
        "source_mutation_count": 0,
        "frozen_at": "2026-09-01T18:00:00.000000Z",
    }
    contract = sdd.freeze_contract(request)
    envelope = sdd.validate_task_envelope(
        {
            "schema": "apatch.sdd.task-envelope.v1",
            "requirement": "SPEC-CLOSURE#R1",
            "contract_hash": contract["document_hash"],
            "baseline_hash": request["baseline_hash"],
            "allowed_reads": ["src/**", "checks/**"],
            "allowed_writes": ["src/feature.py"],
            "allowed_symbols": [],
            "forbidden_paths": ["docs/**", "tests/**"],
            "tools": ["apatch_sdd_verify"],
            "commands": [command],
            "network": [],
            "remote": [],
            "services": [],
            "budgets": {"files": 1, "insertions": 10, "deletions": 10, "seconds": 30},
            "checks": ["AC-1"],
            "rollback_owner": "session:self",
            "containment": "mediated_only",
        }
    )
    return contract, envelope, source, judge


def _start(root: Path, contract: dict, envelope: dict) -> dict:
    from apatch.runtime.session import start_session

    started = start_session(
        str(root),
        "Implement the exact frozen requirement",
        artifacts=["spec:SPEC-CLOSURE#R1"],
        sdd_contract=contract,
        task_envelope=envelope,
        actor={"actor_id": "agent:codex", "role": "implementation"},
    )
    assert started["ok"] is True
    return started


def test_rfp_traceability_is_exact() -> None:
    rfp = Path(RFP).read_text(encoding="utf-8")
    spec = Path(SPEC).read_text(encoding="utf-8")
    ids = [f"SDD-CLOSE-{index}" for index in range(1, 10)]
    assert all(rfp.count(f"| {item} |") == 1 for item in ids)
    assert all(spec.count(f"| {item} |") == 1 for item in ids)
    assert spec.count("(verify:") == 10


def test_session_retains_exact_contract_but_public_projection_does_not(tmp_path: Path) -> None:
    from apatch import sdd_integrity as sdd
    from apatch.session_state import load_session_state

    contract, envelope, _source, _judge = _fixture(tmp_path)
    _start(tmp_path, contract, envelope)
    binding = load_session_state(str(tmp_path))["sdd"]
    assert binding["verification_contract"] == contract
    projected = sdd.session_attestation_projection({"sdd": binding})
    assert projected["contract_hash"] == contract["document_hash"]
    assert "verification_contract" not in projected


def test_fixed_verifier_has_no_caller_supplied_judge_or_result(tmp_path: Path) -> None:
    from apatch import sdd_integrity as sdd

    parameters = inspect.signature(sdd.run_frozen_session_verification).parameters
    assert set(parameters) == {"target_dir", "expected_session_id", "timeout"}
    assert not {"command", "result", "role", "perspectives"} & set(parameters)

    contract, envelope, _source, _judge = _fixture(tmp_path)
    started = _start(tmp_path, contract, envelope)
    with pytest.raises(sdd.SddContractError, match="exact governed session"):
        sdd.run_frozen_session_verification(
            str(tmp_path), expected_session_id=started["session"]["session_id"] + "-foreign"
        )


@pytest.mark.parametrize("drift", ["command", "judge"])
def test_command_or_judge_drift_blocks_before_execution(tmp_path: Path, drift: str) -> None:
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, judge = _fixture(
        tmp_path, marker=True, bad_command_hash=drift == "command"
    )
    started = _start(tmp_path, contract, envelope)
    if drift == "judge":
        judge.write_text(judge.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    result = sdd.run_frozen_session_verification(
        str(tmp_path), expected_session_id=started["session"]["session_id"]
    )
    assert result["ok"] is False
    assert result["error_code"] in {"command_hash_mismatch", "judge_asset_drift"}
    assert not (tmp_path / "executed.marker").exists()


def test_command_executes_as_argv_and_evidence_is_content_safe(tmp_path: Path) -> None:
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path)
    started = _start(tmp_path, contract, envelope)
    result = sdd.run_frozen_session_verification(
        str(tmp_path), expected_session_id=started["session"]["session_id"]
    )
    assert result["ok"] is True
    assert result["verification"]["decision"]["accepted"] is True
    encoded = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in encoded
    assert 'VALUE = "green"' not in encoded
    assert "stdout" not in encoded and "stderr" not in encoded
    assert result["verification"]["result"]["commands"][0]["output_hash"].startswith("sha256:")


def test_material_gate_observes_red_and_restores_exact_target(tmp_path: Path) -> None:
    from apatch import sdd_integrity as sdd

    contract, envelope, source, _judge = _fixture(tmp_path)
    source.chmod(source.stat().st_mode | stat.S_IXUSR)
    before = source.read_bytes()
    before_mode = stat.S_IMODE(source.stat().st_mode)
    started = _start(tmp_path, contract, envelope)
    result = sdd.run_frozen_session_verification(
        str(tmp_path), expected_session_id=started["session"]["session_id"]
    )
    assert result["ok"] is True
    assert result["falsification"]["observed_red"] is True
    assert result["falsification"]["restored_green"] is True
    assert source.read_bytes() == before
    assert stat.S_IMODE(source.stat().st_mode) == before_mode


def test_proof_is_persisted_atomically_on_exact_session(tmp_path: Path) -> None:
    from apatch import sdd_integrity as sdd
    from apatch.session_state import load_session_state

    contract, envelope, _source, _judge = _fixture(tmp_path)
    started = _start(tmp_path, contract, envelope)
    result = sdd.run_frozen_session_verification(
        str(tmp_path), expected_session_id=started["session"]["session_id"]
    )
    binding = load_session_state(str(tmp_path))["sdd"]
    assert result["ok"] is True
    assert binding["verification"]["decision"]["accepted"] is True
    assert binding["falsification"]["observed_red"] is True
    assert binding["verification_run_id"] == result["verification_run_id"]

    broken = dict(binding)
    broken["verification"] = {}
    with pytest.raises(sdd.SddContractError, match="meaningful verifier"):
        sdd.build_session_attestation_evidence(
            str(tmp_path),
            {**load_session_state(str(tmp_path)), "sdd": broken},
            [],
        )


def test_finalize_routes_sdd_and_legacy_sessions_to_their_own_verifiers() -> None:
    from apatch import spec_executor

    source = inspect.getsource(spec_executor.execute_next_workspace)
    assert "rt.sdd_verify()" in source
    assert "rt.verify_run(verify=verify_cmd)" in source
    assert "sdd_verify" in source


def test_verifier_and_attest_run_in_sequence_on_one_session(tmp_path: Path) -> None:
    """The route finalize actually takes must survive the verifier's own write.

    Running the frozen judge records its proof onto the session, which advances
    that session's revision. A runtime still holding the earlier revision then
    conflicts with itself, and finalize can never get past the verifier to
    attest. Drive the two through one runtime, the way finalize does.
    """

    from apatch.runtime.runtime import MutationRuntime

    contract, envelope, _source, _judge = _fixture(tmp_path)
    started = _start(tmp_path, contract, envelope)
    runtime = MutationRuntime(
        str(tmp_path),
        session_id=started["session"]["session_id"],
        session_token=started["session_token"],
    )

    verified = runtime.sdd_verify()
    assert verified.get("ok") is True, verified
    assert verified.get("error_type") != "SESSION_REVISION_MISMATCH"

    attested = runtime.attest(message="frozen judge accepted the implementation")
    assert attested.get("error_type") != "SESSION_REVISION_MISMATCH", attested
    assert attested.get("ok") is True, attested


def test_mcp_surface_is_fixed_purpose() -> None:
    pytest.importorskip("mcp")
    from apatch.mcp import server as mcp_server

    tool = mcp_server.mcp._tool_manager._tools["apatch_sdd_verify"].fn
    parameters = inspect.signature(tool).parameters
    assert {"target_dir", "governed_session_id", "session_token"} <= set(parameters)
    assert not {"verify", "command", "result", "role", "perspectives"} & set(parameters)
