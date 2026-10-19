"""F02 command boundary: public fixture results only; no Secrets or Touch ID."""
import base64
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch.trust_identity import CommandKeyProvider, CommandSigningError, workspace_identity_scope
from apatch.trustchain_helper import TrustChainHelper
from tests.test_workspace_signer_scope import workspace_pin

BINDING = "99999999-9999-4999-8999-999999999999"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class FixtureBroker:
    """Model response loss after completion, with a count of actual sign effects."""
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.receipt_key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.results = {}
        self.signs = 0
        self.requests = []
        self.lose_response = False
        self.mutate = lambda value: None

    def provider(self):
        return CommandKeyProvider("fixture-signer " + BINDING + " --result-json", self.public, "fixture")

    def invoke(self, argv, **kwargs):
        request_id = kwargs["env"]["APATCH_SIGNING_REQUEST_ID"]
        data = kwargs["input"]
        self.requests.append(request_id)
        if request_id not in self.results:
            self.signs += 1
            signature = self.key.sign(data)
            digest = hashlib.sha256(data).hexdigest()
            attestation_public = base64.b64encode(self.receipt_key.public_key().public_bytes_raw()).decode()
            envelope = {"version": 1, "request_id": request_id, "binding_id": BINDING,
                "payload_sha256": digest, "result_sha256": hashlib.sha256(signature).hexdigest(),
                "receipt_id": request_id, "outcome": "success"}
            statement = {"domain": "trustchain-secrets/broker-attestation-key/v1",
                "binding_id": BINDING, "attestation_public_key_b64": attestation_public}
            self.results[request_id] = {"schema": "trustchain.secrets.signing-result.v1",
                "request_id": request_id, "binding_id": BINDING, "payload_sha256": digest,
                "signature_b64": base64.b64encode(signature).decode(), "public_key_b64": self.public,
                "receipt": {"id": request_id, "receipt_envelope_json": canonical(envelope).decode(),
                    "attestation_public_key_b64": attestation_public,
                    "attestation_key_proof_b64": base64.b64encode(self.key.sign(canonical(statement))).decode(),
                    "receipt_signature_b64": base64.b64encode(self.receipt_key.sign(canonical(envelope))).decode()}}
        if self.lose_response:
            self.lose_response = False
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        result = json.loads(json.dumps(self.results[request_id]))
        self.mutate(result)
        return subprocess.CompletedProcess(argv, 0, canonical(result), b"")


def test_lost_signing_response_recovers_same_result_after_provider_restart(monkeypatch):
    broker = FixtureBroker()
    monkeypatch.setattr(subprocess, "run", broker.invoke)
    broker.lose_response = True
    with pytest.raises(CommandSigningError) as lost:
        broker.provider().sign(b"exact operation bytes")
    request = lost.value.to_dict()["signing_request"]
    assert lost.value.reconciliation_required is True
    assert broker.signs == 1
    assert broker.requests == [request["request_id"]]

    recovered = broker.provider()
    signature = recovered.sign(b"exact operation bytes")
    assert recovered.verify(b"exact operation bytes", signature)
    assert recovered.last_signing_result["request_id"] == request["request_id"]
    assert recovered.last_signing_result["receipt_id"] == request["request_id"]
    assert broker.requests == [request["request_id"], request["request_id"]]
    assert broker.signs == 1


@pytest.mark.parametrize("supported", [False, True])
def test_native_result_protocol_is_negotiated_without_changing_configuration(monkeypatch, supported):
    broker = FixtureBroker()
    probes = []
    def negotiate(argv, **kwargs):
        if argv[-1] == "--capabilities":
            probes.append(argv)
            capabilities = {"schema": "trustchain.signer-capabilities.v1",
                "features": ["signing-result-v1"] if supported else ["policy-project-admission-v1"],
                "side_effects": {"sign": False, "keychain": False, "socket": False}}
            return subprocess.CompletedProcess(argv, 0, canonical(capabilities), b"")
        return broker.invoke(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", negotiate)
    command = "/fixture/TrustChainSecretsSigner " + BINDING
    provider = CommandKeyProvider(command, broker.public, "fixture")
    assert probes == [["/fixture/TrustChainSecretsSigner", "--capabilities"]]
    assert broker.signs == 0
    if supported:
        assert provider.verify(b"fixture", provider.sign(b"fixture"))
        assert provider.last_signing_result is not None
    else:
        assert provider._cmd == ["/fixture/TrustChainSecretsSigner", BINDING]
        assert provider._secrets_binding is None


def test_pending_signing_request_is_reported_without_another_attempt(monkeypatch):
    broker = FixtureBroker()
    requests = []
    def pending(argv, **kwargs):
        request_id = kwargs["env"]["APATCH_SIGNING_REQUEST_ID"]
        requests.append(request_id)
        error = {"request_id": request_id, "error_code": "SIGNING_RESULT_UNKNOWN",
                 "message": "request exists; do not repeat signing"}
        stderr = b"trustchain-secrets-signer: SECRETS_SIGNING_ERROR " + canonical(error)
        return subprocess.CompletedProcess(argv, 2, b"", stderr)
    monkeypatch.setattr(subprocess, "run", pending)
    provider = broker.provider()
    with pytest.raises(CommandSigningError) as caught:
        provider.sign(b"exact operation bytes")
    evidence = caught.value.to_dict()
    assert evidence["cause"]["error_code"] == "SIGNING_RESULT_UNKNOWN"
    assert evidence["signing_request"]["request_id"] == requests[0]
    assert evidence["reconciliation_required"] is True
    assert len(requests) == 1 and provider.last_signing_result is None


@pytest.mark.parametrize("defect", ["request", "binding", "payload", "signature", "receipt", "proof"])
def test_signing_result_cannot_be_substituted(monkeypatch, defect):
    broker = FixtureBroker()
    def alter(result):
        if defect == "request": result["request_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        if defect == "binding": result["binding_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        if defect == "payload": result["payload_sha256"] = "f" * 64
        if defect == "signature": result["signature_b64"] = base64.b64encode(b"x" * 64).decode()
        if defect == "receipt": result["receipt"]["id"] = "other-receipt"
        if defect == "proof": result["receipt"]["attestation_key_proof_b64"] = base64.b64encode(b"x" * 64).decode()
    broker.mutate = alter
    monkeypatch.setattr(subprocess, "run", broker.invoke)
    provider = broker.provider()
    with pytest.raises(CommandSigningError) as invalid:
        provider.sign(b"exact operation bytes")
    assert invalid.value.code == "SIGN_COMMAND_RESPONSE_INVALID"
    assert invalid.value.reconciliation_required is True
    assert provider.last_signing_result is None
    assert broker.signs == 1 and len(broker.requests) == 1


def test_verified_signing_request_and_receipt_reach_commit_evidence(tmp_path, monkeypatch):
    broker = FixtureBroker()
    root = workspace_pin(tmp_path / "project", backend="command")
    path = root / ".apatch/agent-identity.json"
    identity = json.loads(path.read_text())
    identity.update(sign_cmd="fixture-signer " + BINDING + " --result-json", public_key=broker.public)
    path.write_text(json.dumps(identity))
    monkeypatch.setattr(subprocess, "run", broker.invoke)
    with workspace_identity_scope(str(root)):
        helper = TrustChainHelper(str(root), auto_init=False)
        assert helper.commit_action("apatch", {"action": "fixture-only"})
        evidence = helper.last_commit_evidence
        assert evidence["persisted"] is True
        assert evidence["signing_request"]["request_id"] == broker.requests[0]
        assert evidence["signing_result"]["request_id"] == broker.requests[0]
        assert evidence["signing_result"]["receipt_id"] == broker.requests[0]
        assert broker.signs == 1


def test_unknown_signing_request_is_retained_in_blocked_checkpoint(tmp_path):
    from apatch.notarization_failure import recover_notarization_exception
    folder = tmp_path / ".apatch/backups/checkpoint"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"session_id": "checkpoint",
        "governed_session_id": "session", "backups": []}))
    request = {"request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "binding_id": BINDING,
               "payload_sha256": "a" * 64}
    tui = SimpleNamespace(target_dir=str(tmp_path.resolve()), report_entries=[],
        backup_mgr=SimpleNamespace(session_id="checkpoint", governed_session_id="session"),
        tc_helper=SimpleNamespace(ledger_head=lambda: None,
            last_commit_evidence={"signing_request": request, "reconciliation_required": True}),
        _write_report=lambda: None)
    failure = recover_notarization_exception(tui, RuntimeError("lost response"), None, {})
    assert failure.result["signing_request"] == request
    assert failure.result["continue"] is False and failure.result["phase"] == "blocked"
    assert failure.result["reconciliation_required"] is True
