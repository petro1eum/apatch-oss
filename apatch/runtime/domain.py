"""Domain constants and lifecycle mapping (RFP-004)."""

from __future__ import annotations

from typing import Dict, Optional

SCHEMA_VERSION = 1

# RFP-004 session lifecycle (state machine)
LIFECYCLE_DRAFT = "draft"
LIFECYCLE_PLANNED = "planned"
LIFECYCLE_APPLYING = "applying"
LIFECYCLE_VERIFYING = "verifying"
LIFECYCLE_COMMITTED = "committed"
LIFECYCLE_FAILED = "failed"
LIFECYCLE_ATTESTED = "attested"
LIFECYCLE_ROLLED_BACK = "rolled_back"
LIFECYCLE_ENDED = "ended"

CORE_INVARIANT = (
    "Intent → Session → Mutation → Verification → Attestation | Rollback"
)

# Map persisted session_state.phase → domain lifecycle
_PHASE_TO_LIFECYCLE: Dict[str, str] = {
    "idle": LIFECYCLE_DRAFT,
    "plan": LIFECYCLE_PLANNED,
    "apply": LIFECYCLE_APPLYING,
    "verify": LIFECYCLE_VERIFYING,
    "arch": LIFECYCLE_VERIFYING,
    "db": LIFECYCLE_VERIFYING,
    "complete": LIFECYCLE_COMMITTED,
    "blocked": LIFECYCLE_FAILED,
    "rollback": LIFECYCLE_ROLLED_BACK,
}


def derive_lifecycle(
    phase: str,
    *,
    attested: bool = False,
    failure: Optional[dict] = None,
    ended: bool = False,
) -> str:
    if ended:
        return LIFECYCLE_ENDED
    if failure:
        return LIFECYCLE_FAILED
    lifecycle = _PHASE_TO_LIFECYCLE.get(phase or "idle", LIFECYCLE_DRAFT)
    if attested and lifecycle == LIFECYCLE_COMMITTED:
        return LIFECYCLE_ATTESTED
    return lifecycle
