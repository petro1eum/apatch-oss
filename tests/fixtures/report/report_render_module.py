"""Report renderers for SPEC-REPORT-1 (RFP-020 architect + manager views)."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def build_report_bundle(target_dir: str = ".") -> Dict[str, Any]:
    """Assemble DTO + schedule + adherence once; renderers consume this bundle only."""
    from apatch.cli_status import build_status_view
    from apatch.plan_adherence import spec_adherence_workspace
    from apatch.spec_interference import spec_schedule_workspace

    dto = build_status_view(target_dir)
    if not dto.get("ok"):
        return dto
    root = dto.get("workspace") or target_dir
    spec_ids = [str(s.get("id")) for s in (dto.get("specs") or []) if s.get("id")]
    schedule: Dict[str, Any] = {}
    if spec_ids:
        sched = spec_schedule_workspace(root, specs=spec_ids)
        if sched.get("ok"):
            schedule = sched
    adherence: List[Dict[str, Any]] = []
    for sid in spec_ids:
        adh = spec_adherence_workspace(root, spec=sid)
        if adh.get("ok"):
            adherence.append(adh)
    return {
        "ok": True,
        "dto": dto,
        "schedule": schedule,
        "adherence": adherence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _spec_row_class(spec: Dict[str, Any]) -> str:
    sm = spec.get("summary") or {}
    if spec.get("done"):
        return "green"
    if int(sm.get("stale") or 0) > 0:
        return "yellow"
    att = int(sm.get("attested") or 0)
    total = int(sm.get("total") or 0)
    if total and att < total:
        return "yellow"
    return "green" if total and att == total else "dim"


def _conflict_edges(graph: Dict[str, Any]) -> List[tuple[str, str]]:
    edges: List[tuple[str, str]] = []
    for src, dsts in (graph or {}).items():
        for dst in dsts or []:
            edges.append((str(src), str(dst)))
    return edges


def _mermaid_graph(graph: Dict[str, Any]) -> str:
    lines = ["graph LR"]
    seen: set[str] = set()
    for src, dst in _conflict_edges(graph):
        key = f"{src}->{dst}"
        if key in seen:
            continue
        seen.add(key)
        sid = src.replace("-", "_")
        did = dst.replace("-", "_")
        lines.append(f"  {sid}[{src}] --> {did}[{dst}]")
    if len(lines) == 1:
        lines.append('  empty["No WR conflicts"]')
    return "\n".join(lines)


def render_html(bundle: Dict[str, Any]) -> str:
    """Self-contained HTML report (embedded mermaid init, no external URLs)."""
    dto = bundle.get("dto") or {}
    schedule = bundle.get("schedule") or {}
    adherence = bundle.get("adherence") or []
    sm = dto.get("summary") or {}
    hygiene = dto.get("hygiene") or {}
    conflicts = dto.get("conflicts") or {}
    graph = (conflicts.get("graph") if isinstance(conflicts, dict) else None) or {}
    mermaid_src = _mermaid_graph(graph)
    gen = html.escape(str(bundle.get("generated_at") or ""))
    workspace = html.escape(str(dto.get("workspace") or ""))

    spec_rows = []
    for spec in dto.get("specs") or []:
        sid = html.escape(str(spec.get("id") or "?"))
        ssum = spec.get("summary") or {}
        att = ssum.get("attested", 0)
        total = ssum.get("total", 0)
        cls = _spec_row_class(spec)
        spec_rows.append(
            f'<tr class="{cls}"><td>{sid}</td><td>{att}/{total}</td>'
            f'<td>{"done" if spec.get("done") else "in progress"}</td></tr>'
        )

    risk_rows = []
    for step in schedule.get("risk_per_step") or []:
        risk_rows.append(
            "<tr>"
            f"<td>{html.escape(str(step.get('spec') or ''))}</td>"
            f"<td>{html.escape(str(step.get('risk') or ''))}</td>"
            f"<td>{html.escape(', '.join(step.get('depends_on') or []))}</td>"
            "</tr>"
        )

    drift_rows = []
    for adh in adherence:
        spec_id = html.escape(str(adh.get("spec") or ""))
        for req in adh.get("requirements") or []:
            if req.get("adherent"):
                continue
            unplanned = ", ".join(req.get("unplanned") or []) or "—"
            untouched = ", ".join(req.get("untouched_planned") or []) or "—"
            drift_rows.append(
                "<tr>"
                f"<td>{spec_id}</td>"
                f"<td>{html.escape(str(req.get('id') or ''))}</td>"
                f"<td>{html.escape(unplanned)}</td>"
                f"<td>{html.escape(untouched)}</td>"
                "</tr>"
            )

    safe_order = conflicts.get("safe_order") if isinstance(conflicts, dict) else None
    safe_order_s = html.escape(" → ".join(safe_order or []) or "—")
    risk_score = conflicts.get("risk_score") if isinstance(conflicts, dict) else None

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>apatch project report</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
h1, h2 {{ margin-top: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }}
th {{ background: #f0f0f0; }}
tr.green td:first-child {{ border-left: 4px solid #2e7d32; }}
tr.yellow td:first-child {{ border-left: 4px solid #f9a825; }}
tr.dim td:first-child {{ border-left: 4px solid #9e9e9e; }}
.meta {{ color: #666; font-size: 0.9rem; }}
.mermaid {{ background: #fafafa; padding: 1rem; border: 1px solid #ddd; }}
</style>
</head>
<body>
<h1>apatch project report</h1>
<p class="meta">Workspace: {workspace}<br/>Generated: {gen}</p>

<h2>Summary</h2>
<ul>
<li>Specs: {sm.get('spec_count', 0)}</li>
<li>Requirements attested: {sm.get('attested', 0)}/{sm.get('total_requirements', 0)} ({sm.get('percent_complete', 0)}%)</li>
<li>Stale requirements: {sm.get('stale_count', 0)}</li>
<li>Hygiene: {html.escape(str(hygiene.get('status') or 'unknown'))}</li>
<li>Safe order: {safe_order_s}</li>
<li>Risk score: {html.escape(str(risk_score if risk_score is not None else '—'))}</li>
</ul>

<h2>Spec status</h2>
<table>
<thead><tr><th>Spec</th><th>Progress</th><th>State</th></tr></thead>
<tbody>
{"".join(spec_rows) if spec_rows else '<tr><td colspan="3">No specs discovered</td></tr>'}
</tbody>
</table>

<h2>Conflict graph</h2>
<pre class="mermaid">{html.escape(mermaid_src)}</pre>

<h2>Schedule risk per step</h2>
<table>
<thead><tr><th>Spec</th><th>Risk</th><th>Depends on</th></tr></thead>
<tbody>
{"".join(risk_rows) if risk_rows else '<tr><td colspan="3">—</td></tr>'}
</tbody>
</table>

<h2>Plan adherence drift</h2>
<table>
<thead><tr><th>Spec</th><th>Requirement</th><th>Unplanned files</th><th>Untouched planned</th></tr></thead>
<tbody>
{"".join(drift_rows) if drift_rows else '<tr><td colspan="4">No drift (or no registered plans)</td></tr>'}
</tbody>
</table>

<script>
(function() {{
  var nodes = {json.dumps(list(graph.keys()) if graph else [])};
  var edges = {json.dumps([[a, b] for a, b in _conflict_edges(graph)])};
  var pre = document.querySelector('.mermaid');
  if (pre && edges.length === 0) {{
    pre.textContent = 'No WR conflict edges.';
  }}
}})();
</script>
</body>
</html>"""


def render_md(bundle: Dict[str, Any], *, locale: str = "en") -> str:
    """Manager markdown summary from attested ledger facts only."""
    dto = bundle.get("dto") or {}
    sm = dto.get("summary") or {}
    att = int(sm.get("attested") or 0)
    total = int(sm.get("total_requirements") or 0)
    stale = int(sm.get("stale_count") or 0)
    hygiene = dto.get("hygiene") or {}
    conflicts = dto.get("conflicts") if isinstance(dto.get("conflicts"), dict) else {}
    failure = dto.get("failure")
    lines: List[str] = []

    if locale == "ru":
        lines.append(f"# Отчёт apatch\n")
        lines.append(f"Выполнено **{att}** из **{total}** требований ({sm.get('percent_complete', 0)}%).")
        need = stale + int(sm.get("spec_count", 0) or 0) - sum(
            1 for s in (dto.get("specs") or []) if s.get("done")
        )
        if need > 0 or stale > 0:
            lines.append(f"**{max(stale, need)}** пунктов требуют внимания.")
        if hygiene.get("status") not in (None, "ok", "clean"):
            lines.append(f"Гигиена workspace: **{hygiene.get('status')}**.")
        if conflicts.get("safe_order"):
            order = " → ".join(conflicts["safe_order"])
            lines.append(f"Рекомендуемый порядок спек: {order}.")
        if failure:
            lines.append(f"Сбой сессии: {failure.get('error_type', '?')}.")
    else:
        lines.append("# apatch report\n")
        lines.append(f"**{att}** of **{total}** requirements complete ({sm.get('percent_complete', 0)}%).")
        attention = stale
        for spec in dto.get("specs") or []:
            ssum = spec.get("summary") or {}
            if int(ssum.get("stale") or 0) > 0:
                attention += int(ssum.get("stale") or 0)
        if attention > 0:
            lines.append(f"**{attention}** items need attention.")
        if hygiene.get("status") not in (None, "ok", "clean"):
            lines.append(f"Workspace hygiene: **{hygiene.get('status')}**.")
        if conflicts.get("safe_order"):
            order = " → ".join(conflicts["safe_order"])
            lines.append(f"Recommended spec order: {order}.")
        if failure:
            lines.append(f"Session failure: {failure.get('error_type', '?')}.")

    activity = dto.get("activity") or []
    if activity:
        last = activity[0]
        author = last.get("signed_by") or "unknown"
        ts = last.get("timestamp") or "—"
        if locale == "ru":
            lines.append(f"Последнее изменение: {ts}, автор: {author}.")
        else:
            lines.append(f"Last change: {ts}, author: {author}.")

    return "\n\n".join(lines) + "\n"
