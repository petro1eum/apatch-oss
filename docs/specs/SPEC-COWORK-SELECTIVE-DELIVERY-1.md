# SPEC-COWORK-SELECTIVE-DELIVERY-1 — Explicit local-to-Cowork publication

> **apatch artifact:** `spec:SPEC-COWORK-SELECTIVE-DELIVERY-1`  
> **Anchors:** RFP-047-COWORK-SELECTIVE-DELIVERY  
> **Depends on:** SPEC-GOVERNED-WORK-BINDINGS-1  
> **Consumer gate:** TrustChain Agent `SPEC-COWORK-PROJECT-DELIVERY-1#R5`

## R0 Traceability and frozen surface

| RFP id | SPEC Rk | Disposition |
|---|---|---|
| CSD-1 | R1 | covered |
| CSD-2 | R2 | covered |
| CSD-3 | R3 | covered |
| CSD-4 | R4 | covered |
| CSD-5 | R5 | covered |
| CSD-6 | R6 | covered |
| CSD-7 | R7 | covered |
| CSD-8 | R8 | covered |

Require exactly eight unique RFP rows and one-to-one mappings. Freeze the public
orchestration names `preview_governed_evidence_publication`,
`publish_governed_evidence`, and `disconnect_governed_work`; no existing governed-work
wire schema or endpoint changes.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r0_traceability_and_surface)

## R1 Local-first evidence construction

Calling `build_governed_evidence(target_dir, binding_id=...)` without a publication
argument behaves as `queue_for_admission=False`. It creates or reuses only the local
signed evidence bundle and matching timesheet. Before and after snapshots prove no outbox,
ACK, network, governed-session or Avatar export mutation. The legacy boolean remains only
as an explicit compatibility request; implicit publication is forbidden.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r1_build_is_local_first)

## R2 Exact closed share plan

Preview reconstructs current signed local documents and exact active public connection
metadata. Return exactly `{ok,operation,plan}`; plan contains schema, plan_hash,
platform_origin, client_subject, tenant_id, project_group_id, scope, evidence_bundle_ref,
timesheet_ref and connection_generation. References contain ids and hashes only; timesheet
also exposes claimed_active_seconds. Canonical hashing excludes plan_hash. Reject non-HTTPS
origin and inconsistent bundle, timesheet or Change scope. Preview performs no write and
returns neither payloads nor signatures.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r2_preview_is_exact_content_free_and_read_only)

## R3 Exact confirmation and stale-plan rejection

Publish receives `plan` and `confirmation` only. It reconstructs the plan from current
local state and requires canonical byte equality plus
`confirmation == "publish:" + plan_hash`. Unknown fields, altered recipient, artifact,
scope, claimed seconds, generation or plan hash fail before outbox and network. Missing or
disconnected configuration is typed and fail-closed.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r3_publish_requires_exact_current_confirmation)

## R4 One canonical queued selection and privacy

A valid publish calls existing `queue_evidence_admission` with the exact signed bundle
and timesheet referenced by the plan. Exact retry is byte-identical and returns one entry.
Scan stored request and safe result for forbidden source, path, diff, prompt, credential,
transcript, grant, economic and Avatar keys and canaries. No alternative payload, endpoint
or task authority is introduced.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r4_publish_queues_once_without_scope_expansion)

## R5 Offline recovery and receipt boundary

Existing sync retries identical queued bytes while connected and records delivery only
after the existing purpose-separated signed receipt matches request, tenant, ProjectGroup,
bundle and hashes. Network failure, 2xx without receipt, mismatch and duplicate retry
preserve one pending entry without mutation.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r5_offline_retry_requires_exact_receipt)

## R6 Disconnect generation fence

Disconnect writes one immutable local fence for the current configuration generation and
is idempotent for the same state. New publish and sync of unacknowledged entries from that
generation fail before HTTP; build, preview and status remain local and available.
Acknowledged history is retained. Explicit reconnect advances generation and never
relabels old pending entries as current or delivered.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r6_disconnect_fences_future_sharing_without_deleting_local_history)

## R7 MCP and compatibility surface

Register three bounded full-profile MCP operations and keep existing tool names,
Change/evidence/timesheet bytes, endpoints and status semantics unchanged. Tool results
contain no local path, payload document, signature value or credential. Update English
onboarding to describe preview, confirm, publish, sync and disconnect semantics.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r7_mcp_surface_and_compatibility)

## R8 Cowork cross-product acceptance fixture

Provide a stable installed-APatch seam for Cowork. It proves local build, read-only preview,
exact publish, offline retry, validated ACK and disconnect while all local signed artifacts
remain byte-identical. The fixture asserts no Avatar export or Avatar outbox is created;
Avatar remains a separate opt-in integration.

(verify: python3 -m pytest -q tests/test_cowork_selective_delivery.py::test_r8_cowork_consumer_round_trip)
