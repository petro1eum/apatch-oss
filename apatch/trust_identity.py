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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


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
        import subprocess

        proc = subprocess.run(
            self._cmd, input=data, capture_output=True, timeout=30
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"sign command failed (exit {proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace')[-500:]}"
            )
        sig = _b64.b64decode(proc.stdout.strip())
        # Fail-closed: verify the device actually produced a valid signature.
        if not self.verify(data, sig):
            raise RuntimeError("sign command returned a signature that does not verify")
        return sig

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
    env_backend = os.environ.get("APATCH_KEY_BACKEND", "").strip().lower()
    env_sign_cmd = os.environ.get("APATCH_AGENT_SIGN_CMD", "").strip()
    env_pubkey = os.environ.get("APATCH_AGENT_PUBKEY", "").strip()
    env_key_path = os.environ.get("APATCH_AGENT_KEY", "").strip()
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
