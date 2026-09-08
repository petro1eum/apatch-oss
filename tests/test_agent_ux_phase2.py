"""SPEC-AGENT-UX-2 (RFP-027 Phase 2) — noop-attest, payload dedupe, staleness."""
import os

import pytest

SPEC_TEXT = """# SPEC-42 Avatar Economics

## R1 Add Sandbox (verify: pytest tests/test_sandbox.py)
Body.

## R4 Shared (verify: pytest tests/test_shared.py)
Body.
"""


def test_r0_self_coverage_rfp_027():
    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(root, "docs", "RFP-027-agent-ux-recovery.md"),
              encoding="utf-8") as f:
        rfp = f.read()
    with open(os.path.join(root, "docs", "specs", "SPEC-AGENT-UX-2.md"),
              encoding="utf-8") as f:
        spec = f.read()
    out = rfp_spec_coverage(rfp, spec, rfp_id="RFP-027", spec_id="SPEC-AGENT-UX-2")
    assert out["passed"] is True
    assert not out.get("gaps")


def test_r1_noop_attest_coverage():
    from apatch.spec import parse_spec, spec_status_from_entries

    spec = parse_spec(SPEC_TEXT)
    h1 = spec.requirements[0].content_hash
    entries = [
        {"id": "i1", "tool_id": "apatch", "timestamp": "2026-01-01T00:01:00",
         "payload": {"action": "engineering_pipeline", "intent": "SPEC-42#R1",
                     "artifacts": [{"kind": "spec", "id": "SPEC-42#R1",
                                    "content_hash": h1}]}},
        # NO mutation row — only a covered_by attestation (noop-attest)
        {"id": "a1", "tool_id": "apatch_attest", "timestamp": "2026-01-01T00:01:45",
         "payload": {"action": "attest", "covered_by": ["R4"]}},
    ]
    out = spec_status_from_entries(spec, entries)
    states = {r["id"]: r["state"] for r in out["requirements"]}
    assert states["R1"] == "attested"  # attested via covered_by, no marker file


def test_r2_payload_dedupe_once_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("APATCH_MCP_GUIDANCE", "full")
    from apatch import agent_guidance as G
    from apatch.session_state import save_session_state

    root = str(tmp_path)
    (tmp_path / ".apatch").mkdir()
    save_session_state(root, {"session_id": "sX", "intent": "i", "phase": "verify"},
                       force=True)
    G._guidance_emitted_sessions.discard("sX")
    o1 = G.attach_artifact_guidance({"ok": True}, "apatch_spec_status", target_dir=root)
    o2 = G.attach_artifact_guidance({"ok": True}, "apatch_spec_status", target_dir=root)
    assert "guidance_deduped" not in o1
    assert "protocol_contract" in o1  # heavy block emitted on first call
    assert o2.get("guidance_deduped") is True
    assert "protocol_contract" not in o2  # subsequent calls → slim guidance_ref


def test_r3_self_edit_restart_signal():
    import apatch.spec  # ensure module is imported in this process
    from apatch.mcp_health import self_edit_restart_signal

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    sig = self_edit_restart_signal(root, ["apatch/spec.py"])
    assert sig and sig["restart_required"] is True
    assert "apatch.spec" in sig["stale_modules"]
    # non-source path → no signal
    assert self_edit_restart_signal(root, ["docs/foo.md"]) is None