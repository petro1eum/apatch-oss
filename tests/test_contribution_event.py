"""Tests for apatch.contribution (SPEC-CONTRIB-TIMESHEET-1 R1-R3)."""
import base64
import datetime
import json
import os

import pytest

pytest.importorskip("avatar_contract")  # contribution emission requires the shared schema

from apatch import contribution as C


def _fake_rows(session_id="apatch_sess_1", n=3):
    return [
        {"id": "op_%d" % i, "timestamp": "2026-06-18T10:00:0%d+00:00" % i,
         "payload": {"governed_session_id": session_id,
                     "files": {"a.py": {}, "b.py": {}},
                     "insertions": 5, "deletions": 2}}
        for i in range(n)
    ]


def _session(session_id="apatch_sess_1"):
    return {
        "session_id": session_id,
        "intent": "implement X",
        "artifacts": ["spec:SPEC-X#R1"],
        "started_at": "2026-06-18T10:00:00+00:00",
        "ended_at": "2026-06-18T10:30:00+00:00",
    }


def test_r1_schema(monkeypatch):
    monkeypatch.setattr(C, "resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 32, "cert_fingerprint": "sha256:deadbeef",
        "agent_id": "tester", "ca": "platform", "trust_level": "attested"})
    monkeypatch.setattr(C, "project_identity", lambda *_a, **_k: {
        "id": "proj01", "name": "apatch", "remote": None})
    ev = C.build_event(_session(), target_dir=".", ledger_rows=_fake_rows())
    d = ev.to_dict()
    # ADR-006 shape (C26-A / C26-J); emission follows the shared contract version.
    assert d["schema_version"] == C.current_schema_version()
    if d["schema_version"] >= 3:
        created_at = datetime.datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
        assert created_at.tzinfo is not None and created_at.utcoffset() is not None
    assert d["kind"] == "fact"
    assert d["source"] == "apatch"
    assert d["trust_level"] == "attested"
    assert d["idempotency_key"] == d["event_id"]
    assert len(d["event_id"]) == 32
    assert d["identity"]["key_id"] == "k" * 32
    assert d["project"]["id"] == "proj01"
    assert d["session"]["duration_sec"] == 1800.0
    assert d["volume"] == {"ops": 3, "files_touched": 2, "insertions": 15, "deletions": 6}
    assert d["proof_ref"]["op_ids"] == ["op_0", "op_1", "op_2"]
    assert d["avatar_id"] == "k" * 32  # cross-org avatar anchor = identity key_id
    assert datetime.datetime.fromisoformat(d["created_at"]).tzinfo is not None
    assert "attestation" not in d  # current schema no longer emits the v1 key
    # event_id deterministic
    ev2 = C.build_event(_session(), target_dir=".", ledger_rows=_fake_rows())
    assert ev2.event_id == ev.event_id
    # canonical excludes signature
    assert "signature" not in ev.canonical()


def test_exact_spec_and_explicit_completion_create_review_submission(tmp_path):
    spec_dir = tmp_path / "docs" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC-REVIEW-1.md").write_text(
        "# SPEC-REVIEW-1 - Public delivery\n\n"
        "## R1 Deliver result\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    from apatch.spec import resolve_requirement

    resolved = resolve_requirement(str(tmp_path), "SPEC-REVIEW-1#R1")
    session = _session()
    session["intent"] = "private customer prompt and internal command"
    session["artifacts"] = [resolved["artifact"]]
    rows = _fake_rows()
    rows.append({
        "id": "op_gate",
        "tool_id": "apatch_probe",
        "payload": {
            "action": "probe_falsify",
            "gate_quality": "falsified",
            "verify_sha256": "a" * 64,
        },
    })
    event = C.build_event(
        session,
        target_dir=str(tmp_path),
        identity={
            "key_id": "k" * 32,
            "agent_id": "tester",
            "ca": "legacy",
            "trust_level": "claimed",
        },
        project={"id": "p", "name": "demo", "remote": None},
        ledger_rows=rows,
        completion_summary="  Delivered the reviewable result.  ",
    ).to_dict()

    serialized = json.dumps(event)
    assert "private customer prompt" not in serialized
    assert event["session"]["intent"] == "SPEC-REVIEW-1 - Public delivery"
    assert event["payload"]["review_submission"] == {
        "schema_version": 1,
        "objective": "SPEC-REVIEW-1 - Public delivery",
        "delivery_summary": "Delivered the reviewable result.",
        "acceptance_criteria": ["Deliver result"],
        "artifact_refs": [resolved["artifact"]],
        "limitations": [],
    }


def test_v3_created_at_is_part_of_the_signature():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    pub_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    event = C.build_event(
        _session(),
        identity={
            "key_id": "k" * 32,
            "agent_id": "tester",
            "ca": "legacy",
            "trust_level": "claimed",
        },
        project={"id": "p", "name": "demo", "remote": None},
        ledger_rows=_fake_rows(),
        created_at="2026-07-18T12:00:00+00:00",
    )
    C.sign_event(event, type("Signer", (), {"sign": staticmethod(key.sign)})())
    raw = event.to_dict()
    assert C.verify_event(raw, pub_raw)
    raw["created_at"] = "2026-07-18T12:00:01+00:00"
    assert not C.verify_event(raw, pub_raw)


def test_recorded_probe_quality_enters_signed_event_payload(monkeypatch):
    monkeypatch.setattr(C, "resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 32, "cert_fingerprint": "sha256:deadbeef",
        "agent_id": "tester", "ca": "platform", "trust_level": "attested"})
    monkeypatch.setattr(C, "project_identity", lambda *_a, **_k: {
        "id": "proj01", "name": "apatch", "remote": None})
    rows = _fake_rows()
    rows.append({
        "id": "op_gate",
        "tool_id": "apatch_probe",
        "payload": {
            "action": "probe_falsify",
            "gate_quality": "falsified",
            "verify_sha256": "a" * 64,
        },
    })

    event = C.build_event(_session(), target_dir=".", ledger_rows=rows)
    assert event.to_dict()["payload"] == {
        "gate_quality": "falsified",
        "gate_evidence_refs": ["op_gate"],
    }

    rows.append({
        "id": "op_false",
        "tool_id": "apatch_probe",
        "payload": {
            "action": "probe_falsify",
            "gate_quality": "false_gate",
        },
    })
    unsafe = C.build_event(_session(), target_dir=".", ledger_rows=rows)
    assert unsafe.to_dict()["payload"]["gate_quality"] == "false_gate"


def test_latest_signed_result_supersedes_same_gate_only(monkeypatch):
    monkeypatch.setattr(C, "resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 32, "cert_fingerprint": "sha256:deadbeef",
        "agent_id": "tester", "ca": "platform", "trust_level": "attested"})
    monkeypatch.setattr(C, "project_identity", lambda *_a, **_k: {
        "id": "proj01", "name": "apatch", "remote": None})
    rows = _fake_rows()
    gate = {"action": "probe_falsify", "verify_sha256": "b" * 64, "files": ["x.py"]}
    rows.extend([
        {"id": "op_false", "tool_id": "apatch_probe",
         "payload": {**gate, "gate_quality": "false_gate"}},
        {"id": "op_fixed", "tool_id": "apatch_probe",
         "payload": {**gate, "gate_quality": "falsified"}},
    ])

    event = C.build_event(_session(), target_dir=".", ledger_rows=rows)
    assert event.to_dict()["payload"] == {
        "gate_quality": "falsified",
        "gate_evidence_refs": ["op_fixed"],
    }

    rows.append({
        "id": "op_other_false",
        "tool_id": "apatch_probe",
        "payload": {
            "action": "probe_falsify",
            "verify_sha256": "c" * 64,
            "files": ["y.py"],
            "gate_quality": "false_gate",
        },
    })
    blocked = C.build_event(_session(), target_dir=".", ledger_rows=rows)
    assert blocked.to_dict()["payload"]["gate_quality"] == "false_gate"


def test_r10_v1_receipt_backcompat(tmp_path):
    """A pre-migration v1 receipt (carrying `attestation`, schema_version 1) must
    still verify unchanged under current code — the signature is over the raw event
    minus `signature`, so no key branching is needed (SPEC R10)."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    pub_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    # Hand-build a v1-shaped event exactly as the old code would have stored it.
    v1 = {
        "schema_version": 1, "kind": "contribution", "event_id": "e" * 32,
        "source": "apatch", "trust_level": "attested", "idempotency_key": "e" * 32,
        "identity": {"key_id": "k" * 32}, "project": {"id": "proj01"},
        "session": {"session_id": "s1"}, "volume": {"ops": 1},
        "attestation": {"op_ids": ["op_0"], "head": "op_0", "committed_at": 1.0},
    }
    sig = key.sign(C._canonical(v1).encode("utf-8"))
    v1["signature"] = base64.b64encode(sig).decode("ascii")
    # v1 receipt verifies under current code, no re-signing
    assert C.verify_event(v1, pub_raw) is True

    # timesheet --verify treats both v1 (attestation) and v2 (proof_ref) keys as known
    from apatch.timesheet import _ALLOWED_EVENT_KEYS
    assert {"attestation", "proof_ref"} <= _ALLOWED_EVENT_KEYS


def test_r2_emit_on_session_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "resolve_identity", lambda *_a, **_k: {
        "key_id": "abc123", "cert_fingerprint": None,
        "agent_id": "tester", "ca": "legacy", "trust_level": "audit"})
    monkeypatch.setattr(C, "project_identity", lambda *_a, **_k: {
        "id": "proj01", "name": "apatch", "remote": None})
    # zero mutations -> no event
    monkeypatch.setattr(C, "_ledger_rows_for_session", lambda *_a, **_k: [])
    assert C.emit_contribution(".", _session(), store_dir=str(tmp_path)) is None
    assert not list(tmp_path.glob("**/*.json"))
    # with mutations -> signed receipt written under the identity key_id
    monkeypatch.setattr(C, "_ledger_rows_for_session", lambda *_a, **_k: _fake_rows())
    out = C.emit_contribution(".", _session(), store_dir=str(tmp_path))
    assert out is not None
    written = list((tmp_path / "abc123").glob("*.json"))
    assert len(written) == 1
    assert written[0].name == out["event_id"] + ".json"
    first_created_at = out["created_at"]
    monkeypatch.setattr(C, "_utc_now_iso", lambda: "2099-01-01T00:00:00+00:00")
    replay = C.emit_contribution(".", _session(), store_dir=str(tmp_path))
    assert replay == out
    assert replay["created_at"] == first_created_at
    assert len(list((tmp_path / "abc123").glob("*.json"))) == 1


def test_attested_receipt_is_never_written_unsigned(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "resolve_identity", lambda *_a, **_k: {
        "key_id": "k" * 32, "cert_fingerprint": "sha256:dead",
        "agent_id": "tester", "ca": "platform", "trust_level": "attested"})
    monkeypatch.setattr(C, "project_identity", lambda *_a, **_k: {
        "id": "proj01", "name": "apatch", "remote": None})
    monkeypatch.setattr(C, "_ledger_rows_for_session", lambda *_a, **_k: _fake_rows())
    monkeypatch.setattr("apatch.trust_identity.load_local_identity", lambda _root: None)
    with pytest.raises(RuntimeError, match="without Ed25519 signature"):
        C.emit_contribution(".", _session(), store_dir=str(tmp_path))
    assert not list(tmp_path.glob("**/*.json"))


def test_r3_identity_from_cert(tmp_path, monkeypatch):
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "agent.key"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tester")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2020, 1, 1))
            .not_valid_after(datetime.datetime(2040, 1, 1)).sign(key, None))
    cert_path = tmp_path / "agent.crt"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    monkeypatch.setenv("APATCH_AGENT_ID", "tester")
    monkeypatch.setenv("APATCH_AGENT_KEY", str(key_path))
    monkeypatch.setenv("APATCH_AGENT_CERT", str(cert_path))
    ident = C.resolve_identity(str(tmp_path))
    assert ident["ca"] == "platform"
    assert ident["trust_level"] == "attested"
    assert len(ident["key_id"]) == 32
    assert ident["cert_fingerprint"] and ident["cert_fingerprint"].startswith("sha256:")

    # round-trip signature with the real key
    ev = C.build_event(_session(), target_dir=str(tmp_path), identity=ident,
                       project={"id": "p", "name": "x", "remote": None},
                       ledger_rows=_fake_rows())
    from apatch.trust_identity import load_local_identity
    li = load_local_identity(str(tmp_path))
    C.sign_event(ev, li.key_provider)
    assert C.verify_event(ev.to_dict(), li.key_provider.get_public_key())

    # legacy fallback (no cert/key)
    monkeypatch.delenv("APATCH_AGENT_KEY", raising=False)
    monkeypatch.delenv("APATCH_AGENT_CERT", raising=False)
    leg = C.resolve_identity(str(tmp_path))
    assert leg["ca"] == "legacy"
    # canon §7.2/§8: un-enrolled floor is 'claimed' (was 'audit', not a canon level)
    assert leg["trust_level"] == "claimed"
