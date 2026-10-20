from __future__ import annotations

import hashlib
import inspect
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

RFP = "docs/RFP-045-sdd-verifier-closure.md"
SPEC = "docs/specs/SPEC-SDD-VERIFIER-CLOSURE-1.md"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    root: Path, *, marker: bool = False, bad_command_hash: bool = False,
    judge_body: str | None = None, material: bool = True, clean_environment: bool = False,
) -> tuple[dict, dict, Path, Path]:
    from apatch import sdd_integrity as sdd

    source = root / "src" / "feature.py"
    judge = root / "checks" / "check_feature.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    judge.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('VALUE = "green"\n', encoding="utf-8")
    marker_line = '    Path("executed.marker").write_text("yes", encoding="utf-8")\n' if marker else ""
    body = judge_body if judge_body is not None else (
        "def test_feature():\n"
        + marker_line
        + "    scope = {}\n"
        + '    exec(Path("src/feature.py").read_text(encoding="utf-8"), scope)\n'
        + '    assert scope["VALUE"] == "green"\n'
    )
    judge.write_text("from pathlib import Path\n" + body, encoding="utf-8")
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "checks/check_feature.py"]
    if clean_environment:
        command = ["/usr/bin/env", "-i", "PYTEST_ADDOPTS=-q", *command]
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
        "material": material,
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


@pytest.mark.parametrize(
    "judge_body, expected",
    [
        ("import pytest\n@pytest.mark.skip(reason='required case unavailable')\n"
         "def test_skipped(): pass\n", (1, 0, 0, 0, 1)),
        ("import pytest\ndef test_pass(): assert True\n"
         "@pytest.mark.skip(reason='required boundary unavailable')\n"
         "def test_skipped(): pass\n", (2, 1, 1, 0, 1)),
        ("def test_pass(): assert True\ndef test_fail(): assert False\n", (2, 2, 1, 1, 0)),
        ("# no test cases\n", (0, 0, 0, 0, 0)),
    ],
)
def test_real_pytest_results_cannot_be_replaced_by_command_success(tmp_path, judge_body, expected):
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path, judge_body=judge_body, material=False)
    started = _start(tmp_path, contract, envelope)
    answer = sdd.run_frozen_session_verification(
        str(tmp_path), expected_session_id=started["session"]["session_id"])

    assert answer["ok"] is False
    result = answer["verification"]["result"]
    assert tuple(result[key] for key in ("collected", "executed", "passed", "failed", "skipped")) == expected
    if expected[-1]:
        # pytest exits successfully even when all required cases are skipped.
        assert result["commands"][0]["returncode"] == 0
        assert "skipped_required_tests" in answer["verification"]["decision"]["reasons"]
    with pytest.raises(sdd.SddContractError, match="meaningful verifier"):
        from apatch.session_state import load_session_state
        sdd.build_session_attestation_evidence(str(tmp_path), load_session_state(str(tmp_path)), [])


@pytest.mark.parametrize("selection", [None, "first"])
def test_real_pytest_case_counts_and_identities_are_recorded(tmp_path, monkeypatch, selection):
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path, material=False, judge_body=
        "def test_first(): assert True\ndef test_second(): assert True\n")
    request = {key: value for key, value in contract.items() if key != "document_hash"}
    expected_ids = {sdd.canonical_hash({"classname": "checks.check_feature", "name": name})
                    for name in ("test_first", "test_second")}
    request["obligations"][0]["expected_case_ids"] = sorted(expected_ids)
    contract = sdd.freeze_contract(request)
    envelope = sdd.validate_task_envelope({
        **{key: value for key, value in envelope.items() if key != "document_hash"},
        "contract_hash": contract["document_hash"],
    })
    _start(tmp_path, contract, envelope)
    if selection:
        monkeypatch.setenv("PYTEST_ADDOPTS", "-k " + selection)
    answer = sdd.run_frozen_session_verification(str(tmp_path))

    assert answer["ok"] is (selection is None), answer
    result = answer["verification"]["result"]
    observed_count = 1 if selection else 2
    assert (result["collected"], result["executed"], result["passed"], result["failed"], result["skipped"]) == (observed_count, observed_count, observed_count, 0, 0)
    cases = result["commands"][0]["test_report"]["cases"]
    assert len({case["case_id"] for case in cases}) == observed_count
    assert all(case["outcome"] == "passed" for case in cases)
    if selection:
        assert "test_case_mismatch" in answer["verification"]["decision"]["reasons"]
    else:
        assert {case["case_id"] for case in cases} == expected_ids


def test_successful_command_without_a_test_report_is_not_a_test_pass(tmp_path):
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path)
    request = {key: value for key, value in contract.items() if key != "document_hash"}
    command = [sys.executable, "-c", "print('100 passed')"]
    request["obligations"][0].update(command=command, command_hash=sdd.canonical_hash(command), material=False)
    contract = sdd.freeze_contract(request)
    envelope = sdd.validate_task_envelope({
        **{key: value for key, value in envelope.items() if key != "document_hash"},
        "contract_hash": contract["document_hash"], "commands": [command],
    })
    _start(tmp_path, contract, envelope)
    answer = sdd.run_frozen_session_verification(str(tmp_path))

    assert answer["ok"] is False
    result = answer["verification"]["result"]
    assert result["commands"][0]["ok"] is True
    assert (result["collected"], result["executed"], result["passed"]) == (0, 0, 0)
    assert "test_report_missing" in answer["verification"]["decision"]["reasons"]


@pytest.mark.parametrize("bad_report", ["missing", "stale", "invalid"])
def test_missing_stale_or_invalid_report_cannot_create_a_pass(tmp_path, monkeypatch, bad_report):
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path, material=False)
    _start(tmp_path, contract, envelope)
    read = sdd._read_pytest_report

    def changed_report(path, run_id):
        if bad_report == "missing":
            path.unlink()
        elif bad_report == "stale":
            path.write_text(path.read_text().replace(run_id, "previous_run"))
        else:
            path.write_text("not XML")
        return read(path, run_id)

    monkeypatch.setattr(sdd, "_read_pytest_report", changed_report)
    answer = sdd.run_frozen_session_verification(str(tmp_path))

    assert answer["ok"] is False
    assert answer["verification"]["result"]["commands"][0]["returncode"] == 0
    assert answer["error_code"] == ("test_report_missing" if bad_report == "missing" else "test_report_invalid")


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


@pytest.mark.parametrize("change", ["dirty_source", "ignored_input", "judge", "delete", "add", "mode"])
def test_green_cannot_attest_changed_file_contents(tmp_path, monkeypatch, change):
    from apatch import sdd_integrity as sdd
    from apatch.session_state import load_session_state

    contract, envelope, source, judge = _fixture(tmp_path, material=False)
    ignored = source.parent / "ignored-input.txt"
    ignored.write_text("original dependency\n")
    (tmp_path / ".gitignore").write_text("src/ignored-input.txt\n__pycache__/\n.pytest_cache/\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "add", "src/feature.py"], cwd=tmp_path, check=True, capture_output=True)
    source.write_text(source.read_text() + "# dirty before verification\n")
    started = _start(tmp_path, contract, envelope)
    verified = sdd.run_frozen_session_verification(str(tmp_path))
    assert verified["ok"] is True, verified
    state = load_session_state(str(tmp_path))
    # Constructing evidence here does not sign or publish anything.
    sdd.build_session_attestation_evidence(str(tmp_path), state, [])

    def git_status():
        return subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"],
                              cwd=tmp_path, check=True, capture_output=True).stdout

    before_status = git_status()
    if change == "dirty_source":
        source.write_text('VALUE = "broken after GREEN"\n')
    elif change == "ignored_input":
        ignored.write_text("changed dependency\n")
    elif change == "judge":
        judge.write_text(judge.read_text() + "# changed after GREEN\n")
    elif change == "delete":
        source.unlink()
    elif change == "add":
        (source.parent / "new_dependency.py").write_text("NEW_INPUT = True\n")
    else:
        source.chmod(source.stat().st_mode ^ stat.S_IXUSR)
    if change in {"dirty_source", "ignored_input", "judge"}:
        assert git_status() == before_status
    with pytest.raises(sdd.SddContractError, match="content.*changed"):
        sdd.build_session_attestation_evidence(str(tmp_path), state, [])
    if change == "dirty_source":
        from unittest.mock import Mock
        from apatch.runtime.runtime import MutationRuntime

        # Exercise the real refusal route without invoking a signing provider.
        notary = Mock()
        notary.has_trustchain.return_value = False
        monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", lambda *a, **kw: notary)
        monkeypatch.setattr("apatch.runtime.attestation.build_attestation_view", lambda _root: {})
        answer = MutationRuntime(str(tmp_path), session_id=started["session"]["session_id"],
                                 session_token=started["session_token"]).attest()
        assert answer["ok"] is False
        assert "content changed" in answer["error"]
        notary.commit_action.assert_not_called()


def test_verifier_cannot_mutate_source_and_keep_green_without_git(tmp_path):
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path, material=False, judge_body=
        "def test_mutating_judge():\n"
        "    Path('src/feature.py').write_text('VALUE = 999\\n')\n"
        "    assert True\n")
    _start(tmp_path, contract, envelope)
    result = sdd.run_frozen_session_verification(str(tmp_path))
    assert result["verification"]["result"]["passed"] == 1
    assert result["ok"] is False
    assert result["error_code"] == "verifier_mutated_assets"


def test_unavailable_post_check_snapshot_is_not_green(tmp_path, monkeypatch):
    from apatch import sdd_integrity as sdd

    contract, envelope, _source, _judge = _fixture(tmp_path, material=False)
    _start(tmp_path, contract, envelope)
    snapshot = sdd._verification_content_snapshot
    reads = 0

    def unavailable_after_execution(*args):
        nonlocal reads
        reads += 1
        if reads == 2:
            raise OSError("input unavailable after check")
        return snapshot(*args)

    monkeypatch.setattr(sdd, "_verification_content_snapshot", unavailable_after_execution)
    result = sdd.run_frozen_session_verification(str(tmp_path))
    assert result["ok"] is False
    assert result["error_code"] == "verification_content_unavailable"
    assert result["verification"]["result"]["passed"] == 1
    assert result["verification"]["result"]["mutated"] is False


def test_green_requires_its_content_snapshot_and_can_be_refreshed(tmp_path, monkeypatch):
    from apatch import sdd_integrity as sdd
    from apatch.session_state import load_session_state

    contract, envelope, source, _judge = _fixture(tmp_path, material=False)
    _start(tmp_path, contract, envelope)
    first = sdd.run_frozen_session_verification(str(tmp_path))
    assert first["ok"] is True
    original = load_session_state(str(tmp_path))
    missing = {**original, "sdd": dict(original["sdd"])}
    missing["sdd"].pop("verification_content_snapshot", None)
    with pytest.raises(sdd.SddContractError, match="content snapshot.*required"):
        sdd.build_session_attestation_evidence(str(tmp_path), missing, [])

    source.write_text(source.read_text() + "# new implementation with the same required behavior\n")
    with pytest.raises(sdd.SddContractError, match="content.*changed"):
        sdd.build_session_attestation_evidence(str(tmp_path), original, [])
    refreshed = sdd.run_frozen_session_verification(str(tmp_path))
    assert refreshed["ok"] is True
    assert (refreshed["verification"]["content_snapshot_hash"]
            != first["verification"]["content_snapshot_hash"])
    current = load_session_state(str(tmp_path))
    evidence = sdd.build_session_attestation_evidence(str(tmp_path), current, [])
    assert evidence["verification"] == refreshed["verification"]
    assert "verification_content_snapshot" not in evidence

    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    with pytest.raises(sdd.SddContractError, match="content.*changed"):
        sdd.build_session_attestation_evidence(str(tmp_path), current, [])


def test_finalize_routes_sdd_and_legacy_sessions_to_their_own_verifiers() -> None:
    from apatch import spec_executor

    source = inspect.getsource(spec_executor.execute_next_workspace)
    assert "rt.sdd_verify()" in source
    assert "rt.verify_run(verify=verify_cmd)" in source
    assert "sdd_verify" in source


def test_amended_contract_cannot_inherit_green_even_when_source_is_unchanged(tmp_path):
    """CDD-G04: actual pytest GREEN belongs to its frozen contract/envelope."""
    from copy import deepcopy
    from apatch import sdd_integrity as sdd
    from apatch.session_state import load_session_state

    contract, envelope, _source, _judge = _fixture(tmp_path, material=False)
    _start(tmp_path, contract, envelope)
    verified = sdd.run_frozen_session_verification(str(tmp_path))
    assert verified["ok"] is True
    original = load_session_state(str(tmp_path))
    old_green = deepcopy(original["sdd"]["verification"])
    request = {key: value for key, value in contract.items() if key != "document_hash"}
    request.update(source_mutation_count=0, supersedes_hash=contract["document_hash"],
                   plan_hash="sha256:" + "9" * 64)
    amended = sdd.freeze_contract(request)
    new_envelope = {key: value for key, value in envelope.items() if key != "document_hash"}
    new_envelope["contract_hash"] = amended["document_hash"]
    new_envelope = sdd.validate_task_envelope(new_envelope)
    binding = sdd.build_session_binding(amended, new_envelope,
                                        {"actor_id": "agent:codex", "role": "implementation"})
    transplanted = {**original, "sdd": {**original["sdd"], **binding}}
    with pytest.raises(sdd.SddContractError, match="verification content changed since GREEN"):
        sdd.build_session_attestation_evidence(str(tmp_path), transplanted, [])
    # Use the native state writer, then obtain a new actual pytest result.
    sdd._save_sdd_binding(str(tmp_path), original, binding)
    new_run = sdd.run_frozen_session_verification(str(tmp_path))
    assert new_run["ok"] is True
    current = load_session_state(str(tmp_path))
    evidence = sdd.build_session_attestation_evidence(str(tmp_path), current, [])
    assert evidence["contract_hash"] == amended["document_hash"]
    assert evidence["envelope_hash"] == new_envelope["document_hash"]
    assert current["sdd"]["verification_run_id"] != original["sdd"]["verification_run_id"]
    assert original["sdd"]["verification"] == old_green


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


@pytest.mark.parametrize("skipped", [False, True])
def test_studio_clean_environment_reports_actual_pytest_results(tmp_path, skipped):
    from apatch import sdd_integrity as sdd
    body = ("import pytest\n@pytest.mark.skip(reason='unavailable')\ndef test_feature(): pass\n"
            if skipped else None)
    contract, envelope, source, _judge = _fixture(
        tmp_path, judge_body=body, clean_environment=True, material=not skipped)
    _start(tmp_path, contract, envelope)
    before = source.read_bytes()
    result = sdd.run_frozen_session_verification(str(tmp_path))
    assert result["ok"] is (not skipped), result
    actual = result["verification"]["result"]
    assert actual["collected"] == 1
    assert actual["skipped"] == int(skipped)
    assert actual["executed"] == int(not skipped)
    assert source.read_bytes() == before
