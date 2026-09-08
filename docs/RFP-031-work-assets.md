# RFP-031 — WorkAsset / Наработка layer

> **Status:** Draft v1 · **Date:** 2026-06-20 · **Owner:** apatch product/core  
> **Package context:** 0.7.x — make apatch not only record work, but form reusable human methodology assets  
> **Depends on:** [RFP-025 Avatar Foundation](./RFP-025-avatar-foundation.md) (`asset_summary`) · [RFP-026 Contribution Timesheet](./RFP-026-contribution-timesheet.md) (`ContributionEvent`) · [RFP-028 Avatar wiring](./RFP-028-avatar-wiring.md) (apatch → HC visible flow) · [vision.md](./vision.md)  
> **External consumers:** Human_Capital/HC_Platform, HC_Tracker, HC Capital / escrow / market surfaces  
> **Origin:** Product discussion: apatch should collect and reuse a person's accumulated experience / methodology, while HC Capital monetizes the resulting portable professional capital.

---

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| A31-A | `WorkAsset` schema exists for a reusable professional method: stable id, title, kind, problem signature, applicability, trigger signals, required context, outputs, spec refs, proof refs, verification command refs, portability policy, confidentiality class, lifecycle state, owner identity, and source evidence | MUST |
| A31-B | apatch can derive WorkAsset candidates deterministically from governed artifacts (`RFP`, `SPEC`, sessions, attestations, `asset_summary`) without source code, secrets, private prompts, or LLM scoring as source of truth | MUST |
| A31-C | Human review / promotion flow exists: `candidate → accepted → deprecated/rejected`; every promotion is governed and anchored to a ledger event | MUST |
| A31-D | CLI and MCP expose `list`, `show`, `search`, and `export` surfaces for WorkAssets; responses are content-safe and portable across AI tools | MUST |
| A31-E | apatch can suggest relevant WorkAssets for a new task with explainable match reasons and a guarded next step; suggestions never directly mutate code without a normal governed session | MUST |
| A31-F | Using a WorkAsset in a session emits a contribution/use record that links `asset_id`, `spec_refs`, `proof_ref`, and verification result, but carries no PI/GPI/Creator Bonus/clearing fields | MUST |
| A31-G | Export bundles for HC contain only safe metadata and proof references: no employer source code, private data, credentials, SSH topology, proprietary model prompts, or raw logs | MUST |
| A31-H | HC Tracker can ingest/read WorkAsset summaries and show a person's growing library of proven WorkAssets separately from raw timeline events | SHOULD |
| A31-I | HC Capital receives an opt-in economic lens over WorkAssets: pricing, portability, dependency split, and market use are HC responsibilities; apatch never computes PI/GPI or clearing | MUST |
| A31-J | Integration status upgrades from binary `apatch online` to asset readiness: `asset_summary`, `work_assets_count`, `last_export`, `last_ingest`, `contribution_events`, and `hc_capital_ready` | SHOULD |
| A31-K | Tests cover extraction, promotion, search/suggest, export redaction, use-event emission, and HC fixture ingest without network, SSH, or external credentials | MUST |
| A31-L | Onboarding docs explain how a new user gets from daily work to visible WorkAssets in Avatar Home and how HC Capital consumes them | SHOULD |

Canonical ids: this section.

---

## 1. Problem

RFP-025 gave apatch an `asset_summary`: a deterministic read model over attested work. RFP-026 gave it signed `ContributionEvent` receipts. RFP-028 connected the event stream to HC.

That is necessary, but still too low-level for the product promise.

Today apatch can prove that work happened and count attested artifacts. It does not yet expose the middle object that a person, an agent, or HC Capital actually needs:

```text
ContributionEvent  = one fact of work
WorkAsset          = reusable methodology / pattern / automation formed by work
asset_summary      = aggregate portfolio view
HC Capital lens    = opt-in market/economic interpretation
```

Without `WorkAsset`, the system has a gap:

| User / system question | Current answer | Missing answer |
|------------------------|----------------|----------------|
| What did I do? | `ContributionEvent`, timeline | ok |
| What have I learned / built as a reusable method? | scattered specs/RFPs/sessions | `WorkAsset` |
| Can an agent reuse this on a new task? | only if it remembers or searches docs | searchable/suggestable method |
| Can HC Tracker show my capital? | raw timeline / empty if no ingest | library of proven WorkAssets |
| Can HC Capital monetize it? | PI/GPI can price a profile, but not specific portable capabilities | economic lens over portable WorkAssets |

Observed current state on 2026-06-20: `apatch asset summary` over the apatch repo reports hundreds of attested artifacts and specs, while HC Tracker `/avatar-home` can still be empty if no `ContributionEvent` ingest has occurred. The value exists in apatch, but is not yet a product object visible to the person or consumable by HC Capital.

---

## 2. Product goal

Make apatch the **factory and proof engine for WorkAssets**:

```text
daily governed work
  → ContributionEvents and attestations
    → WorkAsset candidates
      → human/governed promotion
        → searchable reusable methodology
          → safe export / Avatar Home view
            → HC Capital opt-in economic lens
```

The target user experience:

- A person keeps working normally through agents and governed sessions.
- apatch discovers repeated methods, specs, automations, and proven patterns.
- The person sees a growing library: "these are my portable WorkAssets".
- Agents can apply the library safely in future tasks.
- HC Capital can price and monetize the portable capital without seeing protected code or secrets.

One-line product statement:

> apatch turns professional behavior into reusable, proven WorkAssets; Human Capital builds the market around them.

---

## 3. Definition: WorkAsset / Наработка

A `WorkAsset` is not code, not a resume bullet, not a model, and not a raw log. It is a portable, executable description of a professional method that has evidence behind it.

Examples:

- "Remote SSH governed repair workflow" from RFP-029/RFP-030.
- "Category onboarding methodology" from repeated `kb-catalog` category tasks.
- "Five-layer UX audit" from RFP-015.
- "Avatar event wiring playbook" from RFP-028.
- "Build failure diagnosis loop" from RFP-018.

Minimal schema v1:

```jsonc
{
  "schema_version": 1,
  "kind": "work_asset",
  "asset_id": "wa_...",
  "title": "Remote SSH governed repair workflow",
  "asset_kind": "methodology | playbook | automation | checklist | spec_bundle | diagnostic_pattern",
  "owner": { "key_id": "...", "agent_id": "..." },
  "problem_signature": ["remote workspace", "approval noise", "guarded patch"],
  "applicability": "When an agent must repair a remote repo through local MCP without raw SSH exposure.",
  "trigger_signals": ["remote_target", "ssh", "approval prompts", "workspace outside sandbox"],
  "required_context": ["remote policy alias", "apatch runtime on remote"],
  "outputs": ["governed patch", "verify report", "attestation"],
  "spec_refs": ["spec:SPEC-REMOTE-SSH-MCP-1#R3"],
  "rfp_refs": ["rfp:RFP-030"],
  "proof_refs": [{ "ledger": "trustchain", "op_ids": ["..."] }],
  "verification_refs": ["pytest tests/test_remote_ssh_transport.py -q"],
  "reuse": { "count": 0, "last_used_at": null },
  "portability_policy": {
    "content_safe": true,
    "exports_code": false,
    "exports_private_prompts": false,
    "exports_credentials": false,
    "redaction": "strict"
  },
  "dependency_lens": {
    "human": 1.0,
    "personal_ai": 0.0,
    "company_ai": 0.0,
    "company_data": 0.0,
    "third_party": 0.0
  },
  "lifecycle": "candidate | accepted | deprecated | rejected"
}
```

The `dependency_lens` is attribution metadata only. It is not PI/GPI and does not produce money inside apatch.

---

## 4. Layer ownership

This RFP preserves the Avatar Architecture Canon.

| Layer | Owner | Responsibility |
|-------|-------|----------------|
| Trust / proof | `trust_chain`, TrustChain Platform | keys, signatures, anchors, `key_id` |
| Work facts | `apatch` | governed sessions, specs, attestations, `ContributionEvent` emission |
| WorkAsset formation | `apatch` | candidate extraction, promotion, search, safe export |
| Avatar view | HC Tracker | timeline plus human-readable WorkAsset library |
| Economic interpretation | HC Capital / HC Platform | PI/GPI, Creator Bonus, pricing, escrow, clearing, company split |

Critical invariant: **apatch forms and proves WorkAssets; HC Capital monetizes them.**

---

## 5. Scope

### 5.1 apatch WorkAsset index

Add a deterministic local index over governed artifacts:

- specs and requirement coverage;
- RFP references;
- session intents and artifacts;
- attestation/proof refs;
- verification commands and outcomes;
- existing `asset_summary` fields.

The index should produce stable candidate ids and stable accepted asset ids from canonical JSON, not from mutable prose.

### 5.2 Promotion workflow

Candidates are not automatically claimed as assets. A governed promotion marks a candidate as accepted and records why it is portable.

Required states:

```text
candidate -> accepted
candidate -> rejected
accepted  -> deprecated
accepted  -> superseded_by another asset
```

Each transition must be ledger-backed.

### 5.3 Search and suggestion

Agents need a reusable surface:

- `apatch work-assets list --json`
- `apatch work-assets show <asset_id> --json`
- `apatch work-assets search <query> --json`
- `apatch work-assets suggest --intent "..." --json`
- MCP equivalents: `apatch_work_assets`, `apatch_work_asset_show`, `apatch_work_asset_suggest`

Suggestion output must include match reasons and guarded next action, for example:

```jsonc
{
  "asset_id": "wa_remote_ssh_guarded_repair",
  "score": 0.82,
  "reasons": ["remote workspace", "approval noise", "guarded session"],
  "next": {
    "tool": "apatch_session_start",
    "artifacts": ["work_asset:wa_remote_ssh_guarded_repair"]
  }
}
```

### 5.4 Use record

When a WorkAsset is used, apatch records the use in the governed session and emitted contribution facts:

- `asset_id`;
- version/hash;
- task/session id;
- verification result;
- proof ref;
- whether it was applied unchanged or adapted.

This is what makes reuse visible and monetizable later.

### 5.5 Content-safe export for HC

Export bundle for HC must contain:

- WorkAsset metadata;
- proof refs;
- spec/RFP refs and hashes;
- verification summaries;
- portability policy;
- dependency lens metadata.

It must not contain:

- source code blobs;
- employer data;
- credentials;
- raw shell logs with secrets;
- SSH hosts/paths/topology;
- private prompts or model transcripts;
- arbitrary files copied from repos.

### 5.6 HC Tracker and HC Capital integration

HC Tracker should display:

- total WorkAssets;
- accepted vs candidates;
- trust level and proof coverage;
- last used / reuse count;
- categories of methods;
- safe details for the person.

HC Capital should consume:

- accepted WorkAssets;
- proof coverage;
- portability policy;
- dependency lens;
- market/category tags;
- reuse and verification signals.

HC Capital may then compute price, portability, scarcity, Creator Bonus split, escrow behavior, or company_ai_share. apatch must not.

---

## 6. Non-goals

| Non-goal | Owner / reason |
|----------|----------------|
| PI/GPI, Creator Bonus, clearing, escrow | HC Capital / HC Platform |
| Public marketplace of WorkAssets | Later product RFP |
| Full IRS / DID identity system | HC ADR-004 / separate identity track |
| Exporting code or private data | Forbidden by ADR-007 non-invasiveness |
| LLM quality score for a person | Forbidden as source of truth; only deterministic proof signals |
| Automatic ownership claim over employer IP | WorkAsset metadata must separate portable method from company dependency |
| Replacing specs/RFPs | WorkAssets reference specs/RFPs; they do not replace governance |

---

## 7. Implementation phases

### Phase 1 — Schema and read-only index

Deliver `WorkAsset` schema, deterministic candidate extraction, and read-only CLI/MCP list/show/search over existing apatch data.

Candidate spec: `SPEC-WORK-ASSET-INDEX-1`.

### Phase 2 — Promotion and use tracking

Deliver governed promotion states, ledger-backed transitions, and session use records.

Candidate spec: `SPEC-WORK-ASSET-LIFECYCLE-1`.

### Phase 3 — Suggest/apply integration

Deliver intent-based suggestion and guarded next-step integration. Suggestions must not bypass normal sessions, verification, or attestation.

Candidate spec: `SPEC-WORK-ASSET-SUGGEST-1`.

### Phase 4 — Export bundle and HC fixture ingest

Deliver content-safe export and fixture ingest compatible with HC Tracker/HC Capital. No network dependency in apatch tests.

Candidate spec: `SPEC-WORK-ASSET-EXPORT-1`.

### Phase 5 — Avatar Home and HC Capital readiness

Consumer-side work in Human_Capital: Avatar Home WorkAssets view and HC Capital lens. apatch owns contract fixtures and docs, not HC UI implementation.

Candidate HC specs:

- `SPEC-AVATAR-WORK-ASSETS-1`
- `SPEC-HC-CAPITAL-WORK-ASSET-LENS-1`

---

## 8. Current evidence and gap

What exists now:

- `apatch asset summary` can expose a large proof-backed portfolio (`artifact_count`, `spec_count`, `methodology_tags`).
- HC Tracker has `/api/v1/integrations/status` and backend-owned TrustChain signing.
- HC Tracker has `contribution_events` and Avatar Home read-model plumbing.
- Services screen can show apatch / TrustChain / HC Capital / Witness status.

What is missing:

- no first-class WorkAsset schema;
- no candidate extraction;
- no promotion workflow;
- no use/reuse record;
- no WorkAsset export bundle;
- no Avatar Home WorkAsset library;
- no HC Capital lens over portable methods.

Therefore `apatch online` is not sufficient. The product needs `apatch has proven WorkAssets that can be reused and monetized`.

---

## 9. Metrics

| Metric | Target |
|--------|--------|
| WorkAsset candidates discovered per active repo | baseline from existing ledger |
| Accepted WorkAssets per professional | > 0 in design partner pilot |
| WorkAsset reuse events | tracked per asset/session |
| Export redaction failures | 0 |
| HC ingest fixture pass rate | 100% |
| Avatar Home visible WorkAssets | at least accepted assets with proof refs |
| HC Capital readiness | lens can price from metadata without source content |

---

## 10. Open questions

1. Should candidate extraction begin only from explicit `SPEC-*`/`RFP-*`, or also mine repeated session intents?
2. Should `dependency_lens` be self-declared at promotion time, inferred from claims, or left empty until HC declaration?
3. Is `WorkAsset` owned by a person key, a repo, or both? Canon suggests person key with project evidence.
4. Should deprecated WorkAssets remain exportable for audit/history?
5. Should consumer repos store imported WorkAssets or only cache summaries from apatch exports?

Resolve in the Phase 1 spec before implementation.

---

## 11. Product sentence

For users:

> "Your daily work becomes a library of proven, reusable methods you can carry forward."

For companies:

> "You can safely see and compensate portable professional capital without receiving proprietary source code or secrets."

For HC Capital:

> "WorkAssets are the unit that connects proof-backed human capability to market pricing and transfer economics."
