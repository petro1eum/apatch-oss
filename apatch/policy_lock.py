"""Signed policy — notarize the monitor's own configuration (RFP-005 §0.3).

The control-plane lock (RFP-005 §audit #1) stops the *agent channel* from
rewriting the monitor's config. But an out-of-channel edit (a terminal, another
editor, a compromised process) is not stopped — and, worse, is not *detectable*.

This module makes the policy **tamper-evident**: it computes a canonical
manifest of the monitor's config files (`.apatch/sandbox.json`,
`.apatch/enforcement.json`, `.apatch/remote.json`, `.cursor/hooks.json`) and
signs it with apatch's enrolled TrustChain identity, writing
`.apatch/policy.lock.json`. Any later change to a config file — through *any*
channel — is then detectable by re-hashing and verifying the signature
(`verify_policy`). `doctor` reports the status; `ci-gate` fails on drift under
enforcement.

The signature is anchored: when the signing key is the enrolled identity whose
leaf chains to the TrustChain root (§5.2–5.3), a forged lock would require the
root-anchored private key, not merely write access to the repo.
"""

from __future__ import annotations
from pathlib import Path

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apatch.enforcement import file_sha256

POLICY_LOCK_REL = os.path.join(".apatch", "policy.lock.json")

# The monitor's configuration surface — the files whose integrity defines the
# policy. Order-independent; only existing files are included in the manifest.
POLICY_FILES = (
    os.path.join(".apatch", "sandbox.json"),
    os.path.join(".apatch", "enforcement.json"),
    os.path.join(".apatch", "remote.json"),
    os.path.join(".cursor", "hooks.json"),
)


def policy_lock_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), POLICY_LOCK_REL)


def compute_policy_manifest(root: str = ".") -> Dict[str, Any]:
    """Canonical {rel_path: sha256} manifest of the monitor config that exists."""
    root = os.path.abspath(root)
    files: Dict[str, str] = {}
    for rel in POLICY_FILES:
        abs_p = os.path.join(root, rel)
        if os.path.isfile(abs_p):
            files[rel.replace("\\", "/")] = file_sha256(abs_p)
    return {"version": 1, "files": files}


def _canonical(manifest: Dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_policy_lock(root: str = ".") -> Optional[Dict[str, Any]]:
    path = policy_lock_path(root)
    if not os.path.isfile(path):
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def sign_policy(root: str = ".", *, identity: Any = None) -> Dict[str, Any]:
    """Sign the current policy manifest with apatch's enrolled identity.

    Writes ``.apatch/policy.lock.json``. Requires an enrolled identity
    (``APATCH_AGENT_ID`` + ``APATCH_AGENT_KEY``); without one the policy cannot
    be anchored and signing is refused (an ephemeral signature would be
    forgeable by anyone with repo access, defeating the purpose).
    """
    from apatch.trust_identity import anchor_status, load_local_identity

    identity = identity or load_local_identity(root)
    if identity is None:
        return {
            "ok": False,
            "error": "no enrolled identity — cannot anchor policy signature",
            "hint": (
                "Enroll apatch (apatch trustchain enroll ...) and set "
                "APATCH_AGENT_ID + APATCH_AGENT_KEY, then re-run policy sign."
            ),
        }

    manifest = compute_policy_manifest(root)
    payload = _canonical(manifest)
    signature = identity.key_provider.sign(payload)
    pub_raw = identity.key_provider.get_public_key()
    lock = {
        "version": 1,
        "manifest": manifest,
        "algorithm": "ed25519",
        "signer_key_id": identity.agent_id,
        "signer_public_key": base64.b64encode(pub_raw).decode("ascii"),
        "signature": signature.hex(),
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }
    path = policy_lock_path(root)
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.path.islink(path):
        return {
            "ok": False,
            "error": "policy lock path must not be a symbolic link",
        }
    temporary = os.path.join(
        directory,
        f".policy.lock.{os.getpid()}.{secrets.token_hex(8)}.tmp",
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
            descriptor = -1
            json.dump(lock, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    anchor = anchor_status(root)
    return {
        "ok": True,
        "lock_path": path,
        "files": list(manifest["files"].keys()),
        "signer_key_id": identity.agent_id,
        "anchor_secure": bool(anchor.get("secure")),
    }


def verify_policy(root: str = ".") -> Dict[str, Any]:
    """Verify the policy lock: signature valid and no config drift.

    Returns a structured dict:

    * ``signed``  — a ``policy.lock.json`` exists.
    * ``signature_ok`` — the lock's manifest is signed by the embedded key.
    * ``drift``   — config files whose hash diverged from the signed manifest
      (``added`` / ``removed`` / ``changed``).
    * ``secure``  — the signing key is the root-anchored enrolled identity.
    * ``ok``      — signed, signature valid, and no drift.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    lock = load_policy_lock(root)
    if lock is None:
        return {
            "ok": False,
            "signed": False,
            "reason": "policy not signed (.apatch/policy.lock.json absent)",
            "hint": "Run `apatch policy sign` after enrolling to make the policy tamper-evident.",
        }

    errors: List[str] = []
    signed_manifest = lock.get("manifest") or {}
    signature_ok = False
    try:
        pub_raw = base64.b64decode(lock["signer_public_key"])
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(
            bytes.fromhex(lock["signature"]), _canonical(signed_manifest)
        )
        signature_ok = True
    except Exception as exc:
        errors.append(f"signature invalid: {exc}")

    current = compute_policy_manifest(root)
    signed_files = (signed_manifest.get("files") or {}) if isinstance(signed_manifest, dict) else {}
    cur_files = current["files"]
    drift: Dict[str, List[str]] = {"added": [], "removed": [], "changed": []}
    for rel, sha in cur_files.items():
        if rel not in signed_files:
            drift["added"].append(rel)
        elif signed_files[rel] != sha:
            drift["changed"].append(rel)
    for rel in signed_files:
        if rel not in cur_files:
            drift["removed"].append(rel)
    has_drift = any(drift.values())
    if has_drift:
        errors.append("policy config drifted from the signed manifest")

    secure = _signer_is_anchored(lock, root=root)

    return {
        "ok": signature_ok and not has_drift,
        "signed": True,
        "signature_ok": signature_ok,
        "drift": drift,
        "has_drift": has_drift,
        "secure": secure,
        "signer_key_id": lock.get("signer_key_id"),
        "signed_at": lock.get("signed_at"),
        "errors": errors,
    }


def _signer_is_anchored(lock: Dict[str, Any], *, root: str = ".") -> bool:
    """True when the lock's signing key matches the enrolled (root-anchored) key."""
    from apatch.trust_identity import load_local_identity

    identity = load_local_identity(root)
    if identity is None:
        return False
    try:
        lock_pub = base64.b64decode(lock.get("signer_public_key", ""))
        return identity.key_provider.get_public_key() == lock_pub
    except Exception:
        return False


def policy_status(root: str = ".") -> Dict[str, Any]:
    """Compact policy posture for ``doctor`` (no raid on enforcement state)."""
    lock = load_policy_lock(root)
    if lock is None:
        return {"signed": False, "ok": False, "secure": False}
    res = verify_policy(root)
    return {
        "signed": True,
        "ok": res.get("ok", False),
        "signature_ok": res.get("signature_ok", False),
        "has_drift": res.get("has_drift", False),
        "secure": res.get("secure", False),
        "signer_key_id": res.get("signer_key_id"),
    }
