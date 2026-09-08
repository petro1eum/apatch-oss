# SPEC-APATCH-EXTENSIONS-1 — Open local extensions and search workflow separation

> **apatch artifact:** `spec:SPEC-APATCH-EXTENSIONS-1`
> **Anchors:** RFP-040; TrustChain Platform RFP-105#P0-8 (A16–A23)
> **Frozen compatibility floor:** APatch v0.8.21 (`a595eef8f24cd6f5b0878f5c96b023b28cb6bf52`)

APatch provides an open local Extension Host. Users can author and run their own
tools without an account or edition entitlement. Core APatch remains the only
authority for governed session, mutation, verification, attestation, and rollback.

Dependencies: `SPEC-APATCH-TRANSACTIONAL-SESSIONS-1` owns lifecycle capabilities;
`SPEC-WORK-ASSET-EXPORT-1` owns evidence-only export; RFP-019 owns MCP profiles.
Final distribution license selection is handled separately and cannot narrow the
already-public v0.8.21 compatibility floor.

## R0 RFP traceability gate

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| EXT-A | R1 | covered |
| EXT-B | R2 | covered |
| EXT-C | R3 | covered |
| EXT-D | R4 | covered |
| EXT-E | R5 | covered |
| EXT-F | R6 | covered |
| EXT-G | R7 | covered |

The mapping MUST cover every mandatory RFP-040 acceptance row exactly once and
MUST remain aligned with external RFP-105 A16–A23.

(verify: `python3 -m apatch.cli rfp coverage --rfp RFP-040 --spec SPEC-APATCH-EXTENSIONS-1 --target-dir . --json`)

## R1 Manifest and deterministic lock contract

APatch MUST publish a strict `apatch.extension.v1` manifest and lock contract.
A manifest declares a reverse-DNS extension id, semantic version, supported APatch
API range, unique namespaced tools, JSON input/output schemas, `read_only` or
`proposal` authority, runtime bounds, requested capabilities, owner, license,
exportability, notices, and executable artifacts with SHA-256 digests.

The workspace lock MUST explicitly pin extension id/version, manifest path and
digest, enabled state, source, and effective user grants. A capability request is
not a grant. Unknown execution fields, duplicate/colliding tools, API-major
mismatch, path escape, symlink artifacts, invalid bounds, and digest drift fail
with typed results before code execution.

(verify: `python3 -m pytest tests/test_extensions.py::test_r1_manifest_and_lock_are_explicit_and_digest_pinned -q`)

## R2 Explicit discovery and no silent updates

Discovery MUST read only explicit entries in `.apatch/extensions.lock.json`.
It MUST NOT recursively scan the current directory, import from `PYTHONPATH`,
load packaging entry points, download packages, resolve a latest version, or
update a lock automatically.

Every list, inspect, validate, and run operation revalidates lock, manifest, and
artifact digests. Unlisted manifests are invisible. A version changes only
through a separate explicit user action that rewrites the governed lock.

(verify: `python3 -m pytest tests/test_extensions.py::test_r2_discovery_is_explicit_and_never_auto_updates -q`)

## R3 Bounded out-of-process runner and proposal authority

Extensions execute out of process with one JSON object on stdin and one JSON
object on stdout, fixed host-built argv (`shell=False`), a minimal declared
environment, deadline, bounded stdout/stderr, and deterministic termination on
timeout or overflow. Malformed/multiple JSON, schema drift, path escape, artifact
drift, and reserved session/capability/attestation fields fail closed.

Version 1 authority is exactly `read_only` or `proposal`. A proposal may return
schema-validated `proposed_needles`, but the host MUST NOT open a session, apply,
verify, attest, or rollback. The child receives no session capability. The
process boundary is an execution contract, not a claim of full operating-system
isolation.

(verify: `python3 -m pytest tests/test_extensions.py::test_r3_runner_is_bounded_and_proposal_never_applies -q`)

## R4 Stable workflow, CLI, and MCP host surfaces

Core MUST expose `list`, `inspect`, `validate`, and `run` workflows through:

- `apatch extension list|inspect|validate|run`;
- `apatch_extension_list`, `apatch_extension_inspect`,
  `apatch_extension_validate`, and `apatch_extension_run`.

These host surfaces are static; changing an explicit lock does not dynamically
load code into the APatch process. List/inspect/validate are lifecycle read-only.
Run may execute an extension but is not a core mutation-success tool. CLI/MCP
parity, profile membership, and documentation stay mechanically checked.

(verify: `python3 -m pytest tests/test_extensions.py::test_r4_cli_workflow_and_mcp_parity tests/test_mcp.py::test_mcp_tools_registered tests/test_docs_in_sync.py -q`)

## R5 Open local authoring and execution

A user MUST be able to scaffold, explicitly pin, validate, and run a local
extension without an account, organization, entitlement service, managed
catalog, or network call. New local extensions default to zero grants,
`exportable=false`, and a private LicenseRef. Pinning is separate from
scaffolding, and repinning any changed manifest requires another explicit action.
Only managed organization distribution may be product-gated.

(verify: `python3 -m pytest tests/test_extensions.py::test_r5_local_scaffold_pin_and_run_need_no_account_or_network -q`)

## R6 Open search compatibility package and private adapter boundary

All seven search workflow modules already published in APatch v0.8.21 MUST live
in the bundled open `apatch_search_workflows` package. Historical
`apatch.feedback_status`, `apatch.repair_map`, and `apatch.slug_*` imports,
the five CLI/MCP `apatch_slug_*` surfaces, DTOs, profile/lifecycle placement,
Codex approval placement, remote routing, and monkeypatch seams MUST remain
compatible through identity-preserving aliases.

Reusable vocabulary, replay, lint, intake, cockpit, ratification, repair-map
logic, and every compatibility default already published in v0.8.21 remain open.
This includes the frozen parsers, JDE/`decision_graph` field handling, status
vocabulary, aliases, and localhost defaults required to avoid behavior drift.
Only new or owner/workspace-specific endpoints, SSO references, payload and field
mappings, corpora, taxonomies, dictionaries, status-policy/alias additions, and
operational topology belong in an independently pinned `personal` or `workspace`
adapter such as `edcher-search`. Core and the bundled open package MUST NOT import
that adapter.

(verify: `python3 -m pytest tests/test_search_workflows_compatibility.py tests/test_feedback_status.py tests/test_repair_map.py tests/test_slug_close.py tests/test_slug_feedback_lint.py tests/test_slug_intake.py tests/test_slug_cockpit.py tests/test_slug_ratify.py tests/test_brand_fallback.py tests/test_remote_worker_protocol.py tests/test_remote_mcp_routing.py tests/test_spec_owned_mutation_gate.py -q`)

## R7 Extension rights and WorkAsset separation

Owner, license, exportability, notices, manifest digest, and package digest MUST
remain extension-owned and digest-pinned. A WorkAsset link is a bounded opaque
reference in the extension manifest only. WorkAsset export v1 MUST NOT embed an
extension manifest, executable artifacts, code, notices, owner, or license.
Changing rights metadata requires a new manifest digest and explicit repin.

(verify: `python3 -m pytest tests/test_extensions.py::test_r7_extension_rights_are_pinned_and_reference_only tests/test_work_asset_export_contract.py::test_extension_payload_is_not_a_work_asset_export -q`)
