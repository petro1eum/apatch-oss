"""Attestation (TrustChain evidence) domain view."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from apatch.doctor import build_trustchain_status, run_doctor
from apatch.enforcement import is_enforcement_enabled, notarized_index_path
from apatch.runtime.domain import SCHEMA_VERSION
from apatch.runtime.events import events_path, tail_events
from apatch.trustchain_helper import TrustChainHelper


def _read_head(trustchain_dir: str) -> str | None:
    head_file = os.path.join(trustchain_dir, "HEAD")
    try:
        with open(head_file, encoding="utf-8") as f:
            val = f.read().strip()
            return val or None
    except OSError:
        return None


def build_attestation_view(target_dir: str) -> Dict[str, Any]:
    """Cryptographic evidence separate from functional Verification."""
    root = os.path.abspath(target_dir)
    doctor = run_doctor(root)
    tc_block = doctor.get("trustchain") or {}
    tc = TrustChainHelper(root, auto_init=False)
    enforcement_on = is_enforcement_enabled(root)
    trustchain_status = build_trustchain_status(
        root,
        tc_active=tc.has_trustchain(),
        enforcement_on=enforcement_on,
    )
    head = _read_head(tc.trustchain_dir) if tc.has_trustchain() else None
    notary = notarized_index_path(root)
    recent = [
        e for e in tail_events(root, limit=10)
        if e.get("type") in ("AttestationCommitted", "MutationApplied", "SessionStarted")
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "workspace": root,
        "attestation": {
            "mode": trustchain_status.get("mode"),
            "summary": trustchain_status.get("summary"),
            "active": trustchain_status.get("active"),
            "path": trustchain_status.get("path"),
            "head": head,
            "behaviors": trustchain_status.get("behaviors"),
            "upgrade_to_enforce": trustchain_status.get("upgrade_to_enforce"),
        },
        "verification_note": (
            "Verification checks system health (tests, arch, sandbox). "
            "Attestation records who changed what (TrustChain)."
        ),
        "notarized_index": {
            "path": notary,
            "present": os.path.isfile(notary),
        },
        "recent_events": recent,
        "events_log": events_path(root),
        "doctor_trustchain": tc_block,
    }


def export_attestation_bundle(
    target_dir: str,
    out_path: str,
    *,
    event_limit: int = 500,
) -> Dict[str, Any]:
    """Write session + attestation + events for audit / procurement (RFP-004 export)."""
    from apatch.runtime.session import build_session_view

    root = os.path.abspath(target_dir)
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "workspace": root,
        "session": build_session_view(root),
        "attestation": build_attestation_view(root),
        "events": tail_events(root, limit=event_limit),
    }
    out_abs = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    with open(out_abs, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return {"ok": True, "path": out_abs, "event_count": len(bundle["events"])}
