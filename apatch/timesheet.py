"""Cross-project contribution timesheet (RFP-026 / SPEC-CONTRIB-TIMESHEET-1).

Reads signed ContributionEvent receipts from the per-identity store and aggregates
time + volume by identity / project / spec / day. ``verify`` re-derives each event
from the raw signed ledger to detect tampering. Read-only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apatch.contribution import (
    _ledger_rows_for_session,
    _to_epoch,
    _volume_from_rows,
    contribution_store_dir,
)

_VOLUME_KEYS = ("ops", "files_touched", "insertions", "deletions")
# Event-key allowlist. Source of truth is the shared avatar-contract package
# (covers v1 `attestation` + v2 `proof_ref`/`methodology_tags`/`payload`/
# `cv_delta`/`declares_for`/`created_at` — Avatar Architecture Canon §7), but the
# import must not be hard: a bare apatch install (no `avatar` extra) still reads
# timesheets, so fall back to the frozen literal mirror of RECOMMENDED_ALLOWED_KEYS.
try:
    from avatar_contract import RECOMMENDED_ALLOWED_KEYS as _CONTRACT_ALLOWED_KEYS

    _ALLOWED_EVENT_KEYS = set(_CONTRACT_ALLOWED_KEYS)
except ImportError:  # avatar extra not installed — reading receipts stays possible
    _ALLOWED_EVENT_KEYS = {
        "schema_version", "kind", "event_id", "idempotency_key", "avatar_id",
        "source", "trust_level", "identity", "project", "session", "volume",
        "proof_ref", "attestation", "payload", "cv_delta", "declares_for",
        "methodology_tags", "created_at", "signature",
    }


def load_events(store_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load all contribution receipts from the per-identity store."""
    base = store_dir or contribution_store_dir()
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    ev = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(ev, dict) and ev.get("kind") in ("fact", "contribution"):
                out.append(ev)
    return out


def _event_ts(ev: Dict[str, Any]) -> float:
    sess = ev.get("session") or {}
    return _to_epoch(sess.get("ended_at")) or _to_epoch(sess.get("started_at"))


def _day_of(ev: Dict[str, Any]) -> str:
    ts = _event_ts(ev)
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _group_keys(ev: Dict[str, Any], by) -> tuple:
    keys = []
    for dim in by:
        if dim == "identity":
            keys.append((ev.get("identity") or {}).get("key_id") or "unknown")
        elif dim == "project":
            keys.append((ev.get("project") or {}).get("id") or "unknown")
        elif dim == "spec":
            arts = (ev.get("session") or {}).get("artifacts") or []
            specs = [a for a in arts if isinstance(a, str) and a.startswith("spec:")]
            keys.append(",".join(specs) if specs else "(none)")
        elif dim == "day":
            keys.append(_day_of(ev))
        else:
            keys.append("*")
    return tuple(keys)


def filter_events(events, *, since=None, until=None, agent=None,
                  project=None, include_audit=False):
    since_ts = _to_epoch(since) if since else 0.0
    until_ts = _to_epoch(until) if until else 0.0
    out = []
    for ev in events:
        if not include_audit and ev.get("trust_level") != "attested":
            continue
        if agent and (ev.get("identity") or {}).get("key_id") != agent:
            continue
        if project and (ev.get("project") or {}).get("id") != project:
            continue
        ts = _event_ts(ev)
        if since_ts and ts and ts < since_ts:
            continue
        if until_ts and ts and ts > until_ts:
            continue
        out.append(ev)
    return out


def aggregate(events, by=("identity",), idle_gap_min=30.0, ramp_up_min=15.0):
    """Group events; estimate honest work time + sum volume.

    ``hours`` is the *honest* work estimate: the larger of the recorded session
    duration and a git-hours-style estimate from op spacing. Governed ops are
    sub-second, so ``duration_sec`` is ~0 and the real work happens between ops;
    ``estimate_active_seconds`` recovers it. Rows are sorted by work time desc.
    """
    groups: Dict[tuple, Dict[str, Any]] = {}
    for ev in events:
        key = _group_keys(ev, by)
        g = groups.get(key)
        if g is None:
            g = {"key": list(key), "sessions": 0, "duration_sec": 0.0,
                 "estimated_sec": 0.0, "active_sec": 0.0, "ops": 0,
                 "files_touched": 0, "insertions": 0, "deletions": 0, "_ts": []}
            groups[key] = g
        sess = ev.get("session") or {}
        vol = ev.get("volume") or {}
        g["sessions"] += 1
        g["duration_sec"] += float(sess.get("duration_sec") or 0)
        ts = _event_ts(ev)
        if ts:
            g["_ts"].append(ts)
        for k in _VOLUME_KEYS:
            g[k] += int(vol.get(k) or 0)
    rows = []
    for g in groups.values():
        ts = g.pop("_ts")
        recorded = round(g["duration_sec"], 1)
        estimated = estimate_active_seconds(ts, idle_gap_min, ramp_up_min)
        g["duration_sec"] = recorded
        g["estimated_sec"] = estimated
        g["active_sec"] = round(max(recorded, estimated), 1)
        g["hours"] = round(g["active_sec"] / 3600.0, 2)
        rows.append(g)
    rows.sort(key=lambda r: r["active_sec"], reverse=True)
    return rows


def verify_events(events, target_dir="."):
    """Re-derive each event's volume from the raw ledger; flag drift / unsigned."""
    drift = []
    unsigned = []
    for ev in events:
        sid = (ev.get("session") or {}).get("session_id") or ""
        rederived = _volume_from_rows(_ledger_rows_for_session(target_dir, sid))
        claimed = {k: int((ev.get("volume") or {}).get(k) or 0) for k in _VOLUME_KEYS}
        if rederived != claimed:
            drift.append({"event_id": ev.get("event_id"), "session_id": sid,
                          "claimed": claimed, "rederived": rederived})
        if not ev.get("signature"):
            unsigned.append(ev.get("event_id"))
    return {"ok": not drift and not unsigned, "checked": len(events),
            "drift": drift, "unsigned": unsigned}


def idle_gap_split(timestamps, gap_min):
    """Sum inter-op spans, excluding gaps longer than ``gap_min`` minutes."""
    ts = sorted(float(t) for t in timestamps if t)
    if len(ts) < 2:
        return 0.0
    gap = gap_min * 60.0
    active = 0.0
    for a, b in zip(ts, ts[1:]):
        d = b - a
        if d <= gap:
            active += d
    return round(active, 1)


def estimate_active_seconds(timestamps, idle_gap_min=30.0, ramp_up_min=15.0):
    """Honest work-time estimate from sparse op timestamps (git-hours heuristic).

    Governed ops are sub-second, so a session's recorded ``duration_sec`` is ~0 and
    the real work happens BETWEEN ops. Cluster the op stream at gaps longer than
    ``idle_gap_min`` (a break), sum the in-cluster spans, and add ``ramp_up_min`` per
    cluster for the work done before its first recorded op. A single op therefore
    counts as ``ramp_up_min`` (not zero); an unbroken burst counts as its span plus
    one ramp-up. This is an *estimate*, not a stopwatch.
    """
    ts = sorted(float(t) for t in timestamps if t)
    if not ts:
        return 0.0
    in_cluster = idle_gap_split(ts, idle_gap_min)
    breaks = sum(1 for a, b in zip(ts, ts[1:]) if (b - a) > idle_gap_min * 60.0)
    clusters = breaks + 1
    return round(in_cluster + clusters * ramp_up_min * 60.0, 1)


def _apply_idle_gap(events, target_dir, gap_min):
    for ev in events:
        sid = (ev.get("session") or {}).get("session_id") or ""
        rows = _ledger_rows_for_session(target_dir, sid)
        ev.setdefault("session", {})["active_sec"] = idle_gap_split(
            [_to_epoch(r.get("timestamp")) for r in rows], gap_min
        )
    return events


def format_report(rows, by, fmt="md"):
    if fmt == "json":
        return json.dumps({"by": list(by), "groups": rows}, indent=2, ensure_ascii=False)
    if not rows:
        return "_No contribution events._"
    header = list(by) + ["sessions", "hours", "ops", "files", "+", "-"]
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        cells = list(r["key"]) + [str(r["sessions"]), str(r["hours"]), str(r["ops"]),
                                  str(r["files_touched"]), str(r["insertions"]),
                                  str(r["deletions"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("_hours = max(recorded session time, op-spacing estimate); "
                 "governed ops are sub-second so hours is estimated from op "
                 "spacing (git-hours heuristic) \u2014 an estimate, not a stopwatch._")
    return "\n".join(lines)


def run_timesheet(*, target_dir=".", by="identity", since=None, until=None,
                  agent=None, project=None, idle_gap=None, ramp_up=None, fmt="md",
                  do_verify=False, include_audit=False, store_dir=None):
    """Top-level: load -> filter -> verify|aggregate -> report.

    ``idle_gap`` (minutes, default 30) marks gaps longer than itself as breaks;
    ``ramp_up`` (minutes, default 15) is the per-block allowance for work done
    before the first recorded op. Both feed the honest ``hours`` estimate.
    """
    dims = tuple(d.strip() for d in str(by).split(",") if d.strip()) or ("identity",)
    gap_min = float(idle_gap) if idle_gap else 30.0
    ramp_min = float(ramp_up) if ramp_up is not None else 15.0
    events = load_events(store_dir)
    events = filter_events(events, since=since, until=until, agent=agent,
                           project=project, include_audit=include_audit)
    if do_verify:
        return {"verify": verify_events(events, target_dir)}
    rows = aggregate(events, dims, gap_min, ramp_min)
    if fmt == "json":
        return {"by": list(dims), "groups": rows}
    return format_report(rows, dims, "md")