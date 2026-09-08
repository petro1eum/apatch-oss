"""Push signed apatch steps to TrustChain Platform verifiable log."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


def _canonical_log_envelope(
    *,
    tool: str,
    agent_id: str,
    data: dict,
    timestamp: float,
    nonce: str,
    parent_hash: Optional[str],
    metadata: Optional[dict],
) -> bytes:
    return json.dumps(
        {
            "tool": tool,
            "agent_id": agent_id,
            "data": data,
            "timestamp": timestamp,
            "nonce": nonce,
            "parent_hash": parent_hash,
            "metadata": metadata or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def push_step(
    *,
    tool: str,
    data: dict,
    base_url: str,
    agent_id: str,
    private_key,
    metadata: Optional[dict] = None,
    timeout: float = 10.0,
    http_client=None,
    return_op_id: bool = False,
):
    """Fetch merkle root, sign envelope, POST /api/log/append.

    Returns ``False`` on any failure. On success returns ``True`` by default,
    or — when ``return_op_id=True`` — the server-reported ``op_id`` string
    (content-addressable; usable for ``GET /api/pub/log/proof/{op_id}`` once
    the async flusher commits). Falls back to ``True`` if the server response
    omits an op_id, preserving backward compatibility.
    """
    if httpx is None and http_client is None:
        return False

    base = base_url.rstrip("/")
    try:
        if http_client is not None:
            client = http_client
            close = False
        else:
            client = httpx.Client(timeout=timeout)
            close = True
        try:
            root_resp = client.get(f"{base}/api/pub/log/merkle-root")
            if root_resp.status_code != 200:
                return False
            parent_hash = root_resp.json().get("merkle_root")

            timestamp = time.time()
            nonce = uuid.uuid4().hex
            meta = dict(metadata or {})
            envelope = _canonical_log_envelope(
                tool=tool,
                agent_id=agent_id,
                data=data,
                timestamp=timestamp,
                nonce=nonce,
                parent_hash=parent_hash,
                metadata=meta,
            )
            signature = private_key.sign(envelope).hex()

            append_resp = client.post(
                f"{base}/api/log/append",
                json={
                    "tool": tool,
                    "agent_id": agent_id,
                    "data": data,
                    "timestamp": timestamp,
                    "nonce": nonce,
                    "parent_hash": parent_hash,
                    "signature": signature,
                    "metadata": meta,
                },
            )
            if append_resp.status_code != 200:
                return False
            if return_op_id:
                try:
                    op_id = append_resp.json().get("op_id")
                except Exception:
                    op_id = None
                return op_id or True
            return True
        finally:
            if close:
                client.close()
    except Exception:
        return False


def push_revert(
    *,
    target_op_id: str,
    reason: str,
    base_url: str,
    agent_id: str,
    private_key,
    timeout: float = 10.0,
) -> bool:
    """Push a compensating tc_revert entry to the platform log."""
    return push_step(
        tool="tc_revert",
        data={"action": "revert", "target_op": target_op_id, "reason": reason},
        base_url=base_url,
        agent_id=agent_id,
        private_key=private_key,
        metadata={"compensation": True},
        timeout=timeout,
    )


def platform_config_from_env() -> Optional[dict]:
    url = os.environ.get("APATCH_PLATFORM_URL", "").strip()
    agent_id = os.environ.get("APATCH_AGENT_ID", "").strip()
    key_path = os.environ.get("APATCH_AGENT_KEY", "").strip()
    tenant_id = os.environ.get("APATCH_TENANT_ID", "").strip() or None
    if not url or not agent_id or not key_path:
        return None
    return {"base_url": url, "agent_id": agent_id, "key_path": key_path, "tenant_id": tenant_id}
