import json

from apatch.remote.policy import resolve_remote_target
from apatch.remote.orchestrator import remote_task_run
from apatch.remote.transport import FakeRemoteTransport


def test_mcp_remote_task_run_dry_run_contract():
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run

    result = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/example/search-workspace",
        intent="Fix engineering system filter visibility",
        plan={"needles": [{"path": "src/MatchingTool.tsx", "find": "old", "replace": "new"}]},
        verify="npm test -- MatchingTool",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["transport"] == "fake"
    assert result["approval_boundary"] == "single_intent"
    assert result["approval_prompts_expected"] == 1
    assert [step["operation"] for step in result["timeline"]] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ]
    # A dry-run simulates the WHOLE ceremony (incl. apply + attest) but writes nothing —
    # the response must say so unmistakably, so an agent never reads it as a real apply.
    assert result["applied"] is False
    assert "DRY-RUN" in result["message"]
    assert "dry_run=false" in result.get("next_action", "")




def test_remote_task_routes_commit_attested_as_standalone_operation():
    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {"ok": True},
            "apatch_commit_attested": {
                "ok": True,
                "committed": True,
                "files": ["a.py"],
            },
        }
    )
    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="Commit exact attested search files",
        plan={
            "commit_attested": True,
            "session_ids": ["session-a", "session-b"],
            "push": True,
        },
        transport=transport,
    )
    assert result["ok"] is True
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_commit_attested",
    ]
    commit_args = transport.calls[1]["arguments"]
    assert commit_args["plan"]["session_ids"] == ["session-a", "session-b"]
    assert commit_args["plan"]["push"] is True
    assert commit_args["message"] == "Commit exact attested search files"


def test_mcp_remote_task_reports_commit_attested_write_and_validation(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    import apatch.remote.orchestrator as orchestrator

    def fake_remote_task_run(*_args, plan, **_kwargs):
        validated = bool(plan.get("dry_run"))
        return {
            "ok": True,
            "timeline": [
                {"operation": "apatch_doctor", "ok": True, "result": {"ok": True}},
                {
                    "operation": "apatch_commit_attested",
                    "ok": True,
                    "result": {
                        "ok": True,
                        "committed": not validated,
                        "pushed": not validated,
                        "dry_run": validated,
                        "files": ["a.py"],
                    },
                },
            ],
        }

    monkeypatch.setattr(orchestrator, "remote_task_run", fake_remote_task_run)
    real = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/repo",
        intent="commit exact files",
        plan={
            "commit_attested": True,
            "session_ids": ["session-a"],
            "push": True,
            "dry_run": False,
        },
        dry_run=False,
    )
    assert real["applied"] is True
    assert "COMMITTED and PUSHED" in real["message"]
    assert "NO mutation step" not in real["message"]

    validation = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/repo",
        intent="validate exact files",
        plan={
            "commit_attested": True,
            "session_ids": ["session-a"],
            "push": True,
            "dry_run": True,
        },
        dry_run=False,
    )
    assert validation["applied"] is False
    assert "VALIDATED" in validation["message"]
    assert "NO mutation step" not in validation["message"]


def test_remote_task_commit_attested_requires_explicit_sessions():
    transport = FakeRemoteTransport()
    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="Must fail before transport",
        plan={"commit_attested": True},
        transport=transport,
    )
    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_COMMIT_REQUIRES_SESSIONS"
    assert transport.calls == []

def test_mcp_remote_task_run_dry_run_logs_path_guidance():
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run

    result = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/example/search-workspace/opensearch/search",
        intent="Apply a prebuilt remote patch log",
        plan={"logs_path": ".apatch/remote/prebuilt.jsonl"},
        verify="true",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert [step["operation"] for step in result["timeline"]] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ]
    assert "logs_path" in result["next_action"]
    assert "needles" not in result["next_action"]


def test_r7_remote_task_run_single_approval_boundary():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {"ok": True, "logs_path": ".apatch/remote/patches.jsonl"},
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="Fix engineering system filter visibility",
        plan={
            "needles": [
                {
                    "path": "src/MatchingTool.tsx",
                    "find": "engineeringSystem === selectedEngineeringSystem",
                    "replace": "candidate.products.some(...)",
                }
            ]
        },
        verify={"command": "npm test -- MatchingTool"},
        transport=transport,
    )

    assert result["ok"] is True
    assert result["approval_boundary"] == "single_intent"
    assert result["approval_prompts_expected"] == 1
    assert result["target"]["host"] == "example-search-host"
    assert result["summary"]["apply_steps"] == 1
    assert result["summary"]["verify_steps"] == 1
    assert result["summary"]["last_operation"] == "apatch_session_end"
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ]
    assert transport.calls[3]["arguments"]["logs_path"] == ".apatch/remote/patches.jsonl"
    assert transport.calls[5]["arguments"]["verify"]["command"] == "npm test -- MatchingTool"

    multi_transport = FakeRemoteTransport(
        {
            "apatch_spec_run_multi": {
                "ok": True,
                "completed_specs": ["SPEC-MUFTA-1", "SPEC-GILZA-1"],
            }
        }
    )
    multi_plan = {
        "specs": ["SPEC-MUFTA-1", "SPEC-GILZA-1"],
        "requirements": {
            "SPEC-MUFTA-1": {"R14": {"needles": [{"action": "replace"}]}},
            "SPEC-GILZA-1": {"R10": {"needles": [{"action": "replace"}]}},
        },
        "cross_verify": True,
    }
    multi_result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="Fix cross-owned sleeve signals",
        plan=multi_plan,
        transport=multi_transport,
    )

    assert multi_result["ok"] is True
    assert multi_result["summary"]["apply_steps"] == 1
    assert [call["operation"] for call in multi_transport.calls] == [
        "apatch_doctor",
        "apatch_spec_run_multi",
    ]
    assert multi_transport.calls[1]["arguments"]["plan"] == multi_plan
    assert "apatch_session_start" not in [
        call["operation"] for call in multi_transport.calls
    ]

    fix_transport = FakeRemoteTransport(
        {
            "apatch_resume_session": {
                "ok": True,
                "resumed": True,
                "session_id": "apatch_sess_failed_1",
                "session_capability": {
                    "session_id": "apatch_sess_failed_1",
                    "session_token": "rotated-fix-forward-token",
                },
            },
            "apatch_generate_batch": {
                "ok": True,
                "logs_path": ".apatch/remote/fix-forward.jsonl",
            },
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )
    fix_result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="Fix failed remote verify in the same governed session",
        plan={
            "fix_forward_current": True,
            "needles": [
                {
                    "action": "replace",
                    "target_file": "tests/live/test_slug.py",
                    "find_text": "vendor_code.keyword",
                    "replace_text": "vendor_code",
                }
            ],
        },
        verify="pytest tests/live/test_slug.py -q",
        transport=fix_transport,
    )

    assert fix_result["ok"] is True
    assert fix_result["summary"]["apply_steps"] == 1
    assert [call["operation"] for call in fix_transport.calls] == [
        "apatch_doctor",
        "apatch_resume_session",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ]
    assert fix_transport.calls[2]["arguments"]["plan"]["out_path"] == (
        ".apatch/tmp/apatch_sess_failed_1/remote-patches.jsonl"
    )
    assert fix_transport.calls[4]["arguments"]["plan"]["reset"] is True
    assert fix_transport.calls[4]["arguments"]["plan"]["session_path"] == (
        ".apatch/tmp/apatch_sess_failed_1/remote-apply-session.json"
    )
    for operation in (
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ):
        arguments = next(
            call["arguments"]
            for call in fix_transport.calls
            if call["operation"] == operation
        )
        assert arguments["governed_session_id"] == "apatch_sess_failed_1"
        assert arguments["session_token"] == "rotated-fix-forward-token"
    assert "apatch_session_start" not in [
        call["operation"] for call in fix_transport.calls
    ]

    missing_verify = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="unsafe fix-forward",
        plan={"fix_forward_current": True, "needles": [{"action": "replace"}]},
        transport=FakeRemoteTransport(),
    )
    assert missing_verify["error_type"] == "REMOTE_FIX_FORWARD_REQUIRES_VERIFY"

    missing_patch = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="empty fix-forward",
        plan={"fix_forward_current": True},
        verify="pytest",
        transport=FakeRemoteTransport(),
    )
    assert missing_patch["error_type"] == "REMOTE_FIX_FORWARD_REQUIRES_PATCH"




    rebind_transport = FakeRemoteTransport(
        {
            "apatch_rebind_stale": {
                "ok": True,
                "spec": "SPEC-SHARED-1",
                "rebound": ["R9"],
            }
        }
    )
    rebind_plan = {
        "rebind_stale": True,
        "spec": "SPEC-SHARED-1",
    }
    rebind_result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace",
        intent="Rebind shared-file stale requirements once",
        plan=rebind_plan,
        transport=rebind_transport,
    )

    assert rebind_result["ok"] is True
    assert [call["operation"] for call in rebind_transport.calls] == [
        "apatch_doctor",
        "apatch_rebind_stale",
    ]
    assert rebind_transport.calls[1]["arguments"]["plan"] == rebind_plan
    assert "apatch_session_start" not in [
        call["operation"] for call in rebind_transport.calls
    ]


def test_remote_task_routes_targeted_execute_next_without_generic_session():
    transport = FakeRemoteTransport(
        {
            "apatch_execute_next": {
                "ok": True,
                "applied": True,
                "requirement_attested": True,
            }
        }
    )
    plan = {
        "execute_next": True,
        "spec": "SPEC-PATRUBOK-1",
        "requirement": "SPEC-PATRUBOK-1#R6",
        "needles": [
            {
                "action": "replace",
                "target_file": "docs/specs/slug_contracts/patrubok.yaml",
                "find_text": "old",
                "replace_text": "new",
            }
        ],
    }

    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace/opensearch/search",
        intent="Re-attest one exact requirement",
        plan=plan,
        transport=transport,
    )

    assert result["ok"] is True
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_execute_next",
    ]
    assert transport.calls[1]["arguments"]["plan"] == plan
def test_remote_task_routes_slug_ratify_without_generic_session():
    transport = FakeRemoteTransport(
        {
            "apatch_slug_ratify": {
                "ok": True,
                "slug": "podves",
                "gated": True,
            }
        }
    )
    plan = {
        "slug_ratify": True,
        "slug": "podves",
        "spec": "SPEC-PODVES-1",
    }

    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace/opensearch/search",
        intent="Ratify one completed search category",
        plan=plan,
        transport=transport,
    )

    assert result["ok"] is True
    assert result["summary"]["apply_steps"] == 1
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_slug_ratify",
    ]
    assert transport.calls[1]["arguments"]["plan"] == plan


def test_remote_task_routes_single_spec_without_dummy_peer():
    transport = FakeRemoteTransport(
        {
            "apatch_spec_run": {
                "ok": True,
                "done": True,
                "spec": "SPEC-TRAP-1",
            }
        }
    )
    plan = {
        "spec": "SPEC-TRAP-1",
        "requirements": {
            "R1": {"needles": [{"action": "replace"}]}
        },
    }

    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace/opensearch/search",
        intent="Fix one owned search category",
        plan=plan,
        transport=transport,
    )

    assert result["ok"] is True
    assert result["summary"]["apply_steps"] == 1
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_spec_run",
    ]
    assert transport.calls[1]["arguments"]["plan"] == plan


def test_remote_task_run_binds_capability_and_isolates_runtime_paths():
    session_id = "apatch_sess_remote_1"
    session_token = "remote-secret-token"
    transport = FakeRemoteTransport(
        {
            "apatch_session_start": {
                "ok": True,
                "session": {"session_id": None},
                "session_capability": {
                    "session_id": session_id,
                    "session_token": session_token,
                },
            },
            "apatch_generate_batch": {
                "ok": True,
                "out_path_rel": f".apatch/tmp/{session_id}/remote-patches.jsonl",
            },
            "apatch_apply_session": {
                "ok": True,
                "continue": False,
                "applied": 1,
                "session_path": (
                    f".apatch/tmp/{session_id}/remote-apply-session.json"
                ),
            },
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="apply one isolated remote transaction",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        transport=transport,
    )

    assert result["ok"] is True
    calls = {call["operation"]: call for call in transport.calls}
    generate_plan = calls["apatch_generate_batch"]["arguments"]["plan"]
    apply_plan = calls["apatch_apply_session"]["arguments"]["plan"]
    assert generate_plan["out_path"] == (
        f".apatch/tmp/{session_id}/remote-patches.jsonl"
    )
    assert apply_plan["session_path"] == (
        f".apatch/tmp/{session_id}/remote-apply-session.json"
    )
    for operation in (
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_attest",
        "apatch_session_end",
    ):
        arguments = calls[operation]["arguments"]
        assert arguments["governed_session_id"] == session_id
        assert arguments["session_token"] == session_token
    assert calls["apatch_session_end"]["arguments"]["session_path"].endswith(
        "remote-apply-session.json"
    )

    rendered = json.dumps(result, ensure_ascii=False)
    assert session_token not in rendered
    assert "***REDACTED***" in rendered


def test_remote_task_run_existing_logs_path_skips_generate_batch():
    transport = FakeRemoteTransport(
        {
            "apatch_apply_session": {"ok": True, "applied": 66, "phase": "done"},
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace/opensearch/search",
        intent="Apply prebuilt remote patch log",
        plan={"logs_path": ".apatch/remote/unowned_feedback_owner_assign.jsonl"},
        verify={"command": "pytest tests/unit/test_feedback_triage_audit.py -q"},
        transport=transport,
    )

    assert result["ok"] is True
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ]
    assert transport.calls[2]["arguments"]["logs_path"] == ".apatch/remote/unowned_feedback_owner_assign.jsonl"
    assert transport.calls[3]["arguments"]["logs_path"] == ".apatch/remote/unowned_feedback_owner_assign.jsonl"


def test_remote_task_run_treats_slug_close_as_patch_plan():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {"ok": True, "logs_path": ".apatch/remote/slug.jsonl"},
            "apatch_apply_session": {"ok": True, "applied": 1, "phase": "done"},
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/example/search-workspace/opensearch/search",
        intent="Close otvod feedback triage",
        plan={"slug_close": {"slug": "otvod"}},
        transport=transport,
    )

    assert result["ok"] is True
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_attest",
        "apatch_session_end",
    ]
    assert transport.calls[2]["arguments"]["plan"]["slug_close"]["slug"] == "otvod"


def test_remote_task_run_compacts_doctor_timeline_payload():
    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {
                "ok": True,
                "version": "0.7.0",
                "workspace": "/srv/repo",
                "protocol_contract": {"huge": "playbook"},
                "spec_run": {"huge": "playbook"},
                "mcp_health": {
                    "ok": True,
                    "mcp_extra_installed": True,
                    "mcp_tool_catalog": {"count": 95, "fingerprint": "abc"},
                },
                "trustchain": {"active": True, "mode": "audit", "path": "/srv/.trustchain"},
                "hygiene": {"status": "clean", "orphan_count": 0, "inferred_count": 0},
            }
        }
    )

    result = remote_task_run("ssh://example-search-host/srv/repo", intent="compact doctor", transport=transport)
    doctor = result["timeline"][0]["result"]
    assert doctor["version"] == "0.7.0"
    assert doctor["mcp_health"]["tool_count"] == 95
    assert doctor["trustchain"]["mode"] == "audit"
    assert doctor["details_truncated"] is True
    assert "protocol_contract" not in doctor
    assert "spec_run" not in doctor


def test_remote_task_run_self_heals_stale_apply_session():
    """apply_session returns ok:True + SESSION_ALREADY_COMPLETE (a stale session frozen at
    100%, nothing applied). The autopilot calls apply_session without reset=true, so a fresh
    session and a new patch still see 'already complete' and write nothing. The orchestrator
    must retry ONCE with reset=true so the current patch actually applies — same self-heal
    class as the stale-session-lock deadlock."""
    transport = FakeRemoteTransport(
        {
            "apatch_apply_session": [
                {"ok": True, "status": "SESSION_ALREADY_COMPLETE", "applied": 0},
                {"ok": True, "applied": 1, "phase": "done"},
            ]
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="Fix filter",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        transport=transport,
    )

    assert result["ok"] is True
    apply_calls = [c for c in transport.calls if c["operation"] == "apatch_apply_session"]
    assert len(apply_calls) == 2  # original + reset retry
    retry_args = apply_calls[1]["arguments"]
    assert retry_args.get("reset") is True or (retry_args.get("plan") or {}).get("reset") is True
    apply_steps = [s for s in result["timeline"] if s["operation"] == "apatch_apply_session"]
    assert apply_steps[-1]["result"].get("applied") == 1  # the re-apply actually wrote


def test_remote_task_run_reset_retry_is_one_shot_across_chunks():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {"ok": True, "logs_path": ".apatch/remote/current.jsonl"},
            "apatch_apply_session": [
                {"ok": True, "status": "SESSION_ALREADY_COMPLETE", "applied": 0},
                {"ok": True, "continue": True, "applied": 5, "checkpoint": "chunk-1"},
                {"ok": True, "continue": False, "applied": 2, "checkpoint": "chunk-2"},
            ],
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="resume a reset remote batch",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        transport=transport,
    )

    assert result["ok"] is True
    calls = [call for call in transport.calls if call["operation"] == "apatch_apply_session"]
    assert len(calls) == 3
    assert calls[0]["arguments"].get("reset") is not True
    assert calls[1]["arguments"]["reset"] is True
    assert calls[1]["arguments"]["plan"]["reset"] is True
    assert calls[2]["arguments"].get("reset") is not True
    assert (calls[2]["arguments"].get("plan") or {}).get("reset") is not True


def test_remote_task_run_exposes_governed_checkpoint_rollback():
    transport = FakeRemoteTransport(
        {
            "apatch_rollback": {"ok": True, "restored": ["x.py"]},
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="rollback failed remote apply",
        plan={"rollback_session": "apatch_sess_chunk_1"},
        transport=transport,
    )

    assert result["ok"] is True
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_rollback",
        "apatch_session_end",
    ]
    assert transport.calls[1]["arguments"]["session_id"] == "apatch_sess_chunk_1"


def test_remote_task_run_rollback_requires_checkpoint():
    transport = FakeRemoteTransport()

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="unsafe rollback",
        plan={"rollback_session": ""},
        transport=transport,
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_ROLLBACK_REQUIRES_CHECKPOINT"
    assert transport.calls == []


def test_remote_task_run_stale_lock_reset_is_one_shot_across_chunks():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {"ok": True, "logs_path": ".apatch/remote/current.jsonl"},
            "apatch_apply_session": [
                {
                    "ok": False,
                    "error": "apply_session: in-progress session for '/repo/old.jsonl' (1/3 chunks); pass reset=true",
                },
                {"ok": True, "continue": True, "applied": 5, "checkpoint": "chunk-1"},
                {"ok": True, "continue": False, "applied": 2, "checkpoint": "chunk-2"},
            ],
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="replace a stale remote batch",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        transport=transport,
    )

    assert result["ok"] is True
    calls = [call for call in transport.calls if call["operation"] == "apatch_apply_session"]
    assert len(calls) == 3
    assert calls[1]["arguments"]["reset"] is True
    assert calls[2]["arguments"].get("reset") is not True
    assert (calls[2]["arguments"].get("plan") or {}).get("reset") is not True


def test_remote_task_run_self_heals_stale_apply_session_lock():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {"ok": True, "logs_path": ".apatch/remote/current.jsonl"},
            "apatch_apply_session": [
                {
                    "ok": False,
                    "error": (
                        "apply_session: in-progress session for "
                        "'/repo/.apatch/remote/old.jsonl' (3/4 chunks); pass reset=true"
                    ),
                },
                {"ok": True, "applied": 1, "phase": "done"},
            ],
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="Fix stale apply lock",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        transport=transport,
    )

    assert result["ok"] is True
    apply_calls = [c for c in transport.calls if c["operation"] == "apatch_apply_session"]
    assert len(apply_calls) == 2
    retry_args = apply_calls[1]["arguments"]
    assert retry_args["_auto_recovery"] == "stale_apply_session_lock"
    assert retry_args["reset"] is True
    assert retry_args["plan"]["reset"] is True
    apply_steps = [s for s in result["timeline"] if s["operation"] == "apatch_apply_session"]
    assert apply_steps[-1]["result"]["applied"] == 1


def test_remote_task_run_repeats_apply_session_until_complete():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {"ok": True, "logs_path": ".apatch/remote/patches.jsonl"},
            "apatch_apply_session": [
                {"ok": True, "continue": True, "applied": 5, "checkpoint": "chunk-1"},
                {"ok": True, "continue": True, "applied": 5, "checkpoint": "chunk-2"},
                {"ok": True, "continue": False, "applied": 2, "checkpoint": "chunk-3"},
            ],
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="apply all remote chunks",
        plan={
            "needles": [{"path": "x.py", "find": "a", "replace": "b"}],
            "chunk_max_files": 5,
        },
        verify="pytest",
        transport=transport,
    )

    assert result["ok"] is True
    ops = [c["operation"] for c in transport.calls]
    assert ops.count("apatch_apply_session") == 3
    assert ops.index("apatch_verify_run") > max(
        i for i, op in enumerate(ops) if op == "apatch_apply_session"
    )
    apply_steps = [s for s in result["timeline"] if s["operation"] == "apatch_apply_session"]
    assert [s["result"]["checkpoint"] for s in apply_steps] == ["chunk-1", "chunk-2", "chunk-3"]


def test_remote_task_run_forwards_requirement_to_session_start():
    transport = FakeRemoteTransport()

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="attest one requirement",
        plan={
            "requirement": "SPEC-OTVOD-1#R13",
            "spec_path": "docs/specs/SPEC-OTVOD-1.md",
            "artifacts": ["reality:otvod-feedback"],
        },
        verify="pytest tests/live/test_otvod_feedback_all.py -q",
        transport=transport,
    )

    assert result["ok"] is True
    start = next(c for c in transport.calls if c["operation"] == "apatch_session_start")
    assert start["arguments"]["requirement"] == "SPEC-OTVOD-1#R13"
    assert start["arguments"]["spec_path"] == "docs/specs/SPEC-OTVOD-1.md"
    assert start["arguments"]["artifacts"] == ["reality:otvod-feedback"]


def test_remote_task_run_noop_attest_rebinds_requirement_without_mutation():
    transport = FakeRemoteTransport(
        {
            "apatch_session_start": {
                "ok": True,
                "session": {"session_id": "apatch_sess_noop_1"},
                "session_capability": {
                    "session_id": "apatch_sess_noop_1",
                    "session_token": "start-token",
                },
            },
            "apatch_resume_session": {
                "ok": True,
                "resumed": True,
                "session_capability": {
                    "session_id": "apatch_sess_noop_1",
                    "session_token": "rotated-token",
                },
            },
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
            "apatch_noop_attest": {"ok": True, "noop": True, "covered_by": ["R1"]},
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="rebind green requirement",
        plan={
            "requirement": "SPEC-KLAPAN-1#R6",
            "noop_covered_by": ["R1"],
        },
        verify="pytest tests/live/test_klapan_feedback_inventory.py -q",
        transport=transport,
    )

    assert result["ok"] is True
    ops = [c["operation"] for c in transport.calls]
    assert ops == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_resume_session",
        "apatch_verify_run",
        "apatch_noop_attest",
        "apatch_session_end",
    ]
    noop = next(c for c in transport.calls if c["operation"] == "apatch_noop_attest")
    assert noop["arguments"]["covered_by"] == ["R1"]
    for operation in ("apatch_verify_run", "apatch_noop_attest", "apatch_session_end"):
        arguments = next(
            call["arguments"] for call in transport.calls if call["operation"] == operation
        )
        assert arguments["governed_session_id"] == "apatch_sess_noop_1"
        assert arguments["session_token"] == "rotated-token"
    assert "apatch_attest" not in ops


def test_remote_task_run_finalize_current_resumes_active_session_without_mutation():
    transport = FakeRemoteTransport(
        {
            "apatch_resume_session": {"ok": True, "resumed": True, "lifecycle": "verifying"},
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
            "apatch_attest": {"ok": True, "committed": True},
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="finalize after slow service restart",
        plan={"finalize_current": True},
        verify="pytest tests/live/test_kran_feedback_corpus.py -q",
        transport=transport,
    )

    assert result["ok"] is True
    assert result["summary"]["apply_steps"] == 0
    assert result["summary"]["verify_steps"] == 1
    assert [c["operation"] for c in transport.calls] == [
        "apatch_doctor",
        "apatch_resume_session",
        "apatch_verify_run",
        "apatch_attest",
        "apatch_session_end",
    ]


def test_remote_task_run_finalize_current_requires_fresh_verify():
    transport = FakeRemoteTransport()

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="unsafe finalize",
        plan={"finalize_current": True},
        transport=transport,
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_FINALIZE_REQUIRES_VERIFY"
    assert transport.calls == []


def test_remote_task_run_policy_default_allows_noop_attest(tmp_path):
    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "prod": {
                        "host": "example-search-host",
                        "path": "/srv/repo",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    transport = FakeRemoteTransport(
        {
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
            "apatch_noop_attest": {"ok": True, "noop": True},
        }
    )

    result = remote_task_run(
        "prod",
        intent="policy default noop rebind",
        plan={"requirement": "SPEC-KLAPAN-1#R6", "noop_covered_by": ["R1"]},
        verify="true",
        transport=transport,
        policy_root=str(tmp_path),
    )

    assert result["ok"] is True
    assert [c["operation"] for c in transport.calls][-3:] == [
        "apatch_verify_run",
        "apatch_noop_attest",
        "apatch_session_end",
    ]


def test_remote_task_run_policy_default_allows_rebind_stale(tmp_path):
    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "prod": {
                        "host": "example-search-host",
                        "path": "/srv/repo",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    transport = FakeRemoteTransport(
        {
            "apatch_rebind_stale": {
                "ok": True,
                "spec": "SPEC-SHARED-1",
                "rebound": ["R9"],
            }
        }
    )

    result = remote_task_run(
        "prod",
        intent="policy default stale rebind",
        plan={"rebind_stale": True, "spec": "SPEC-SHARED-1"},
        transport=transport,
        policy_root=str(tmp_path),
    )

    assert result["ok"] is True
    assert [c["operation"] for c in transport.calls] == [
        "apatch_doctor",
        "apatch_rebind_stale",
    ]


def test_remote_task_run_reconciles_completed_execute_next_after_timeout():
    transport = FakeRemoteTransport(
        {
            "apatch_execute_next": {
                "ok": False,
                "error_type": "REMOTE_TIMEOUT",
                "message": "Remote operation timed out.",
            },
            "apatch_session_state": {
                "ok": True,
                "session": {
                    "session_id": "apatch_sess_remote_done",
                    "lifecycle": "ended",
                    "phase": "complete",
                    "last_tool": "apatch_attest",
                    "ended_at": "2026-08-05T14:00:00+00:00",
                    "failure": None,
                    "artifacts": ["spec:SPEC-TRUBKA-1#R2@sha256:test"],
                },
            },
        }
    )
    plan = {
        "execute_next": True,
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

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="repair exact requirement",
        plan=plan,
        transport=transport,
    )

    assert result["ok"] is True
    assert result["last_result"]["recovered_from"] == "REMOTE_TIMEOUT"
    assert result["last_result"]["applied_reconciled"] is True
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_execute_next",
        "apatch_session_state",
    ]


def test_remote_task_run_keeps_unproven_timeout_typed_and_does_not_close_session():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {
                "ok": False,
                "error_type": "REMOTE_TIMEOUT",
                "message": "Remote operation timed out.",
            },
            "apatch_session_state": {
                "ok": True,
                "session": {
                    "session_id": "apatch_sess_still_running",
                    "lifecycle": "applying",
                    "phase": "apply",
                    "last_tool": "apatch_apply_session",
                    "failure": None,
                    "artifacts": [],
                },
            },
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="do not guess after timeout",
        plan={"needles": [{"action": "replace", "target_file": "x.py"}]},
        transport=transport,
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_TIMEOUT"
    assert result["recommended_action"] == "reconcile_remote_state"
    assert "apatch_session_end" not in [call["operation"] for call in transport.calls]


def test_remote_task_run_stops_on_typed_failure():
    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {
                "ok": False,
                "error_type": "REMOTE_UNREACHABLE",
                "message": "SSH host is not reachable.",
                "recoverable": True,
                "recommended_action": "Check SSH config and retry.",
            }
        }
    )

    result = remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="Fix filter",
        verify="npm test",
        transport=transport,
    )

    assert result["ok"] is False
    assert result["failed_step"] == "apatch_doctor"
    assert result["error_type"] == "REMOTE_UNREACHABLE"
    assert result["recommended_action"] == "Check SSH config and retry."
    assert [call["operation"] for call in transport.calls] == ["apatch_doctor"]


def test_remote_task_run_uses_out_path_rel_from_generate_batch():
    transport = FakeRemoteTransport(
        {"apatch_generate_batch": {"ok": True, "out_path_rel": ".apatch/tmp/session/patches.jsonl"}}
    )

    remote_task_run(
        "example-search-host:/srv/example/search-workspace",
        intent="Fix filter",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        transport=transport,
    )

    assert transport.calls[3]["arguments"]["logs_path"] == ".apatch/tmp/session/patches.jsonl"


def test_remote_task_run_with_alias_redacts_timeline(tmp_path):
    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "search-example": {
                        "host": "example-search-host",
                        "path": "/srv/example/search-workspace",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {
                "ok": True,
                "host": "example-search-host",
                "workspace": "/srv/example/search-workspace",
            }
        }
    )

    result = remote_task_run(
        "search-example",
        intent="Fix filter",
        transport=transport,
        policy_root=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["target"]["redacted"] is True
    assert "host" not in result["target"]
    assert result["timeline"][0]["result"]["host"] == "search-example"
    assert result["timeline"][0]["result"]["workspace"] == "search-example"
    assert transport.calls[0]["target"]["redacted"] is True
    assert "example-search-host" not in json.dumps(result)
    assert "/srv/example/search-workspace" not in json.dumps(result)


def test_remote_task_run_policy_denies_operation(tmp_path):
    target = resolve_remote_target(
        "limited",
        policy_root=str(tmp_path),
        policy={
            "targets": {
                "limited": {
                    "host": "example-search-host",
                    "path": "/srv/example/search-workspace",
                    "allowed_operations": ["apatch_doctor"],
                }
            }
        },
    )
    transport = FakeRemoteTransport()

    result = remote_task_run(target, intent="Fix filter", transport=transport)

    assert result["ok"] is False
    assert result["failed_step"] == "apatch_session_start"
    assert result["error_type"] == "REMOTE_OPERATION_DENIED"
    assert [call["operation"] for call in transport.calls] == ["apatch_doctor"]


def test_mcp_remote_task_run_uses_alias_policy_and_redacts(tmp_path):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run

    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "search-example": {
                        "host": "example-search-host",
                        "path": "/srv/example/search-workspace",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = apatch_remote_task_run(
        remote_target="search-example",
        intent="Fix filter",
        target_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["target"]["alias"] == "search-example"
    assert result["target"]["redacted"] is True
    assert "example-search-host" not in json.dumps(result)


def test_mcp_remote_task_run_reports_nested_apply_counts_truthfully(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    from apatch.remote import orchestrator

    def run_with(applied, *, operation="apatch_spec_run_multi", **outcomes):
        nested = {"ok": True, "applied": applied, **outcomes}
        monkeypatch.setattr(
            orchestrator,
            "remote_task_run",
            lambda *_a, **_k: {
                "ok": True,
                "timeline": [{
                    "operation": operation,
                    "ok": True,
                    "result": nested,
                }],
            },
        )
        return apatch_remote_task_run(
            remote_target="ssh://example-search-host/srv/repo",
            intent="truthful multi-run",
            plan={"specs": ["SPEC-A", "SPEC-B"], "requirements": {}},
            dry_run=False,
        )

    assert run_with(0)["applied"] is False
    assert run_with(3)["applied"] is True

    execute_next = run_with(
        0,
        operation="apatch_execute_next",
        chunk_result={"applied": 1},
    )
    assert execute_next["applied"] is True

    mixed = run_with(
        2,
        already_satisfied_requirements=["SPEC-B#R1"],
    )
    assert mixed["applied"] is True
    assert "APPLIED" in mixed["message"]
    assert "NOOP" not in mixed["message"]


def test_mcp_remote_task_run_noop_and_ratify_are_not_source_applies(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    from apatch.remote import orchestrator

    def run_timeline(timeline, plan):
        monkeypatch.setattr(
            orchestrator,
            "remote_task_run",
            lambda *_a, **_k: {"ok": True, "timeline": timeline},
        )
        return apatch_remote_task_run(
            remote_target="ssh://example-search-host/srv/repo",
            intent="truthful noop",
            plan=plan,
            dry_run=False,
        )

    satisfied = run_timeline(
        [{
            "operation": "apatch_spec_run",
            "ok": True,
            "result": {
                "ok": True,
                "applied": 0,
                "already_satisfied_requirements": ["R1"],
            },
        }],
        {"spec": "SPEC-A", "requirements": {"R1": {"needles": [{"action": "delete", "target_file": "gone"}]}}},
    )
    assert satisfied["applied"] is False
    assert satisfied["already_satisfied_requirements"] == ["R1"]
    assert "ALREADY-SATISFIED NOOP" in satisfied["message"]
    assert "No retry" in satisfied["next_action"]

    ratified = run_timeline(
        [{"operation": "apatch_slug_ratify", "ok": True, "result": {"ok": True}}],
        {"slug_ratify": True, "slug": "slug", "spec": "SPEC-SLUG"},
    )
    assert ratified["applied"] is False
    assert "RATIFIED" in ratified["message"]


def test_mcp_remote_task_run_reset_session_message(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    from apatch.remote import orchestrator

    def fake_remote_task_run(*_args, **_kwargs):
        return {
            "ok": True,
            "approval_boundary": "single_intent",
            "approval_prompts_expected": 1,
            "target": {"host": "example-search-host", "path": "/srv/repo", "redacted": False},
            "intent": "reset stale lock",
            "timeline": [
                {"operation": "apatch_doctor", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_session_end", "ok": True, "result": {"ok": True}},
            ],
        }

    monkeypatch.setattr(orchestrator, "remote_task_run", fake_remote_task_run)
    result = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/repo",
        intent="reset stale lock",
        plan={"reset_session": True},
        dry_run=False,
    )
    assert result["applied"] is False
    assert "reset completed" in result["message"]
    assert "NO mutation step" not in result["message"]


def test_mcp_remote_task_run_noop_attest_message(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    import apatch.remote.orchestrator as orchestrator

    def fake_remote_task_run(*_args, **_kwargs):
        return {
            "ok": True,
            "timeline": [
                {"operation": "apatch_doctor", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_session_start", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_verify_run", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_noop_attest", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_session_end", "ok": True, "result": {"ok": True}},
            ],
        }

    monkeypatch.setattr(orchestrator, "remote_task_run", fake_remote_task_run)
    result = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/repo",
        intent="rebind green requirement",
        plan={"requirement": "SPEC-KLAPAN-1#R6", "noop_covered_by": ["R1"]},
        verify="pytest",
        dry_run=False,
    )

    assert result["applied"] is False
    assert "NOOP-ATTESTED" in result["message"]
    assert "NO mutation step" not in result["message"]


def test_mcp_remote_task_run_finalize_current_message(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    import apatch.remote.orchestrator as orchestrator

    def fake_remote_task_run(*_args, **_kwargs):
        return {
            "ok": True,
            "timeline": [
                {"operation": "apatch_doctor", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_resume_session", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_verify_run", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_attest", "ok": True, "result": {"ok": True}},
                {"operation": "apatch_session_end", "ok": True, "result": {"ok": True}},
            ],
        }

    monkeypatch.setattr(orchestrator, "remote_task_run", fake_remote_task_run)
    result = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/repo",
        intent="finalize current remote session",
        plan={"finalize_current": True},
        verify="pytest",
        dry_run=False,
    )

    assert result["applied"] is False
    assert "FINALIZED" in result["message"]
    assert "NO mutation step" not in result["message"]


def test_mcp_remote_task_run_preserves_failed_step_message(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_task_run
    import apatch.remote.orchestrator as orchestrator

    def fake_remote_task_run(*_args, **_kwargs):
        return {
            "ok": False,
            "timeline": [
                {"operation": "apatch_apply_session", "ok": False, "result": {"ok": False}}
            ],
            "failed_step": "apatch_apply_session",
            "message": "apply_session: in-progress session for old logs",
            "recommended_action": "retry_chunk",
        }

    monkeypatch.setattr(orchestrator, "remote_task_run", fake_remote_task_run)
    result = apatch_remote_task_run(
        remote_target="ssh://example-search-host/srv/repo",
        intent="x",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        dry_run=False,
    )

    assert result["ok"] is False
    assert result["message"] == "apply_session: in-progress session for old logs"
    assert "NO mutation step" not in result["message"]


def test_mcp_remote_service_action_plans_alias_policy(tmp_path):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_service_action

    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "search-example": {
                        "host": "example-search-host",
                        "path": "/srv/example/search-workspace",
                        "services": {
                            "api": {
                                "kind": "systemd",
                                "allowed": ["status", "restart", "logs"],
                                "unit": "search-api.service",
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = apatch_remote_service_action(
        alias="search-example",
        service="api",
        action="restart",
        target_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["approval_boundary"] == "single_service_action"
    assert result["approval_prompts_expected"] == 0
    assert result["argv"] == ["systemctl", "restart", "search-api.service"]
    assert "example-search-host" not in json.dumps(result)
    assert "/srv/example/search-workspace" not in json.dumps(result)


def test_mcp_remote_service_action_failure_stays_idle(monkeypatch):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    import apatch.remote.services as services
    from apatch.mcp.server import apatch_remote_service_action

    def fake_execute_service_action(**_kwargs):
        return {
            "ok": False,
            "error_type": "REMOTE_SERVICE_COMMAND_FAILED",
            "stderr": "bad command",
        }

    monkeypatch.setattr(services, "execute_service_action", fake_execute_service_action)

    result = apatch_remote_service_action(
        alias="prod",
        service="search",
        action="exec",
        command="false",
        execute=True,
    )

    assert result["ok"] is False
    assert result["state_update"]["phase"] == "idle"
    assert "stdout/stderr/status" in result["recommended_action"]
    assert "rollback" not in result["recommended_action"].lower()


def test_mcp_remote_source_handoff_plans_alias_policy(tmp_path):
    import pytest

    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp.server import apatch_remote_source_handoff

    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "targets": {
                    "search-example": {
                        "host": "example-search-host",
                        "path": "/srv/example/search-workspace",
                        "source_handoff": {
                            "enabled": True,
                            "local_roots": [str(tmp_path)],
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = apatch_remote_source_handoff(
        alias="search-example",
        source_dir=str(tmp_path),
        target_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["approval_boundary"] == "single_source_handoff"
    assert result["approval_prompts_expected"] == 0
    serialized = json.dumps(result, ensure_ascii=False)
    assert "example-search-host" not in serialized
    assert "/srv/example/search-workspace" not in serialized
    assert str(tmp_path) not in serialized
    assert "github" not in serialized.lower()
    assert "credential" not in serialized.lower()
    assert "ssh" not in serialized.lower()


def test_r2_governed_mutation_tools_route_remote():
    transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {
                "ok": True,
                "logs_path": ".apatch/remote/patches.jsonl",
            },
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
        }
    )
    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="route mutations",
        plan={"needles": [{"path": "x.py", "find": "a", "replace": "b"}]},
        verify={"command": "pytest"},
        transport=transport,
    )
    assert result["ok"] is True
    ops = [c["operation"] for c in transport.calls]
    # generate/simulate/apply/attest/session_end all execute remotely
    for op in (
        "apatch_generate_batch",
        "apatch_simulate",
        "apatch_apply_session",
        "apatch_attest",
        "apatch_session_end",
    ):
        assert op in ops
    # generate_batch logs_path is reused by simulate without local translation
    assert transport.calls[3]["arguments"]["logs_path"] == ".apatch/remote/patches.jsonl"


def test_r1_doctor_and_session_start_remote_route():
    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {"ok": True, "workspace": "/srv/repo", "version": "0.7.0"},
            "apatch_session_start": {"ok": True, "session_id": "remote_sess"},
        }
    )
    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="route doctor and session start",
        transport=transport,
    )
    assert result["ok"] is True
    ops = [c["operation"] for c in transport.calls]
    # doctor and session_start are the first remote-routed operations
    assert ops[0] == "apatch_doctor"
    assert ops[1] == "apatch_session_start"
    # responses carry remote identity; no local session_state drives the remote repo
    assert result["target"]["host"] == "example-search-host"
    assert result["timeline"][1]["operation"] == "apatch_session_start"
    assert result["timeline"][1]["result"]["session_id"] == "remote_sess"


def test_r4_remote_audit_metadata():
    import os

    transport = FakeRemoteTransport(
        {
            "apatch_doctor": {
                "ok": True,
                "git_head": "abc123",
                "version": "0.7.0",
                "workspace": "/srv/repo",
            }
        }
    )
    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="audit metadata",
        transport=transport,
    )
    assert result["ok"] is True
    # remote executor identity is recorded (host/root/workspace id)
    tgt = result["target"]
    assert tgt["host"] == "example-search-host"
    assert tgt["path"] == "/srv/repo"
    assert tgt["workspace_id"]
    # remote doctor metadata (git head / version) survives into the audit timeline
    doctor_result = result["timeline"][0]["result"]
    assert doctor_result["git_head"] == "abc123"
    assert doctor_result["version"] == "0.7.0"
    # the local controller path is never recorded as the remote workspace
    assert os.getcwd() not in json.dumps(result)


def test_r6_codex_remote_docs_present():
    import os

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    text = ""
    for name in (
        "remote-onboarding.md",
        "mcp_setup.md",
        "RFP-029-remote-ssh-workspaces.md",
    ):
        path = os.path.join(root, "docs", name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text += f.read().lower()
    assert text, "remote setup docs must exist"
    # local MCP stays local; remote work is selected by target URI, not cwd change
    assert "codex" in text
    assert "ssh://" in text
    assert "target_dir" in text
    # the broker model warns against ad-hoc remote shell editing
    assert "broker" in text


def test_r3_remote_session_state_authoritative():
    from apatch.remote.doctor import remote_session_state
    from apatch.remote.target import build_remote_target

    target = build_remote_target("example-search-host", "/srv/repo", alias="x")
    transport = FakeRemoteTransport(
        {
            "apatch_session_state": {
                "ok": True,
                "lifecycle": "verifying",
                "session_id": "remote_sess",
            }
        }
    )

    # state read is always routed to the remote worker (authoritative)
    out = remote_session_state(target, transport=transport)
    assert out["ok"] is True
    assert out["authoritative"] == "remote"
    assert out["lifecycle"] == "verifying"
    assert out["remote"]["host"] == "example-search-host"

    # a follow-up call re-fetches from the remote, never a local ghost
    remote_session_state(target, transport=transport)
    assert [c["operation"] for c in transport.calls] == [
        "apatch_session_state",
        "apatch_session_state",
    ]


def test_r5_mixed_workspace_rejected():
    transport = FakeRemoteTransport()
    # a local absolute logs path passed to a remote apply is rejected; no apply runs
    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="mixed state",
        plan={
            "needles": [{"path": "x.py", "find": "a", "replace": "b"}],
            "logs_path": "/Users/edcher/local/patches.jsonl",
        },
        transport=transport,
    )
    assert result["ok"] is False
    assert result["error_type"] in {"WORKSPACE_MISMATCH", "REMOTE_PROTOCOL_ERROR"}
    assert result["failed_step"] == "preflight"
    assert transport.calls == []

    # a remote-relative logs path is accepted
    ok_transport = FakeRemoteTransport(
        {
            "apatch_generate_batch": {
                "ok": True,
                "logs_path": ".apatch/remote/patches.jsonl",
            }
        }
    )
    ok = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="consistent state",
        plan={
            "needles": [{"path": "x.py", "find": "a", "replace": "b"}],
            "logs_path": ".apatch/remote/patches.jsonl",
        },
        transport=ok_transport,
    )
    assert ok["ok"] is True


def test_r0_remote_mcp_rfp_coverage():
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
        os.path.join(root, "docs", "specs", "SPEC-REMOTE-SSH-MCP-1.md"),
        encoding="utf-8",
    ) as f:
        spec = f.read()
    out = rfp_spec_coverage(
        rfp, spec, rfp_id="RFP-029", spec_id="SPEC-REMOTE-SSH-MCP-1"
    )
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r7_remote_task_run_fails_closed_without_ending_active_session():
    transport = FakeRemoteTransport(
        {
            "apatch_session_start": {
                "ok": False,
                "error": "active session exists",
                "session_id": "foreign-live",
            },
            "apatch_session_state": {
                "ok": True,
                "session": {
                    "session_id": "foreign-live",
                    "lifecycle": "applying",
                    "revision": 7,
                    "started_at": "2026-07-29T10:00:00Z",
                    "intent": "other agent mutation",
                },
            },
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="must not take over a live session",
        verify="pytest",
        transport=transport,
    )

    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_SESSION_BUSY"
    assert result["active_session"] == {
        "session_id": "foreign-live",
        "lifecycle": "applying",
        "revision": 7,
        "started_at": "2026-07-29T10:00:00Z",
        "intent": "other agent mutation",
    }
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_session_state",
    ]
    assert "apatch_session_end" not in [
        step["operation"] for step in result["timeline"]
    ]


def test_r7_remote_task_run_compacts_large_needles_in_timeline():
    payload = "private-find-and-replace-" * 2000
    transport = FakeRemoteTransport(
        {
            "apatch_session_start": {
                "ok": True,
                "session": {"session_id": "batch"},
                "session_token": "secret",
            },
            "apatch_generate_batch": {
                "ok": True,
                "logs_path": ".apatch/tmp/batch/patches.jsonl",
            },
            "apatch_apply_session": {"ok": True, "continue": False, "applied": 1},
            "apatch_verify_run": {"ok": True, "verified": True,
                                  "requirement_verification": {"complete": True}},
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="compact a large governed plan",
        plan={
            "needles": [
                {
                    "action": "replace",
                    "target_file": "src/large.py",
                    "find_text": payload,
                    "replace_text": payload[::-1],
                }
            ]
        },
        verify="pytest",
        transport=transport,
    )

    encoded = json.dumps(result, ensure_ascii=False)
    assert payload not in encoded
    assert payload[::-1] not in encoded
    assert len(encoded) < 20_000
    summaries = [
        step["arguments"]["plan"]["needles_summary"]
        for step in result["timeline"]
        if isinstance(step.get("arguments", {}).get("plan"), dict)
        and "needles_summary" in step["arguments"]["plan"]
    ]
    assert summaries
    assert all(summary["count"] == 1 for summary in summaries)
    assert all(summary["target_files"] == ["src/large.py"] for summary in summaries)
    assert len({summary["sha256"] for summary in summaries}) == 1


def test_remote_task_run_closes_generic_session_after_pre_mutation_failure():
    transport = FakeRemoteTransport(
        {
            "apatch_session_start": {
                "ok": True,
                "session": {"session_id": "draft"},
                "session_token": "secret",
            },
            "apatch_generate_batch": {
                "ok": False,
                "error_type": "SPEC_WORKFLOW_REQUIRED",
                "message": "exact SPEC requirement required",
                "recommended_action": "use_spec_workflow",
                "generation_started": False,
            },
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="generic plan rejected by SPEC ownership",
        plan={
            "needles": [
                {
                    "action": "replace",
                    "target_file": "docs/specs/slug_contracts/x.yaml",
                    "find_text": "a",
                    "replace_text": "b",
                }
            ]
        },
        transport=transport,
    )

    assert result["ok"] is False
    assert result["error_type"] == "SPEC_WORKFLOW_REQUIRED"
    assert result["session_cleanup"] == {
        "attempted": True,
        "ok": True,
        "reason": "pre_mutation_failure",
    }
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_session_start",
        "apatch_generate_batch",
        "apatch_session_end",
    ]
    cleanup_args = transport.calls[-1]["arguments"]
    assert cleanup_args["governed_session_id"] == "draft"
    assert cleanup_args["session_token"] == "secret"
    assert cleanup_args["_auto_recovery"] == "pre_mutation_failure"


def test_remote_task_run_closes_targeted_session_after_pre_mutation_failure():
    transport = FakeRemoteTransport(
        {
            "apatch_execute_next": {
                "ok": False,
                "error_type": "APPLY_FAILED",
                "message": "anchor not found",
                "steps_completed": [
                    "lint",
                    "dependencies",
                    "session_start",
                    "generate_batch",
                ],
            },
            "apatch_session_end": {"ok": True, "status": "closed"},
        }
    )

    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="targeted mutation rejected before apply",
        plan={
            "execute_next": True,
            "spec": "SPEC-X",
            "requirement": "SPEC-X#R1",
            "needles": [{
                "action": "replace",
                "target_file": "docs/specs/SPEC-X.md",
                "find_text": "missing",
                "replace_text": "new",
            }],
        },
        transport=transport,
    )

    assert result["ok"] is False
    assert result["session_cleanup"] == {
        "attempted": True,
        "ok": True,
        "reason": "execute_next_pre_mutation_failure",
    }
    assert [call["operation"] for call in transport.calls] == [
        "apatch_doctor",
        "apatch_execute_next",
        "apatch_session_end",
    ]
    assert transport.calls[-1]["arguments"]["_auto_recovery"] == (
        "execute_next_pre_mutation_failure"
    )


def test_remote_task_run_reset_session_lever():
    transport = FakeRemoteTransport({"apatch_session_end": {"ok": True, "status": "closed"}})

    result = remote_task_run(
        "ssh://example-search-host/srv/repo",
        intent="reset stale lock",
        plan={"reset_session": True},
        transport=transport,
    )

    assert result["ok"] is True
    assert [c["operation"] for c in transport.calls] == ["apatch_doctor", "apatch_session_end"]


def test_remote_task_run_self_heal_skipped_when_session_end_denied(tmp_path):
    target = resolve_remote_target(
        "limited",
        policy_root=str(tmp_path),
        policy={
            "targets": {
                "limited": {
                    "host": "example-search-host",
                    "path": "/srv/repo",
                    "allowed_operations": ["apatch_doctor", "apatch_session_start"],
                }
            }
        },
    )
    transport = FakeRemoteTransport(
        {
            "apatch_session_start": {
                "ok": False,
                "error": "active session exists",
                "session_id": "stale",
            }
        }
    )

    result = remote_task_run(target, intent="x", transport=transport)

    assert result["ok"] is False
    assert result["failed_step"] == "apatch_session_start"
    assert "apatch_session_end" not in [c["operation"] for c in transport.calls]
