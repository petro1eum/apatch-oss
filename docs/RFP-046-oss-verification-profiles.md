# RFP-046 — Explicit OSS verification profiles

> **Status:** Implementation scope authorized by the owner conversation on 2026-09-08; not release approval.
> **Owner:** APatch core
> **Executable contract:** `docs/specs/SPEC-OSS-VERIFICATION-PROFILES-1.md`

## Purpose

Qualify the already implemented standalone OSS runtime independently of an optional
canonical Avatar peer, without hiding failed tests or asserting acceptance of the
connected service. Local work does not require a Platform account or Avatar
installation. A separately installed shared contract remains the only authority
for its schema; this work must not replace, vendor or publish that private peer.

The existing public-only run has 40 peer-dependent failures and six skips.
A test profile is a named evidence scope, not a weaker replacement for the existing
standing contract. Existing tests, assertions, frozen judge assets, enrollment and
native conformance semantics remain unchanged.

## Verification profiles

- `standalone`: requires an environment without the Avatar module; runs the complete
  unchanged test suite and exhaustive CI-safe conformance in separate fresh source
  snapshots. Only specifically reviewed absent-dependency observations may be
  classified outside this profile. The raw failures and global contract verdict
  remain visible; Avatar acceptance is explicitly unavailable.
- `avatar`: requires the installed canonical peer and a clean checkout at its
  declared commit. A missing, substituted or incompatible peer is a failed
  prerequisite, never success or a new skipped test. It requires the complete
  local suite plus canonical shared-contract checks. This is not live HC/Platform
  deployment acceptance.

The report distinguishes `profile_passed`, `raw_suite_passed`,
`raw_contract_holds` and `external_acceptance`. No profile run authorizes upload,
changes repository visibility, proves production inclusion or signs owner approval.

## Acceptance

| Id | Criterion | Level |
|----|-----------|-------|
| QP-A | An explicit dependency inventory names exact test and requirement identities, reasons and source hashes; unknown or changed declarations fail closed. Whole mixed modules cannot be excluded. | MUST |
| QP-B | Both profiles preserve the complete suite and exhaustive CI-safe standing gate, use isolated workspaces, retain raw outputs and reject filtered, stale or incomplete runs. Timeouts terminate the process group. | MUST |
| QP-C | Standalone accepts only reviewed failures/skips caused by a genuinely absent optional dependency; new failures, changed tests, unknown skips, malformed reports and unexpected conformance failures block it. Local tests in mixed modules still run. | MUST |
| QP-D | Avatar requires the canonical installed peer and declared checkout; missing/substituted inputs block before execution. Integration failures/skips cannot be waived as absent-peer limitations. | MUST |
| QP-E | Reports and English documentation distinguish scoped qualification from full contract and external acceptance. Public CI invokes the named standalone profile and retains raw evidence; no frozen assertion or enrollment is relaxed. | MUST |

## Non-goals

No change to Avatar schemas, runtime behavior, installed tooling, production
services, signing identities, dependency distribution or publication destination.
A green standalone profile does not change the red full-contract verdict.
