"""Trust anchor identity for apatch's local TrustChain ledger.

apatch signs its `.trustchain/` ledger so that every mutation is attributable.
By default the signer key is **ephemeral** (generated per run) — fine for dev,
but the resulting ledger is self-signed with no external root of trust.

When an operator enrolls apatch as a TrustChain agent (``tc cert request`` or
``POST /api/enroll/csr``), the Platform issues a leaf certificate that chains to
the TrustChain **root CA** and certifies the agent's Ed25519 public key. The
private key (``agent.key``, PEM) stays with the client.

This module wires that enrolled key into ``TrustChain.sign()`` via a soft-KMS
``key_provider`` so the ledger signatures are made by the *same* key the leaf
certificate certifies. A verifier with the leaf cert + Platform root can then
verify every ledger entry to the root — closing RFP-005 audit gap #3.

Env contract (shared with ``platform_client.platform_config_from_env``):

* ``APATCH_AGENT_ID``   — enrolled agent CN (the cert subject).
* ``APATCH_AGENT_KEY``  — path to the enrolled Ed25519 private key (PEM, PKCS8).
* ``APATCH_AGENT_CERT`` — optional path to the leaf cert PEM (for verification).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


_WORKSPACE_IDENTITY_SCOPE = ContextVar("apatch_workspace_identity_scope", default=None)


class SigningIdentityError(RuntimeError):
    """A configured/scoped identity may not downgrade to an ephemeral signer."""


def active_workspace_identity_scope() -> Optional[str]:
    return _WORKSPACE_IDENTITY_SCOPE.get()


@contextmanager
def workspace_identity_scope(workspace_root: str):
    """Used only after trusted routing has verified the roaming workspace."""
    root = os.path.realpath(os.path.abspath(workspace_root))
    token = _WORKSPACE_IDENTITY_SCOPE.set(root)
    try:
        yield
    finally:
        _WORKSPACE_IDENTITY_SCOPE.reset(token)


def _scoped_identity(workspace_root: Optional[str]) -> Optional[LocalIdentity]:
    scope = active_workspace_identity_scope()
    if scope is None:
        return None
    if workspace_root is not None and os.path.realpath(os.path.abspath(workspace_root)) != scope:
        raise SigningIdentityError("signing workspace differs from the verified routing scope")
    from apatch.workspace_identity import load_workspace_identity
    data = load_workspace_identity(scope)
    if data is None:
        raise SigningIdentityError("verified workspace has no usable pinned identity")
    resolved = resolve_key_provider(data["agent_id"], key_path=data.get("key"),
        backend=data.get("key_backend"), sign_cmd=data.get("sign_cmd"),
        public_key=data.get("public_key"), use_environment=False)
    if resolved is None:
        raise SigningIdentityError("verified workspace pinned provider is unavailable")
    provider, backend, key_path = resolved
    public = provider.get_public_key()
    if not isinstance(public, bytes) or len(public) != 32 or provider.get_key_id() != data["agent_id"]:
        raise SigningIdentityError("verified workspace provider does not bind its exact Ed25519 identity")
    certificate_path = data.get("cert")
    if certificate_path:
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            with open(certificate_path, "rb") as handle:
                certificate = x509.load_pem_x509_certificate(handle.read())
            if certificate.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != public:
                raise SigningIdentityError("workspace signing key differs from the pinned certificate")
        except SigningIdentityError:
            raise
        except Exception as exc:
            raise SigningIdentityError("workspace pinned certificate is unavailable") from exc
    return LocalIdentity(data["agent_id"], key_path, certificate_path, provider, backend)


class _PemKeyProvider:
    """Soft-KMS provider backed by an enrolled Ed25519 PEM private key.

    Implements the subset of ``trustchain.kms.KeyProvider`` that
    ``TrustChain._load_or_create_signer`` consumes (``get_seed`` / ``get_key_id``)
    plus the rest of the protocol for forward-compatibility.
    """

    def __init__(self, key_path: str, key_id: str) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key,
        )

        with open(key_path, "rb") as fh:
            priv = load_pem_private_key(fh.read(), password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            raise ValueError(
                f"{key_path}: expected an Ed25519 private key for the apatch identity"
            )
        self._priv = priv
        self._seed = priv.private_bytes_raw()
        self._pub_raw = priv.public_key().public_bytes_raw()
        self._key_id = key_id

    def get_metadata(self):
        from trustchain.kms import KeyProviderMetadata

        return KeyProviderMetadata(
            provider="apatch-enrolled-pem",
            key_id=self._key_id,
            algorithm="ed25519",
            uri="file://apatch-agent-key",
        )

    def get_public_key(self) -> bytes:
        return self._pub_raw

    def get_key_id(self) -> str:
        return self._key_id

    def get_seed(self) -> bytes:
        return self._seed

    def sign(self, data: bytes) -> bytes:
        return self._priv.sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self._priv.public_key().verify(signature, data)
            return True
        except Exception:
            return False


_SECRETS_SIGNING_MESSAGES = {
    "CONSENT_WINDOW_NOT_VISIBLE": "Secrets could not show the consent window.",
    "CONSENT_TIMED_OUT": "Secrets did not receive consent in time.",
    "USER_DECLINED": "The user declined the signing request.",
    "TOUCH_ID_TIMED_OUT": "Touch ID confirmation timed out.",
    "TOUCH_ID_CANCELLED": "Touch ID confirmation was cancelled.",
    "TOUCH_ID_UNAVAILABLE": "Touch ID is unavailable.",
    "CALLER_CHANGED": "The signing caller changed.",
    "CALLER_DISCONNECTED": "The signing caller disconnected.",
    "SIGNING_FAILED": "Secrets could not complete signing.",
    "TOUCH_ID_DENIED": "Touch ID authorization was denied.",
    "REQUEST_REJECTED": "Secrets rejected the signing request.",
    "SIGNING_RESULT_UNKNOWN": "The signing request already exists; its result must be reconciled.",
}


def _secrets_signing_cause(stderr: bytes) -> Optional[dict[str, str]]:
    """Decode bounded diagnostics, never proof of cancellation or retry authority."""
    import json
    from uuid import UUID

    if not isinstance(stderr, bytes) or len(stderr) > 4096:
        return None
    prefix = "trustchain-secrets-signer: SECRETS_SIGNING_ERROR "

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate diagnostic field")
            result[key] = value
        return result

    try:
        text = stderr.decode("utf-8").rstrip("\r\n")
        if not text.startswith(prefix):
            return None
        body = json.loads(text[len(prefix):], object_pairs_hook=unique_fields)
        if not isinstance(body, dict) or set(body) != {"request_id", "error_code", "message"}:
            return None
        request_id, code = body["request_id"], body["error_code"]
        if not all(isinstance(body[key], str) for key in body):
            return None
        if code not in _SECRETS_SIGNING_MESSAGES or str(UUID(request_id)) != request_id:
            return None
    except (ValueError, UnicodeError, RecursionError):
        return None
    # Do not expose arbitrary stderr/message contents, which may contain secrets.
    return {"source": "trustchain-secrets", "request_id": request_id,
            "error_code": code, "message": _SECRETS_SIGNING_MESSAGES[code]}


class CommandSigningError(SigningIdentityError):
    """Safe command failure; a timed-out client does not prove remote cancellation."""

    def __init__(self, code: str, *, timeout_seconds=None, returncode=None,
                 reconciliation_required: bool = True, cause=None, signing_request=None):
        super().__init__(code)
        self.code = code
        self.timeout_seconds = timeout_seconds
        self.returncode = returncode
        self.reconciliation_required = reconciliation_required
        self.cause = dict(cause) if cause is not None else None
        self.signing_request = dict(signing_request) if signing_request is not None else None

    def to_dict(self):
        result = {"error_type": "SIGN_COMMAND_ERROR", "error_code": self.code,
                  "timeout_seconds": self.timeout_seconds, "returncode": self.returncode,
                  "reconciliation_required": self.reconciliation_required}
        if self.cause is not None:
            result["cause"] = dict(self.cause)
        if self.signing_request is not None:
            result["signing_request"] = dict(self.signing_request)
        return result


class CommandKeyProvider:
    """Hard-KMS bridge: signs via an external command; seed never enters apatch.

    For HSM / cloud-KMS (PKCS#11, Vault, cloud KMS CLIs) where the private key
    must not touch the host process. Implements the *hard* half of
    ``trustchain.kms.KeyProvider``: ``get_seed()`` raises, signing is delegated
    to a configured command. TrustChain's core (``Signer.from_provider``) and
    apatch's policy signing both route through ``sign()``.

    Contract of the sign command (``APATCH_AGENT_SIGN_CMD``):

    * receives the raw bytes to sign on **stdin**;
    * writes the Ed25519 signature, **base64-encoded**, to **stdout**.

    A Secrets helper advertising ``signing-result-v1`` is upgraded in memory
    to ``--result-json``. Its request id binds the binding, pinned key and exact
    bytes, so a recreated provider can read the same public result. Generic
    commands and older helpers retain the stdin/base64 contract.

    ``APATCH_AGENT_SIGN_TIMEOUT_SECONDS`` is an integer from 1 through 120,
    default 90. For a Secrets helper advertising ``signing-budget-v1``, the
    effective timeout is at least its complete budget plus delivery reserve;
    a shorter configured value cannot truncate attended consent or Touch ID.
    Generic/older commands retain the configured timeout. This bounds one
    invocation; it does not extend broker grants or certificates.
    A timeout kills/reaps the direct child, never retries, and requires remote
    reconciliation because a broker may already have processed the request.

    The public key (``APATCH_AGENT_PUBKEY``: base64 raw 32-byte Ed25519, or a
    path to such) is supplied out-of-band so apatch can verify locally without
    invoking the device.
    """

    def __init__(self, sign_cmd: str, pub_b64: str, key_id: str) -> None:
        import base64 as _b64
        import shlex

        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        raw = _b64.b64decode(pub_b64.strip())
        if len(raw) != 32:
            raise ValueError("APATCH_AGENT_PUBKEY must be a base64 raw 32-byte Ed25519 key")
        self._pub_raw = raw
        self._pub = Ed25519PublicKey.from_public_bytes(raw)
        self._cmd = shlex.split(sign_cmd)
        if not self._cmd:
            raise ValueError("APATCH_AGENT_SIGN_CMD is empty")
        self._key_id = key_id
        self._secrets_binding = None
        self.last_signing_request = None
        self.last_signing_result = None
        self.signing_budget = None
        raw_timeout = os.environ.get("APATCH_AGENT_SIGN_TIMEOUT_SECONDS", "90").strip()
        if (not raw_timeout or not raw_timeout.isascii() or not raw_timeout.isdecimal()
                or len(raw_timeout) > 3 or not 1 <= int(raw_timeout) <= 120):
            raise CommandSigningError("SIGN_COMMAND_TIMEOUT_INVALID",
                                      reconciliation_required=False)
        self._timeout_seconds = int(raw_timeout)
        self._configure_secrets_result_protocol()

    def _configure_secrets_result_protocol(self) -> None:
        from uuid import UUID
        native_arguments = len(self._cmd) == 2 or (len(self._cmd) == 3 and self._cmd[-1] == "--result-json")
        if native_arguments and os.path.basename(self._cmd[0]) == "TrustChainSecretsSigner":
            import json
            import subprocess
            # This native entry point explicitly promises no signing, socket
            # or Keychain access. Never probe generic HSM/Vault commands.
            try:
                probe = subprocess.run([self._cmd[0], "--capabilities"], input=b"",
                    capture_output=True, timeout=min(self._timeout_seconds, 5))
                if probe.returncode == 0 and len(probe.stdout) <= 4096:
                    capabilities = json.loads(probe.stdout)
                    if (isinstance(capabilities, dict)
                            and capabilities.get("schema") == "trustchain.signer-capabilities.v1"
                            and isinstance(capabilities.get("features"), list)
                            and capabilities.get("side_effects") == {"sign": False, "keychain": False, "socket": False}):
                        if "signing-result-v1" in capabilities["features"] and self._cmd[-1] != "--result-json":
                            self._cmd.append("--result-json")
                        if "signing-budget-v1" in capabilities["features"]:
                            self._apply_secrets_timeout_budget(capabilities.get("signing_budget"))
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, RecursionError):
                pass  # An older helper continues using the existing contract.
        if self._cmd[-1] == "--result-json":
            if len(self._cmd) < 3:
                raise ValueError("Secrets result protocol requires a binding UUID")
            self._secrets_binding = str(UUID(self._cmd[-2]))

    def _apply_secrets_timeout_budget(self, budget) -> None:
        import math
        stages = ("presentation_seconds", "consent_seconds", "biometric_seconds",
                  "metadata_seconds", "processing_seconds")
        fields = (*stages, "helper_seconds", "client_reserve_seconds")
        valid = isinstance(budget, dict) and budget.get("schema") == "trustchain.secrets.signing-budget.v1"
        if valid:
            valid = all(isinstance(budget.get(key), (int, float))
                and not isinstance(budget[key], bool) and 0 < budget[key] <= 300
                and math.isfinite(budget[key]) for key in fields)
        if valid:
            valid = (math.isclose(sum(budget[key] for key in stages), budget["helper_seconds"], abs_tol=1e-9)
                     and 1 <= budget["client_reserve_seconds"] <= 60)
        if not valid:
            raise CommandSigningError("SIGN_COMMAND_TIMEOUT_BUDGET_INVALID", reconciliation_required=False)
        self.signing_budget = dict(budget)
        self._timeout_seconds = max(self._timeout_seconds,
            budget["helper_seconds"] + budget["client_reserve_seconds"])

    def get_metadata(self):
        from trustchain.kms import KeyProviderMetadata

        return KeyProviderMetadata(
            provider="apatch-command-hsm",
            key_id=self._key_id,
            algorithm="ed25519",
            uri="cmd://apatch-agent-sign",
        )

    def get_public_key(self) -> bytes:
        return self._pub_raw

    def get_key_id(self) -> str:
        return self._key_id

    def get_seed(self) -> bytes:
        from trustchain.kms import KeyProviderError

        raise KeyProviderError(
            "CommandKeyProvider is a hard-KMS provider — the private seed "
            "never leaves the signing device. Use sign()/verify()."
        )

    def sign(self, data: bytes) -> bytes:
        import base64 as _b64
        import hashlib
        import subprocess

        self.last_signing_result = None
        self.last_signing_request = None
        command_options = {}
        if self._secrets_binding is not None:
            from uuid import UUID, uuid5
            digest = hashlib.sha256(data).hexdigest()
            # Identical bytes under the same binding/key name the same request,
            # including after the provider/process is recreated. Not authority.
            request_id = str(uuid5(UUID(self._secrets_binding), self._pub_raw.hex() + ":" + digest))
            self.last_signing_request = {"request_id": request_id,
                "binding_id": self._secrets_binding, "payload_sha256": digest}
            command_options["env"] = {**os.environ, "APATCH_SIGNING_REQUEST_ID": request_id}
        try:
            proc = subprocess.run(
                self._cmd, input=data, capture_output=True, timeout=self._timeout_seconds,
                **command_options
            )
        except subprocess.TimeoutExpired:
            raise CommandSigningError("SIGN_COMMAND_TIMEOUT",
                                      timeout_seconds=self._timeout_seconds,
                                      signing_request=self.last_signing_request) from None
        except OSError:
            raise CommandSigningError("SIGN_COMMAND_START_FAILED",
                                      reconciliation_required=False,
                                      signing_request=self.last_signing_request) from None
        if proc.returncode != 0:
            # Exit codes/localized stderr do not establish user cancellation.
            raise CommandSigningError("SIGN_COMMAND_FAILED", returncode=proc.returncode,
                                      cause=_secrets_signing_cause(proc.stderr),
                                      signing_request=self.last_signing_request)
        if self._secrets_binding is not None:
            return self._secrets_result(data, proc.stdout)
        try:
            sig = _b64.b64decode(proc.stdout.strip(), validate=True)
        except (ValueError, _b64.binascii.Error):
            raise CommandSigningError("SIGN_COMMAND_RESPONSE_INVALID") from None
        if len(sig) != 64:
            raise CommandSigningError("SIGN_COMMAND_RESPONSE_INVALID")
        # Only the configured public key and exact current bytes are accepted.
        if not self.verify(data, sig):
            raise CommandSigningError("SIGN_COMMAND_SIGNATURE_INVALID")
        return sig

    def _secrets_result(self, data: bytes, output: bytes) -> bytes:
        """Accept only a pinned signature and its request-bound signed receipt."""
        import base64
        import hashlib
        import json
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate result field")
                result[key] = value
            return result

        def canonical(value):
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()

        try:
            if len(output) > 96 * 1024:
                raise ValueError("oversized result")
            result = json.loads(output.decode("utf-8"), object_pairs_hook=unique_fields)
            if result["schema"] != "trustchain.secrets.signing-result.v1":
                raise ValueError("unknown signing result")
            if any(result[key] != value for key, value in self.last_signing_request.items()):
                raise ValueError("signing request mismatch")
            signature = base64.b64decode(result["signature_b64"], validate=True)
            if base64.b64decode(result["public_key_b64"], validate=True) != self._pub_raw or not self.verify(data, signature):
                raise ValueError("signing key or payload mismatch")
            receipt = result["receipt"]
            envelope_bytes = receipt["receipt_envelope_json"].encode("utf-8")
            envelope = json.loads(envelope_bytes, object_pairs_hook=unique_fields)
            if canonical(envelope) != envelope_bytes or envelope["version"] != 1 or envelope["outcome"] != "success":
                raise ValueError("invalid receipt envelope")
            expected = {**self.last_signing_request, "receipt_id": receipt["id"],
                        "result_sha256": hashlib.sha256(signature).hexdigest()}
            if any(envelope[key] != value for key, value in expected.items()):
                raise ValueError("receipt request mismatch")
            attestation_raw = base64.b64decode(receipt["attestation_public_key_b64"], validate=True)
            if attestation_raw == self._pub_raw:
                raise ValueError("receipt key not separated")
            statement = canonical({"domain": "trustchain-secrets/broker-attestation-key/v1",
                "binding_id": self._secrets_binding, "attestation_public_key_b64": receipt["attestation_public_key_b64"]})
            self._pub.verify(base64.b64decode(receipt["attestation_key_proof_b64"], validate=True), statement)
            Ed25519PublicKey.from_public_bytes(attestation_raw).verify(
                base64.b64decode(receipt["receipt_signature_b64"], validate=True), envelope_bytes)
        except Exception:
            raise CommandSigningError("SIGN_COMMAND_RESPONSE_INVALID",
                                      signing_request=self.last_signing_request) from None
        self.last_signing_result = {**expected, "receipt": {key: receipt[key] for key in (
            "receipt_envelope_json", "attestation_public_key_b64", "attestation_key_proof_b64", "receipt_signature_b64")}}
        return signature

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self._pub.verify(signature, data)
            return True
        except Exception:
            return False


@dataclass
class LocalIdentity:
    """Resolved enrolled identity for signing apatch's local ledger."""

    agent_id: str
    key_path: Optional[str]
    cert_path: Optional[str]
    key_provider: Any
    backend: str = "pem"


def _read_pubkey_b64(source: str) -> Optional[str]:
    """Return base64 raw Ed25519 pubkey from a literal b64 or a file path."""
    s = source.strip()
    if not s:
        return None
    if os.path.isfile(s):
        with open(s, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return s


def resolve_key_provider(
    agent_id: str,
    *,
    key_path: Optional[str] = None,
    backend: Optional[str] = None,
    sign_cmd: Optional[str] = None,
    public_key: Optional[str] = None,
    use_environment: bool = True,
) -> Optional[tuple]:
    """Resolve a (provider, backend, key_path) for the configured key backend.

    Backends (``APATCH_KEY_BACKEND``, workspace fallback, or auto-detected):

    * ``command`` — external HSM/KMS signer (``APATCH_AGENT_SIGN_CMD`` +
      ``APATCH_AGENT_PUBKEY``); private seed never enters apatch.
    * ``pem`` (default) — local Ed25519 PEM at ``APATCH_AGENT_KEY``.

    Explicit environment values override the corresponding workspace values;
    an env ``APATCH_AGENT_KEY`` also selects PEM when the env does not select a
    command backend. Returns ``None`` when no usable key material is configured
    (ephemeral).
    """
    env_backend = os.environ.get("APATCH_KEY_BACKEND", "").strip().lower() if use_environment else ""
    env_sign_cmd = os.environ.get("APATCH_AGENT_SIGN_CMD", "").strip() if use_environment else ""
    env_pubkey = os.environ.get("APATCH_AGENT_PUBKEY", "").strip() if use_environment else ""
    env_key_path = os.environ.get("APATCH_AGENT_KEY", "").strip() if use_environment else ""
    workspace_backend = (backend or "").strip().lower()
    if env_backend:
        backend = env_backend
    elif env_sign_cmd:
        # Preserve command-backend auto-detection from the env contract.
        backend = "command"
    elif env_key_path:
        # An explicitly configured env PEM must outrank a workspace backend.
        backend = "pem"
    else:
        backend = workspace_backend
    sign_cmd = env_sign_cmd or (sign_cmd or "").strip()
    pubkey_src = env_pubkey or (public_key or "").strip()

    if backend == "command" or (not backend and sign_cmd):
        pub_b64 = _read_pubkey_b64(pubkey_src) if pubkey_src else None
        if not sign_cmd or not pub_b64:
            return None
        try:
            return CommandKeyProvider(sign_cmd, pub_b64, key_id=agent_id), "command", None
        except CommandSigningError:
            raise
        except Exception:
            return None

    if env_key_path:
        key_path = env_key_path
    if not key_path or not os.path.isfile(key_path):
        return None
    try:
        return _PemKeyProvider(key_path, key_id=agent_id), "pem", key_path
    except Exception:
        return None


def load_local_identity(workspace_root: Optional[str] = None) -> Optional[LocalIdentity]:
    """Resolve the enrolled signing identity from env or workspace file.

    Precedence: ``APATCH_AGENT_*`` env vars, then
    ``.apatch/agent-identity.json`` under *workspace_root*. Missing material
    falls back to ephemeral signing (returns ``None``).
    """
    if active_workspace_identity_scope() is not None:
        return _scoped_identity(workspace_root)
    from apatch.workspace_identity import load_workspace_identity

    agent_id = os.environ.get("APATCH_AGENT_ID", "").strip()
    key_path = os.environ.get("APATCH_AGENT_KEY", "").strip()
    cert_path = os.environ.get("APATCH_AGENT_CERT", "").strip() or None

    ws = load_workspace_identity(workspace_root)
    backend = None
    sign_cmd = None
    public_key = None
    if ws:
        agent_id = agent_id or ws["agent_id"]
        key_path = key_path or ws.get("key", "")
        cert_path = cert_path or ws.get("cert")
        backend = ws.get("key_backend")
        sign_cmd = ws.get("sign_cmd")
        public_key = ws.get("public_key")

    if not agent_id:
        return None
    resolved = resolve_key_provider(
        agent_id,
        key_path=key_path or None,
        backend=backend,
        sign_cmd=sign_cmd,
        public_key=public_key,
    )
    if resolved is None:
        return None
    provider, backend, resolved_key_path = resolved
    cert_path = cert_path if (cert_path and os.path.isfile(cert_path)) else None
    return LocalIdentity(
        agent_id=agent_id,
        key_path=resolved_key_path,
        cert_path=cert_path,
        key_provider=provider,
        backend=backend,
    )


def anchor_status(workspace_root: Optional[str] = None) -> dict:
    """Describe the trust-anchor level of apatch's ledger signatures (for doctor).

    Returns a dict with:

    * ``level``    — ``"ca-issued"`` (enrolled identity → root) or
      ``"ephemeral"`` (self-signed dev key, insecure).
    * ``secure``   — bool; True only for ``ca-issued``.
    * ``agent_id`` — enrolled CN when present.
    * ``hint``     — operator guidance when not anchored.
    """
    ident = load_local_identity(workspace_root)
    if ident is None:
        return {
            "level": "ephemeral",
            "secure": False,
            "agent_id": None,
            "has_cert": False,
            "backend": None,
            "hint": (
                "Ledger is signed with an ephemeral key (dev-only, self-signed). "
                "Enroll apatch as a TrustChain agent (tc cert request / "
                "POST /api/enroll/csr) and set APATCH_AGENT_ID + APATCH_AGENT_KEY "
                "to anchor signatures to the TrustChain root CA (RFP-005 §5.2)."
            ),
        }
    return {
        "level": "ca-issued",
        "secure": True,
        "agent_id": ident.agent_id,
        "has_cert": ident.cert_path is not None,
        "backend": ident.backend,
        "hint": None,
    }


# ── Anchor verification (Ring 2 / CI gate) ──────────────────────────────────


def _read_pem(source: Optional[str]) -> Optional[str]:
    """Return PEM text from a file path or a literal PEM string (or None)."""
    if not source:
        return None
    s = source.strip()
    if "-----BEGIN" in s:
        return s
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as fh:
            return fh.read()
    return None


def _fetch_registry_bundle(registry_base: str, agent_id: str):
    """Fetch root/intermediate/agent/CRL PEMs from a TrustChain pub registry."""
    import urllib.parse
    import urllib.request

    base = registry_base.rstrip("/")

    def _get(path: str) -> str:
        with urllib.request.urlopen(f"{base}{path}", timeout=15) as resp:  # noqa: S310
            return resp.read().decode("utf-8")

    quoted = urllib.parse.quote(agent_id, safe="")
    root_pem = _get("/api/pub/root-ca")
    int_pem = _get("/api/pub/ca")
    agent_pem = _get(f"/api/pub/agents/{quoted}/cert")
    try:
        crl_pem = _get("/api/pub/crl")
    except Exception:
        crl_pem = None
    return root_pem, int_pem, agent_pem, crl_pem


def verify_anchor(
    *,
    root_ca: Optional[str] = None,
    intermediate: Optional[str] = None,
    leaf_cert: Optional[str] = None,
    signer_key: Optional[str] = None,
    registry_base: Optional[str] = None,
    agent_id: Optional[str] = None,
    crl: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Verify apatch's signing identity chains to a pinned TrustChain root.

    Two source modes (mirrors ``tc-verify --full-chain``):

    * **PEM files/strings:** ``root_ca`` + ``intermediate`` + ``leaf_cert``.
    * **Live registry:** ``registry_base`` + ``agent_id`` → fetches
      ``/api/pub/{root-ca,ca,agents/<id>/cert,crl}``.

    Checks performed:

    1. PKIX chain — root signs intermediate, intermediate signs leaf.
    1b. Validity — every certificate in the chain is inside its own date range.
    2. CRL — leaf serial not revoked (when a CRL is available).
    3. Key binding — the leaf's Ed25519 key equals the key apatch signs with
       (derived from ``signer_key`` PEM, or ``APATCH_AGENT_KEY``). This proves
       the *signing* key — not just some cert — is anchored to the root.

    Returns a structured dict; ``ok`` is True only when every requested check
    passes. Falls back to env (``APATCH_ROOT_CA``, ``APATCH_INTERMEDIATE_CA``,
    ``APATCH_AGENT_CERT``, ``APATCH_AGENT_KEY``, ``APATCH_AGENT_ID``) for any
    unset argument.
    """
    import base64 as _b64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    from cryptography.x509 import (
        load_pem_x509_certificate,
        load_pem_x509_crl,
    )

    errors: list[str] = []
    root_ca = root_ca or os.environ.get("APATCH_ROOT_CA", "").strip() or None
    intermediate = (
        intermediate or os.environ.get("APATCH_INTERMEDIATE_CA", "").strip() or None
    )
    leaf_cert = leaf_cert or os.environ.get("APATCH_AGENT_CERT", "").strip() or None
    signer_key = signer_key or os.environ.get("APATCH_AGENT_KEY", "").strip() or None
    agent_id = agent_id or os.environ.get("APATCH_AGENT_ID", "").strip() or None
    registry_base = (
        registry_base or os.environ.get("APATCH_PLATFORM_URL", "").strip() or None
    )

    root_pem = int_pem = leaf_pem = crl_pem = None
    source = None
    if root_ca and intermediate and leaf_cert:
        source = "pem"
        root_pem = _read_pem(root_ca)
        int_pem = _read_pem(intermediate)
        leaf_pem = _read_pem(leaf_cert)
        crl_pem = _read_pem(crl)
    elif registry_base and agent_id:
        source = "registry"
        try:
            root_pem, int_pem, leaf_pem, crl_pem = _fetch_registry_bundle(
                registry_base, agent_id
            )
        except Exception as e:
            return {
                "ok": False,
                "source": source,
                "errors": [f"registry fetch failed: {e}"],
            }
    else:
        return {
            "ok": False,
            "source": None,
            "errors": [
                "need either (root_ca + intermediate + leaf_cert) or "
                "(registry_base + agent_id); set them or the APATCH_* env vars"
            ],
        }

    if not (root_pem and int_pem and leaf_pem):
        missing = [
            n
            for n, v in (("root", root_pem), ("intermediate", int_pem), ("leaf", leaf_pem))
            if not v
        ]
        return {
            "ok": False,
            "source": source,
            "errors": [f"could not load PEM material: {', '.join(missing)}"],
        }

    chain_ok = False
    leaf = None
    try:
        root = load_pem_x509_certificate(root_pem.encode("utf-8"))
        inter = load_pem_x509_certificate(int_pem.encode("utf-8"))
        leaf = load_pem_x509_certificate(leaf_pem.encode("utf-8"))
        root.public_key().verify(inter.signature, inter.tbs_certificate_bytes)
        inter.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes)
        chain_ok = True
    except Exception as e:
        errors.append(f"PKIX chain verification failed: {e}")

    # A chain that verifies cryptographically can still be worthless: PKIX
    # validity is a date range, and an expired leaf proves nothing about today.
    validity_ok = None
    if chain_ok:
        moment = now or datetime.now(timezone.utc)
        validity_ok = True
        for label, cert in (("root", root), ("intermediate", inter), ("leaf", leaf)):
            try:
                starts = cert.not_valid_before_utc
                ends = cert.not_valid_after_utc
            except Exception as e:  # pragma: no cover - malformed certificate
                validity_ok = False
                errors.append(f"{label} validity could not be read: {e}")
                continue
            if moment < starts:
                validity_ok = False
                errors.append(f"{label} certificate is not valid until {starts.isoformat()}")
            elif moment > ends:
                validity_ok = False
                errors.append(f"{label} certificate expired at {ends.isoformat()}")

    revoked = None
    if leaf is not None and crl_pem:
        try:
            crl_obj = load_pem_x509_crl(crl_pem.encode("utf-8"))
            revoked = (
                crl_obj.get_revoked_certificate_by_serial_number(leaf.serial_number)
                is not None
            )
            if revoked:
                errors.append(f"leaf serial {leaf.serial_number} is on CRL")
        except Exception as e:
            errors.append(f"CRL check failed: {e}")

    key_match = None
    leaf_pub_b64 = None
    if leaf is not None and chain_ok:
        try:
            leaf_pk = leaf.public_key()
            if not isinstance(leaf_pk, Ed25519PublicKey):
                errors.append("leaf public key is not Ed25519")
            else:
                leaf_raw = leaf_pk.public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
                leaf_pub_b64 = _b64.b64encode(leaf_raw).decode("ascii")
                signing_pub = None
                if signer_key:
                    signing_pub = _PemKeyProvider(
                        signer_key, key_id=agent_id or "apatch"
                    ).get_public_key()
                else:
                    # Hard-KMS (command backend): no PEM, use the published pubkey.
                    pub_src = os.environ.get("APATCH_AGENT_PUBKEY", "").strip()
                    if pub_src:
                        pub_b64 = _read_pubkey_b64(pub_src)
                        if pub_b64:
                            signing_pub = _b64.b64decode(pub_b64)
                if signing_pub is not None:
                    key_match = signing_pub == leaf_raw
                    if not key_match:
                        errors.append(
                            "signing key does not match leaf certificate — the "
                            "ledger is signed by a key the cert does not certify"
                        )
        except Exception as e:
            errors.append(f"key binding check failed: {e}")

    # ok = chain valid, not revoked, and (if a signer key was given) it matches.
    ok = chain_ok and not errors
    return {
        "ok": ok,
        "source": source,
        "chain_ok": chain_ok,
        "validity_ok": validity_ok,
        "revoked": revoked,
        "key_match": key_match,
        "agent_id": agent_id,
        "leaf_public_key": leaf_pub_b64,
        "errors": errors,
    }


# ── Enrollment bridge (obtain a root-anchored identity) ─────────────────────


def enroll_agent(
    *,
    invitation: str,
    platform_url: str,
    out_dir: str,
    agent_id: Optional[str] = None,
) -> dict:
    """Enroll apatch as a TrustChain agent via the ``tc`` CLI (``tc cert request``).

    Bridges to the OSS ``trust_chain`` CLI, which generates a local Ed25519 key,
    submits the public key with the invitation to ``POST /api/enroll/csr``, and
    saves ``agent.key`` / ``agent.crt`` / ``ca.pem`` / ``root-ca.pem`` into
    ``out_dir``. Returns the resolved paths so the operator can export
    ``APATCH_AGENT_ID`` / ``APATCH_AGENT_KEY`` / ``APATCH_AGENT_CERT``.

    Requires the ``tc`` binary on PATH (``pip install trustchain``).
    """
    import shutil
    import subprocess

    if shutil.which("tc") is None:
        return {
            "ok": False,
            "error": "tc CLI not found on PATH — install with `pip install trustchain`",
        }
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "tc",
        "cert",
        "request",
        "--auto",
        "-i",
        invitation,
        "-p",
        platform_url.rstrip("/"),
        "-o",
        out_dir,
    ]
    if agent_id:
        cmd += ["--agent-id", agent_id]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return {"ok": False, "error": f"tc cert request failed to start: {e}"}
    if res.returncode != 0:
        return {
            "ok": False,
            "error": f"tc cert request exited {res.returncode}",
            "stderr": (res.stderr or "").strip()[-2000:],
        }

    def _path_if(name: str) -> Optional[str]:
        p = os.path.join(out_dir, name)
        return p if os.path.isfile(p) else None

    key_path = _path_if("agent.key")
    cert_path = _path_if("agent.crt")
    return {
        "ok": key_path is not None,
        "out_dir": os.path.abspath(out_dir),
        "key_path": key_path,
        "cert_path": cert_path,
        "ca_path": _path_if("ca.pem"),
        "root_ca_path": _path_if("root-ca.pem"),
        "next_steps": [
            f"export APATCH_AGENT_ID={agent_id or '<agent-id-from-cert>'}",
            f"export APATCH_AGENT_KEY={key_path or os.path.join(out_dir, 'agent.key')}",
            f"export APATCH_AGENT_CERT={cert_path or os.path.join(out_dir, 'agent.crt')}",
        ],
    }
