# SPEC-AVATAR-SELECTIVE-PUBLICATION-1 — Exact governed evidence publication to Avatar

> **apatch artifact:** `spec:SPEC-AVATAR-SELECTIVE-PUBLICATION-1`  
> **Anchors:** RFP-048-AVATAR-SELECTIVE-PUBLICATION  
> **Depends on:** SPEC-GOVERNED-WORK-BINDINGS-1 and SPEC-COWORK-SELECTIVE-DELIVERY-1  
> **ownership mode:** strict

## R0 RFP traceability gate (meta)

owns: docs/RFP-048-AVATAR-SELECTIVE-PUBLICATION.md, docs/specs/SPEC-AVATAR-SELECTIVE-PUBLICATION-1.md, tests/test_avatar_selective_publication.py

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| AVP-1 | R1 | covered |
| AVP-2 | R2 | covered |
| AVP-3 | R3 | covered |
| AVP-4 | R4 | covered |
| AVP-5 | R5 | covered |
| AVP-6 | R6 | covered |
| AVP-7 | R7 | covered |
| AVP-8 | R8 | covered |

Require exactly eight unique acceptance ids, one-to-one R1–R8 mappings, strict
ownership declarations and no placeholder verification command.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r0_traceability_and_ownership)

## R1 Exact source-bound selection

owns: apatch/governed_work_mcp.py, tests/test_avatar_selective_publication.py

Resolve publication from one exact binding using the already validated current evidence
bundle and matching timesheet. Load the binding and revalidate each selected
ContributionEvent signature and source-binding artifact. Match the bundle's complete,
nonempty, sorted, duplicate-free refs to exactly one local event each by both
`event_id` and `document_hash`. Require one nonempty subject key and one nonempty
avatar id across the selection. An unrelated store event is not selected. Missing,
duplicate, hash-drifted, signature-invalid, mixed-identity and mismatched-binding
fixtures each fail before returning documents.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r1_exact_bundle_selection)

## R2 Closed read-only preview

owns: apatch/governed_work_mcp.py, tests/test_avatar_selective_publication.py

`preview_avatar_contribution_publication` returns exactly
`{ok, operation, plan}`. The plan has only the schema, canonical recipient origin,
avatar and subject ids, tenant and ProjectGroup, scope, binding ref, bundle ref,
ContributionEvent refs and `plan_hash` defined by RFP-048. Two previews are
byte-identical. Filesystem and HTTP spies prove zero writes, receipts, outbox entries,
sessions and requests. Recursive key/value and canary checks prove the result contains
no event body, signature, token, path, source, prompt, transcript, timesheet/economics,
decision, capability assertion or unrelated id.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r2_preview_closed_stable_and_read_only)

## R3 Exact recipient boundary

owns: apatch/governed_work_mcp.py, tests/test_avatar_selective_publication.py

Canonicalize recipient origins as lower-case HTTPS scheme plus lower-case IDNA host and
optional non-default numeric port, with no userinfo, path other than empty or `/`,
query or fragment. The default allowlist contains only `https://trust-chain.ai`;
additional exact origins come only from `APATCH_AVATAR_ALLOWED_ORIGINS`. Reject HTTP,
credentials, paths, queries, fragments, malformed ports, Unicode/lookalike or suffix
hosts, and every unlisted origin. Preview and publish apply the same validator before
reading a token or constructing a client.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r3_recipient_allowlist_is_exact)

## R4 Confirmation, reconstruction and tamper rejection

owns: apatch/governed_work_mcp.py, tests/test_avatar_selective_publication.py

`publish_avatar_contributions` accepts an exact plan and confirmation, validates the
closed schema without coercion, requires `confirmation == "publish:" + plan_hash`,
reconstructs the current plan from signed local state, and requires canonical byte
equality. Unknown fields, bool-as-string/integer confusion, changed origin, scope,
identity, binding, bundle, event id/hash, order or plan hash, and changed local state
all fail before token lookup, HTTP, receipt or other persistence.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r4_publish_reconstructs_and_requires_confirmation)

## R5 Selected events only

owns: apatch/contribution_export.py, apatch/governed_work_mcp.py, tests/test_avatar_selective_publication.py

Add an exact allowlist mode to the existing Avatar contribution transport. It resolves
every requested id exactly once and fails closed on a missing or duplicate local id.
Upload and reconciliation receive only the allowlisted events. The governed publisher
passes only bundle refs and never calls broad export, Avatar CapabilityEvidence,
Cowork evidence admission or timesheet delivery. A trap fixture with at least one valid
unrelated event asserts that its id appears in no request, receipt, result or helper
argument.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r5_unrelated_event_never_crosses_boundary)

## R6 Idempotent fail-closed delivery

owns: apatch/contribution_export.py, apatch/governed_work_mcp.py, tests/test_avatar_selective_publication.py

Use the existing per-event digest-bound Avatar receipts for the selected subset only.
After exact remote ACK and reconciliation, replay attempts zero uploads. Network
failure, malformed or partial ACK, remote identity refusal, missing/empty/rejected token
and wrong-owner response create no false-success receipt and retain the exact retry set.
The token is read only after all local and recipient validation and is absent from
files, plan, result, exception text and repr.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r6_delivery_idempotency_and_token_fail_closed)

## R7 Bounded MCP and compatibility surface

owns: apatch/conformance.py, apatch/tool_paths.py, tests/test_avatar_selective_publication.py, tests/test_governed_work_mcp.py, tests/test_tool_paths.py

The shared MCP registry remains structurally owned by
`SPEC-SPEC-OWNERSHIP-GATE-1` requirements R11 and R13; any registry edit for this requirement must run as
partitioned shared maintenance, while this requirement owns the semantic surface and
acceptance proof.

Register full-profile `apatch_governed_work_preview_avatar` and
`apatch_governed_work_publish_avatar` tools with exact typed arguments and bounded
results. Keep them out of the compact profile. Native verification strips every MCP-server-only routing, runtime, stdio,
profile and lane variable before launching acceptance subprocesses. Temporary test
workspaces therefore behave like ordinary local processes without weakening the live
MCP boundary. A regression test supplies every server-only variable and proves each is
absent while unrelated environment remains. Assert existing generic Avatar sync,
Cowork preview/publish, ContributionEvent validation and signed document fixtures are
unchanged. The MCP schema has no token return field; explicit token input is marked
secret and never echoed.

SDK-self workspace resolution is checked against the actual imported SDK root
and must use the active interpreter before consumer candidates. An independent
consumer workspace still selects its own executable. These are test-context
corrections only: no tool resolver or Avatar behavior changes.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r7_mcp_surface_and_wire_compatibility tests/test_tool_paths.py::test_build_subprocess_env_drops_mcp_server_context tests/test_tool_paths.py::test_self_workspace_spec_verify_uses_active_python tests/test_tool_paths.py::test_sdk_self_workspace_selects_active_interpreter_before_consumer_candidates tests/test_tool_paths.py::test_independent_consumer_workspace_keeps_its_own_interpreter)

## R8 Cross-product acceptance with a red trap

owns: tests/test_avatar_selective_publication.py

Build a production-shaped fixture with a signed Platform source binding, signed Change,
two valid source-bound local ContributionEvents, a signed evidence bundle that references
only one, and matching timesheet. Exercise preview, rejected tamper, rejected recipient,
rejected missing token, confirmed publish, exact reconciliation and idempotent replay.
Assert one selected event and zero unrelated events cross the boundary, all original
local bytes remain identical, and no Cowork admission, time/price/acceptance mutation or
CapabilityEvidence operation occurs.

(verify: python3 -m pytest -q tests/test_avatar_selective_publication.py::test_r8_cross_product_round_trip)
