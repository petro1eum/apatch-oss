"""Typed domain models (RFP-004)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Session:
    """Governed session aggregate — typed projection of session_state + lifecycle."""

    session_id: Optional[str]
    intent: Optional[str]
    artifacts: List[Dict[str, Any]]
    lifecycle: str
    phase: str
    checkpoint: Optional[str]
    risk_level: str
    next_action: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    failure: Optional[Dict[str, Any]]

    @classmethod
    def from_view(cls, view: Dict[str, Any]) -> Session:
        s = view.get("session") or {}
        failure = s.get("failure") if isinstance(s.get("failure"), dict) else None
        return cls(
            session_id=s.get("session_id"),
            intent=s.get("intent"),
            artifacts=s.get("artifacts") or [],
            lifecycle=s.get("lifecycle") or "draft",
            phase=s.get("phase") or "idle",
            checkpoint=s.get("checkpoint"),
            risk_level=s.get("risk_level") or "low",
            next_action=s.get("next_action"),
            started_at=s.get("started_at"),
            ended_at=s.get("ended_at"),
            failure=failure,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def active(self) -> bool:
        return bool(self.session_id) and not self.ended_at
