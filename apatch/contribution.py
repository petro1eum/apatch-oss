"""ContributionEvent — signed per-session contribution receipt.

RFP-026 / SPEC-CONTRIB-TIMESHEET-1. Derives a deterministic, Ed25519-signed
record from an attested governed session plus the TrustChain ledger, keyed to the
user's enrolled certificate identity so contributions aggregate per-identity
across every project (one global ledger). No source code or secrets are stored —
only paths, hashes, counts, and timing (RFP-025 AF-3 non-invasiveness).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# R1 (Avatar Architecture Canon §2, Rule 1): the ContributionEvent schema is the
# ONE shared `avatar-contract` definition, imported by BOTH apatch (emitter, L1)
# and HC (consumer, L3) — not a second local schema "matching by convention".
# It is imported LAZILY (inside the emit path, see `_event_class`) so a bare apatch
# install (patching only) never requires it; contribution EMISSION requires it
# (install the `avatar` extra / vendor / editable checkout). There is no local
# fallback schema — absence raises, it never silently diverges.

SCHEMA_VERSION = 3

_EVENT_CLASS = None  # cached apatch-side subclass of avatar_contract.ContributionEvent


def current_schema_version() -> int:
    """Return the canonical version without making avatar-contract a base dependency."""
    from avatar_contract.contribution_event import SCHEMA_VERSION as contract_version

    return int(contract_version)


def _event_class():
    """The apatch-side ContributionEvent = the shared contract class + a `to_dict()`
    alias for existing readers. Built lazily so importing this module never requires
    `avatar-contract` (raises ImportError only when contributions are actually built)."""
    global _EVENT_CLASS
    if _EVENT_CLASS is None:
        from avatar_contract import ContributionEvent as _Base

        class ContributionEvent(_Base):
            def to_dict(self) -> Dict[str, Any]:
                return self.to_wire()

        _EVENT_CLASS = ContributionEvent
    return _EVENT_CLASS


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, compact separators (signature input)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_epoch(value: Any) -> float:
    """Coerce an epoch float or ISO-8601 string to epoch seconds (0.0 on failure)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            try:
                return float(value)
            except Exception:
                return 0.0
    return 0.0


def _floor_trust(level: Optional[str]) -> str:
    """Canon §7.2 ladder is exactly {claimed, attested, verified}. Anything else
    (legacy 'audit', None) floors to 'claimed' — the un-enrolled floor (canon §8),
    which the shared `avatar_contract` schema accepts (it rejects 'audit')."""
    from avatar_contract import TRUST_LEVELS
    return level if level in TRUST_LEVELS else "claimed"


def resolve_identity(workspace_root: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the signing identity for contribution events.

    ``key_id`` = sha256(raw Ed25519 public key)[:32] — stable, machine-independent.
    ``cert_fingerprint`` from the Platform-CA-issued ``agent.crt`` when present →
    ``ca="platform"`` / ``trust_level="attested"``; otherwise legacy → ``claimed``
    (canon §7.2/§8 un-enrolled floor — was ``audit``, not a canon trust level).
    """
    from apatch.trust_identity import load_local_identity

    ident = load_local_identity(workspace_root)
    if ident is None or getattr(ident, "key_provider", None) is None:
        return {
            "key_id": "legacy:" + (os.environ.get("APATCH_AGENT_ID", "").strip() or "anonymous"),
            "cert_fingerprint": None,
            "agent_id": os.environ.get("APATCH_AGENT_ID", "").strip() or "anonymous",
            "subject_type": "agent",
            "ca": "legacy",
            "trust_level": "claimed",
        }
    public_key_raw: Optional[bytes] = None
    try:
        public_key_raw = ident.key_provider.get_public_key()
        key_id = _sha256_hex(public_key_raw)[:32]
    except Exception:
        key_id = "legacy:" + (ident.agent_id or "anonymous")
    cert_fingerprint: Optional[str] = None
    ca = "legacy"
    trust_level = "claimed"
    if ident.cert_path and os.path.isfile(ident.cert_path):
        try:
            with open(ident.cert_path, "rb") as fh:
                cert_fingerprint = "sha256:" + _sha256_hex(fh.read())
            ca = "platform"
            trust_level = "attested"
        except Exception:
            pass
    return {
        "key_id": key_id,
        "cert_fingerprint": cert_fingerprint,
        "agent_id": ident.agent_id,
        "subject_type": os.environ.get("APATCH_IDENTITY_KIND", "agent").strip().lower() or "agent",
        "public_key": (
            base64.b64encode(public_key_raw).decode("ascii")
            if public_key_raw is not None else None
        ),
        "ca": ca,
        "trust_level": trust_level,
    }


def project_identity(workspace_root: Optional[str] = None) -> Dict[str, Any]:
    """Stable cross-machine project id: hash of normalized git remote, else root."""
    root = os.path.abspath(workspace_root or ".")
    try:
        from apatch.trustchain_helper import TrustChainHelper

        root = TrustChainHelper.resolve_workspace_root(root)
    except Exception:
        pass
    remote: Optional[str] = None
    try:
        res = subprocess.run(
            ["git", "-C", root, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0:
            remote = res.stdout.strip() or None
    except Exception:
        remote = None
    if remote:
        norm = remote.strip().lower()
        if norm.endswith(".git"):
            norm = norm[:-4]
        pid = _sha256_hex(norm.encode("utf-8"))[:16]
    else:
        pid = _sha256_hex(os.path.realpath(root).encode("utf-8"))[:16]
    return {"id": pid, "name": os.path.basename(root.rstrip("/")) or root, "remote": remote}


def _ledger_rows_for_session(target_dir: str, session_id: str) -> List[Dict[str, Any]]:
    if not session_id:
        return []
    try:
        from apatch.trustchain_helper import TrustChainHelper

        rows = TrustChainHelper(target_dir).iter_ledger_entries()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") or {}
        sid = payload.get("governed_session_id") or payload.get("session_id")
        if sid == session_id:
            out.append(row)
    return out


def _volume_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    files = set()
    insertions = 0
    deletions = 0
    for row in rows:
        payload = row.get("payload") or {}
        files_field = payload.get("files")
        if isinstance(files_field, dict):
            files.update(files_field.keys())
        elif isinstance(files_field, list):
            files.update(str(x) for x in files_field)
        try:
            insertions += int(payload.get("insertions") or 0)
            deletions += int(payload.get("deletions") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "ops": len(rows),
        "files_touched": len(files),
        "insertions": insertions,
        "deletions": deletions,
    }


def _capability_payload_from_rows(
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Carry the latest signed result of each gate into Avatar evidence."""
    latest_by_gate: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(rows):
        tool_id = str(row.get("tool_id") or row.get("tool") or "")
        payload = row.get("payload") or {}
        if (
            tool_id != "apatch_probe"
            or payload.get("action") != "probe_falsify"
        ):
            continue
        quality = str(payload.get("gate_quality") or "")
        if quality not in {"falsified", "false_gate"}:
            continue
        verify_hash = str(payload.get("verify_sha256") or "")
        guarded_files = sorted(str(item) for item in (payload.get("files") or []))
        # Legacy probe rows without a gate fingerprint remain independent evidence.
        gate_key = (
            f"{verify_hash}:{','.join(guarded_files)}"
            if verify_hash
            else f"legacy:{row.get('id') or index}"
        )
        latest_by_gate[gate_key] = {
            "quality": quality,
            "ref": str(row.get("id") or ""),
        }
    if not latest_by_gate:
        return None
    current = list(latest_by_gate.values())
    # A currently insensitive gate makes the composite session evidence unsafe.
    gate_quality = (
        "false_gate"
        if any(item["quality"] == "false_gate" for item in current)
        else "falsified"
    )
    return {
        "gate_quality": gate_quality,
        "gate_evidence_refs": [
            item["ref"] for item in current if item["ref"]
        ],
    }


_EXACT_SPEC_ARTIFACT_RE = re.compile(
    r"^spec:(?P<spec>SPEC-[A-Za-z0-9._-]+)#"
    r"(?P<requirement>[A-Za-z][A-Za-z0-9._-]*\d)"
    r"(?:@(?P<hash>sha256:[0-9a-f]+))?$"
)


def _artifact_token(value: Any) -> str:
    if isinstance(value, dict):
        kind = str(value.get("kind") or "").strip()
        identifier = str(value.get("id") or "").strip()
        if not kind or not identifier:
            return ""
        token = f"{kind}:{identifier}"
        content_hash = str(value.get("content_hash") or "").strip()
        return f"{token}@{content_hash}" if content_hash else token
    return str(value or "").strip()


def _review_submission(
    target_dir: str,
    session: Dict[str, Any],
    completion_summary: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Build only content-safe, explicit material for counterparty review."""
    from apatch.spec import resolve_requirement

    artifact_refs: List[str] = []
    criteria: List[str] = []
    spec_titles: List[str] = []
    limitations: List[str] = []

    for value in session.get("artifacts") or []:
        token = _artifact_token(value)
        match = _EXACT_SPEC_ARTIFACT_RE.fullmatch(token)
        if not match:
            continue
        if token not in artifact_refs:
            artifact_refs.append(token)
        requirement_ref = (
            f"{match.group('spec')}#{match.group('requirement')}"
        )
        resolved = resolve_requirement(target_dir, requirement_ref)
        if not resolved.get("ok"):
            limitations.append(
                f"Public specification text was unavailable for {requirement_ref}."
            )
            continue
        recorded_hash = match.group("hash")
        if recorded_hash and resolved.get("content_hash") != recorded_hash:
            limitations.append(
                f"Public specification changed after {requirement_ref} was recorded."
            )
            continue
        title = " ".join(str(resolved.get("spec_title") or "").split())
        criterion = " ".join(
            str(resolved.get("requirement_title") or "").split()
        )
        if title and len(title) <= 500 and title not in spec_titles:
            spec_titles.append(title)
        if criterion and len(criterion) <= 300 and criterion not in criteria:
            criteria.append(criterion)

    summary: Optional[str] = None
    if isinstance(completion_summary, str):
        normalized = " ".join(completion_summary.split())
        if normalized and len(normalized) <= 600:
            summary = normalized
        elif normalized:
            limitations.append(
                "The public completion summary exceeded 600 characters and was omitted."
            )
    elif completion_summary is not None:
        limitations.append("The public completion summary was not text and was omitted.")

    objective = " | ".join(sorted(spec_titles)) or None
    if objective and len(objective) > 500:
        objective = None
        limitations.append(
            "The public objective exceeded 500 characters and was omitted."
        )

    if summary is None and not spec_titles and not criteria:
        return None
    return {
        "schema_version": 1,
        "objective": objective,
        "delivery_summary": summary,
        "acceptance_criteria": sorted(criteria),
        "artifact_refs": sorted(artifact_refs),
        "limitations": sorted(set(limitations)),
    }


def build_event(
    session: Dict[str, Any],
    *,
    target_dir: str = ".",
    identity: Optional[Dict[str, Any]] = None,
    project: Optional[Dict[str, Any]] = None,
    ledger_rows: Optional[List[Dict[str, Any]]] = None,
    created_at: Optional[str] = None,
    completion_summary: Optional[str] = None,
) -> Any:
    """Build a (still-unsigned) ContributionEvent from session + ledger rows.

    Requires `avatar-contract` (the shared schema); raises ImportError otherwise."""
    identity = identity or resolve_identity(target_dir)
    project = project or project_identity(target_dir)
    session_id = session.get("session_id") or ""
    if ledger_rows is None:
        ledger_rows = _ledger_rows_for_session(target_dir, session_id)
    started = _to_epoch(session.get("started_at"))
    ended = _to_epoch(session.get("ended_at")) or started
    duration = max(0.0, ended - started) if (started and ended) else 0.0
    volume = _volume_from_rows(ledger_rows)
    op_ids = [r.get("id") for r in ledger_rows if r.get("id")]
    key_id = identity["key_id"]
    event_id = _sha256_hex(
        "{}|{}|{}".format(key_id, session_id, ",".join(sorted(str(o) for o in op_ids))).encode("utf-8")
    )[:32]
    schema_version = current_schema_version()
    capability_payload = _capability_payload_from_rows(ledger_rows)
    review_submission = _review_submission(
        target_dir, session, completion_summary
    )
    payload = dict(capability_payload or {})
    if review_submission is not None:
        payload["review_submission"] = review_submission
    public_intent = (
        str(review_submission.get("objective") or "")
        if review_submission is not None else ""
    )
    event_fields: Dict[str, Any] = {
        "schema_version": schema_version,
        "kind": "fact",  # current vocab; v1 "contribution" is a read-only legacy alias
        "event_id": event_id,
        "source": "apatch",
        "trust_level": _floor_trust(identity.get("trust_level")),
        "idempotency_key": event_id,
        "avatar_id": key_id,
        "identity": identity,
        "project": project,
        "session": {
            "session_id": session_id,
            "intent": public_intent,
            "artifacts": session.get("artifacts") or [],
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "duration_sec": round(duration, 3),
            "active_sec": None,
        },
        "volume": volume,
        "proof_ref": {
            "op_ids": op_ids,
            "head": op_ids[-1] if op_ids else None,
            "committed_at": ended or started,
        },
        "payload": payload or None,
    }
    if schema_version >= 3:
        event_fields["created_at"] = created_at or _utc_now_iso()
    return _event_class()(**event_fields)


def sign_event(event: Any, key_provider: Any) -> Any:
    sig = key_provider.sign(event.canonical().encode("utf-8"))
    event.signature = base64.b64encode(sig).decode("ascii")
    return event


def verify_event(event_dict: Dict[str, Any], pub_raw: bytes) -> bool:
    """Verify an event's Ed25519 signature against a raw 32-byte public key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    sig_b64 = event_dict.get("signature")
    if not sig_b64:
        return False
    unsigned = {k: v for k, v in event_dict.items() if k != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(
            base64.b64decode(sig_b64), _canonical(unsigned).encode("utf-8")
        )
        return True
    except Exception:
        return False


def contribution_store_dir() -> str:
    """Global, cross-project store keyed by identity (override: APATCH_CONTRIB_STORE)."""
    override = os.environ.get("APATCH_CONTRIB_STORE", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".trustchain", "contributions")


def emit_contribution(
    target_dir: str,
    session: Dict[str, Any],
    *,
    store_dir: Optional[str] = None,
    completion_summary: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Emit one signed contribution receipt for an attested session.

    Returns the event dict, or ``None`` when the session made zero mutations
    (nothing to attribute). Writes atomically to the per-identity store.
    """
    identity = resolve_identity(target_dir)
    session_id = session.get("session_id") or ""
    rows = _ledger_rows_for_session(target_dir, session_id)
    if _volume_from_rows(rows)["ops"] == 0:
        return None
    event = build_event(
        session,
        target_dir=target_dir,
        identity=identity,
        ledger_rows=rows,
        completion_summary=completion_summary,
    )
    # R1 teeth: refuse to emit anything the shared contract would reject
    # (bad trust_level, avatar_id != key_id, economic-layer key, missing proof_ref).
    event.validate()
    base = store_dir or contribution_store_dir()
    out_dir = os.path.join(base, identity["key_id"])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, event.event_id + ".json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"existing ContributionEvent is unreadable: {path}"
            ) from exc
        existing_identity = existing.get("identity") or {}
        existing_avatar_id = existing.get("avatar_id") or existing_identity.get("key_id")
        if (
            existing.get("event_id") != event.event_id
            or existing_avatar_id != identity["key_id"]
        ):
            raise RuntimeError(
                "existing ContributionEvent conflicts with the deterministic event id"
            )
        return existing

    signing_error: Optional[Exception] = None
    try:
        from apatch.trust_identity import load_local_identity

        ident = load_local_identity(target_dir)
        if ident is not None and getattr(ident, "key_provider", None) is not None:
            sign_event(event, ident.key_provider)
    except Exception as exc:
        signing_error = exc
    if event.trust_level in ("attested", "verified") and not event.signature:
        detail = f": {signing_error}" if signing_error else ""
        raise RuntimeError(
            "refusing to persist attested ContributionEvent without Ed25519 signature"
            + detail
        )
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(event.to_dict(), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return event.to_dict()
