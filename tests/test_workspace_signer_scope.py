"""Workspace signer-scope regressions; all keys and ledgers are disposable."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
import pytest

from apatch.trust_identity import (SigningIdentityError, _PemKeyProvider,
    active_workspace_identity_scope, load_local_identity, workspace_identity_scope)
from apatch.trustchain_helper import TrustChainHelper

IDENTITY_ENV = ("APATCH_AGENT_ID", "APATCH_AGENT_KEY", "APATCH_AGENT_CERT", "APATCH_KEY_BACKEND",
    "APATCH_AGENT_SIGN_CMD", "APATCH_AGENT_PUBKEY", "APATCH_PLATFORM_URL", "APATCH_CANONICAL_RUNTIME",
    "APATCH_LANE_ID", "APATCH_TC_PYTHON", "APATCH_TC_PYTHONPATH")


def fixture_key(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    path.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return str(path), base64.b64encode(private.public_key().public_bytes_raw()).decode()


def workspace_pin(root, *, backend="pem", identity="project-fixture"):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".trustchain").mkdir(exist_ok=True)
    key, public = fixture_key(root.parent / (root.name + ".pem"))
    data = {"agent_id": identity, "key": key}
    if backend == "command":
        script = root.parent / (root.name + "-command.py")
        script.write_text("import base64,sys\nfrom cryptography.hazmat.primitives.serialization import load_pem_private_key\n"
            + "key=load_pem_private_key(open(" + repr(key) + ", 'rb').read(),None)\n"
            + "sys.stdout.write(base64.b64encode(key.sign(sys.stdin.buffer.read())).decode())\n")
        data = {"agent_id": identity, "key_backend": "command", "sign_cmd": shlex.join([sys.executable, str(script)]), "public_key": public}
    (root / ".apatch").mkdir(exist_ok=True)
    (root / ".apatch/agent-identity.json").write_text(json.dumps(data))
    return root


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)
    root = workspace_pin(tmp_path / "project")
    ambient_key, _ = fixture_key(tmp_path / "ambient-agent.pem")
    monkeypatch.setenv("APATCH_AGENT_ID", "caller-agent-fixture")
    monkeypatch.setenv("APATCH_AGENT_KEY", ambient_key)
    monkeypatch.setenv("APATCH_KEY_BACKEND", "pem")
    return root


def persisted_record(root, evidence):
    raw = json.loads((root / ".trustchain" / evidence["object_path"]).read_text())
    return raw.get("value", raw)


def tree_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / ".trustchain").rglob("*") if p.is_file()}


def test_unscoped_native_owner_env_still_wins(project):
    assert load_local_identity(str(project)).agent_id == "caller-agent-fixture"
    helper = TrustChainHelper(str(project), auto_init=False)
    assert helper.commit_action("apatch", {"action": "owner-fixture", "functional_acceptance": False})
    assert persisted_record(project, helper.last_commit_evidence)["key_id"] == "caller-agent-fixture"
    assert helper.last_commit_evidence["pinned_signature_valid"] is True


def test_scoped_project_pin_wins_without_environment_swap(project):
    before = dict(os.environ)
    with workspace_identity_scope(str(project)):
        assert load_local_identity(str(project)).agent_id == "project-fixture"
        helper = TrustChainHelper(str(project), auto_init=False)
        assert helper.commit_action("apatch", {"action": "scoped-fixture", "functional_acceptance": False})
        evidence = helper.last_commit_evidence
        assert evidence["pinned_signature_valid"] is True
        assert evidence["key_id_matches"] is True
        assert persisted_record(project, evidence)["key_id"] == "project-fixture"
    assert dict(os.environ) == before
    assert active_workspace_identity_scope() is None
    assert load_local_identity(str(project)).agent_id == "caller-agent-fixture"


def test_nested_scopes_restore_and_mismatched_roots_fail_closed(project, tmp_path):
    other = workspace_pin(tmp_path / "other", identity="other-project-fixture")
    with workspace_identity_scope(str(project)):
        with pytest.raises(SigningIdentityError): load_local_identity(str(other))
        with workspace_identity_scope(str(other)):
            assert load_local_identity(str(other)).agent_id == "other-project-fixture"
        assert load_local_identity(str(project)).agent_id == "project-fixture"


def test_disjoint_scopes_are_thread_local_and_leave_caller_env(project, tmp_path):
    other = workspace_pin(tmp_path / "other", identity="other-project-fixture")
    before = dict(os.environ)
    def commit(root):
        with workspace_identity_scope(str(root)):
            helper = TrustChainHelper(str(root), auto_init=False)
            assert helper.commit_action("apatch", {"action": "parallel-fixture"})
            return persisted_record(root, helper.last_commit_evidence)["key_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, (project, other)))
    assert results == ["project-fixture", "other-project-fixture"]
    assert dict(os.environ) == before


@pytest.mark.parametrize("defect", ["missing", "malformed", "missing_key", "wrong_certificate"])
def test_invalid_scoped_pin_cannot_degrade_to_ephemeral(project, defect):
    path = project / ".apatch/agent-identity.json"
    data = json.loads(path.read_text())
    if defect == "missing": path.unlink()
    if defect == "malformed": path.write_text("not-json")
    if defect == "missing_key": data["key"] = str(project / "absent.pem"); path.write_text(json.dumps(data))
    if defect == "wrong_certificate": data["cert"] = str(project / "absent.crt"); path.write_text(json.dumps(data))
    helper = TrustChainHelper(str(project), auto_init=False)
    before = tree_hashes(project)
    with workspace_identity_scope(str(project)):
        assert helper.commit_action("apatch", {"action": "must-fail"}) is False
        assert helper.last_commit_evidence["error_type"] == "SIGNING_IDENTITY_UNAVAILABLE"
    assert tree_hashes(project) == before


@pytest.mark.parametrize("backend", ["pem", "command"])
def test_pre_sign_import_error_child_preserves_scoped_provider(project, tmp_path, monkeypatch, backend):
    root = project if backend == "pem" else workspace_pin(tmp_path / "command-project", backend="command")
    before_env = dict(os.environ)
    def fail_import(*args, **kwargs): raise ImportError("isolated-dependency-import-fixture")
    with workspace_identity_scope(str(root)):
        helper = TrustChainHelper(str(root), auto_init=False)
        monkeypatch.setattr(helper, "_tc_config", fail_import)
        assert helper.commit_action("apatch", {"action": "child-fixture"}) is True
        evidence = helper.last_commit_evidence
        assert evidence["validation"] == "single_object_subprocess"
        assert evidence["pinned_signature_valid"] is True
        assert evidence["key_id_matches"] is True
        assert evidence["primary_exception_type"] == "ImportError"
        assert persisted_record(root, evidence)["key_id"] == "project-fixture"
    assert dict(os.environ) == before_env


def test_child_refuses_key_rotation_before_append(project):
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        original = load_local_identity(str(project))
        data = json.loads((project / ".apatch/agent-identity.json").read_text())
        fixture_key(Path(data["key"]))
        before = tree_hashes(project)
        assert helper._commit_via_subprocess("apatch", {"action": "rotated-fixture"},
            before_head=helper.ledger_head(), identity=original) is False
        assert tree_hashes(project) == before


@pytest.mark.parametrize("stage", ["primary", "subprocess"])
@pytest.mark.parametrize("claimed_id", ["wrong-agent-fixture", "project-fixture"])
def test_genuine_wrong_key_or_key_id_is_not_notarized(project, tmp_path, monkeypatch, stage, claimed_id):
    import trustchain
    actual_tc = trustchain.TrustChain
    actual_config = trustchain.TrustChainConfig
    alien_key, _ = fixture_key(tmp_path / "alien.pem")
    provider = _PemKeyProvider(alien_key, key_id=claimed_id)
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        pushed = []
        monkeypatch.setattr(helper, "_maybe_push_to_platform", lambda *args: pushed.append(True))
        if stage == "primary":
            monkeypatch.setattr(helper, "_tc_config", lambda identity=None: actual_config(
                enable_chain=True, chain_storage="file", chain_dir=helper.trustchain_dir, key_provider=provider))
        else:
            monkeypatch.setattr(helper, "_tc_config", lambda *args, **kwargs: (_ for _ in ()).throw(ImportError("dependency-fixture")))
            def wrong_child(argv, **kwargs):
                env = kwargs["env"]
                tc = actual_tc(actual_config(enable_chain=True, chain_storage="file",
                    chain_dir=env["TC_CHAIN_DIR"], key_provider=provider))
                response = tc.sign(tool_id=env["TC_TOOL_ID"], data=json.loads(env["TC_PAYLOAD"]))
                return subprocess.CompletedProcess(argv, 0, json.dumps({"signature":response.signature,"length":tc.chain.length}), "")
            monkeypatch.setattr(subprocess, "run", wrong_child)
        assert helper.commit_action("apatch", {"files":{"src/fixture.py":{"sha256":"1"*64}}}) is False
        assert helper.last_commit_evidence["pinned_signature_valid"] is False
        assert helper.last_commit_evidence["key_id_matches"] is (claimed_id == "project-fixture")
        assert not pushed
        index = project / ".apatch/notarized_index.json"
        assert not index.exists() or "src/fixture.py" not in json.loads(index.read_text()).get("files", {})


@pytest.mark.parametrize("error_class", [RuntimeError, ImportError])
def test_primary_partial_append_never_retries_a_second_signer(project, monkeypatch, error_class):
    import trustchain
    actual_tc = trustchain.TrustChain
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        class Partial:
            def __init__(self, cfg):
                self.tc = actual_tc(cfg)
                self.chain = self.tc.chain
            def sign(self, **kwargs):
                self.tc.sign(**kwargs)
                raise error_class("after-real-append-fixture")
        monkeypatch.setattr(trustchain, "TrustChain", Partial)
        monkeypatch.setattr(helper, "_commit_via_subprocess", lambda *args, **kwargs: pytest.fail("partial append may not retry"))
        assert helper.commit_action("apatch", {"action": "partial-fixture"}) is False
        assert helper.last_commit_evidence["error_type"] == "PRIMARY_SIGNING_PARTIAL_APPEND"


@pytest.mark.parametrize("fallback", [False, True])
def test_scoped_pem_pin_cannot_be_replaced_by_caller_command_provider(project, tmp_path, monkeypatch, fallback):
    import trustchain
    ambient = workspace_pin(tmp_path / "ambient-command", backend="command", identity="caller-command-fixture")
    data = json.loads((ambient / ".apatch/agent-identity.json").read_text())
    monkeypatch.delenv("APATCH_AGENT_KEY", raising=False)
    monkeypatch.setenv("APATCH_AGENT_ID", data["agent_id"])
    monkeypatch.setenv("APATCH_KEY_BACKEND", "command")
    monkeypatch.setenv("APATCH_AGENT_SIGN_CMD", data["sign_cmd"])
    monkeypatch.setenv("APATCH_AGENT_PUBKEY", data["public_key"])
    assert load_local_identity(str(project)).agent_id == "caller-command-fixture"
    before = dict(os.environ)
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        if fallback:
            def fail_import(*args, **kwargs): raise ImportError("controlled-dependency-fixture")
            monkeypatch.setattr(helper, "_tc_config", fail_import)
        assert helper.commit_action("apatch", {"action": "correct-project-fixture"}) is True
        assert helper.last_commit_evidence["pinned_signature_valid"] is True
        assert persisted_record(project, helper.last_commit_evidence)["key_id"] == "project-fixture"
    assert dict(os.environ) == before


def test_refusing_command_signer_never_downgrades_or_retries(project, tmp_path, monkeypatch):
    root = workspace_pin(tmp_path / "refusing-command", backend="command")
    data = json.loads((root / ".apatch/agent-identity.json").read_text())
    script = root.parent / (root.name + "-command.py")
    calls = root.parent / "refused-sign-calls"
    script.write_text("from pathlib import Path\np=Path(" + repr(str(calls)) + ")\n"
        + "p.write_text(p.read_text()+'x' if p.exists() else 'x')\nraise SystemExit(42)\n")
    with workspace_identity_scope(str(root)):
        helper = TrustChainHelper(str(root), auto_init=False)
        monkeypatch.setattr(helper, "_commit_via_subprocess", lambda *args, **kwargs: pytest.fail("denial cannot retry in child"))
        before_head = helper.ledger_head()
        assert helper.commit_action("apatch", {"files":{"src/must-not-notarize.py":{"sha256":"2"*64}}}) is False
        assert helper.last_commit_evidence["error_type"] == "PRIMARY_SIGNING_FAILED"
        assert calls.read_text() == "x"
        assert helper.ledger_head() == before_head
        assert not list((root / ".trustchain/objects").glob("op_*.json"))
        assert not (root / ".apatch/notarized_index.json").exists()


def test_enforced_unconfigured_workspace_refuses_ephemeral_signing(tmp_path, monkeypatch):
    for name in IDENTITY_ENV: monkeypatch.delenv(name, raising=False)
    root = workspace_pin(tmp_path / "enforced")
    (root / ".apatch/agent-identity.json").unlink()
    (root / ".apatch/enforcement.json").write_text(json.dumps({"mode":"strict"}))
    helper = TrustChainHelper(str(root), auto_init=False)
    before = tree_hashes(root)
    assert helper.commit_action("apatch", {"action":"missing-pin-fixture"}) is False
    assert helper.last_commit_evidence["error_type"] == "SIGNING_IDENTITY_UNAVAILABLE"
    assert tree_hashes(root) == before


@pytest.mark.parametrize("stage", ["primary", "subprocess"])
def test_changed_pin_after_signing_cannot_reach_index_or_platform(project, tmp_path, monkeypatch, stage):
    import trustchain
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        pushed = []
        monkeypatch.setattr(helper, "_maybe_push_to_platform", lambda *args: pushed.append(True))
        def rotate_after(operation):
            def wrapped(*args, **kwargs):
                result = operation(*args, **kwargs)
                data = json.loads((project / ".apatch/agent-identity.json").read_text())
                changed, _ = fixture_key(tmp_path / "changed-after-sign.pem")
                data["key"] = changed
                (project / ".apatch/agent-identity.json").write_text(json.dumps(data))
                return result
            return wrapped
        if stage == "primary":
            monkeypatch.setattr(helper, "_validate_python_commit", rotate_after(helper._validate_python_commit))
        else:
            monkeypatch.setattr(helper, "_commit_via_subprocess", rotate_after(helper._commit_via_subprocess))
            def fail_import(*args, **kwargs): raise ImportError("controlled-dependency-fixture")
            monkeypatch.setattr(helper, "_tc_config", fail_import)
        assert helper.commit_action("apatch", {"files":{"src/drift-fixture.py":{"sha256":"3"*64}}}) is False
        assert helper.last_commit_evidence["pinned_signature_valid"] is True
        assert helper.last_commit_evidence["persisted"] is False
        assert helper.last_commit_evidence["identity_unchanged"] is False
        assert helper.last_commit_evidence["error_type"] == "SIGNING_IDENTITY_CHANGED"
        assert not pushed
        assert not (project / ".apatch/notarized_index.json").exists()


@pytest.mark.parametrize("error_class", [RuntimeError, PermissionError, ImportError])
def test_primary_sign_denial_or_lazy_import_never_calls_child(project, monkeypatch, error_class):
    import trustchain
    actual_tc = trustchain.TrustChain
    calls = []
    class Refused:
        def __init__(self, cfg):
            self.tc = actual_tc(cfg)
            self.chain = self.tc.chain
        def sign(self, **kwargs):
            calls.append(True)
            raise error_class("no-second-sign-fixture")
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        before_head = helper.ledger_head()
        monkeypatch.setattr(trustchain, "TrustChain", Refused)
        monkeypatch.setattr(helper, "_commit_via_subprocess", lambda *args, **kwargs: pytest.fail("sign error cannot retry"))
        assert helper.commit_action("apatch", {"action":"denied-fixture"}) is False
        assert helper.last_commit_evidence["error_type"] == "PRIMARY_SIGNING_FAILED"
        assert calls == [True]
        assert helper.ledger_head() == before_head
        assert not list((project / ".trustchain/objects").glob("op_*.json"))


def fixture_certificate(key_path, cert_path, serial=1):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(Path(key_path).read_bytes(), None)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "fixture-project")])
    moment = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(serial)
        .not_valid_before(moment-timedelta(minutes=1)).not_valid_after(moment+timedelta(days=1))
        .sign(key, None))
    cert_path.write_bytes(certificate.public_bytes(Encoding.PEM))
    return str(cert_path)


@pytest.mark.parametrize("lane", ["best_effort", "durable"])
@pytest.mark.parametrize("defect", ["actor", "key", "certificate_key", "certificate_serial", "unpinned_certificate", "missing_certificate"])
def test_roaming_platform_mismatch_cannot_reach_any_transport(project, tmp_path, monkeypatch, lane, defect):
    import apatch.platform_client as client
    data = json.loads((project / ".apatch/agent-identity.json").read_text())
    cert = fixture_certificate(data["key"], tmp_path / "project.crt")
    if defect != "unpinned_certificate":
        data["cert"] = cert
        (project / ".apatch/agent-identity.json").write_text(json.dumps(data))
    cfg = {"base_url":"https://no-network.invalid", "agent_id":data["agent_id"],
        "key_path":data["key"], "tenant_id":"fixture-tenant", "cert_path":cert}
    if defect == "actor": cfg["agent_id"] = "caller-agent-fixture"
    if defect == "key": cfg["key_path"], _ = fixture_key(tmp_path / "wrong-transport.pem")
    if defect == "certificate_key":
        alien, _ = fixture_key(tmp_path / "alien-cert.pem")
        cfg["cert_path"] = fixture_certificate(alien, tmp_path / "alien.crt")
    if defect == "certificate_serial":
        cfg["cert_path"] = fixture_certificate(data["key"], tmp_path / "other-cert.crt", serial=2)
    if defect == "missing_certificate": cfg["cert_path"] = None
    monkeypatch.setattr(client, "platform_config_from_env", lambda: cfg)
    # These are transport spies, not a proposed old-runtime Platform feature.
    monkeypatch.setattr(client, "durable_lane_configured", lambda config: lane == "durable", raising=False)
    monkeypatch.setattr(client, "certificate_identity", lambda path: {}, raising=False)
    pushed = []
    for method in ("push_step", "push_durable_step", "push_revert"):
        monkeypatch.setattr(client, method, lambda **kwargs: pushed.append(kwargs), raising=False)
    with workspace_identity_scope(str(project)):
        helper = TrustChainHelper(str(project), auto_init=False)
        assert helper.commit_action("apatch", {"action":"locally-pinned-fixture"}) is True
        assert helper.last_commit_evidence["pinned_signature_valid"] is True
        assert helper.last_platform_push["error_type"] == "PLATFORM_IDENTITY_MISMATCH"
        helper._maybe_push_revert_to_platform("fixture-op", "fixture-only")
        assert helper.last_platform_push["error_type"] == "PLATFORM_IDENTITY_MISMATCH"
    assert not pushed


@pytest.mark.parametrize("scoped", [False, True])
def test_matching_platform_projection_preserves_native_owner_semantics(project, monkeypatch, scoped):
    from contextlib import nullcontext
    import apatch.platform_client as client
    pushed = []
    with workspace_identity_scope(str(project)) if scoped else nullcontext():
        identity = load_local_identity(str(project))
        cfg = {"base_url":"https://no-network.invalid", "agent_id":identity.agent_id,
            "key_path":identity.key_path, "tenant_id":None, "cert_path":None}
        monkeypatch.setattr(client, "platform_config_from_env", lambda: cfg)
        monkeypatch.setattr(client, "durable_lane_configured", lambda config: False, raising=False)
        monkeypatch.setattr(client, "certificate_identity", lambda path: {}, raising=False)
        monkeypatch.setattr(client, "push_durable_step", lambda **kwargs: pytest.fail("unexpected durable transport"), raising=False)
        monkeypatch.setattr(client, "push_step", lambda **kwargs: pushed.append(kwargs) or True)
        helper = TrustChainHelper(str(project), auto_init=False)
        assert helper.commit_action("apatch", {"action":"matched-fixture"}) is True
        assert helper.last_platform_push["ok"] is True
        assert len(pushed) == 1
        assert pushed[0]["agent_id"] == ("project-fixture" if scoped else "caller-agent-fixture")
