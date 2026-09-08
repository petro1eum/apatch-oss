# Executable specifications (RFP-007)

Specs live here as `SPEC-<ID>.md`. **Fresh agent:** [agent-onboarding.md](../agent-onboarding.md) —
`apatch_doctor` once, then `apatch_spec_lint` (format + RFP gates + `plan_scaffold` when passed).

1. Copy `SPEC-TEMPLATE.md` → `SPEC-<YOUR-ID>.md`
2. `apatch_spec_lint(spec='SPEC-<YOUR-ID>')` — fix until `passed: true`; read `plan_scaffold` / `agent_next`
3. Fill needles → `apatch_spec_run` (§3K) or `apatch_spec_next` / `apatch_execute_next` (§3I–§3J)

## Index

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-RUN-1](./SPEC-RUN-1.md) | RFP-009 spec run engine | attested |
| [SPEC-MONITOR-TAIL-1](./SPEC-MONITOR-TAIL-1.md) | Monitor & spec_run hardening (audit tail) | attested |
| [SPEC-APPLY-OBS-1](./SPEC-APPLY-OBS-1.md) | apply_session resume integrity & verify observability | attested |
| [SPEC-COVERAGE-1](./SPEC-COVERAGE-1.md) | RFP-010 requirement coverage & staleness | attested |
| [SPEC-PLAN-ARTIFACT-1](./SPEC-PLAN-ARTIFACT-1.md) | RFP-011 plan as artifact (decision + execution) | attested |
| [SPEC-ADHERENCE-1](./SPEC-ADHERENCE-1.md) | RFP-012 plan adherence | attested |
| [SPEC-LEDGER-ACTOR-1](./SPEC-LEDGER-ACTOR-1.md) | Named ledger actor (`signed_by` / HC attribution) | attested |
| [SPEC-INTERFERENCE-1](./SPEC-INTERFERENCE-1.md) | RFP-014 L1/L2 detection + graph | attested |
| [SPEC-INTERFERENCE-2](./SPEC-INTERFERENCE-2.md) | RFP-014 Level-3 cross-verify | attested |
| [SPEC-INTERFERENCE-3](./SPEC-INTERFERENCE-3.md) | RFP-014 Phase 3 coordination (MVCC, multi-run) | attested (7/7) |
| [SPEC-INTERFERENCE-4](./SPEC-INTERFERENCE-4.md) | RFP-014 L0 domain tag routing | attested (4/4) |
| [SPEC-INTERFERENCE-5](./SPEC-INTERFERENCE-5.md) | RFP-014 MCP strategy param parity | attested (2/2) |
| [SPEC-UX-SPECIALIST-1](./SPEC-UX-SPECIALIST-1.md) | RFP-015 five-layer UX audit + Phase 4 domain tags | attested (7/7) |
| [SPEC-VERSION-1](./SPEC-VERSION-1.md) | Semver 0.3.0 release + release-versioning policy | attested |
| [SPEC-EXECUTOR-1](./SPEC-EXECUTOR-1.md) | RFP-008 spec executor (`execute_next`) | attested (7/7) |
| [SPEC-BUILD-DIAGNOSE-1](./SPEC-BUILD-DIAGNOSE-1.md) | RFP-018 compiler feedback loop + VERIFY_FAILED enrich | attested (6/6) |
| [SPEC-DOC-OUTLINE-1](./SPEC-DOC-OUTLINE-1.md) | Numbered markdown / doc_outline playbook | attested |
| [SPEC-MCP-SCALE-1](./SPEC-MCP-SCALE-1.md) | RFP-019 L1 lanes, hygiene, profiles, apply_session isolation | attested (6/6) |
| [SPEC-GOVERNANCE-OPERATIONS-1](./SPEC-GOVERNANCE-OPERATIONS-1.md) | RFP-041 exact sessions, atomic cleanup, artifact isolation, differential verify and recovery | implemented; attestation pending |
| [SPEC-EPISODE-1](./SPEC-EPISODE-1.md) | RFP-037 Phase 1 WorkEpisode read model + qualification gate | implemented |
| [SPEC-CAPABILITY-1](./SPEC-CAPABILITY-1.md) | RFP-037 Phase 2 capability compiler (taxonomy, signals, uncertainty) | implemented |
| [SPEC-AVATAR-EVIDENCE-1](./SPEC-AVATAR-EVIDENCE-1.md) | RFP-037 Phase 4 capability_evidence bundle + `apatch avatar` CLI + status embed | implemented |
| [SPEC-PATH-LEASES-1](./SPEC-PATH-LEASES-1.md) | RFP-042 path-scoped writer leases, rollback isolation, ordered TrustChain commits | implemented; attestation pending |
| [SPEC-GOVERNED-WORK-BINDINGS-1](./SPEC-GOVERNED-WORK-BINDINGS-1.md) | RFP-043 TrustChain Change/source binding/evidence/outbox integration | implemented |

### RFP-021 Agent reliability (design partner)

Execution order: [RFP-021](../RFP-021-agent-reliability-design-partner.md) · playbook: [design-partner-playbook](../design-partner-playbook.md)

| Spec | AR / theme | Status |
|------|------------|--------|
| [SPEC-STATUS-BLOCKED-1](./SPEC-STATUS-BLOCKED-1.md) | AR-6 supervisor `session.blocker` | attested |
| [SPEC-SESSION-RECOVERY-1](./SPEC-SESSION-RECOVERY-1.md) | AR-1 MCP crash recovery | attested |
| [SPEC-VERIFY-ASYNC-1](./SPEC-VERIFY-ASYNC-1.md) | AR-2 async verify jobs | attested |
| [SPEC-FAILURE-TAXONOMY-2](./SPEC-FAILURE-TAXONOMY-2.md) | AR-3 fix_forward vs rollback | attested |
| [SPEC-DESIGN-PARTNER-1](./SPEC-DESIGN-PARTNER-1.md) | AR-7 playbook + G1–G7 | attested |

### RFP-029 Remote SSH workspaces

Execution order: [RFP-029-IMPLEMENTATION.md](./RFP-029-IMPLEMENTATION.md)

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-REMOTE-SSH-CORE-1](./SPEC-REMOTE-SSH-CORE-1.md) | remote target parser, allowlists, typed guards | attested (6/6) |
| [SPEC-REMOTE-SSH-WORKER-1](./SPEC-REMOTE-SSH-WORKER-1.md) | SSH JSON worker protocol, bootstrap/doctor handshake | attested (6/6) |
| [SPEC-REMOTE-SSH-MCP-1](./SPEC-REMOTE-SSH-MCP-1.md) | local MCP routing to remote governed operations | attested (8/8) |
| [SPEC-REMOTE-SSH-VERIFY-1](./SPEC-REMOTE-SSH-VERIFY-1.md) | remote verify, diagnostics, disconnect resume | attested (6/6) |

### RFP-023 Documentation governance (RFP→SPEC coverage)

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-RFP-COVERAGE-1](./SPEC-RFP-COVERAGE-1.md) | RFP Acceptance ↔ SPEC traceability gate | attested (8/8) |

### RFP-024 Needles hint v2 + scaffold clarity

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-NEEDLES-HINT-1](./SPEC-NEEDLES-HINT-1.md) | needles_hints + scaffold/templates clarity | attested (4/4) |

### RFP-026 Per-identity contribution ledger & cross-project timesheet

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-CONTRIB-TIMESHEET-1](./SPEC-CONTRIB-TIMESHEET-1.md) | signed ContributionEvent + `apatch timesheet` (identity/project/spec/day) | attested (10/10) |

### RFP-027 Agent UX & recovery hardening

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-AGENT-UX-1](./SPEC-AGENT-UX-1.md) | resume_session recovery, gc-safe backups, typed rollback, no silent apply no-op | attested (6/6) |
| [SPEC-AGENT-UX-2](./SPEC-AGENT-UX-2.md) | Phase 2: noop-attest (covered_by), per-session payload dedupe, self-edit restart signal | attested (4/4) |

### RFP-025 Avatar Foundation (asset compiler)

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-AVATAR-FOUNDATION-1](./SPEC-AVATAR-FOUNDATION-1.md) | Phase 1: deterministic `asset_summary` (avatar read model), CLI `apatch asset summary` + MCP `apatch_asset_summary`, non-invasive, economic boundary | attested (6/6) |

### RFP-031 WorkAsset / Narabotka layer

| Spec | Theme | Status |
|------|-------|--------|
| [SPEC-WORK-ASSET-INDEX-1](./SPEC-WORK-ASSET-INDEX-1.md) | WorkAsset schema, deterministic candidates, read-only CLI/MCP list/show/search/export | attested (5/5) |
| [SPEC-WORK-ASSET-EXPORT-1](./SPEC-WORK-ASSET-EXPORT-1.md) | Stable WorkAsset export contract, JSON Schema, content-safe fixture for HC consumers | attested (4/4) |
| [SPEC-WORK-ASSET-LIFECYCLE-1](./SPEC-WORK-ASSET-LIFECYCLE-1.md) | governed promotion, revocation, use records, and retention | attested (5/5) |
| [SPEC-WORK-ASSET-SUGGEST-1](./SPEC-WORK-ASSET-SUGGEST-1.md) | intent-based recall and bounded RecallBundle assembly | attested (5/5) |

### RFP-022 Understanding layer (diagnostics & knowledge graph)

Execution order: [RFP-022](../RFP-022-unified-diagnostics-knowledge-graph.md)

| Spec | Phase | Status |
|------|-------|--------|
| [SPEC-DIAGNOSTIC-GRAPH-1](./SPEC-DIAGNOSTIC-GRAPH-1.md) | 1 — `Diagnostic[]` schema, adapters, spine integration | attested |
| [SPEC-CONTRACT-EDGES-1](./SPEC-CONTRACT-EDGES-1.md) | 2 — symbol/contract edges on diagnostics | attested |
| [SPEC-KNOWLEDGE-GRAPH-1](./SPEC-KNOWLEDGE-GRAPH-1.md) | 3 — session graph, status/report views | attested |

### RFP-020 Productization (one data layer, three views)

Execution order: [RFP-020](../RFP-020-three-views.md)

| Spec | Stage | Status |
|------|-------|--------|
| [SPEC-PRODUCT-STAB-1](./SPEC-PRODUCT-STAB-1.md) | 0 — tests, cleanup, docs reconcile | attested |
| [SPEC-PROJECT-STATUS-1](./SPEC-PROJECT-STATUS-1.md) | 1 — unified `project_status` DTO + MCP | attested |
| [SPEC-CLI-STATUS-1](./SPEC-CLI-STATUS-1.md) | 2a — `apatch status`, spec list, doctor UX | attested |
| [SPEC-REPORT-1](./SPEC-REPORT-1.md) | 2b — HTML + manager MD reports | attested |
| [SPEC-ONBOARDING-1](./SPEC-ONBOARDING-1.md) | 3 — README quickstart, parity matrix | attested |

### RFP-016 Artifact Governance (implementation chain)

Execution order: [RFP-016-IMPLEMENTATION.md](./RFP-016-IMPLEMENTATION.md)

| Spec | Phase | Status |
|------|-------|--------|
| [SPEC-HYGIENE-CORE](./SPEC-HYGIENE-CORE.md) | invariants | attested (7/7) |
| [SPEC-HYGIENE-1](./SPEC-HYGIENE-1.md) | Phase 1 registry + report | attested (7/7) |
| [SPEC-HYGIENE-2](./SPEC-HYGIENE-2.md) | Phase 2 CLI/MCP report | attested (4/4) |
| [SPEC-GC-1](./SPEC-GC-1.md) | Phase 3 safe delete/rotate | attested (5/5) |
| [SPEC-SESSION-LIFECYCLE-1](./SPEC-SESSION-LIFECYCLE-1.md) | Phase 4 leases + session_end | attested (4/4) |
| [SPEC-LAYOUT-1](./SPEC-LAYOUT-1.md) | Phase 5 layout migrate (opt) | attested (3/3) |
| [SPEC-CONFORMANCE-GATE-1](./SPEC-CONFORMANCE-GATE-1.md) | RFP-035 continuous conformance — the gate under its own contract | attested (10/10) |

**Consumer mirror:** Human_Capital SPEC-TRUSTCHAIN-AUDIT-1 (external reference; not included in this OSS snapshot) (`trustchain_audit` v2).

Authoring standard: apatch `docs/spec-authoring.md` (apatch repo). Consumer playbook: `AGENTS.md` §1 (install/hygiene) + §3I–§3K. Runtime hygiene: [RFP-016](../RFP-016-runtime-hygiene.md) · [mcp_setup § hygiene](../mcp_setup.md).
