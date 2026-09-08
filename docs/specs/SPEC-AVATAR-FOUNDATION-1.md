# SPEC-AVATAR-FOUNDATION-1 — Avatar Compiler (asset_summary read model)

> **Status:** Draft v1 · **Owner:** apatch product  
> **apatch artifact:** `spec:SPEC-AVATAR-FOUNDATION-1`  
> **Anchors:** [RFP-025](../RFP-025-avatar-foundation.md) (AF-1/2/3/7/9) · [vision.md](../vision.md) · [RFP-026](../RFP-026-contribution-timesheet.md) (identity reuse)

## 0. Motivation

Phase 1 of RFP-025: turn the already-signed governed ledger into a deterministic
`asset_summary` — the read model of a professional's accumulated, attested work
(the "you discover you have an asset" moment). Read-only, non-invasive (counts, ids,
hashes, timestamps — no source content, no economics), discovered from usage not
uploaded. Stages 2–4 (export bundle, HC ContributionEvent ingest, company lens,
economic interpretation) are out of scope here.

## R0 RFP traceability gate (meta)

RFP-025 Acceptance: AF-1/2/3/7/9 land here; AF-4/5/6/8 are later phases (covers AF-9).

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| AF-1 | R1 | covered |
| AF-2 | R2 | covered |
| AF-3 | R3 | covered |
| AF-7 | R4 | covered |
| AF-9 | R0 | covered |
| AF-4 | — | waiver: Phase 2 — portable export bundle |
| AF-5 | — | waiver: apatch-side ContributionEvent emit done in RFP-026; HC ingest deferred |
| AF-6 | — | waiver: Phase 1 SHOULD — company/org lens deferred |
| AF-8 | — | waiver: key_id via RFP-026 resolve_identity; full Platform-CA hook deferred |

(verify: python3 -m pytest tests/test_avatar_compiler.py::test_r0_self_coverage_rfp_025 -q)

## R1 asset_summary read model

`apatch/avatar_compiler.py` builds a deterministic `asset_summary` from the TrustChain
ledger + traceability: `artifact_count`, `spec_ids[]`, `spec_count`,
`requirement_states` (attested Rk per spec), `methodology_tags[]` (derived from
artifact kinds + spec families — no LLM), `attested_at_range`, and `identity`
(`key_id`/`cert_fingerprint`/`agent_id`/`ca`, reused from RFP-026). Same ledger →
same summary (reproducible).

(verify: python3 -m pytest tests/test_avatar_compiler.py::test_r1_asset_summary_schema -q)

## R2 Discovery, not upload

The summary is computed only from governed usage already in the ledger —
`build_asset_summary(target_dir)` takes no profile/upload input. There is no
"build my profile" form.

(verify: python3 -m pytest tests/test_avatar_compiler.py::test_r2_discovery_no_upload -q)

## R3 Opt-in economic boundary

`asset_summary` MUST NOT contain PI, GPI, Creator Bonus, clearing, or any economic
interpretation — only counts, ids, hashes, tags, timestamps. Economic meaning is a
separate HC product surface (RFP-025 AF-3).

(verify: python3 -m pytest tests/test_avatar_compiler.py::test_r3_economic_boundary -q)

## R4 Aha surface in project_status

`project_status_workspace` embeds `asset_summary` so a professional sees "N attested
artifacts / methodology" without opening any market UI (RFP-025 AF-7).

(verify: python3 -m pytest tests/test_avatar_compiler.py::test_r4_project_status_embeds_asset_summary -q)

## R5 CLI + MCP surfaces

CLI `apatch asset summary [--json]` and read-only MCP `apatch_asset_summary` expose
the compiler; both never mutate.

(verify: python3 -m pytest tests/test_avatar_compiler.py::test_r5_cli_and_mcp_surfaces -q)

## Non-goals

- Economic interpretation (PI/GPI/marketplace) — explicit HC opt-in surface.
- Portable export bundle, HC event ingest, company lens — later RFP-025 phases.
- LLM scoring of "how good" — deterministic counts only.
