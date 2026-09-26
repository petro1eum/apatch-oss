"""The durable execution-evidence lane (TrustChain Platform PR #57).

A durable push is a three-step protocol: fetch this agent's signed evidence
head, bind the new envelope to that head's signature as ``parent_hash`` and
mark it with an evidence schema, then read the synchronous completion. Only a
``durable`` completion means the Platform committed the record.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from apatch import platform_client as P

AGENT = "agent-durable-1"
TENANT = "acme"


def _self_signed(tmp_path):
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, AGENT)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, None)
    )
    path = tmp_path / "agent.crt"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert, path


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Platform:
    """Enough of the Platform to exercise the protocol, including one rebind."""

    def __init__(self, *, public_key, cert, head_parent=None, rebind_once_to=None, head_status=200):
        self.public_key = public_key
        self.cert = cert
        self.head_parent = head_parent
        self.rebind_once_to = rebind_once_to
        self.head_status = head_status
        self.head_requests = []
        self.appends = []

    def get(self, url, headers=None, **_kw):
        assert url.endswith(f"/api/log/evidence-heads/{AGENT}")
        headers = dict(headers or {})
        self.head_requests.append(headers)
        if self.head_status != 200:
            return _Response(self.head_status, {"detail": "nope"})
        signed = {
            "schema": headers["X-TC-Head-Schema"],
            "tenant_id": headers["X-TC-Tenant-ID"],
            "agent_id": headers["X-TC-Agent-ID"],
            "nonce": headers["X-TC-Request-Nonce"],
            "requested_at_ms": int(headers["X-TC-Requested-At-Ms"]),
            "certificate_serial": headers["X-TC-Agent-Cert-Serial"],
            "certificate_fingerprint": headers["X-TC-Agent-Cert-Fingerprint"],
        }
        # Exactly what the Platform verifies: the strict canonical JSON of
        # the header identity, signed by the certificate's key.
        self.public_key.verify(
            bytes.fromhex(headers["X-TC-Agent-Signature"]),
            json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(),
        )
        assert signed["schema"] == P.EVIDENCE_HEAD_REQUEST_SCHEMA
        assert re.fullmatch(r"tchead_[0-9a-f]{32}", signed["nonce"])
        assert signed["certificate_serial"] == str(self.cert.serial_number)
        assert signed["certificate_fingerprint"] == self.cert.fingerprint(hashes.SHA256()).hex()[:24]
        return _Response(200, {
            "schema": P.EVIDENCE_HEAD_SCHEMA,
            "tenant_id": TENANT,
            "agent_id": AGENT,
            "parent_hash": self.head_parent,
            "evidence_op_id": None,
            "durable_log_op_id": None,
            "updated_at": None,
        })

    def post(self, url, json=None, **_kw):
        assert url.endswith("/api/log/append")
        body = dict(json)
        self.appends.append(body)
        envelope = P._canonical_log_envelope(
            tool=body["tool"], agent_id=body["agent_id"], data=body["data"],
            timestamp=body["timestamp"], nonce=body["nonce"],
            parent_hash=body["parent_hash"], metadata=body["metadata"],
        )
        self.public_key.verify(bytes.fromhex(body["signature"]), envelope)
        if self.rebind_once_to is not None and body["parent_hash"] != self.rebind_once_to:
            required = self.rebind_once_to
            self.head_parent = required
            return _Response(409, {
                "status": "rebind_required",
                "op_id": "logical-1",
                "completion": {"state": "rebind_required", "required_parent_hash": required},
            })
        return _Response(200, {
            "status": "completed",
            "op_id": "logical-1",
            "completion": {
                "schema": P.EVIDENCE_COMPLETION_SCHEMA,
                "state": "durable",
                "durable": True,
                "op_id": "logical-1",
                "durable_log_op_id": "log-record-7",
                "next_parent_hash": body["signature"],
                "parent_hash": body["parent_hash"],
            },
        })


def test_certificate_identity_matches_the_platform_display_form(tmp_path):
    _key, cert, path = _self_signed(tmp_path)
    identity = P.certificate_identity(str(path))
    assert identity == {
        "serial": str(cert.serial_number),
        "fingerprint": cert.fingerprint(hashes.SHA256()).hex()[:24],
    }


def test_durable_push_binds_to_the_signed_head_and_marks_the_schema(tmp_path):
    key, cert, path = _self_signed(tmp_path)
    platform = _Platform(public_key=key.public_key(), cert=cert, head_parent="prior-signature")

    result = P.push_durable_step(
        tool="apatch", data={"action": "strip", "files": 2}, base_url="https://trust-chain.ai/",
        agent_id=AGENT, tenant_id=TENANT, private_key=key,
        certificate=P.certificate_identity(str(path)), metadata={"source": "apatch"},
        http_client=platform,
    )

    assert result["ok"] is True and result["state"] == "durable"
    assert result["op_id"] == "logical-1"
    assert result["durable_log_op_id"] == "log-record-7"
    assert len(platform.head_requests) == 1 and len(platform.appends) == 1
    sent = platform.appends[0]
    assert sent["parent_hash"] == "prior-signature"
    assert sent["metadata"]["schema"] == P.EXECUTION_EVIDENCE_SCHEMA
    assert sent["metadata"]["tenant_id"] == TENANT
    assert sent["metadata"]["source"] == "apatch"
    assert sent["tenant_id"] == TENANT
    assert result["next_parent_hash"] == sent["signature"]


def test_durable_push_rebinds_once_when_the_head_moved(tmp_path):
    key, cert, path = _self_signed(tmp_path)
    platform = _Platform(
        public_key=key.public_key(), cert=cert, head_parent=None, rebind_once_to="moved-signature",
    )

    result = P.push_durable_step(
        tool="apatch", data={"action": "strip"}, base_url="https://trust-chain.ai",
        agent_id=AGENT, tenant_id=TENANT, private_key=key,
        certificate=P.certificate_identity(str(path)), http_client=platform,
    )

    assert result["ok"] is True and result["state"] == "durable"
    assert result["attempts"] == 2
    assert [a["parent_hash"] for a in platform.appends] == [None, "moved-signature"]
    # The 409 names the parent the Platform now requires; no second head
    # round-trip is needed to rebind.
    assert len(platform.head_requests) == 1


def test_durable_push_reports_a_head_it_cannot_get(tmp_path):
    key, cert, path = _self_signed(tmp_path)
    platform = _Platform(public_key=key.public_key(), cert=cert, head_status=403)

    result = P.push_durable_step(
        tool="apatch", data={"action": "strip"}, base_url="https://trust-chain.ai",
        agent_id=AGENT, tenant_id=TENANT, private_key=key,
        certificate=P.certificate_identity(str(path)), http_client=platform,
    )

    assert result["ok"] is False
    assert result["state"] == "head_unavailable"
    assert result["head"]["status"] == "head_http_403"
    assert platform.appends == []


def test_platform_config_finds_the_certificate_next_to_the_key(monkeypatch, tmp_path):
    (tmp_path / "agent.key").write_text("dummy")
    (tmp_path / "agent.crt").write_text("dummy")
    monkeypatch.setenv("APATCH_PLATFORM_URL", "https://trust-chain.ai")
    monkeypatch.setenv("APATCH_AGENT_ID", AGENT)
    monkeypatch.setenv("APATCH_AGENT_KEY", str(tmp_path / "agent.key"))
    monkeypatch.setenv("APATCH_TENANT_ID", TENANT)
    monkeypatch.delenv("APATCH_AGENT_CERT", raising=False)

    cfg = P.platform_config_from_env()

    assert cfg["cert_path"] == str(tmp_path / "agent.crt")
    assert P.durable_lane_configured(cfg) is True

    monkeypatch.delenv("APATCH_TENANT_ID")
    assert P.durable_lane_configured(P.platform_config_from_env()) is False
