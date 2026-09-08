"""Per-workspace enrolled agent identity (.apatch/agent-identity.json)."""

from __future__ import annotations

import json
import os
from typing import Any, Optional


def load_workspace_identity(workspace_root: Optional[str]) -> Optional[dict[str, Any]]:
    """Load a PEM or command-backed ``.apatch/agent-identity.json``.

    Supported schemas are::

        {"agent_id": "...", "key": "/path/agent.key", "cert": "..."}

    and::

        {
          "agent_id": "...",
          "key_backend": "command",
          "sign_cmd": "/path/signer --flag",
          "public_key": "<base64 raw Ed25519 public key>",
          "cert": "..."
        }

    ``cert`` is optional in both forms. Invalid or incomplete identities are
    ignored so callers can safely fall back to ephemeral signing.
    """
    if not workspace_root:
        return None
    path = os.path.join(os.path.abspath(workspace_root), ".apatch", "agent-identity.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    agent_id = str(data.get("agent_id") or "").strip()
    if not agent_id:
        return None
    cert = data.get("cert")
    cert_path = str(cert).strip() if cert else None
    backend = str(data.get("key_backend") or "pem").strip().lower()
    if backend == "command":
        sign_cmd = str(data.get("sign_cmd") or "").strip()
        public_key = str(data.get("public_key") or "").strip()
        if not sign_cmd or not public_key:
            return None
        return {
            "agent_id": agent_id,
            "key_backend": "command",
            "sign_cmd": sign_cmd,
            "public_key": public_key,
            "cert": cert_path,
        }
    if backend != "pem":
        return None
    key_path = str(data.get("key") or "").strip()
    if not key_path:
        return None
    return {
        "agent_id": agent_id,
        "key_backend": "pem",
        "key": key_path,
        "cert": cert_path,
    }
