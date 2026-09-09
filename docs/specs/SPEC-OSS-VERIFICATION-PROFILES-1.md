# SPEC-OSS-VERIFICATION-PROFILES-1 — Explicit OSS verification profiles

> **apatch artifact:** `spec:SPEC-OSS-VERIFICATION-PROFILES-1`  
> **Anchors:** RFP-046  
> **ownership mode:** strict

## R0 RFP traceability gate (meta)

owns: docs/RFP-046-oss-verification-profiles.md, docs/specs/SPEC-OSS-VERIFICATION-PROFILES-1.md, tests/test_oss_verification_profiles.py

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| QP-A | R1 | covered |
| QP-B | R2 | covered |
| QP-C | R3 | covered |
| QP-D | R4 | covered |
| QP-E | R5 | covered |

(verify: python3 -m pytest tests/test_oss_verification_profiles.py::test_traceability -q)

## R1 Exact, source-bound dependency inventory

owns: scripts/qualify_oss.py, docs/oss-verification-profiles.json, docs/oss-verification.md, tests/test_oss_verification_profiles.py

An explicit dependency inventory names exact test and requirement identities, reasons and source hashes; unknown or changed declarations fail closed. Whole mixed modules cannot be excluded.

(verify: python3 -m pytest tests/test_oss_verification_profiles.py::test_inventory_is_exact_and_hash_bound -q)

## R2 Complete, isolated verification runs

owns: scripts/qualify_oss.py, docs/oss-verification-profiles.json, docs/oss-verification.md, tests/test_oss_verification_profiles.py

Both profiles preserve the complete suite and exhaustive CI-safe standing gate, use isolated workspaces, retain raw outputs and reject filtered, stale or incomplete runs. Timeouts terminate the process group.

(verify: python3 -m pytest tests/test_oss_verification_profiles.py::test_full_run_is_unfiltered_and_isolated -q)

## R3 Fail-closed standalone qualification

owns: scripts/qualify_oss.py, docs/oss-verification-profiles.json, docs/oss-verification.md, tests/test_oss_verification_profiles.py

Standalone accepts only reviewed failures/skips caused by a genuinely absent optional dependency; new failures, changed tests, unknown skips, malformed reports and unexpected conformance failures block it. Local tests in mixed modules still run.

(verify: python3 -m pytest tests/test_oss_verification_profiles.py::test_standalone_fails_closed -q)

## R4 Canonical Avatar prerequisite and verification

owns: scripts/qualify_oss.py, docs/oss-verification-profiles.json, docs/oss-verification.md, tests/test_oss_verification_profiles.py

Avatar requires the canonical installed peer and declared checkout; missing/substituted inputs block before execution. Integration failures/skips cannot be waived as absent-peer limitations.

(verify: python3 -m pytest tests/test_oss_verification_profiles.py::test_avatar_requires_canonical_peer -q)

## R5 Honest reporting and public CI

owns: scripts/qualify_oss.py, docs/oss-verification-profiles.json, docs/oss-verification.md, tests/test_oss_verification_profiles.py

Reports and English documentation distinguish scoped qualification from full contract and external acceptance. Public CI invokes the named standalone profile and retains raw evidence; no frozen assertion or enrollment is relaxed.

(verify: python3 -m pytest tests/test_oss_verification_profiles.py::test_report_never_claims_global_acceptance -q)
