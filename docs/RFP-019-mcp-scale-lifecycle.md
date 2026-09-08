# RFP-019: MCP Scale, Lifecycle & External Release Architecture

> **Status:** Draft v1 — **Level 1 implemented** (L1-1…L1-10, [SPEC-MCP-SCALE-1](./specs/SPEC-MCP-SCALE-1.md) attested); Levels 2–3 **documented only** (no product rollout)  
> **Date:** 2026-06-10  
> **Depends on:** RFP-003 (MCP transport), RFP-005 (sandbox/lease), RFP-016 (AGL), [mcp_performance.md](./mcp_performance.md)  
> **Blocks:** External pip/marketplace release, hosted MCP SaaS (future)

---

## 0. Problem Statement

apatch MCP today is a **long-lived stdio Python process** per IDE window. That model works for dogfood but fails operationally when:

1. IDE reload does not kill the old process → **ghost MCP** instances compete for CPU and sandbox leases.
2. `write_lease.json` survives process death until the next `acquire_lease` → perceived hangs on `apply_session`.
3. Every hot-path tool may attach **multi-KB playbooks** (`protocol_contract`, `spec_run`) → token and serialize cost at agent scale.
4. `session_state.json` is written on **every** MCP tool response → unnecessary disk I/O.

For an **external release** (millions of installs) and a possible **hosted MCP** product, these are not polish items — they are **capacity and trust** requirements.

This RFP separates three maturity levels. **Only Level 1 is implemented incrementally now.** Levels 2–3 are architecture contracts for when the product is ready to ship — not deployment targets for 2026-Q2.

---

## 1. Deployment Models (mental model)

```text
Level 1 — Local stdio (today)
  One IDE window → one apatch-mcp process → one consumer workspace tree
  State: .apatch/* on local disk
  Scale unit: user machine

Level 2 — Distributed client package (pip / marketplace)
  Same as L1, but packaged, versioned, supported for arbitrary repos
  Scale unit: N independent local processes (N ≈ active users × IDE windows)

Level 3 — Hosted MCP (SSE / HTTP / WebSocket)
  Remote agent → apatch worker fleet → ephemeral workspace volume
  State: tenant-scoped store (Redis/DB), not shared NFS leases
  Scale unit: horizontally scaled pods behind LB
```

**Critical:** Level 2 does **not** centralize traffic. "Millions of users" at L2 means millions of **local** MCP processes, each O(1) to apatch ops. Level 3 is the only model where apatch runs as a **multi-tenant service**.

---

## 2. Level 1 — Local stdio (implement now)

### 2.1 Goals

| ID | Goal | Status |
|----|------|--------|
| L1-1 | MCP process releases sandbox leases on shutdown | **Implemented** — `apatch/mcp/lifecycle.py` |
| L1-2 | Stale lease sweep on first workspace touch per process | **Implemented** — `touch_workspace()` |
| L1-3 | Skip subprocess MCP health probe in stdio mode | **Done** (2026-06-10) |
| L1-4 | Orphan lease reclaim in stdio `acquire_lease` | **Done** (2026-06-10) |
| L1-5 | `APATCH_MCP_GUIDANCE=doctor_only` env — slim hot-path responses | **Implemented** — `agent_guidance.mcp_guidance_mode`, consumer `mcp.json` default |
| L1-6 | Session state write-behind (persist on phase change only) | **Implemented** — `_WRITE_TRIGGER_KEYS`, `session_state_write_behind` |
| L1-7 | `APATCH_MCP_PROFILE=compact\|core\|spec\|full` tool tiers | **Implemented** — `apatch/mcp/profiles.py`; RFP-041 makes the 15-tool `compact` profile default |
| L1-8 | `apatch_mcp_hygiene` — ghost PIDs + stale leases report | **Implemented** — MCP tool + `apatch mcp hygiene`; `healthy` vs `ok` |
| L1-9 | Lane-scoped runtime (spec → `.apatch/lanes/<id>/`) | **Implemented** — `lane.py`, `lane_context.py`, `worktree_lane.py`; [SPEC-MCP-SCALE-1](./specs/SPEC-MCP-SCALE-1.md) |
| L1-10 | MCP Resources for static playbooks | **Implemented** — `apatch/mcp/resources.py`: 8 URIs under `apatch://playbook/*` (index, protocol_contract, runtime_hygiene, sandbox_protocol, spec_authoring, tool_usage, spec_run, spec_execution); mirrored in `apatch_doctor` |

### 2.2 Lifecycle contract (L1-1, L1-2)

```text
MCP startup (launcher)
  → register_mcp_lifecycle_hooks()  # atexit + SIGTERM/SIGINT
  → sweep_stale_lease(cwd) if .apatch/ exists

Each MCP tool call (server wrapper)
  → touch_workspace(target_dir)
       → sweep stale lease once per workspace per process

MCP shutdown (IDE reload, kill, crash after signal)
  → release_leases_for_current_pid() across touched workspaces
```

**Invariant:** A dead MCP pid must not leave a **valid** lease blocking a live MCP pid on the same repo. `_lease_valid()` already treats dead pids as invalid; L1-2 removes the stale file eagerly.

### 2.3 Operator playbook (local)

1. **Parallel agents on one repo:** use **git worktree lanes** — one Cursor window per worktree path, each with isolated `.apatch/` (lease, session_state). Main clone for integration/PRs only. Consumer example: `manifests/worktrees.yaml` + `scripts/apatch-worktree.py` (TrustChain_Platform).
2. One apatch MCP process **per IDE window**; never two MCP writers on the same `target_dir`.
3. After MCP reload: `ps aux | grep 'apatch-mcp\|apatch.mcp.launcher'` — expect **~1 process per open Cursor window** (0 if MCP disabled).
4. `apatch_doctor` once per agent session — not per chunk.
5. `apply_session` with `verify_deferred=true`; run `apatch_verify_run` separately.
6. See [mcp_performance.md](./mcp_performance.md) for benchmarks and hot-path diagram.

### 2.4 Remaining L1 backlog (priority)

| Priority | Item | Effect |
|----------|------|--------|
| P3 | Lazy imports in `server.py` | Faster cold start |
| P3 | Hosted MCP L2/L3 | **Deferred** — documented only; no product rollout |

---

## 3. Level 2 — External client release (documented, not shipping)

> **Gate:** Product, legal, and support readiness. **No hosted infra required.** Distribution: `pip install apatch[mcp]`, Cursor MCP marketplace, docs site.

### 3.1 Product shape

| Aspect | Contract |
|--------|----------|
| **Runtime** | Local stdio only (`python -m apatch.mcp.launcher`) |
| **Trust** | Local `.trustchain/` by default; optional Platform sync via env |
| **Updates** | Semver; `tooling_refresh` in doctor when apatch source mismatch |
| **Config** | Project `.apatch/mcp.json` (canonical); IDE stub → `python -m apatch.mcp.workspace_launcher` |
| **Support matrix** | macOS + Linux first; Windows stdio documented separately |

### 3.2 Packaging requirements

```json
{
  "mcpServers": {
    "apatch": {
      "command": "<stable-python>",
      "args": ["-m", "apatch.mcp.launcher"],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "APATCH_MCP_PROFILE": "compact",
        "APATCH_MCP_GUIDANCE": "doctor_only"
      }
    }
  }
}
```

| Env var | Values | Purpose |
|---------|--------|---------|
| `APATCH_MCP_PROFILE` | `compact` (default), `core`, `spec`, `full` | Intent-level default; expand only for specialist work |
| `APATCH_MCP_GUIDANCE` | `doctor_only`, `full` | Playbook attachment policy |
| `APATCH_PLATFORM_URL` | optional | TrustChain Platform push |
| `APATCH_TELEMETRY` | `off` (default), `opt-in` | Aggregated latency/conflict metrics |

### 3.3 Tool tiers (target)

| Profile | ~Tools | Audience |
|---------|--------|----------|
| **compact** | 15 | **Default** — complete intent-level governed workflow |
| **core** | 26 | Opt-in — compact + generation, chunked apply, extensions and hygiene |
| **spec** | 37 | Opt-in — core + advanced multi-SPEC and slug operations |
| **full** | 124 | Opt-in — complete product surface |

Handshake size scales with profile.

### 3.4 Singleton guard (optional, L2)

File lock `~/.apatch/mcp-instance.lock` or repo-local `.apatch/mcp.lock`:

- Second MCP on **same `target_dir`** gets structured error `MCP_INSTANCE_CONFLICT` with hint to restart IDE MCP — instead of silent lease ping-pong.
- Different repos on same machine: allowed.

### 3.5 Telemetry (opt-in only)

| Metric | Use |
|--------|-----|
| `tool_latency_ms` histogram per tool | Perf regression detection |
| `lease_conflict_total` | L1 lifecycle effectiveness |
| `ghost_process_detected` | Installer health |
| **Never** | Source code, patch content, file paths |

Batch upload to Platform or drop to local `DEBUG` log only.

### 3.6 Release checklist (before public pip)

- [ ] L1-1…L1-8 complete or explicitly waived with docs
- [ ] RFP-016 GC safe mode tested on fresh consumer scaffold
- [ ] `apatch init-consumer --with-mcp` produces canonical config
- [ ] Restart docs: MCP reload + ghost process FAQ
- [ ] Semver policy for MCP tool additions (minor = new tools optional)
- [ ] Security review: sandbox hooks, lease scope, control-plane paths
- [ ] Load test: 1000 sequential `apply_session` chunks on medium repo (local CI)

### 3.7 What L2 is NOT

- Not a centralized apatch server
- Not multi-tenant workspace hosting
- Not replacement for TrustChain Platform (audit hub stays separate)

---

## 4. Level 3 — Hosted MCP service (documented, future)

> **Gate:** L2 stable, Platform API mature, SRE runbooks. **Separate product SKU** (e.g. TrustChain Agent hosted tools).

### 4.1 Architecture

```text
                    ┌─────────────────┐
  Agent clients     │  API Gateway    │  Auth: API key / OAuth / mTLS
  (SSE / HTTP MCP)  │  rate limit     │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        apatch-worker   apatch-worker   apatch-worker
        (stateless)     (stateless)     (stateless)
              │              │              │
              └──────────────┼──────────────┘
                             ▼
              ┌──────────────────────────────┐
              │ Ephemeral workspace volume    │
              │ per session_id (TTL 1–24h)    │
              └──────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
        Redis (lease/session)      TrustChain Platform
        Postgres (tenant meta)     (async notarization queue)
```

### 4.2 Transport

| Transport | Use case |
|-----------|----------|
| **stdio** | Local IDE only — never exposed on network |
| **SSE** | Browser / remote agents (MCP spec HTTP binding) |
| **WebSocket** | Low-latency bidirectional (optional) |

RFP-003 §4.2 envisioned `apatch mcp --transport sse`. L3 implements that behind auth, not as open port on user laptop.

### 4.3 State model (must differ from L1)

| L1 (file) | L3 (service) |
|-----------|--------------|
| `write_lease.json` | Redis lease key `tenant:session:lease` TTL 300s |
| `session_state.json` | Redis hash `tenant:session:state` |
| `.apatch/backups/` | Object storage per session prefix |
| `.trustchain/` local | Platform append + optional session export |

**Rule:** Workers must not share a writable NFS workspace between pods. Clone git snapshot → isolated volume → destroy on session end.

### 4.4 Request path (hot tool)

```text
1. AuthN + tenant quota check
2. Resolve session_id → workspace volume (create if new)
3. Execute tool in-process (same workflows.py — no fork per call)
4. Verify jobs → async queue (response returns job_id, not blocking pytest)
5. Notarization → batch Platform append (eventual consistency)
6. Response: slim JSON (no full playbooks — client fetched doctor at session start)
```

### 4.5 Scaling dimensions

| Dimension | Strategy |
|-----------|----------|
| Concurrent users | HPA on worker CPU + queue depth |
| Large repos | Shallow git clone; PathIndex warm per session |
| Heavy verify | K8s Job per verify; MCP polls `apatch_verify_status` |
| Multi-region | Stateless workers; Platform is global audit sink |
| Cold start | Pre-baked container image with tree-sitter grammars |

### 4.6 SLO targets (draft)

| SLO | Target |
|-----|--------|
| `apatch_session_state` p99 | < 200 ms (no verify) |
| `apatch_apply_session` p99 | < 2 s per chunk (5 files, no verify) |
| `apatch_doctor` p99 | < 3 s |
| Availability | 99.9% (hosted tier) |
| Session data retention | 24h default; export to customer S3 optional |

### 4.7 Security (L3)

- Tenant isolation at volume + Redis key prefix
- No cross-tenant `target_dir` traversal
- Secrets via KMS; never in MCP tool args logging
- Rate limits per API key (tools/list, apply_session)
- Audit: every mutation → Platform op_id (same as local enforce semantics)

### 4.8 Cost model (order of magnitude)

At 1M MAU, assume 10% daily active × 50 tool calls:

- **L2:** Compute on user machines — apatch cost ≈ **support + CDN**, not CPU fleet
- **L3:** 5M tool calls/day → ~60 RPS average, ~600 RPS peak → modest K8s cluster if verify async; **verify-sync** would dominate cost (forbid on hosted default)

### 4.9 Migration path L2 → L3

| Phase | Deliverable |
|-------|-------------|
| 3a | SSE transport local (`localhost`) — dev only |
| 3b | Single-tenant hosted beta (Team SKU) |
| 3c | Multi-tenant + quota + async verify |
| 3d | Geo replication + enterprise mTLS |

### 4.10 What L3 is NOT

- Not "run stdio MCP on a VPS and expose stdin" — fragile, unscalable
- Not replacement for Cursor's local sandbox hooks (hosted agents use different write policy)

---

## 5. Cross-cutting: response slimming (L1 → L3)

| Field | Today | Target |
|-------|-------|--------|
| `protocol_contract` | doctor + generate + spec_* | **doctor only** (or MCP Resource) |
| `spec_run` playbook | doctor + spec tools | doctor + `apatch_spec_run` only |
| `tooling_refresh` | doctor + apply after source edit | doctor + apply in apatch repo only |
| `state_update` | every tool | keep (small) |
| `entries[]` in apply | capped 25 | keep `compact_apply_result` |

Estimated savings: **30–50%** MCP response bytes on spec-heavy sessions.

---

## 6. Relationship to other RFPs

| RFP | Link |
|-----|------|
| RFP-003 | MCP transport abstraction; SSE entry point for L3 |
| RFP-005 | Lease + sandbox — L1 lifecycle implements operational half |
| RFP-016 | AGL GC — EPHEMERAL `write_lease.json` class; complements L1-2 |
| RFP-009 | spec_run batching — reduces round-trips (agent protocol, not transport) |
| mcp_performance | Benchmarks, P0/P1 history, L1 backlog B1–B8 |

---

## 7. Implementation log

| Date | Change |
|------|--------|
| 2026-06-10 | RFP-019 draft; L1-1/L1-2 `apatch/mcp/lifecycle.py` + launcher/server hooks |
| 2026-06-10 | L1-3/L1-4 mcp_health stdio skip + orphan lease reclaim (prior commit) |
| 2026-06-11 | **Package 0.5.0** — L1-10 full MCP resources + RFP-018 build diagnose + RFP-016 reconcile |
| 2026-06-10 | **Package 0.4.0** — L1 shipped with RFP-016 minor; L2/L3 remain doc-only |
| 2026-06-11 | L1-6…L1-10 — lane isolation, hygiene `healthy`, profiles, MCP Resources, apply_session lane scope; [SPEC-MCP-SCALE-1](./specs/SPEC-MCP-SCALE-1.md) attested |

---

## 8. Open questions (for product review)

1. **Marketplace:** Cursor-only first or Claude Code / Windsurf parity at L2 launch?
2. **Default profile:** **Resolved by RFP-041** — `compact` (15 tools); `core`, `spec`, and `full` are deliberate capability expansions.
3. **Hosted verify:** Mandatory async on L3, or premium sync tier?
4. **Singleton lock:** Opt-in or default on L2?

---

## 9. References

- [mcp_performance.md](./mcp_performance.md) — measured hot path, rollback commits
- [mcp_setup.md](./mcp_setup.md) — install, compact default and 110-tool full parity table
- [archive/RFP-003-agent-mcp-platform.md](./archive/RFP-003-agent-mcp-platform.md) — original transport vision
- [RFP-016-runtime-hygiene.md](./RFP-016-runtime-hygiene.md) — EPHEMERAL lease artifact class
