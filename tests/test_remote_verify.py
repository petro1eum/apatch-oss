"""SPEC-REMOTE-SSH-VERIFY-1 — Remote verify, diagnostics, and resume."""

from apatch.remote.target import build_remote_target
from apatch.remote.transport import FakeRemoteTransport
from apatch.remote.verify import (
    bound_log,
    redact_env,
    remote_resume_after_disconnect,
    remote_verify,
)


def _target():
    return build_remote_target("example-search-host", "/srv/repo", alias="x")


def test_r1_remote_verify_argv():
    transport = FakeRemoteTransport(
        {"apatch_verify_run": {"ok": True, "verified": True, "returncode": 0}}
    )

    out = remote_verify(
        _target(),
        verify={"command": ["pytest", "-k", "not slow"]},
        transport=transport,
    )
    assert out["ok"] is True
    # argv form is passed as structured data to the remote worker, not a shell string
    call = transport.calls[0]
    assert call["operation"] == "apatch_verify_run"
    assert call["arguments"]["verify"]["command"] == ["pytest", "-k", "not slow"]

    # a plain shell string is normalized into the verify command envelope
    transport2 = FakeRemoteTransport({"apatch_verify_run": {"ok": True}})
    remote_verify(_target(), verify="pytest tests/", transport=transport2)
    assert transport2.calls[0]["arguments"]["verify"]["command"] == "pytest tests/"


def test_r2_remote_verify_logs_bounded_and_redacted():
    # bound_log truncates explicitly by lines and bytes (never silently)
    long_text = "\n".join("line %d" % i for i in range(5000))
    bounded = bound_log(long_text, max_lines=100, max_bytes=1024)
    assert bounded["truncated"] is True
    assert len(bounded["text"].splitlines()) <= 100
    assert bounded["original_bytes"] > len(bounded["text"].encode("utf-8"))

    short = bound_log("ok", max_lines=100, max_bytes=1024)
    assert short["truncated"] is False
    assert short["text"] == "ok"

    # secret values are redacted out of the stream
    redacted = redact_env("token=SECRET123 ok", ["SECRET123"])
    assert "SECRET123" not in redacted
    assert "REDACTED" in redacted

    # remote_verify applies redaction + bounding to remote stdout/stderr
    transport = FakeRemoteTransport(
        {
            "apatch_verify_run": {
                "ok": True,
                "stdout": "leak=TOPSECRET done",
                "stderr": "",
            }
        }
    )
    out = remote_verify(
        _target(),
        verify={"command": "pytest"},
        transport=transport,
        secrets=["TOPSECRET"],
    )
    assert "TOPSECRET" not in out["stdout"]["text"]
    assert out["stdout"]["truncated"] is False


def test_r3_remote_baseline_capture_compare():
    # baseline='capture' is routed to the remote worker (written under remote .apatch)
    cap = FakeRemoteTransport(
        {"apatch_verify_run": {"ok": True, "baseline": "captured"}}
    )
    remote_verify(
        _target(),
        verify={"command": "pytest", "baseline": "capture"},
        transport=cap,
    )
    assert cap.calls[0]["arguments"]["verify"]["baseline"] == "capture"

    # baseline='compare' is also routed remotely; no local baseline file is implied
    cmp_t = FakeRemoteTransport(
        {"apatch_verify_run": {"ok": True, "baseline": "compared", "new_failures": []}}
    )
    out = remote_verify(
        _target(),
        verify={"command": "pytest", "baseline": "compare"},
        transport=cmp_t,
    )
    assert cmp_t.calls[0]["arguments"]["verify"]["baseline"] == "compare"
    assert out["ok"] is True


def test_r4_remote_diagnostics_shape():
    diagnostics = [
        {
            "source": "pytest",
            "type": "test_failure",
            "severity": "error",
            "location": {"file": "/srv/repo/tests/test_x.py", "symbol": "test_x"},
        }
    ]
    transport = FakeRemoteTransport(
        {
            "apatch_verify_run": {
                "ok": False,
                "diagnostics": diagnostics,
                "stdout": "F",
            }
        }
    )
    out = remote_verify(_target(), verify={"command": "pytest"}, transport=transport)
    # diagnostics[] survive transport unchanged, remote file paths preserved
    assert out["diagnostics"] == diagnostics
    assert out["diagnostics"][0]["location"]["file"] == "/srv/repo/tests/test_x.py"
    # stdout is still returned in bounded/structured form
    assert out["stdout"]["text"] == "F"


def test_r5_remote_disconnect_resume():
    # after a disconnect, the resume decision re-reads authoritative remote state
    transport = FakeRemoteTransport(
        {
            "apatch_session_state": {
                "ok": True,
                "lifecycle": "verifying",
                "session_id": "remote_sess",
            }
        }
    )
    out = remote_resume_after_disconnect(_target(), transport=transport)
    assert out["ok"] is True
    assert out["authoritative"] == "remote"
    assert out["lifecycle"] == "verifying"
    assert "poll" in out["next_action"] or "resume" in out["next_action"]
    # local state never forces a rollback
    assert out["local_state_forced_rollback"] is False
    assert transport.calls[0]["operation"] == "apatch_session_state"

    # a failed-lifecycle remote yields a resume + re-verify next action
    failed = FakeRemoteTransport(
        {"apatch_session_state": {"ok": True, "lifecycle": "failed"}}
    )
    out2 = remote_resume_after_disconnect(_target(), transport=failed)
    assert "resume_session" in out2["next_action"]


def test_r0_remote_verify_rfp_coverage():
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
        os.path.join(root, "docs", "specs", "SPEC-REMOTE-SSH-VERIFY-1.md"),
        encoding="utf-8",
    ) as f:
        spec = f.read()
    out = rfp_spec_coverage(
        rfp, spec, rfp_id="RFP-029", spec_id="SPEC-REMOTE-SSH-VERIFY-1"
    )
    assert out["passed"] is True
    assert not out.get("gaps")