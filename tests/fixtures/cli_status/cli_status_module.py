"""Developer CLI status rendering (SPEC-CLI-STATUS-1)."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from rich.console import Console


def build_status_view(target_dir: str = ".") -> Dict[str, Any]:
    from apatch.project_status import project_status_workspace
    from apatch.session_state import load_session_state

    dto = project_status_workspace(target_dir)
    if not dto.get("ok"):
        return dto
    root = os.path.abspath(target_dir)
    st = load_session_state(root)
    dto["session_phase"] = st.get("phase")
    dto["next_action"] = st.get("next_action")
    if st.get("failure"):
        dto["failure"] = st.get("failure")
    sess = dto.get("active_session")
    if st.get("checkpoint"):
        if isinstance(sess, dict):
            sess.setdefault("checkpoint", st.get("checkpoint"))
        elif sess is None:
            dto["active_session"] = {
                "checkpoint": st.get("checkpoint"),
                "phase": st.get("phase"),
            }
    return dto


def spec_list_workspace(target_dir: str = ".") -> Dict[str, Any]:
    from apatch.project_status import _discover_spec_ids

    root = os.path.abspath(target_dir)
    ids = _discover_spec_ids(root)
    return {"ok": True, "workspace": root, "specs": ids, "count": len(ids)}


def _spec_traffic_mark(spec: Dict[str, Any]) -> str:
    sm = spec.get("summary") or {}
    if spec.get("done"):
        return "green"
    stale = int(sm.get("stale") or 0)
    att = int(sm.get("attested") or 0)
    total = int(sm.get("total") or 0)
    if stale > 0:
        return "yellow"
    if att < total and total > 0:
        return "yellow"
    return "green" if att == total and total > 0 else "dim"


def format_status_plain_lines(dto: Dict[str, Any]) -> List[str]:
    """Plain text lines for tests (no Rich markup)."""
    lines: List[str] = []
    if not dto.get("ok"):
        lines.append(f"status error: {dto.get('error', 'unknown')}")
        return lines

    phase = dto.get("session_phase") or "idle"
    lines.append(f"Session: phase={phase}")
    if dto.get("failure"):
        fail = dto["failure"]
        lines.append(f"Failure: {fail.get('error_type', '?')}")

    hygiene = dto.get("hygiene") or {}
    hstat = hygiene.get("status") or "unknown"
    lines.append(f"Hygiene: {hstat}")

    sm = dto.get("summary") or {}
    lines.append(
        f"Specs: {sm.get('spec_count', 0)} "
        f"({sm.get('attested', 0)}/{sm.get('total_requirements', 0)} attested)"
    )
    for spec in dto.get("specs") or []:
        sid = spec.get("id", "?")
        ssum = spec.get("summary") or {}
        att = ssum.get("attested", 0)
        total = ssum.get("total", 0)
        mark = _spec_traffic_mark(spec)
        lines.append(f"  [{mark}] {sid} {att}/{total}")

    conflicts = dto.get("conflicts")
    if conflicts and isinstance(conflicts, dict) and conflicts.get("safe_order"):
        order = " → ".join(conflicts["safe_order"])
        lines.append(f"Safe order: {order}")

    nxt = dto.get("next_action")
    if nxt:
        lines.append(f"Next: {nxt}")

    return lines


def render_status_human(console: Console, dto: Dict[str, Any]) -> None:
    if not dto.get("ok"):
        console.print(f"[bold red]status error:[/bold red] {dto.get('error', 'unknown')}")
        return

    phase = dto.get("session_phase") or "idle"
    sess = dto.get("active_session") or {}
    ckpt = sess.get("session_id") or sess.get("checkpoint") or "—"
    console.print(f"[bold]Session[/bold]: phase={phase}  checkpoint={ckpt}")
    if dto.get("failure"):
        fail = dto["failure"]
        console.print(f"[bold red]Failure[/bold red]: {fail.get('error_type', '?')}")

    hygiene = dto.get("hygiene") or {}
    hstat = hygiene.get("status") or "unknown"
    hcolor = {"ok": "green", "clean": "green", "warn": "yellow", "degraded": "yellow", "critical": "red"}.get(
        hstat, "white"
    )
    console.print(f"[bold]Hygiene[/bold]: [{hcolor}]{hstat}[/{hcolor}]")
    if hygiene.get("gc_recommendation"):
        console.print(f"  [dim]{hygiene['gc_recommendation']}[/dim]")

    sm = dto.get("summary") or {}
    console.print(
        f"\n[bold]Specs[/bold] ({sm.get('spec_count', 0)}): "
        f"{sm.get('attested', 0)}/{sm.get('total_requirements', 0)} attested "
        f"({sm.get('percent_complete', 0)}%)"
    )
    for spec in dto.get("specs") or []:
        sid = spec.get("id", "?")
        ssum = spec.get("summary") or {}
        att = ssum.get("attested", 0)
        total = ssum.get("total", 0)
        mark = _spec_traffic_mark(spec)
        colors = {"green": "green", "yellow": "yellow", "dim": "dim"}
        sym = {"green": "✓", "yellow": "!", "dim": "·"}[mark]
        c = colors[mark]
        done = " [green]done[/green]" if spec.get("done") else ""
        console.print(f"  [{c}]{sym}[/{c}] {sid} {att}/{total}{done}")

    conflicts = dto.get("conflicts")
    if conflicts and isinstance(conflicts, dict):
        safe = conflicts.get("safe_order") or []
        if len(safe) >= 2:
            order = " → ".join(safe)
            risk = conflicts.get("risk_score")
            risk_s = f"  risk={risk}" if risk is not None else ""
            console.print(f"\n[bold]Conflicts[/bold]: safe_order {order}{risk_s}")

    nxt = dto.get("next_action")
    if nxt:
        console.print(f"\n[bold]Next[/bold]: {nxt}")


def render_apply_session_human(result: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    if result.get("error_type") == "RUNTIME_TRANSITION":
        lines.append(f"error: {result.get('error')}")
        return lines
    if not result.get("ok"):
        et = result.get("error_type") or "APPLY_FAILED"
        lines.append(f"apply-session failed ({et}): {result.get('error') or 'see logs'}")
        return lines
    prog = result.get("progress") or {}
    done = prog.get("chunks_done", "?")
    total = prog.get("chunks_total", "?")
    lines.append(f"chunk {done}/{total} checkpoint={result.get('checkpoint') or '—'}")
    if result.get("continue"):
        lines.append("continue: run apply-session again or use --loop")
    else:
        lines.append("apply-session complete")
    return lines


def print_apply_session_human(console: Console, result: Dict[str, Any]) -> None:
    for line in render_apply_session_human(result):
        if line.startswith("error:") or "failed" in line:
            console.print(f"[bold red]{line}[/bold red]")
        elif "complete" in line:
            console.print(f"[bold green]{line}[/bold green]")
        else:
            console.print(line)
