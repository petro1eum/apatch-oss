# A35-E — Transparency Anchor (CI-verifiable governance)

> **Status: active with a documented legacy limitation.** The transparency log runs at
> `https://trust-chain.ai`; CI verifies the source-controlled portable manifest.
> Runtime anchoring was repaired on 2026-07-11, but the current production log uses
> the legacy Merkle scheme, so the independent witness cannot yet require RFC 6962
> growth consistency. See the incident report (private operational report; not included in this OSS snapshot).

The local TrustChain ledger proves governance **on the machine that ran it**. A CI
runner has no ledger, so a `git commit --no-verify` that bypasses the local Ring-1
hook would be indistinguishable from a governed commit — which is why the lease-based
sandbox self-gate could only ever be advisory in CI (it has since been retired in
favour of this anchor).

A35-E closes that gap with an **external append-only transparency log**
(`trust-chain.ai`). Every governed op is pushed to the public Merkle log; CI verifies,
independently of the local ledger, that each recorded op is included. A fabricated
record (an `op_id` not in the public log) fails CI. A wiped or rewritten local ledger
cannot erase what the external log already anchored.

## The loop

```
apatch governed op  ──push──▶  trust-chain.ai  ──append──▶  Postgres verifiable log
   (commit_action)              /api/log/append            (async flusher worker)
        │                                                        │
        ▼                                                        ▼
 runtime .apatch/inclusion.jsonl ──publish verified subset──▶ manifests/apatch-inclusion.jsonl
   (ignored local runtime)                  │       /api/pub/log/merkle-root
        │                                   │       /api/pub/log/proof/{op_id}
        ▼                                   │                    │
   CI job: apatch trustchain verify-inclusion ──fetch proof──────┘
        → verify_audit_path(leaf → root) == public root, for every committed record
```

## Enabling the push (at attest time)

apatch pushes only when `APATCH_PLATFORM_*` is set in the environment where the
governed op runs (the MCP server / CLI / agent shell):

```bash
export APATCH_PLATFORM_URL=https://trust-chain.ai
export APATCH_AGENT_ID=your-enrolled-agent
export APATCH_AGENT_KEY=$HOME/.apatch/identity/your-enrolled-agent/agent.key
```

Enroll the agent once (public OSS path, no tenant/operator key needed):

```bash
apatch trustchain enroll --platform-url https://trust-chain.ai --agent-id <id> --invitation <token>
# or the self-serve public challenge/confirm flow
```

With those set, every `commit_action` (notarized governed op) is pushed to the log
and its `op_id` is appended to runtime `.apatch/inclusion.jsonl` (ignored by git). A reviewed, verified subset is published through governed mutation to `manifests/apatch-inclusion.jsonl` for CI.

## Verifying (locally or in CI)

```bash
apatch trustchain verify-inclusion --platform-url https://trust-chain.ai --json
```

- `ok: true` — every committed record is included in the public log (exit 0).
- empty / no records committed yet — vacuously ok (exit 0).
- a recorded op is **missing** from the public log, or its proof is **inconsistent**
  with the published root — exit 1 (CI blocks).

The CI job `transparency-anchor` (`.github/workflows/ci.yml`) stages `manifests/apatch-inclusion.jsonl` into an isolated runtime root, runs this verifier, and additionally rejects empty or degraded results.

## Why it can be a *blocking* gate (unlike the sandbox self-gate)

The sandbox `ci-gate` proves governance via an **active write-lease**, which is
ephemeral and never present on a fresh checkout — so it can only ever be advisory in
CI. The transparency anchor proves governance via a **committed `op_id` + a public
inclusion proof**, both of which survive into CI and to any third-party auditor. That
is the difference between "we promise it was governed" and "here is the public,
tamper-evident proof."

## Operational notes (platform side)

The transparency log runs on the platform host: `trustchain-platform-backend.service`
(append API, live `chain_head` read) and `trustchain-platform-worker.service` (the
async flusher that commits the Redis queue into the Postgres verifiable log). The
public read endpoints (`/api/pub/log/merkle-root`, `/api/pub/log/proof/{op_id}`,
`/api/pub/log/verify`) require no auth. Agent certs honor `TC_VALIDITY_HOURS`.

## Auto-renewal

Agent certs are short-lived (default `TC_VALIDITY_HOURS`). The key holder renews
unattended: re-run the public enroll (challenge/confirm) with the **same key** and the
platform re-issues a fresh cert — no operator/revoke, since the signed challenge proves
key ownership. On macOS a launchd job (`ai.trustchain.apatch-cert-renew` →
`~/.apatch/identity/<agent>/renew_cert.py`) does this weekly + at login, so the agent
always holds a valid cert without manual steps.
