"""Security regressions for signed, file-bound reverification; temp ledgers only."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from apatch.spec import parse_spec_file
from apatch.spec_coverage import spec_status_with_coverage
from apatch.spec_reverification import capture_reverification, ReverificationError
from apatch.runtime.runtime import MutationRuntime


@pytest.fixture
def case(tmp_path, monkeypatch):
    folder = tmp_path / "docs/specs"
    folder.mkdir(parents=True)
    specfile = folder / "SPEC-REVERIFY-1.md"
    specfile.write_text("# SPEC-REVERIFY-1\n\n## R1 Existing control\n\n(verify: true)\n")
    source = tmp_path / "source.py"
    source.write_text("original = True\n")
    parsed = parse_spec_file(str(specfile))
    anchor = {"kind": "spec", "id": "SPEC-REVERIFY-1#R1",
              "content_hash": parsed.requirements[0].content_hash}
    entries = [
        {"id": "m1", "tool_id": "apatch", "timestamp": 1,
         "payload": {"action": "apply", "applied_patches": 1,
                     "governed_session_id": "original", "artifacts": [anchor],
                     "files": {"source.py": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}}},
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": 2,
         "payload": {"session_id": "original", "artifacts": [anchor]}},
    ]
    monkeypatch.setattr("apatch.spec_coverage._ledger_entries", lambda root: (entries, True))
    raw = {"intent": "verify current files", "session_id": "recheck", "artifacts": [anchor]}
    monkeypatch.setattr("apatch.session_state.load_session_state", lambda root: raw)
    class Ledger:
        def __init__(self, *args, **kwargs):
            pass
        def has_trustchain(self):
            return True
        def commit_action(self, action, payload):
            entries.append({"id": f"a{len(entries) + 1}", "tool_id": action,
                            "timestamp": len(entries) + 1, "payload": deepcopy(payload)})
            return True
    monkeypatch.setattr("apatch.trustchain_helper.TrustChainHelper", Ledger)
    rt = MutationRuntime(str(tmp_path))
    monkeypatch.setattr(rt, "_capture_binding", lambda operation: None)
    monkeypatch.setattr(rt, "_finish", lambda tool, result: result)
    return {"root": str(tmp_path), "source": source, "specfile": specfile,
            "entries": entries, "anchor": anchor, "raw": raw, "rt": rt}


def row(c):
    return spec_status_with_coverage(c["root"], spec="SPEC-REVERIFY-1")["requirements"][0]


def capture(c):
    return capture_reverification(c["root"], "SPEC-REVERIFY-1", [row(c)])


def refresh(c):
    return c["rt"].noop_attest([], reverification=capture(c).verified({"R1": True}))


def test_reverify_current_bytes_and_detect_future_drift(case):
    case["source"].write_text("new_verified = True\n")
    assert row(case)["state"] == "stale"
    assert refresh(case)["committed"]
    proof = row(case)
    assert proof["state"] == "attested"
    assert proof["evidence_status"] == "complete"
    assert proof["reference_complete"]
    assert proof["op_ids"] == ["m1"]
    assert proof["session_id"] == proof["reference_session_id"] == "recheck"
    assert proof["attestation_op_id"] == proof["reference_attestation_op_id"]
    case["source"].write_text("later_drift = True\n")
    assert row(case)["state"] == "stale"


def test_repeat_reverification_preserves_real_mutation_lineage(case):
    assert refresh(case)["ok"]
    first = row(case)["attestation_op_id"]
    assert refresh(case)["ok"]
    proof = case["entries"][-1]["payload"]["file_reverification"]["spec:SPEC-REVERIFY-1#R1"]
    assert proof["reference_attestation_op_id"] == first
    assert row(case)["op_ids"] == ["m1"]
    assert row(case)["state"] == "attested"


@pytest.mark.parametrize("mode", ["changed", "restored", "deleted", "spec_text", "verify_command", "reference", "new_mutation"])
def test_reject_drift_between_capture_and_attestation(case, mode):
    snapshot = capture(case).verified({"R1": True})
    if mode == "changed":
        case["source"].write_text("changed_during_verify = True\n")
    elif mode == "restored":
        import os
        original, stat = case["source"].read_bytes(), case["source"].stat()
        case["source"].write_text("transient_different_bytes = True\n")
        case["source"].write_bytes(original)
        os.utime(case["source"], ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif mode == "deleted":
        case["source"].unlink()
    elif mode == "spec_text":
        case["specfile"].write_text(case["specfile"].read_text().replace("Existing control", "Different intent"))
    elif mode == "verify_command":
        case["specfile"].write_text(case["specfile"].read_text().replace("verify: true", "verify: false"))
    elif mode == "reference":
        assert refresh(case)["ok"]
    else:
        case["entries"][0]["payload"]["files"]["source.py"]["sha256"] = "e" * 64
    before = len(case["entries"])
    result = case["rt"].noop_attest([], reverification=snapshot)
    assert result["ok"] is False
    assert result["error_type"] == "REVERIFICATION_INVALID"
    assert len(case["entries"]) == before


@pytest.mark.parametrize("results", [None, {}, {"R1": False}, {"R1": 1}, {"R2": True}])
def test_only_completed_green_result_can_be_signed(case, results):
    snapshot = capture(case)
    if results is not None:
        snapshot = snapshot.verified(results)
    assert not case["rt"].noop_attest([], reverification=snapshot)["ok"]
    assert len(case["entries"]) == 2


def test_untyped_user_evidence_cannot_create_file_freshness(case):
    result = case["rt"].noop_attest([], reverification={"files": {"source.py": "f" * 64}})
    assert not result["ok"]
    assert len(case["entries"]) == 2
    assert case["rt"].noop_attest([], evidence={"file_reverification": {"verified": True}})["ok"]
    assert row(case)["evidence_status"] == "incomplete"


@pytest.mark.parametrize("mode", ["all_missing", "partly_missing", "invalid_hash", "in_progress"])
def test_incomplete_original_scope_is_not_reconstructed_from_disk(case, mode):
    files = case["entries"][0]["payload"]["files"]
    if mode == "all_missing":
        files["source.py"] = {}
    elif mode == "partly_missing":
        files["unknown.py"] = {}
    elif mode == "invalid_hash":
        files["source.py"]["sha256"] = "not-a-digest"
    else:
        case["entries"].pop()
    with pytest.raises(ReverificationError):
        capture(case)


@pytest.mark.parametrize("mode", ["traversal", "absolute", "symlink"])
def test_capture_rejects_unsafe_path_scope(case, tmp_path, mode):
    path = "../outside.py" if mode == "traversal" else "/outside.py"
    if mode == "symlink":
        link = tmp_path / "link.py"
        link.symlink_to(case["source"])
        path = "link.py"
    case["entries"][0]["payload"]["files"] = {path: {"sha256": "f" * 64}}
    with pytest.raises(ReverificationError):
        capture(case)


def test_partition_does_not_borrow_sibling_files(case):
    payload = case["entries"][0]["payload"]
    payload["files"]["foreign.py"] = {"sha256": "f" * 64}
    payload["artifact_files"] = {"spec:SPEC-REVERIFY-1#R1": ["source.py"],
                                "spec:SPEC-REVERIFY-1#R2": ["foreign.py"]}
    assert refresh(case)["ok"]
    assert row(case)["files"] == ["source.py"]


@pytest.mark.parametrize("tamper", ["reference", "op_ids", "extra_file", "remove_file", "artifact", "content_hash", "verified", "command"])
def test_malformed_signed_reverification_stays_incomplete(case, tamper):
    assert refresh(case)["ok"]
    proof = case["entries"][-1]["payload"]["file_reverification"]["spec:SPEC-REVERIFY-1#R1"]
    if tamper == "reference":
        proof["reference_attestation_op_id"] = "foreign"
    elif tamper == "op_ids":
        proof["mutation_op_ids"] = ["foreign"]
    elif tamper == "extra_file":
        proof["files"]["foreign.py"] = "f" * 64
    elif tamper == "remove_file":
        proof["files"] = {}
    elif tamper == "artifact":
        proof["artifact"] = "spec:SPEC-REVERIFY-1#R2"
    elif tamper == "content_hash":
        proof["content_hash"] = "sha256:foreign"
    elif tamper == "verified":
        proof["verified"] = False
    else:
        proof["verify_sha256"] = "f" * 64
    assert row(case)["state"] == "stale"
    assert row(case)["evidence_status"] == "incomplete"


def test_legacy_noop_then_real_reverification_restores_complete_evidence(case):
    assert case["rt"].noop_attest([])["ok"]
    assert row(case)["stale_reason"] == "evidence_incomplete"
    assert refresh(case)["ok"]
    assert row(case)["state"] == "attested"


def test_fileless_requirement_remains_legacy_compatible(case):
    case["entries"].clear()
    assert capture(case) is None
    assert case["rt"].noop_attest(["R2"])["ok"]
    assert row(case)["state"] == "attested"
    assert not row(case).get("file_hashes")


def test_wrong_session_binding_never_signs(case, monkeypatch):
    from apatch.runtime.errors import SessionBindingError
    def reject(operation):
        raise SessionBindingError("foreign session", error_type="SESSION_MISMATCH")
    monkeypatch.setattr(case["rt"], "_capture_binding", reject)
    assert case["rt"].noop_attest([])["error_type"] == "SESSION_MISMATCH"
    assert len(case["entries"]) == 2


@pytest.mark.parametrize("mode", ["green", "async_green", "red", "no_verify", "precomputed", "async", "baseline", "mutating"])
def test_rebind_integration_does_not_fake_freshness(case, monkeypatch, mode):
    from apatch.spec_rebind import rebind_stale_requirements
    case["source"].write_text("verified_revision = True\n")
    rt = case["rt"]
    class Adapter:
        session_id = "recheck"
        def __init__(self, root):
            pass
        def open_session(self, intent, artifacts=None):
            return {"ok": True}
        def verify_run(self, verify=None, **kwargs):
            if mode == "async_green":
                from apatch.verify_jobs import start_verify_job
                return start_verify_job(case["root"], verify, session_id=self.session_id)
            if mode == "mutating":
                case["source"].write_text("mutation_during_verify = True\n")
            result = {"ok": mode != "red", "verify_command": verify}
            if mode == "async":
                result["verify_job_id"] = "not-completed"
            if mode == "baseline":
                result["baseline"] = {"mode": "capture", "verify_exit_ok": False}
            return result
        def noop_attest(self, *args, **kwargs):
            return rt.noop_attest(*args, **kwargs)
        def close_session(self):
            return {"ok": True}
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", Adapter)
    out = rebind_stale_requirements(
        case["root"], spec="SPEC-REVERIFY-1", run_verify=mode != "no_verify",
        verify_results={"R1": True} if mode == "precomputed" else None,
    )
    if mode in {"green", "async_green"}:
        assert out["rebound"] == ["R1"]
        assert row(case)["state"] == "attested"
    else:
        assert out["rebound"] == []
        assert row(case)["state"] == "stale"


@pytest.mark.parametrize("mode", ["green", "red", "changed", "override", "incomplete"])
def test_spec_run_file_bound_verify_only(case, monkeypatch, mode):
    from apatch.spec_run import _run_one_requirement
    from apatch.tool_paths import run_shell_verify

    case["source"].write_text("revision_to_verify = True\n")
    if mode == "incomplete":
        case["entries"][0]["payload"]["files"]["missing.py"] = {}
    rt = case["rt"]
    closed, commands = [], []
    monkeypatch.setattr("apatch.spec_run.execute_next_workspace", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("apatch.spec_run.bind_runtime_to_active_session", lambda *a: None)
    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", lambda root: rt)
    monkeypatch.setattr(rt, "close_session", lambda: closed.append(True) or {"ok": True})
    def verify(command, root):
        commands.append(command)
        if mode == "changed":
            case["source"].write_text("changed_by_verifier = True\n")
        return run_shell_verify("false" if mode == "red" else command, root)
    monkeypatch.setattr("apatch.tool_paths.run_shell_verify", verify)
    out = _run_one_requirement(
        case["root"], "SPEC-REVERIFY-1", row(case), [],
        logs_path="unused.jsonl", verify_deferred=True,
        verify_override="printf override" if mode == "override" else None,
    )
    assert closed == [True]
    assert commands == ([] if mode in {"override", "incomplete"} else ["true"])
    if mode == "green":
        assert out["ok"]
        assert row(case)["evidence_status"] == "complete"
        assert row(case)["state"] == "attested"
        assert row(case)["op_ids"] == ["m1"]
    else:
        assert not out["ok"]
        assert len(case["entries"]) == 2
        assert row(case)["state"] == "stale"


@pytest.mark.parametrize("timing", ["before_capture", "during_verify", "after_sign"])
def test_new_mutation_in_another_session_invalidates_evidence(case, timing):
    def append_mutation():
        mutation = deepcopy(case["entries"][0])
        mutation["id"] = "m-during-verification"
        mutation["timestamp"] = len(case["entries"]) + 1
        mutation["payload"]["governed_session_id"] = "another-session"
        mutation["payload"]["files"] = {"added.py": {"sha256": "e" * 64}}
        case["entries"].append(mutation)
    if timing == "before_capture":
        append_mutation()
        with pytest.raises(ReverificationError):
            capture(case)
    elif timing == "during_verify":
        snapshot = capture(case).verified({"R1": True})
        append_mutation()
        assert not case["rt"].noop_attest([], reverification=snapshot)["ok"]
        assert len(case["entries"]) == 3
    else:
        assert refresh(case)["ok"]
        append_mutation()
    assert row(case)["state"] != "attested"
    assert row(case)["evidence_status"] == "incomplete"


@pytest.mark.parametrize("mode", ["green", "red", "session", "command", "baseline", "allowed", "exit", "timeout"])
def test_async_completion_requires_exact_terminal_job(case, monkeypatch, mode):
    from apatch.spec_reverification import completed_verification
    job_id = "vjob_123_0123abcd"
    job = {"session_id": "recheck", "verify_command": "true", "baseline": "off",
           "allowed_failures": [], "returncode": 0}
    if mode == "session":
        job["session_id"] = "foreign"
    elif mode == "command":
        job["verify_command"] = "different"
    elif mode == "baseline":
        job["baseline"] = "capture"
    elif mode == "allowed":
        job["allowed_failures"] = ["failed_test"]
    elif mode in {"red", "exit"}:
        job["returncode"] = 1
    polls = []
    def poll(root, jid):
        assert jid == job_id
        polls.append(jid)
        if len(polls) == 1:
            return {"ok": True, "verify_job_state": "running"}
        return {"ok": mode != "red", "verify_job_state": "failed" if mode == "red" else "passed",
                "verify_job_id": jid, "verify_command": "true"}
    monkeypatch.setattr("apatch.verify_jobs._load_job", lambda path: job)
    monkeypatch.setattr("apatch.verify_jobs.poll_verify_job", poll)
    initial = {"ok": True, "verify_job_id": job_id}
    if mode in {"green", "red"}:
        out = completed_verification(case["root"], initial, verify="true", session_id="recheck")
        assert out["ok"] is (mode == "green")
        assert len(polls) == 2
        assert "verify_job_id" not in out
    else:
        with pytest.raises(ReverificationError) as error:
            completed_verification(case["root"], initial, verify="true", session_id="recheck",
                                   timeout_sec=0 if mode == "timeout" else 1)
        if mode == "timeout":
            assert error.value.verify_job_id == job_id
