"""Push signed apatch steps to TrustChain Platform verifiable log.

Two lanes exist on the Platform:

* the best-effort lane (``push_step``): the envelope's ``parent_hash`` is the
  Platform's current Merkle root and the append is queued asynchronously;
* the durable execution-evidence lane (``push_durable_step``): the client first
  asks for its own signed evidence head, binds the new envelope to that head's
  signature as ``parent_hash``, marks the envelope with an evidence schema and
  gets a synchronous completion back (``durable`` / ``rebind_required`` /
  ``failed``). That is the protocol Platform PR #57 introduced; without it a
  push is recorded, but nothing says it was committed.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

EVIDENCE_HEAD_REQUEST_SCHEMA = "trustchain.execution_evidence.head-request.v1"
EVIDENCE_HEAD_SCHEMA = "trustchain.execution_evidence.head.v1"
EVIDENCE_COMPLETION_SCHEMA = "trustchain.execution_evidence.completion.v1"
EXECUTION_EVIDENCE_SCHEMA = "trustchain.tool_execution_evidence.v2"
_HEAD_NONCE_RE = re.compile(r"^tchead_[0-9a-f]{32}$")


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


def _strict_canonical_json(value: Any) -> bytes:
    """Byte-for-byte what the Platform signs evidence-head requests over."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def certificate_identity(cert_path: str) -> Dict[str, str]:
    """The serial and fingerprint the Platform binds an evidence head to.

    The fingerprint is the Platform's display form: the first 24 hex digits of
    the SHA-256 over the DER certificate.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    with open(cert_path, "rb") as fh:
        cert = x509.load_pem_x509_certificate(fh.read())
    return {
        "serial": str(cert.serial_number),
        "fingerprint": cert.fingerprint(hashes.SHA256()).hex()[:24],
    }


def _client(http_client, timeout: float):
    if http_client is not None:
        return http_client, False
    return httpx.Client(timeout=timeout), True


def fetch_evidence_head(
    *,
    base_url: str,
    agent_id: str,
    tenant_id: str,
    private_key,
    certificate: Dict[str, str],
    timeout: float = 10.0,
    http_client=None,
) -> Dict[str, Any]:
    """Ask the Platform for this agent's prior durable envelope signature."""
    if httpx is None and http_client is None:
        return {"ok": False, "status": "transport_unavailable"}
    base = base_url.rstrip("/")
    nonce = "tchead_" + uuid.uuid4().hex
    requested_at_ms = int(time.time() * 1000)
    signed = {
        "schema": EVIDENCE_HEAD_REQUEST_SCHEMA,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "nonce": nonce,
        "requested_at_ms": requested_at_ms,
        "certificate_serial": certificate["serial"],
        "certificate_fingerprint": certificate["fingerprint"].lower(),
    }
    headers = {
        "X-TC-Head-Schema": EVIDENCE_HEAD_REQUEST_SCHEMA,
        "X-TC-Tenant-ID": tenant_id,
        "X-TC-Agent-ID": agent_id,
        "X-TC-Request-Nonce": nonce,
        "X-TC-Requested-At-Ms": str(requested_at_ms),
        "X-TC-Agent-Cert-Serial": certificate["serial"],
        "X-TC-Agent-Cert-Fingerprint": certificate["fingerprint"].lower(),
        "X-TC-Agent-Signature": private_key.sign(_strict_canonical_json(signed)).hex(),
    }
    client, close = _client(http_client, timeout)
    try:
        response = client.get(f"{base}/api/log/evidence-heads/{agent_id}", headers=headers)
    except Exception as exc:
        return {"ok": False, "status": "head_unreachable", "detail": type(exc).__name__}
    finally:
        if close:
            client.close()
    if response.status_code != 200:
        return {
            "ok": False,
            "status": f"head_http_{response.status_code}",
            "detail": str(getattr(response, "text", ""))[:200],
        }
    try:
        body = response.json()
    except Exception:
        return {"ok": False, "status": "head_invalid"}
    if not isinstance(body, dict) or body.get("schema") != EVIDENCE_HEAD_SCHEMA:
        return {"ok": False, "status": "head_schema_mismatch"}
    if body.get("agent_id") != agent_id or body.get("tenant_id") != tenant_id:
        return {"ok": False, "status": "head_identity_mismatch"}
    return {
        "ok": True,
        "parent_hash": body.get("parent_hash"),
        "evidence_op_id": body.get("evidence_op_id"),
    }


def _append(
    client,
    *,
    base: str,
    tool: str,
    data: dict,
    agent_id: str,
    private_key,
    parent_hash: Optional[str],
    metadata: dict,
    tenant_id: Optional[str],
):
    timestamp = time.time()
    nonce = uuid.uuid4().hex
    envelope = _canonical_log_envelope(
        tool=tool,
        agent_id=agent_id,
        data=data,
        timestamp=timestamp,
        nonce=nonce,
        parent_hash=parent_hash,
        metadata=metadata,
    )
    body = {
        "tool": tool,
        "agent_id": agent_id,
        "data": data,
        "timestamp": timestamp,
        "nonce": nonce,
        "parent_hash": parent_hash,
        "signature": private_key.sign(envelope).hex(),
        "metadata": metadata,
    }
    if tenant_id:
        body["tenant_id"] = tenant_id
    return client.post(f"{base}/api/log/append", json=body)


def push_durable_step(
    *,
    tool: str,
    data: dict,
    base_url: str,
    agent_id: str,
    tenant_id: str,
    private_key,
    certificate: Dict[str, str],
    metadata: Optional[dict] = None,
    timeout: float = 10.0,
    http_client=None,
    max_rebinds: int = 1,
) -> Dict[str, Any]:
    """Commit one step through the Platform's durable execution-evidence lane.

    Returns a dict that always carries ``ok`` and ``state``. ``durable`` is the
    only state in which the Platform has committed the record; it then also
    carries ``op_id`` (the logical evidence id), ``durable_log_op_id`` (the log
    record usable for inclusion proofs) and ``next_parent_hash``. Any other
    state names exactly where the protocol stopped, so the caller can report
    it instead of guessing.
    """
    if httpx is None and http_client is None:
        return {"ok": False, "state": "transport_unavailable"}
    base = base_url.rstrip("/")
    meta = dict(metadata or {})
    meta["schema"] = EXECUTION_EVIDENCE_SCHEMA
    meta["tenant_id"] = tenant_id
    client, close = _client(http_client, timeout)
    try:
        head = fetch_evidence_head(
            base_url=base,
            agent_id=agent_id,
            tenant_id=tenant_id,
            private_key=private_key,
            certificate=certificate,
            timeout=timeout,
            http_client=client,
        )
        if not head.get("ok"):
            return {"ok": False, "state": "head_unavailable", "head": head}
        parent_hash = head.get("parent_hash")
        for attempt in range(max_rebinds + 1):
            try:
                response = _append(
                    client,
                    base=base,
                    tool=tool,
                    data=data,
                    agent_id=agent_id,
                    private_key=private_key,
                    parent_hash=parent_hash,
                    metadata=meta,
                    tenant_id=tenant_id,
                )
            except Exception as exc:
                return {"ok": False, "state": "append_unreachable", "detail": type(exc).__name__}
            try:
                body = response.json() if response.status_code in (200, 409) else {}
            except Exception:
                body = {}
            completion = body.get("completion") if isinstance(body, dict) else None
            completion = completion if isinstance(completion, dict) else {}
            state = str(completion.get("state") or "")
            if response.status_code == 200 and state == "durable":
                return {
                    "ok": True,
                    "state": "durable",
                    "op_id": completion.get("op_id") or body.get("op_id"),
                    "durable_log_op_id": completion.get("durable_log_op_id"),
                    "next_parent_hash": completion.get("next_parent_hash"),
                    "attempts": attempt + 1,
                }
            if response.status_code == 409 and state == "rebind_required":
                if attempt >= max_rebinds:
                    return {
                        "ok": False,
                        "state": "rebind_required",
                        "required_parent_hash": completion.get("required_parent_hash"),
                        "attempts": attempt + 1,
                    }
                # Someone committed for this agent in between: bind to the
                # parent the Platform names and sign again.
                parent_hash = completion.get("required_parent_hash")
                continue
            if response.status_code == 409:
                return {
                    "ok": False,
                    "state": state or "failed",
                    "detail": completion.get("failure_reason") or body.get("message"),
                    "attempts": attempt + 1,
                }
            return {
                "ok": False,
                "state": f"append_http_{response.status_code}",
                "detail": str(getattr(response, "text", ""))[:200],
                "attempts": attempt + 1,
            }
        return {"ok": False, "state": "rebind_required", "attempts": max_rebinds + 1}
    finally:
        if close:
            client.close()


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
    """Fetch merkle root, sign envelope, POST /api/log/append (best-effort lane).

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
        client, close = _client(http_client, timeout)
        try:
            root_resp = client.get(f"{base}/api/pub/log/merkle-root")
            if root_resp.status_code != 200:
                return False
            parent_hash = root_resp.json().get("merkle_root")
            append_resp = _append(
                client,
                base=base,
                tool=tool,
                data=data,
                agent_id=agent_id,
                private_key=private_key,
                parent_hash=parent_hash,
                metadata=dict(metadata or {}),
                tenant_id=None,
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
    # The durable lane needs the agent certificate (serial + fingerprint are
    # part of the signed head request). Enrolment writes agent.crt next to
    # agent.key; APATCH_AGENT_CERT overrides.
    cert_path = os.environ.get("APATCH_AGENT_CERT", "").strip()
    if not cert_path:
        sibling = os.path.join(os.path.dirname(key_path), "agent.crt")
        cert_path = sibling if os.path.isfile(sibling) else ""
    return {
        "base_url": url,
        "agent_id": agent_id,
        "key_path": key_path,
        "tenant_id": tenant_id,
        "cert_path": cert_path or None,
    }


def durable_lane_configured(cfg: Optional[dict]) -> bool:
    """The durable lane needs a tenant binding and the agent certificate."""
    return bool(cfg and cfg.get("tenant_id") and cfg.get("cert_path"))
