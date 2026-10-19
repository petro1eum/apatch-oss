"""F03: negotiated deadlines and virtual late replies, without UI or Secrets."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from apatch.trust_identity import CommandKeyProvider, CommandSigningError
from tests.test_signing_result_recovery import BINDING, FixtureBroker, canonical

BUDGET = {"schema": "trustchain.secrets.signing-budget.v1",
    "presentation_seconds": 5, "consent_seconds": 20, "biometric_seconds": 60,
    "metadata_seconds": 45, "processing_seconds": 10, "helper_seconds": 140,
    "client_reserve_seconds": 10}


def capabilities(budget):
    return {"schema": "trustchain.signer-capabilities.v1",
        "features": ["signing-result-v1", "signing-budget-v1"], "signing_budget": budget,
        "side_effects": {"sign": False, "keychain": False, "socket": False}}


@pytest.fixture(scope="module")
def native_capabilities(tmp_path_factory):
    source_root = os.environ.get("SECRETS_SOURCE_ROOT")
    if not source_root:
        pytest.skip("SECRETS_SOURCE_ROOT selects the existing native source for this cross-repo check")
    macos = Path(source_root) / "local/secrets-console/macos"
    binary = tmp_path_factory.mktemp("signer-capabilities") / "signer"
    compiled = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5",
        str(macos / "BrokerSigningBudget.swift"), str(macos / "BrokerSignerMain.swift"), "-o", str(binary)],
        capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    # The production --capabilities branch returns before socket/Keychain/UI.
    result = subprocess.run([str(binary), "--capabilities"], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("configured", [None, "1", "90", "120"])
@pytest.mark.parametrize("explicit_result_protocol", [False, True])
def test_late_native_reply_fits_outer_timeout(monkeypatch, native_capabilities, configured, explicit_result_protocol):
    if configured is None:
        monkeypatch.delenv("APATCH_AGENT_SIGN_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("APATCH_AGENT_SIGN_TIMEOUT_SECONDS", configured)
    broker = FixtureBroker()
    budget = native_capabilities["signing_budget"]
    # Virtual time: helper uses its whole budget, delivery uses most of the
    # reserve. No real sleep, Touch ID or signing service is involved.
    reply_at = budget["helper_seconds"] + budget["client_reserve_seconds"] - 1
    sign_timeouts = []
    def invoke(argv, **kwargs):
        if argv[-1] == "--capabilities":
            return subprocess.CompletedProcess(argv, 0, canonical(native_capabilities), b"")
        sign_timeouts.append(kwargs["timeout"])
        if kwargs["timeout"] <= reply_at:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return broker.invoke(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", invoke)
    command = "/fixture/TrustChainSecretsSigner " + BINDING
    if explicit_result_protocol:
        command += " --result-json"
    provider = CommandKeyProvider(command, broker.public, "fixture")
    signature = provider.sign(b"exact fixture operation")
    assert provider.verify(b"exact fixture operation", signature)
    assert sign_timeouts == [150]
    assert broker.signs == 1 and len(broker.requests) == 1


@pytest.mark.parametrize(("field", "value"), [
    ("schema", "unknown"), ("helper_seconds", 90), ("helper_seconds", 301),
    ("biometric_seconds", True), ("biometric_seconds", float("nan")),
    ("client_reserve_seconds", 0), ("client_reserve_seconds", "10"),
])
def test_invalid_native_budget_is_refused_before_signing(monkeypatch, field, value):
    broker = FixtureBroker()
    budget = {**BUDGET, field: value}
    calls = []
    def probe(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(capabilities(budget)).encode(), b"")
    monkeypatch.setattr(subprocess, "run", probe)
    with pytest.raises(CommandSigningError) as caught:
        CommandKeyProvider("/fixture/TrustChainSecretsSigner " + BINDING, broker.public, "fixture")
    assert caught.value.code == "SIGN_COMMAND_TIMEOUT_BUDGET_INVALID"
    assert caught.value.reconciliation_required is False
    assert calls == [["/fixture/TrustChainSecretsSigner", "--capabilities"]]
    assert broker.signs == 0


@pytest.mark.parametrize("configured", ["1", "120"])
def test_generic_command_keeps_configured_timeout(monkeypatch, configured):
    monkeypatch.setenv("APATCH_AGENT_SIGN_TIMEOUT_SECONDS", configured)
    broker = FixtureBroker()
    timeouts = []
    def invoke(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        return broker.invoke(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", invoke)
    assert broker.provider().sign(b"exact fixture operation")
    assert timeouts == [int(configured)]
