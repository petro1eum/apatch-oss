# RFP-020 — One data layer, three views

> **Status:** Active · **Owner:** apatch product
> **Executable specs:** SPEC-PROJECT-STATUS-1 → SPEC-CLI-STATUS-1 → SPEC-REPORT-1 → SPEC-ONBOARDING-1

## 1. Problem

apatch already collects attested state in TrustChain and `.apatch/` runtime, but
each client assembles its own picture from many tools. That blocks product UX for
three audiences with different language needs but the same facts.

## 2. Architecture

```text
Sources (read-only)
  TrustChain ledger     — who/when/what attested
  docs/specs/SPEC-*.md  — declared requirements
  .apatch/ runtime      — session, registry, hygiene

Facade
  project_status_workspace()  →  DTO schema_version=1
  apatch_project_status (MCP) — same DTO for IDE agents

Views (presentation only)
  Developer   apatch status              Rich CLI, --json
  Architect   apatch report --html       static conflict graph + spec table
  Manager     apatch report --format md  plain language, ledger facts only
```

| Audience | Question | View |
|----------|----------|------|
| Developer | What is broken / blocked right now? | `apatch status` |
| Architect | Where do specs conflict? Safe order? | HTML report |
| Manager | Are we on track? | Markdown report |

## 3. TrustChain bridge (HC Platform)

apatch is the first **verifiable** implementation of TrustChain attribution in
code: each attested apply links requirements, files, and `signed_by`. That is a
**Contribution Vector for software** — the same mechanic HC Platform applies to
labour-market attribution. apatch does not implement scoring or FIO mapping; it
produces the auditable substrate.

## 4. Implementation chain

| Order | Spec | Deliverable |
|-------|------|-------------|
| 0 | SPEC-PRODUCT-STAB-1 | Green tests, repo cleanup, doc reconcile |
| 1 | SPEC-PROJECT-STATUS-1 | `apatch/project_status.py`, MCP tool, adherence fix |
| 2a | SPEC-CLI-STATUS-1 ✅ | `apatch status`, `spec list`, doctor UX |
| 2b | SPEC-REPORT-1 ✅ | HTML + MD reports |
| 3 | SPEC-ONBOARDING-1 | README, this RFP indexed, parity matrix |

Stages 2a and 2b may run in parallel after Stage 1.

## 5. Non-goals

- Hosted web UI (`apatch ui serve`)
- Contribution economy scoring
- PyPI / license (internal product until decided)

## 6. References

- [RFP-007](./RFP-007-executable-specifications.md) — executable specs
- [RFP-014](./RFP-014-spec-interference-detection.md) — conflict graph
- [RFP-016](./RFP-016-runtime-hygiene.md) — hygiene in status DTO
- [RFP-019](./RFP-019-mcp-scale-lifecycle.md) — MCP resources for agent docs
