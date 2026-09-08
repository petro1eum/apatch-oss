# SPEC-REPORT-1 — Architect HTML and manager markdown reports

> **Status:** Draft v1 · **Owner:** apatch core
> **apatch artifact:** `spec:SPEC-REPORT-1`
> **Anchors:** [RFP-020](../RFP-020-three-views.md) · depends on SPEC-PROJECT-STATUS-1

## 0. Motivation

Architects need a conflict graph and spec traffic-light without calling five APIs.
Managers need plain-language progress from attested ledger facts — not WR/L2 jargon.

Both views render the same `project_status_workspace` DTO; only presentation differs.

Non-goals: live web server (`apatch ui`); PDF export.

## R1 Static HTML report

CLI `apatch report --html [--out .apatch/report.html] [--target-dir .]` writes a
self-contained HTML file (embedded vis.js or mermaid for `conflict_graph`, spec
status table, schedule risk_per_step, adherence drift table).

(verify: python3 -m pytest tests/test_report.py::test_report_html_self_contained -q)

## R2 Manager markdown report

CLI `apatch report --format md [--locale ru|en] [--out -]` prints human sentences:
"N of M requirements complete. K need attention. Last change …, author …" using
only ledger-attested data and DTO blockers (stale, conflicts, session failure).

(verify: python3 -m pytest tests/test_report.py::test_report_md_contains_progress -q)

## R3 Shared renderer module

`apatch/report_render.py` holds HTML and MD renderers; CLI thin wrapper only.
No duplicate DTO assembly in renderers.

(verify: python3 -m pytest tests/test_report.py -q)

## Non-goals

- Email/Slack delivery
- Contribution vector scoring
