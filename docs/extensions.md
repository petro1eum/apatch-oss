# Local extensions

APatch Extension Host v1 lets each user keep reusable or private tools outside
core while preserving the governed mutation lifecycle.

## Boundary

- The host reads only `.apatch/extensions.lock.json`; it does not scan the
  workspace, import Python entry points, download packages, or update versions.
- Each manifest and executable artifact is SHA-256 pinned.
- Extension code runs in a child process with JSON stdin/stdout, fixed argv,
  deadline, and output bounds.
- Version 1 authority is `read_only` or `proposal`. Proposed needles are data;
  applying them requires a separate normal APatch session.
- Local list/inspect/validate/run has no account or entitlement dependency.
- A child process is a protocol boundary, not full operating-system isolation.
  Install only code whose owner and license you accept.

## Commands

```bash
apatch extension list --target-dir . --json
apatch extension inspect dev.example.search --target-dir . --json
apatch extension validate dev.example.search --target-dir . --json
apatch extension run dev.example.search/replay \
  --arguments-json '{"query":"pump"}' --target-dir . --json
```

The MCP equivalents are `apatch_extension_list`,
`apatch_extension_inspect`, `apatch_extension_validate`, and
`apatch_extension_run`.

## Create a personal or workspace tool

```bash
apatch extension init dev.example.my-tool \
  --tool-id run --owner "Your name" --target-dir . --json
apatch extension pin extensions/dev.example.my-tool/apatch-extension.json \
  --source personal --target-dir . --json
apatch extension validate dev.example.my-tool --target-dir . --json
apatch extension run dev.example.my-tool/run \
  --arguments-json '{"query":"example"}' --target-dir . --json
```

The generated package starts private, non-exportable, and with zero grants.
Scaffolding and pinning are separate explicit actions. If its manifest changes,
pinning refuses the drift until the author repeats `pin --replace`. These local
commands do not need an account, organization, managed catalog, or network call.
Use `--source workspace` for a project/team-owned tool; `personal` is the
right default for an owner-specific adapter such as `edcher-search`.

```text
.apatch/extensions.lock.json
extensions/dev.example.my-tool/
  apatch-extension.json
  runner.py
```

The lock pins id, version, source, manifest/package digests, license, enabled
state, and effective grants. Manifest capability requests never grant themselves.
The generated runner is only a minimal JSON protocol example; authors replace its
logic and then explicitly update artifact hashes and repin the manifest.

## Rights and WorkAsset references

The manifest remains the authority for owner, license, exportability, notices,
and package hashes. A tool may carry a short `work_asset:...` reference, but the
WorkAsset export never embeds or relicenses extension code or its manifest.
Changing any rights field changes the manifest digest and requires an explicit
repin.

The reusable search-quality implementation now lives in the bundled open
`apatch_search_workflows` package: feedback vocabulary, repair-map routing,
close, feedback-lint, intake, cockpit, and ratify. Historical `apatch.*`
imports are identity-preserving aliases, so all five public `apatch_slug_*`
surfaces, DTOs, remote routing, and existing monkeypatch seams remain stable.
Exact SPEC ownership resolution is neutral core code used by both APatch and the
open package; core never imports the private adapter.

The frozen v0.8.21 parsers, JDE/`decision_graph` mappings, status vocabulary,
aliases, and localhost defaults stay in the open compatibility package. New or
owner/workspace-specific endpoint and payload contracts, SSO references, corpora,
taxonomies, dictionaries, field/status-policy or alias additions, and operational
topology live in a separately pinned `personal` or `workspace` extension such as
`edcher-search`.
