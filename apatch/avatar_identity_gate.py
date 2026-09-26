"""The Avatar BFF refuses an owner it cannot resolve; read that refusal exactly.

trust-chain.ai resolves the caller's HC professional on every owner operation,
so a lane that ran an hour ago can be refused now. The contract
(bundled ``apatch._vendor.avatar_contract.transport`` ``avatar_bff`` 1.0.2,
block ``identity_gate``)
splits the refusals by HTTP code:

* ``409`` — the person has no connected HC profile, or HC reports it inactive.
  Retrying changes nothing until they act, so the lane stops and says so.
* ``503`` — the site cannot answer about the profile right now. Retryable.

Everything here reads a response that already arrived; nothing performs a
network operation, and the outbox is never touched.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: 409 — the owner must connect or reactivate their HC profile first.
IDENTITY_GATE_BLOCKING = ("hc_identity_unbound", "hc_identity_inactive")
#: 503 — the site could not resolve the owner this time.
IDENTITY_GATE_RETRYABLE = (
    "integration_misconfigured",
    "identity_unavailable",
    "invalid_response",
)
IDENTITY_GATE_STATUSES = IDENTITY_GATE_BLOCKING + IDENTITY_GATE_RETRYABLE
_BY_CODE = {409: IDENTITY_GATE_BLOCKING, 503: IDENTITY_GATE_RETRYABLE}


def identity_gate_refusal(response: Any) -> Optional[Dict[str, Any]]:
    """``{"status", "retryable", "status_code"}`` when this is a gate refusal.

    ``None`` for everything else — including a 409 that means something else on
    the same route (a review conflict, say), which is why the detail decides and
    never the code alone.
    """
    code = getattr(response, "status_code", None)
    expected = _BY_CODE.get(code)
    if expected is None:
        return None
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a body that is not JSON is not a refusal
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, str) or detail not in expected:
        return None
    return {
        "status": detail,
        "retryable": detail not in IDENTITY_GATE_BLOCKING,
        "status_code": code,
    }


def identity_gate_error(refusal: Dict[str, Any]) -> Dict[str, Any]:
    """One error entry naming the refusal, for a lane result's ``errors``."""
    return {
        "status_code": refusal["status_code"],
        "status": refusal["status"],
        "detail": refusal["status"],
        "action_required": (
            "connect_hc_profile" if not refusal["retryable"] else "retry_later"
        ),
    }
