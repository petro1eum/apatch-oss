"""Named signer identity on TrustChain ledger rows (Engineering Truth / HC attribution).

TrustChain stores ``key_id`` on the signed envelope (enrolled agent CN). apatch and
Human_Capital share the same resolution order for display and attribution reports (SPEC-LEDGER-ACTOR-1 R1).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def ledger_signed_by(row: Dict[str, Any]) -> Optional[str]:
    """Resolve human/agent identity for a normalized ledger row."""
    key_id = row.get("key_id")
    if key_id:
        return str(key_id)
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for field in ("signed_by", "agent_id", "key_id"):
        val = payload.get(field)
        if val:
            return str(val)
    return None


def enrich_payload_with_actor(payload: dict, *, agent_id: Optional[str]) -> dict:
    """Stamp ``signed_by`` / ``agent_id`` on commit payload when enrolled."""
    if not agent_id:
        return payload
    out = dict(payload)
    out.setdefault("signed_by", agent_id)
    out.setdefault("agent_id", agent_id)
    out.setdefault("key_id", agent_id)
    return out