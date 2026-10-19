# SPEC-OSS-STABILIZATION-EXPORT-1

> **apatch artifact:** `spec:SPEC-OSS-STABILIZATION-EXPORT-1`

This additive release transfers the approved stabilization fixes to public Core 0.8.51.
The prior v2/v3 source judges and their assertions remain historical evidence.

## R1 Exact reviewed export boundary

(verify: python -m pytest tests/test_public_source_boundary.py tests/test_public_stabilization_boundary.py -q)

Verify the official public baseline, exact reviewed additions and modifications,
private module exclusions, unchanged historical validators, file bytes and modes.

## R2 Installed profiles

(verify: python scripts/qualify_oss.py --profile standalone --output /tmp/apatch-oss-stabilization-standalone)

Require complete installed-wheel suite and live conformance, matching source inventory,
without collecting a private implementation or suppressing failed checks.
