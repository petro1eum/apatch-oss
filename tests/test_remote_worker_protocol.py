"""SPEC-REMOTE-SSH-WORKER-1 — SSH transport and remote worker protocol."""

import io
import json
import subprocess

from apatch.remote.target import build_remote_target, parse_remote_target
from apatch.remote.transport import FakeRemoteTransport


def test_r1_fake_transport_round_trip():
    target = build_remote_target("example-search-host", "/srv/repo", alias="x")
    transport = FakeRemoteTransport(
        {"apatch_doctor": {"ok": True, "workspace": "/srv/repo"}}
    )

    # public call takes RemoteTarget + operation + kwargs dict, returns parsed JSON
    result = transport.call(target, "apatch_doctor", {"k": "v"})
    assert result["ok"] is True
    assert result["workspace"] == "/srv/repo"

    # the call is recorded as structured data, no network required
    assert len(transport.calls) == 1
    assert transport.calls[0]["operation"] == "apatch_doctor"
    assert transport.calls[0]["arguments"] == {"k": "v"}

    # default response path stays ok-shaped for unmapped operations
    assert transport.call(target, "apatch_session_end", {})["ok"] is True


def test_r2_worker_envelope_dispatch(monkeypatch):
    from apatch.remote import worker

    # protocol version mismatch -> typed error before any dispatch
    bad = worker.dispatch(
        {"operation": "apatch_doctor", "protocol_version": 999, "arguments": {}}
    )
    assert bad["ok"] is False
    assert bad["error_type"] == "REMOTE_PROTOCOL_ERROR"

    # operation outside the allowlist is rejected
    denied = worker.dispatch(
        {"operation": "rm_rf", "protocol_version": worker.PROTOCOL_VERSION, "arguments": {}}
    )
    assert denied["ok"] is False
    assert denied["error_type"] == "REMOTE_OPERATION_UNSUPPORTED"

    # allowlist is exactly the governed remote operation set
    assert "apatch_apply_session" in worker.ALLOWED_OPERATIONS
    assert "apatch_execute_next" in worker.ALLOWED_OPERATIONS
    assert "apatch_spec_run" in worker.ALLOWED_OPERATIONS
    assert "apatch_spec_run_multi" in worker.ALLOWED_OPERATIONS
    assert "apatch_slug_ratify" in worker.ALLOWED_OPERATIONS
    assert "apatch_rollback" in worker.ALLOWED_OPERATIONS
    assert "apatch_resume_session" in worker.ALLOWED_OPERATIONS
    assert "apatch_attest" in worker.ALLOWED_OPERATIONS
    assert "apatch_commit_attested" in worker.ALLOWED_OPERATIONS
    from apatch.remote.policy import DEFAULT_REMOTE_TASK_OPERATIONS
    assert "apatch_commit_attested" in DEFAULT_REMOTE_TASK_OPERATIONS
    assert "apatch_noop_attest" in worker.ALLOWED_OPERATIONS
    assert "rm_rf" not in worker.ALLOWED_OPERATIONS
    assert worker._remote_generation_channel(
        {"fix_forward_current": True}, {}
    ) == "apatch_remote_task_run:fix_forward"
    assert worker._remote_generation_channel(
        {"requirement": "SPEC-X#R1"}, {}
    ) == "apatch_remote_task_run:requirement"

    # arguments must be a JSON object, not another JSON type
    bad_args = worker.dispatch(
        {"operation": "apatch_doctor", "arguments": ["unexpected"]}
    )
    assert bad_args["ok"] is False
    assert bad_args["error_type"] == "REMOTE_PROTOCOL_ERROR"


    assert "apatch_rebind_stale" in worker.ALLOWED_OPERATIONS

    captured = {}

    def fake_rebind(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {"ok": True, "spec": "SPEC-X", "rebound": ["R9"]}

    monkeypatch.setattr(
        "apatch.spec_rebind.rebind_stale_requirements",
        fake_rebind,
    )
    rebound = worker.dispatch(
        {
            "operation": "apatch_rebind_stale",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "plan": {
                    "rebind_stale": True,
                    "spec": "SPEC-X",
                }
            },
        }
    )

    assert rebound == {"ok": True, "spec": "SPEC-X", "rebound": ["R9"]}
    assert captured == {
        "target_dir": ".",
        "spec": "SPEC-X",
        "spec_path": None,
        "run_verify": True,
    }




def test_worker_dispatches_commit_attested_without_opening_a_session(monkeypatch):
    from apatch.remote import worker

    captured = {}

    def fake_commit(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {"ok": True, "committed": True, "files": ["a.py"]}

    monkeypatch.setattr("apatch.workflows.commit_attested_workspace", fake_commit)
    result = worker.dispatch(
        {
            "operation": "apatch_commit_attested",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "message": "Commit exact proof",
                "plan": {
                    "commit_attested": True,
                    "session_ids": ["session-a"],
                    "push": True,
                },
            },
        }
    )
    assert result == {"ok": True, "committed": True, "files": ["a.py"]}
    assert captured == {
        "target_dir": ".",
        "governed_session_id": None,
        "session_ids": ["session-a"],
        "message": "Commit exact proof",
        "push": True,
        "remote": "origin",
        "dry_run": False,
    }

def test_worker_main_keeps_protocol_json_clean(monkeypatch):
    from apatch.remote import worker

    stdin = io.StringIO(json.dumps({"operation": "apatch_doctor", "arguments": {}}))
    stdout = io.StringIO()
    stderr = io.StringIO()

    def noisy_dispatch(payload):
        print("TrustChain progress")
        return {"ok": True, "version": "0.8.0"}

    monkeypatch.setattr(worker, "dispatch", noisy_dispatch)
    monkeypatch.setattr(worker.sys, "stdin", stdin)
    monkeypatch.setattr(worker.sys, "stdout", stdout)
    monkeypatch.setattr(worker.sys, "stderr", stderr)

    worker.main()

    assert json.loads(stdout.getvalue()) == {"ok": True, "version": "0.8.0"}
    assert "TrustChain progress" not in stdout.getvalue()
    assert "TrustChain progress" in stderr.getvalue()




def test_worker_execute_next_dispatches_mutate_and_finalize(monkeypatch):
    from apatch.remote import worker

    calls = []

    def fake_execute_next(target_dir, **kwargs):
        calls.append({"target_dir": target_dir, **kwargs})
        if kwargs.get("finalize"):
            return {"ok": True, "requirement_attested": True}
        return {
            "ok": True,
            "continue": False,
            "checkpoint": "cp-1",
            "requirement_token": "SPEC-PATRUBOK-1#R6",
            "steps_completed": ["generate_batch", "apply_session"],
        }

    monkeypatch.setattr(
        "apatch.spec_executor.execute_next_enriched",
        fake_execute_next,
    )
    out = worker.dispatch(
        {
            "operation": "apatch_execute_next",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "plan": {
                    "execute_next": True,
                    "spec": "SPEC-PATRUBOK-1",
                    "requirement": "SPEC-PATRUBOK-1#R6",
                    "needles": [
                        {
                            "action": "replace",
                            "target_file": "contract.yaml",
                            "find_text": "old",
                            "replace_text": "new",
                        }
                    ],
                }
            },
        }
    )

    assert out["ok"] is True
    assert out["applied"] is True
    assert len(calls) == 2
    assert calls[0]["requirement"] == "SPEC-PATRUBOK-1#R6"
    assert calls[1]["finalize"] is True


def test_worker_execute_next_can_defer_finalize_for_service_restart(monkeypatch):
    from apatch.remote import worker

    calls = []

    def fake_execute_next(target_dir, **kwargs):
        calls.append({"target_dir": target_dir, **kwargs})
        return {
            "ok": True,
            "continue": False,
            "checkpoint": "cp-2",
            "requirement_token": "SPEC-TRUBKA-1#R2",
            "steps_completed": ["generate_batch", "apply_session"],
        }

    monkeypatch.setattr(
        "apatch.spec_executor.execute_next_enriched",
        fake_execute_next,
    )
    out = worker.dispatch(
        {
            "operation": "apatch_execute_next",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "plan": {
                    "execute_next": True,
                    "defer_finalize": True,
                    "spec": "SPEC-TRUBKA-1",
                    "requirement": "SPEC-TRUBKA-1#R2",
                    "needles": [
                        {
                            "action": "replace",
                            "target_file": "blocks/diameter.py",
                            "find_text": "old",
                            "replace_text": "new",
                        }
                    ],
                }
            },
        }
    )

    assert out["ok"] is True
    assert out["applied"] is True
    assert out["finalize_deferred"] is True
    assert len(calls) == 1
    assert calls[0]["requirement"] == "SPEC-TRUBKA-1#R2"
    assert calls[0]["defer_verify"] is True
    assert "finalize" not in calls[0]
    assert "finalize_current" in out["agent_next"]


def test_worker_slug_ratify_dispatches_governed_plan(monkeypatch):
    from apatch.remote import worker

    captured = {}

    def fake_slug_ratify(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {"ok": True, "slug": "podves", "gated": True}

    monkeypatch.setattr("apatch.slug_ratify.slug_ratify_enriched", fake_slug_ratify)
    out = worker.dispatch(
        {
            "operation": "apatch_slug_ratify",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "plan": {
                    "slug_ratify": True,
                    "slug": "podves",
                    "spec": "SPEC-PODVES-1",
                }
            },
        }
    )

    assert out["ok"] is True
    assert captured == {
        "target_dir": ".",
        "slug": "podves",
        "spec": "SPEC-PODVES-1",
        "dry_run": False,
    }


def test_worker_spec_run_dispatches_single_spec_plan(monkeypatch):
    from apatch.remote import worker

    captured = {}

    def fake_spec_run(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {"ok": True, "done": True, "spec": "SPEC-TRAP-1"}

    monkeypatch.setattr("apatch.spec_run.spec_run_enriched", fake_spec_run)
    requirements = {"R1": {"needles": [{"action": "replace"}]}}
    out = worker.dispatch(
        {
            "operation": "apatch_spec_run",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "plan": {
                    "spec": "SPEC-TRAP-1",
                    "requirements": requirements,
                }
            },
        }
    )

    assert out["ok"] is True
    assert captured["target_dir"] == "."
    assert captured["spec"] == "SPEC-TRAP-1"
    assert captured["requirements"] == requirements


def test_worker_spec_run_multi_dispatches_plan(monkeypatch):
    from apatch.remote import worker

    captured = {}

    def fake_spec_run_multi(target_dir, **kwargs):
        captured["target_dir"] = target_dir
        captured.update(kwargs)
        return {
            "ok": True,
            "completed_specs": ["SPEC-MUFTA-1", "SPEC-GILZA-1"],
        }

    monkeypatch.setattr(
        "apatch.spec_run_multi.spec_run_multi_enriched",
        fake_spec_run_multi,
    )
    requirements = {
        "SPEC-MUFTA-1": {"R14": {"needles": [{"action": "replace"}]}},
        "SPEC-GILZA-1": {"R10": {"needles": [{"action": "replace"}]}},
    }
    out = worker.dispatch(
        {
            "operation": "apatch_spec_run_multi",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "plan": {
                    "specs": ["SPEC-MUFTA-1", "SPEC-GILZA-1"],
                    "requirements": requirements,
                    "cross_verify": True,
                }
            },
        }
    )

    assert out["ok"] is True
    assert captured == {
        "target_dir": ".",
        "specs": ["SPEC-MUFTA-1", "SPEC-GILZA-1"],
        "requirements": requirements,
        "cross_verify": True,
        "re_interference": True,
        "logs_path": "patches-spec-run-multi.jsonl",
        "verify_deferred": True,
        "execution_mode": "serial",
        "maintenance_verify": None,
        "verify_jobs": 8,
        "verify_timeout": 120.0,
        "maintenance_chunk_max_files": 100,
    }


def test_worker_generate_batch_can_source_slug_close_needles(tmp_path, monkeypatch):
    from apatch.remote import worker

    monkeypatch.chdir(tmp_path)

    def fake_slug_close_workspace(target_dir, **kwargs):
        return {
            "ok": True,
            "slug": kwargs["slug"],
            "rows_scanned": 1,
            "summary": {"updates": 1},
            "suggestions": [{"suggested_status": "fixed"}],
            "proposed_needles": [
                {"action": "replace", "target_file": "x.txt", "find_text": "a", "replace_text": "b"}
            ],
        }

    def fake_generate_patch_jsonl_batch(**kwargs):
        assert kwargs["needles"][0]["target_file"] == "x.txt"
        return {"ok": True, "out_path_rel": ".apatch/remote/patches.jsonl", "count": 1}

    monkeypatch.setattr("apatch.slug_close.slug_close_workspace", fake_slug_close_workspace)
    monkeypatch.setattr("apatch.workflows.generate_patch_jsonl_batch", fake_generate_patch_jsonl_batch)

    out = worker.dispatch(
        {
            "operation": "apatch_generate_batch",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {"plan": {"slug_close": {"slug": "otvod", "timeout": 0.2}}},
        }
    )

    assert out["ok"] is True
    assert out["logs_path"] == ".apatch/remote/patches.jsonl"
    assert out["slug_close"]["slug"] == "otvod"
    assert out["slug_close"]["summary"] == {"updates": 1}


def test_worker_apply_session_completes_chunks_in_one_process(monkeypatch):
    from apatch.remote import worker

    instances = []
    calls = []
    responses = [
        {"ok": True, "continue": True, "checkpoint": "chunk-1"},
        {"ok": True, "continue": True, "checkpoint": "chunk-2"},
        {"ok": True, "continue": False, "checkpoint": "chunk-3"},
    ]

    class FakeRuntime:
        def __init__(self, target_dir, **kwargs):
            instances.append((self, target_dir, kwargs))

        def apply_session(self, logs_path, **kwargs):
            calls.append((self, logs_path, kwargs))
            return responses.pop(0)

    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", FakeRuntime)

    out = worker.dispatch(
        {
            "operation": "apatch_apply_session",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "logs_path": ".apatch/tmp/s1/patches.jsonl",
                "governed_session_id": "s1",
                "session_token": "token-1",
                "plan": {
                    "session_path": ".apatch/tmp/s1/remote-apply-session.json",
                    "chunk_max_files": 2,
                    "reset": True,
                },
            },
        }
    )

    assert out["ok"] is True
    assert out["worker_iterations"] == 3
    assert out["worker_checkpoints"] == ["chunk-1", "chunk-2", "chunk-3"]
    assert len(instances) == 1
    assert instances[0][1:] == (
        ".",
        {
            "session_id": "s1",
            "session_token": "token-1",
            "enforce_binding": True,
        },
    )
    assert len({id(call[0]) for call in calls}) == 1
    assert [call[2]["reset"] for call in calls] == [True, False, False]
    assert all(
        call[2]["session_path"]
        == ".apatch/tmp/s1/remote-apply-session.json"
        for call in calls
    )


def test_worker_session_start_resolves_requirement_artifact(tmp_path, monkeypatch):
    from apatch.remote import worker

    # This unit test exercises requirement resolution in an isolated temporary
    # workspace, not canonical installed-runtime parity of the parent MCP process.
    monkeypatch.delenv("APATCH_CANONICAL_RUNTIME", raising=False)
    monkeypatch.delenv("APATCH_MCP_BOOTSTRAPPED", raising=False)

    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-REMOTE-REQ.md").write_text(
        "# SPEC-REMOTE-REQ\n\n"
        "## R1 Remote requirement (verify: true)\n"
        "Body.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    out = worker.dispatch(
        {
            "operation": "apatch_session_start",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {"requirement": "SPEC-REMOTE-REQ#R1"},
        }
    )

    assert out["ok"] is True
    artifacts = out["session"]["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "spec"
    assert artifacts[0]["id"] == "SPEC-REMOTE-REQ#R1"
    assert artifacts[0]["content_hash"].startswith("sha256:")


def test_worker_noop_attest_dispatch(monkeypatch):
    from apatch.remote import worker

    calls = []

    class FakeRuntime:
        def __init__(self, target_dir):
            self.target_dir = target_dir

        def noop_attest(self, covered_by, message=None):
            calls.append((self.target_dir, covered_by, message))
            return {"ok": True, "noop": True, "covered_by": covered_by}

    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", FakeRuntime)

    out = worker.dispatch(
        {
            "operation": "apatch_noop_attest",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {
                "covered_by": "R1,R4",
                "message": "remote rebind",
            },
        }
    )

    assert out["ok"] is True
    assert out["noop"] is True
    assert calls == [(".", ["R1", "R4"], "remote rebind")]


def test_worker_resume_session_dispatch(monkeypatch):
    from apatch.remote import worker

    calls = []

    class FakeRuntime:
        def __init__(self, target_dir):
            self.target_dir = target_dir

        def resume_session(self):
            calls.append(self.target_dir)
            return {"ok": True, "lifecycle": "verifying"}

    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", FakeRuntime)

    out = worker.dispatch(
        {
            "operation": "apatch_resume_session",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {},
        }
    )

    assert out["ok"] is True
    assert out["lifecycle"] == "verifying"
    assert calls == ["."]


def test_worker_rollback_dispatch(monkeypatch):
    from apatch.remote import worker

    calls = []

    class FakeRuntime:
        def __init__(self, target_dir):
            self.target_dir = target_dir

        def rollback(self, session_id=None, *, preview=False):
            calls.append((self.target_dir, session_id, preview))
            return {"ok": True, "restored": ["x.py"]}

    monkeypatch.setattr("apatch.runtime.runtime.MutationRuntime", FakeRuntime)

    out = worker.dispatch(
        {
            "operation": "apatch_rollback",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {"session_id": "apatch_sess_chunk_1"},
        }
    )

    assert out["ok"] is True
    assert calls == [(".", "apatch_sess_chunk_1", False)]


def test_r3_remote_doctor_handshake():
    from apatch.remote.doctor import remote_doctor

    target = build_remote_target("example-search-host", "/srv/repo", alias="x")

    # healthy remote: doctor ok with version/fingerprint, identity preserved
    ok_transport = FakeRemoteTransport(
        {"apatch_doctor": {"ok": True, "version": "0.7.0", "workspace": "/srv/repo"}}
    )
    out = remote_doctor(target, transport=ok_transport)
    assert out["ok"] is True
    assert out["remote"]["host"] == "example-search-host"
    assert out["remote"]["root"] == "/srv/repo"
    assert out["remote_version"] == "0.7.0"

    # missing remote apatch -> typed remediation, no install attempted
    missing = FakeRemoteTransport(
        {
            "apatch_doctor": {
                "ok": False,
                "error_type": "REMOTE_APATCH_MISSING",
                "message": "no apatch",
            }
        }
    )
    out = remote_doctor(target, transport=missing)
    assert out["ok"] is False
    assert out["error_type"] == "REMOTE_APATCH_MISSING"
    assert out["recoverable"] is True
    assert "install" in out["recommended_action"].lower()

    # bare failure with no typed error -> classified as unreachable
    down = FakeRemoteTransport({"apatch_doctor": {"ok": False}})
    out = remote_doctor(target, transport=down)
    assert out["error_type"] == "REMOTE_UNREACHABLE"


def test_r4_ssh_command_does_not_embed_user_data(monkeypatch):
    from apatch.remote.ssh import SshRemoteTransport

    calls = []

    def fake_run(argv, input, text, capture_output, timeout, check):
        calls.append({"argv": argv, "input": input})
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"ok": True}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    injection = "/srv/repo; rm -rf / #"
    SshRemoteTransport(python="python3").call(
        parse_remote_target("example-search-host:/srv/repo"),
        "apatch_verify_run",
        {"verify": ["pytest", injection], "needle": injection},
    )

    remote_cmd = calls[0]["argv"][-1]
    # fixed remote command; user data is never spliced into the ssh argv
    assert "-m apatch.remote.worker" in remote_cmd
    assert injection not in remote_cmd
    assert "rm -rf" not in remote_cmd
    # the dangerous payload travels only as stdin JSON data
    payload = json.loads(calls[0]["input"])
    assert payload["arguments"]["verify"] == ["pytest", injection]
    assert payload["arguments"]["needle"] == injection


def test_r5_protocol_errors_are_typed(monkeypatch):
    from apatch.remote.ssh import SshRemoteTransport

    target = parse_remote_target("example-search-host:/srv/repo")

    def patch(factory):
        def fake_run(argv, input, text, capture_output, timeout, check):
            return factory(argv)

        monkeypatch.setattr(subprocess, "run", fake_run)

    # invalid JSON stdout -> typed error
    patch(lambda argv: subprocess.CompletedProcess(argv, 0, stdout="not json", stderr=""))
    r = SshRemoteTransport().call(target, "apatch_doctor", {})
    assert r["ok"] is False
    assert r["error_type"] == "REMOTE_JSON_INVALID"

    # non-zero exit with missing remote apatch
    patch(
        lambda argv: subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="No module named apatch"
        )
    )
    r = SshRemoteTransport().call(target, "apatch_doctor", {})
    assert r["error_type"] == "REMOTE_APATCH_MISSING"

    # timeout maps to a typed error rather than a raw exception
    def boom(argv, input, text, capture_output, timeout, check):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    r = SshRemoteTransport().call(target, "apatch_doctor", {})
    assert r["ok"] is False
    assert r["error_type"] == "REMOTE_TIMEOUT"


def test_r0_worker_rfp_coverage():
    import os
    import pytest

    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(
        os.path.join(root, "docs", "RFP-029-remote-ssh-workspaces.md"),
        encoding="utf-8",
    ) as f:
        rfp = f.read()
    with open(
        os.path.join(root, "docs", "specs", "SPEC-REMOTE-SSH-WORKER-1.md"),
        encoding="utf-8",
    ) as f:
        spec = f.read()
    out = rfp_spec_coverage(
        rfp, spec, rfp_id="RFP-029", spec_id="SPEC-REMOTE-SSH-WORKER-1"
    )
    assert out["passed"] is True
    assert not out.get("gaps")
