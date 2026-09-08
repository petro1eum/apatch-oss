"""Mutation Console — G3 read-mostly + G5 interactive actions."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from apatch.console.state import load_console_state, save_console_state
from apatch.doctor import run_doctor
from apatch.runtime.attestation import build_attestation_view
from apatch.runtime.events import tail_events
from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import build_session_view, set_session_intent


def _session_panel(data: dict) -> Panel:
    s = data.get("session") or {}
    lines = [
        f"[bold]session_id[/bold]: {s.get('session_id') or '—'}",
        f"[bold]intent[/bold]: {s.get('intent') or '—'}",
        f"[bold]lifecycle[/bold]: {s.get('lifecycle')}",
        f"[bold]phase[/bold]: {s.get('phase')}",
        f"[bold]checkpoint[/bold]: {s.get('checkpoint') or '—'}",
        f"[bold]risk[/bold]: {s.get('risk_level')}",
        f"[bold]next[/bold]: {s.get('next_action') or '—'}",
    ]
    inv = data.get("invariant") or {}
    if not inv.get("satisfied"):
        lines.append("[yellow]⚠ [i] start session with intent[/yellow]")
    if s.get("failure"):
        lines.append(f"[red]failure: {s['failure'].get('error_type')}[/red]")
    return Panel("\n".join(lines), title="Session", border_style="cyan")


def _attestation_panel(data: dict) -> Panel:
    a = data.get("attestation") or {}
    head = a.get("head")
    lines = [
        f"[bold]mode[/bold]: {a.get('mode')}",
        f"[bold]head[/bold]: {(head[:20] + '…') if head else '—'}",
        f"[dim]{a.get('summary', '')}[/dim]",
    ]
    ni = data.get("notarized_index") or {}
    lines.append(f"[bold]notarized_index[/bold]: {'yes' if ni.get('present') else 'no'}")
    return Panel("\n".join(lines), title="Attestation", border_style="green")


def _verification_panel(root: str) -> Panel:
    d = run_doctor(root)
    sb = d.get("sandbox") or {}
    enf = d.get("enforcement") or {}
    lines = [
        f"[bold]sandbox[/bold]: {sb.get('mode', '—')}",
        f"[bold]enforcement[/bold]: {'on' if enf.get('active') else 'off'}",
        f"[bold]recommended[/bold]: {d.get('recommended_verify') or '—'}",
    ]
    for w in (d.get("warnings") or [])[:2]:
        lines.append(f"[yellow]{w}[/yellow]")
    return Panel("\n".join(lines), title="Verification / Policy", border_style="yellow")


def _mutations_panel(ui_state: dict) -> Panel:
    entries = ui_state.get("plan_entries") or []
    summary = ui_state.get("plan_summary") or {}
    if not entries:
        return Panel(
            "[dim]after [l] logs, press [p] to preview mutations[/dim]",
            title="Mutations",
            border_style="white",
        )
    table = Table(show_header=True, header_style="bold", expand=True, show_lines=False)
    table.add_column("#", max_width=4)
    table.add_column("file", max_width=28)
    table.add_column("strategy", max_width=12)
    table.add_column("conf", max_width=5)
    table.add_column("apply?", max_width=6)
    for e in entries[:8]:
        conf = e.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
        path = e.get("resolved_path") or e.get("target_file") or "?"
        table.add_row(
            str(e.get("step_index", "?")),
            os.path.basename(str(path))[:28],
            str(e.get("strategy") or "—")[:12],
            conf_s,
            "yes" if e.get("would_apply") else "no",
        )
    extra = len(entries) - 8
    title = "Mutations"
    if summary.get("would_apply") is not None:
        title += f" ({summary.get('would_apply')}/{summary.get('total')} apply)"
    if extra > 0:
        table.add_row("…", f"+{extra} more", "", "", "")
    return Panel(table, title=title, border_style="white")


def _events_panel(root: str) -> Panel:
    events = tail_events(root, limit=6)
    if not events:
        return Panel("[dim]no events yet[/dim]", title="Events", border_style="magenta")
    table = Table(show_header=True, header_style="bold", expand=True, show_lines=False)
    table.add_column("ts", max_width=19)
    table.add_column("type", max_width=20)
    for e in reversed(events):
        table.add_row((e.get("ts") or "")[:19], e.get("type") or "")
    return Panel(table, title="Events", border_style="magenta")


def _status_line(ui_state: dict) -> str:
    msg = ui_state.get("last_message")
    logs = ui_state.get("logs_path")
    parts = []
    if logs:
        parts.append(f"logs: {os.path.basename(logs)}")
    if msg:
        parts.append(msg)
    return " · ".join(parts) if parts else "[dim]ready[/dim]"


def _handle_command(root: str, key: str, ui_state: dict, console: Console) -> dict:
    rt = MutationRuntime(root)

    if key in ("i", "intent"):
        intent = console.input("[cyan]Intent[/cyan]: ").strip()
        if not intent:
            ui_state["last_message"] = "[yellow]intent empty[/yellow]"
            return ui_state
        out = set_session_intent(root, intent)
        if out.get("ok"):
            sid = (out.get("session") or {}).get("session_id")
            if out.get("intent_updated"):
                ui_state["last_message"] = f"[green]intent updated ({sid})[/green]"
            else:
                ui_state["last_message"] = f"[green]session {sid}[/green]"
        else:
            hint = out.get("hint")
            ui_state["last_message"] = f"[red]{out.get('error')}" + (
                f" — {hint}[/red]" if hint else "[/red]"
            )
        return ui_state

    if key in ("l", "logs"):
        path = console.input("[cyan]Logs JSONL path[/cyan]: ").strip()
        if path and os.path.isdir(path):
            ui_state["last_message"] = "[red]need a .jsonl file, not a directory[/red]"
        elif path and os.path.isfile(path):
            ui_state["logs_path"] = os.path.abspath(path)
            ui_state["last_message"] = f"[green]logs set: {os.path.basename(path)}[/green]"
        else:
            ui_state["last_message"] = "[red]file not found[/red]"
        return ui_state

    logs = ui_state.get("logs_path")
    if key in ("p", "plan"):
        if not logs:
            ui_state["last_message"] = "[yellow]set logs: [l][/yellow]"
            return ui_state
        out = rt.plan(logs)
        if out.get("ok", True) and out.get("would_apply") is not None:
            ui_state["plan_entries"] = out.get("entries") or []
            ui_state["plan_summary"] = {
                "would_apply": out.get("would_apply"),
                "total": out.get("total"),
            }
            ui_state["last_message"] = (
                f"[green]plan: {out.get('would_apply')}/{out.get('total')} would apply[/green]"
            )
        else:
            ui_state["last_message"] = f"[red]{out.get('error', 'plan failed')}[/red]"
        return ui_state

    if key in ("a", "apply"):
        if not logs:
            ui_state["last_message"] = "[yellow]set logs: [l][/yellow]"
            return ui_state
        out = rt.apply_session(logs, quiet=True)
        prog = out.get("progress") or {}
        if out.get("ok"):
            ui_state["last_message"] = (
                f"[green]chunk {prog.get('chunks_done')}/{prog.get('chunks_total')} "
                f"ckpt={out.get('checkpoint')}[/green]"
            )
        else:
            ui_state["last_message"] = f"[red]{out.get('error', 'apply failed')}[/red]"
        return ui_state

    if key in ("v", "verify"):
        try:
            out = rt.verify_semantic()
        except Exception as exc:
            ui_state["last_message"] = f"[red]verify error: {exc}[/red]"
            return ui_state
        if out.get("error_type") == "RUNTIME_TRANSITION":
            ui_state["last_message"] = f"[red]{out.get('error')}[/red]"
        elif out.get("ok"):
            ui_state["last_message"] = "[green]semantic verify ok[/green]"
        else:
            ui_state["last_message"] = (
                f"[red]verify failed ({len(out.get('violations') or [])} violations)[/red]"
            )
        return ui_state

    if key in ("h", "help"):
        ui_state["last_message"] = "i intent · l logs · p plan · a apply chunk · v verify · r refresh · q quit"
        return ui_state

    ui_state["last_message"] = f"[dim]unknown: {key} (h for help)[/dim]"
    return ui_state


def run_console(target_dir: str = ".", *, refresh: bool = False) -> int:
    """Render Mutation Console; interactive mode when refresh=True and TTY."""
    console = Console()
    root = os.path.abspath(target_dir)
    ui_state = load_console_state(root)

    def _render() -> None:
        session_data = build_session_view(root)
        attestation_data = build_attestation_view(root)
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="status", size=2),
            Layout(name="footer", size=3),
        )
        layout["header"].update(
            Panel(
                Text("apatch Mutation Console", style="bold white")
                + Text(f"  ·  {root}", style="dim"),
                border_style="blue",
            )
        )
        top = Layout()
        top.split_row(
            Layout(_session_panel(session_data), name="session"),
            Layout(_attestation_panel(attestation_data), name="attestation"),
        )
        mid = Layout(_mutations_panel(ui_state), name="mutations", size=8)
        bottom = Layout()
        bottom.split_row(
            Layout(_verification_panel(root), name="verify"),
            Layout(_events_panel(root), name="events"),
        )
        body = Layout()
        body.split_column(top, mid, bottom)
        layout["body"].update(body)
        layout["status"].update(Panel(_status_line(ui_state), border_style="dim"))
        layout["footer"].update(
            Panel(
                "[bold]i[/bold] intent  [bold]l[/bold] logs  [bold]p[/bold] plan  "
                "[bold]a[/bold] apply  [bold]v[/bold] verify  "
                "[bold]r[/bold] refresh  [bold]q[/bold] quit",
                border_style="dim",
            )
        )
        console.clear()
        console.print(layout)

    _render()
    if not refresh or not sys.stdin.isatty():
        return 0

    while True:
        try:
            key = console.input("[dim]>[/dim] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            save_console_state(root, ui_state)
            return 0
        if key in ("q", "quit", "exit"):
            save_console_state(root, ui_state)
            return 0
        if key in ("r", "refresh", ""):
            _render()
            continue
        ui_state = _handle_command(root, key, ui_state, console)
        save_console_state(root, ui_state)
        _render()
