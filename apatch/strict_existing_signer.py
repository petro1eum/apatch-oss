"""Opt-in binding for an already resolved signer; this is not enrollment authority.

This module reads only through the native identity resolver.  It neither creates
identity material nor substitutes another provider when resolution fails.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict


class ExistingSignerRefused(RuntimeError):
    """Finite refusal, with ambiguous signing effects preserved for reconciliation."""

    def __init__(self, code: str, *, reconciliation_required: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.reconciliation_required = reconciliation_required

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "committed": None if self.reconciliation_required else False,
            "error_type": "EXISTING_SIGNER_REFUSED",
            "error_code": self.code,
            "reconciliation_required": self.reconciliation_required,
            "recommended_action": (
                "reconcile_native_commit"
                if self.reconciliation_required
                else "verify_existing_signer_binding"
            ),
        }


@dataclass(frozen=True)
class ExistingSignerBinding:
    """Expected native subject and raw Ed25519 public-key digest, not a grant.

    Enrollment, certificate validity, writer exclusivity and the persisted
    sequence/head are independent prerequisites checked by the owning caller.
    A PEM provider is valid; this type does not require an external command.
    """

    agent_id: str
    public_key_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.agent_id, str)
            or not self.agent_id
            or self.agent_id != self.agent_id.strip()
            or len(self.agent_id) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in self.agent_id)
            or not isinstance(self.public_key_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.public_key_sha256) is None
        ):
            raise ExistingSignerRefused("EXISTING_SIGNER_EXPECTATION_INVALID")

    def resolve(self, workspace_root: str):
        """Resolve through the unchanged native loader and require the exact key."""
        try:
            from apatch.trust_identity import load_local_identity

            identity = load_local_identity(workspace_root)
        except Exception:
            raise ExistingSignerRefused("EXISTING_SIGNER_RESOLUTION_FAILED") from None
        if identity is None or identity.key_provider is None:
            raise ExistingSignerRefused("EXISTING_SIGNER_UNAVAILABLE")
        try:
            provider = identity.key_provider
            public = provider.get_public_key()
            key_id = provider.get_key_id()
            agent_id = identity.agent_id
        except Exception:
            raise ExistingSignerRefused("EXISTING_SIGNER_PROVIDER_INVALID") from None
        if (
            agent_id != self.agent_id
            or key_id != self.agent_id
            or not isinstance(public, bytes)
            or len(public) != 32
            or hashlib.sha256(public).hexdigest() != self.public_key_sha256
        ):
            raise ExistingSignerRefused("EXISTING_SIGNER_BINDING_MISMATCH")
        return identity

    def require_native_key(self, trustchain) -> None:
        """Use public native accessors to bind the constructed signer before sign."""
        try:
            key_id = trustchain.get_key_id()
            public = base64.b64decode(trustchain.export_public_key(), validate=True)
        except Exception:
            raise ExistingSignerRefused("EXISTING_SIGNER_NATIVE_KEY_INVALID") from None
        if (
            key_id != self.agent_id
            or len(public) != 32
            or hashlib.sha256(public).hexdigest() != self.public_key_sha256
        ):
            raise ExistingSignerRefused("EXISTING_SIGNER_NATIVE_KEY_MISMATCH")

    def require_payload_actor(self, payload: dict) -> None:
        for name in ("signed_by", "agent_id", "key_id"):
            if name in payload and payload[name] != self.agent_id:
                raise ExistingSignerRefused("EXISTING_SIGNER_PAYLOAD_ACTOR_MISMATCH")
