# SPEC-VERSION-1 — Semver 0.3.0 release cut

> **apatch artifact:** `spec:SPEC-VERSION-1`
> **Anchors:** [release-versioning.md](../release-versioning.md), [CHANGELOG](../../CHANGELOG.md)

## R1 Release policy document

`docs/release-versioning.md` defines semver rules, cadence, checklist, and anti-patterns.

(verify: python3 -m pytest tests/test_version.py::test_release_policy_doc_exists -q)

## R2 Single-source __version__ loader

`apatch/__init__.py` loads version from `importlib.metadata` or `pyproject.toml`; no hardcoded duplicate.

(verify: python3 -m pytest tests/test_version.py::test_version_matches_pyproject -q)

## R3 The declared version has release notes

The version in `pyproject.toml` is the one CHANGELOG documents and the one the docs
package line states.

This requirement used to demand the package be at `0.3.0` -- a release action, not an
invariant. Its check was not portable, so the contract gate skipped it and nobody saw
that it had been false since the next release.

(verify: python3 -m pytest tests/test_version.py::test_declared_version_has_release_notes -q)

## R4 Version tests green

Full `tests/test_version.py` passes.

(verify: python3 -m pytest tests/test_version.py -q)
