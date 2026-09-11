# APatch Studio, APatch OSS and TrustChain Cowork

This repository supplies the **APatch OSS runtime**, not the Studio UI or the hosted Pro service.
APatch 0.8.43 is the MIT-licensed OSS release represented by this source tree.

## User paths

- A developer uses **APatch Studio OSS** or an MCP-capable editor to prepare and execute
  governed work locally. The full professional runtime remains available without an account.
- A connected developer may voluntarily accept a project proposal and share permitted
  evidence with the selected project. A received proposal is not authorization to execute:
  local governed intake opens the actual work.
- A project lead uses **APatch Studio Pro**, assembled into **TrustChain Cowork**, to agree
  commitments, offer work, observe execution, review delivery and make acceptance decisions.
  Browser work does not require a local APatch installation or repository.
- Teams, solo founders assembling a team and independent developers responding to published
  specifications/RFPs use the same project and authority model; this repository does not
  introduce a separate task marketplace or a second project registry.

## Shared boundaries

Local source, context, work history, WorkAssets and factual time evidence belong to the user.
Connections and exports are explicit and purpose-bound. External services make their own
membership, authority, acceptance and economic decisions.

Studio integrates the existing APatch governed runtime; it does not replace its session state,
verification rules or evidence with UI-only completion flags. Security and verification quality
are not a Pro-only feature.

## Local bridge requirements

A local Studio bridge binds to loopback, validates Host and same-origin requests, uses
process-scoped authorization for stateful operations, and exposes fixed-purpose actions.
Credentials do not belong in URLs, logs, persistent browser state or evidence projections.
Workspace selection, mutation and recovery must be explicit and attributable.
A browser tab is not an authority; APatch capabilities and scoped leases remain authoritative.

## Delivery qualification

The Studio UI, Cowork service integration and online Pro activation have separate builds,
configuration and acceptance gates. A green runtime suite or a public Python package does not
establish that those deliveries are complete. No hosted Pro implementation or private
activation client is bundled here.

See the [delivery matrix](product-matrix.md), [runtime invariants](governed-runtime-invariants.md)
and [local-first integration guide](governed-work-trustchain.md).
