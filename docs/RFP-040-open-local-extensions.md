# RFP-040 -- Open local Extension Host and search workflow separation

> **Status:** Implemented in source, unreleased (August 7, 2026)
> **Scope:** APatch core extensions, open search compatibility, and user-owned adapters
> **Executable contract:** `docs/specs/SPEC-APATCH-EXTENSIONS-1.md`
> **External alignment:** TrustChain Platform `RFP-105#P0-8`, acceptance A16–A23

## Context

APatch already includes five search-quality MCP surfaces and their supporting
workflow modules. All of them were shipped in public `v0.8.21`. Their frozen
parsers, field handling, vocabulary, aliases, and localhost defaults remain part
of the open compatibility floor. New owner/workspace endpoint contracts, corpora,
field/status-policy additions, and runtime topology still need a separate home.

The `v0.8.21` baseline had no public extension interface. The current unreleased
source now provides a static local Extension Host while keeping MCP tools, CLI
commands, profiles, lifecycle classifications, and remote operations explicitly
registered. This boundary prevents reusable workflows and workspace customization
from accumulating together inside core.

## Decision

APatch SHALL provide an open local Extension Host. A user can author, pin,
inspect, validate, and run their own tools without an account, entitlement
service, managed catalog, or network dependency in the host.

Discovery is explicit and digest-pinned. Extensions execute out of process via
fixed host-built argv and JSON stdin/stdout. Version 1 grants only `read_only`
and `proposal` authority. A proposal may return validated mutation needles,
but core APatch remains the sole owner of session, apply, verify, attestation,
and rollback operations.

The frozen search surfaces remain available without entitlement through a
bundled open compatibility package. Reusable search-quality logic and all frozen
v0.8.21 compatibility defaults belong there. Only new owner/environment-specific
endpoint and payload contracts, SSO references, corpora, taxonomies, dictionaries,
field/status-policy or alias additions, operational commands, and remote topology
belong in an independently versioned workspace adapter such as `edcher-search`.

Local authoring and execution are open-core capabilities. A managed product may
add organization catalogs, shared approvals, signed catalog updates, and fleet
distribution, but those services are not prerequisites for local tools.

## Acceptance

| ID | Requirement | Level |
|---|---|---|
| EXT-A | Manifest and lock schemas carry namespaced ids, versions, API range, schemas, authority, rights metadata, and pinned digests. | MUST |
| EXT-B | Discovery reads only explicit lock entries; there is no recursive import, automatic download, or automatic update. | MUST |
| EXT-C | Execution is out of process, schema-checked, deadline/output bounded, path-contained, and fails closed on drift. | MUST |
| EXT-D | Static CLI/MCP surfaces list, inspect, validate, and run extensions without loading extension code into the APatch process. | MUST |
| EXT-E | Local authoring and execution require no account or entitlement; only managed organization distribution may be product-gated. | MUST |
| EXT-F | The five public `apatch_slug_*` surfaces remain open and compatible while reusable logic moves into a bundled package and owner data moves into a private adapter. | MUST |
| EXT-G | Each extension retains its own owner, license, exportability, hashes, and notices; WorkAsset metadata never silently embeds or relicenses extension code. | MUST |

## Integrity invariants

- Extension input cannot supply argv, executable path, upstream URL, or secret name
  unless that field is explicitly declared by the extension's own input schema.
- The host verifies lock, manifest, and executable artifact digests before run.
- Extension output never applies a mutation by itself.
- Missing or ambiguous ownership providers fail with a typed response rather than
  weakening SPEC ownership.
- Legacy search tool names, DTOs, CLI paths, profile membership, and remote operation
  strings remain covered during migration.
- Core and bundled open packages never import the private owner adapter.

## Migration order

1. Freeze the historical Python, CLI, MCP, profile, remote, and DTO contracts.
2. Add Extension Host manifest/lock validation and the bounded runner.
3. Add static workflow, CLI, and MCP surfaces.
4. Introduce provider registries and remove core-to-search reverse imports.
5. Move leaf search modules, then close/lint/intake/cockpit, and ratify last.
6. Leave explicit compatibility shims at the historical import paths.
7. Add the private workspace adapter only after the open parity gate is green.

## Non-goals

- in-process imports of arbitrary workspace Python;
- automatic marketplace installation or background updates;
- entitlement checks for local extensions;
- transferring user extension ownership to APatch or TrustChain;
- changing the existing governed mutation lifecycle;
- removing or paywalling behavior already shipped in `v0.8.21`.
