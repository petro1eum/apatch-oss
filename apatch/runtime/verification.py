"""Verification status projection (RFP-004 verify status facade)."""

from __future__ import annotations

import os
from typing import Any, Dict

from apatch.doctor import run_doctor
from apatch.runtime.domain import SCHEMA_VERSION
from apatch.runtime.session import build_session_view


def build_verification_status(target_dir: str) -> Dict[str, Any]:
    """Unified verification / policy / session phase view for agents and CLI."""
    root = os.path.abspath(target_dir)
    session_view = build_session_view(root)
    doctor = run_doctor(root)
    s = session_view.get("session") or {}
    pol = session_view.get("policy") or {}
    tc = doctor.get("trustchain") or {}
    sb = doctor.get("sandbox") or {}
    enf = doctor.get("enforcement") or {}

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "workspace": root,
        "session": {
            "session_id": s.get("session_id"),
            "intent": s.get("intent"),
            "lifecycle": s.get("lifecycle"),
            "phase": s.get("phase"),
            "checkpoint": s.get("checkpoint"),
            "risk_level": s.get("risk_level"),
            "next_action": s.get("next_action"),
            "failure": s.get("failure"),
        },
        "verification": {
            "recommended": doctor.get("recommended_verify") or "",
            "recommended_resolved": doctor.get("recommended_verify_resolved") or "",
            "trustchain_mode": tc.get("mode"),
            "sandbox_mode": sb.get("mode"),
            "enforcement_active": bool(enf.get("active")),
            "warnings": (doctor.get("warnings") or [])[:5],
        },
        "policy": pol,
        "invariant_satisfied": (session_view.get("invariant") or {}).get("satisfied"),
    }
