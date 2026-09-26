"""Governed intake for exact pre-existing, untracked source files."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from typing import Any, Dict, List, Mapping

from apatch.enforcement import file_sha256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_MANIFEST = re.compile(r"^manifests/SPEC-[A-Za-z0-9_.-]+\.run\.json$")


def _git(root: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        check=False,
        timeout=30,
    )


def _fail(code: str, message: str, **details: Any) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_type": code,
        "error": message,
        "recoverable": True,
        "recommended_action": "correct_exact_intake_plan",
        **details,
    }


def validate_existing_sources(
    root: str, needles: List[Mapping[str, Any]], artifact_files: Mapping[str, List[str]]
) -> Dict[str, Any]:
    """Reject altered, ignored, tracked, symlinked, or wrongly owned files."""
    root = os.path.realpath(os.path.abspath(root))
    if not needles or len(needles) > 100:
        return _fail("SOURCE_INTAKE_SCOPE_INVALID", "Intake needs 1–100 exact files.")
    staged = _git(root, "diff", "--cached", "--name-only", "-z")
    if staged.returncode != 0:
        return _fail("SOURCE_INTAKE_GIT_ERROR", "Could not inspect the Git index.")
    if staged.stdout:
        return _fail("SOURCE_INTAKE_INDEX_DIRTY", "Git index must be empty.")

    assignments: Dict[str, str] = {}
    for owner, paths in artifact_files.items():
        if not isinstance(paths, list) or not paths:
            return _fail("SOURCE_INTAKE_PARTITION_INVALID", "Every owner needs exact paths.")
        for rel in paths:
            if rel in assignments:
                return _fail("SOURCE_INTAKE_PARTITION_INVALID", "One file has multiple owners.")
            assignments[rel] = owner

    expected: Dict[str, str] = {}
    for needle in needles:
        rel = str(needle.get("target_file") or "")
        sha = str(needle.get("sha256") or "")
        if (
            str(needle.get("action") or "") != "intake"
            or set(needle) != {"action", "target_file", "sha256"}
            or not rel
            or rel.startswith("/")
            or "\\" in rel
            or any(part in ("", ".", "..") for part in rel.split("/"))
            or rel in expected
            or not _SHA256.fullmatch(sha)
        ):
            return _fail("SOURCE_INTAKE_PLAN_INVALID", "Each file needs a canonical path and SHA-256.")
        if rel not in assignments:
            return _fail("SOURCE_INTAKE_PARTITION_INVALID", "File has no assigned requirement.", path=rel)
        if _RUN_MANIFEST.fullmatch(rel):
            owner = assignments[rel]
            owner_match = re.fullmatch(r"spec:(SPEC-[A-Za-z0-9_-]+)#[A-Za-z0-9_-]+", owner)
            spec_id = owner_match.group(1) if owner_match else ""
            pattern = rf"manifests/{re.escape(spec_id)}(?:\.[A-Za-z0-9_-]+)*\.run\.json"
            if not spec_id or re.fullmatch(pattern, rel) is None:
                return _fail("SOURCE_INTAKE_OWNER_MISMATCH", "Run manifest has the wrong SPEC owner.", path=rel)
        path = os.path.join(root, rel)
        if os.path.realpath(path) != path:
            return _fail("SOURCE_INTAKE_UNSAFE_PATH", "Symlink path is not admissible.", path=rel)
        try:
            mode = os.stat(path, follow_symlinks=False).st_mode
        except OSError:
            return _fail("SOURCE_INTAKE_FILE_MISSING", "Source file is missing.", path=rel)
        if not stat.S_ISREG(mode):
            return _fail("SOURCE_INTAKE_UNSAFE_PATH", "Source is not a regular file.", path=rel)
        actual = file_sha256(path)
        if actual != sha:
            return _fail(
                "SOURCE_INTAKE_HASH_DRIFT", "File differs from declared SHA-256.",
                path=rel, expected_sha256=sha, actual_sha256=actual,
            )
        expected[rel] = sha
    if set(assignments) != set(expected):
        return _fail("SOURCE_INTAKE_PARTITION_INVALID", "Partition includes non-intake files.")

    query = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *sorted(expected))
    if query.returncode != 0:
        return _fail("SOURCE_INTAKE_GIT_ERROR", "Could not inspect untracked paths.")
    untracked = {
        part.decode("utf-8", errors="surrogateescape")
        for part in query.stdout.split(b"\0") if part
    }
    if untracked != set(expected):
        return _fail(
            "SOURCE_INTAKE_NOT_UNTRACKED",
            "Every file must be untracked and not ignored.",
            missing=sorted(set(expected) - untracked),
        )
    return {
        "ok": True,
        "files": {
            rel: {
                "sha256": sha,
                "provenance": "preexisting_untracked",
                "content_mutated": False,
            }
            for rel, sha in sorted(expected.items())
        },
        "target_count": len(expected),
    }


def run_partitioned_source_intake(
    root: str,
    *,
    prepared: Mapping[str, Any],
    specs: List[str],
    schedule: Any = None,
    maintenance_verify: str | None = None,
    verify_jobs: int = 8,
    verify_timeout: float = 120.0,
    single_spec: bool = False,
) -> Dict[str, Any]:
    """Sign and attest exact existing files; never rewrite their bytes or modes."""
    from apatch.runtime.runtime import MutationRuntime
    from apatch.runtime.state_machine import OP_APPLY_SESSION, assert_operation
    from apatch.shared_maintenance import _coverage_postcheck, _verify_selected
    from apatch.spec_ownership import authorize_spec_owned_needles
    from apatch.trustchain_helper import TrustChainHelper

    needles = list(prepared["needles"])
    partition = prepared["artifact_files"]
    preflight = validate_existing_sources(root, needles, partition)
    if not preflight.get("ok"):
        return preflight

    rt = MutationRuntime(root)
    opened = rt.open_session(
        f"existing source intake: {len(needles)} exact files across {len(specs)} SPECs",
        artifacts=prepared["artifacts"],
        artifact_files=partition,
    )
    if not opened.get("ok"):
        return opened

    try:
        owner_check = authorize_spec_owned_needles(
            root,
            [{"action": "replace", "target_file": row["target_file"]} for row in needles],
            governed_session_id=rt.session_id,
            created_by_tool=(
                "apatch_spec_run_multi:single_source_intake"
                if single_spec else "apatch_spec_run_multi:shared_maintenance"
            ),
        )
        if not owner_check.get("ok"):
            return owner_check
        rt._ensure_mutation("source_intake")
        assert_operation(root, OP_APPLY_SESSION)
        rt._capture_binding("source_intake")
        exact = validate_existing_sources(root, needles, partition)
        if not exact.get("ok"):
            return exact

        tc = TrustChainHelper(root, auto_init=True)
        if not tc.commit_action(
            "apatch",
            {
                "action": "source_intake",
                "files": exact["files"],
                "source_content_mutated": False,
            },
        ):
            return _fail("SOURCE_INTAKE_NOTARIZATION_FAILED", "No signed source-intake receipt.")
        admitted = rt._finish(
            "apatch_source_intake",
            {
                "ok": True,
                "intaken": len(needles),
                "source_content_mutated": False,
                "trustchain_committed": True,
                "receipt": tc.last_commit_evidence,
            },
        )
        if not admitted.get("ok"):
            return admitted

        verification = _verify_selected(
            root,
            list(prepared["verify_rows"]),
            maintenance_verify=maintenance_verify,
            jobs=verify_jobs,
            timeout=verify_timeout,
        )
        verified = rt._finish("apatch_verify_run", verification)
        if not verified.get("ok"):
            return {
                **verified, "error_type": "VERIFY_FAILED",
                "source_content_mutated": False, "intake_attested": False,
            }
        unchanged = validate_existing_sources(root, needles, partition)
        if not unchanged.get("ok"):
            return {**unchanged, "intake_attested": False}

        attested = rt.attest(
            message="Exact pre-existing source hashes admitted; owner requirements verified"
        )
        if not attested.get("ok"):
            return {**attested, "intake_attested": False}
        coverage = _coverage_postcheck(root, partition)
        if not coverage.get("ok"):
            return {
                **coverage, "error_type": "SOURCE_INTAKE_COVERAGE_FAILED",
                "source_content_mutated": False, "intake_attested": True,
            }
        return {
            "ok": True,
            "execution_mode": "shared_maintenance",
            "action": "source_intake",
            "source_content_mutated": False,
            "intaken": len(needles),
            "applied": 0,
            "session_id": rt.session_id,
            "completed_specs": list(specs),
            "selected_requirements": prepared["selected_requirements"],
            "target_count": len(needles),
            "files": sorted(exact["files"]),
            "verify": verified,
            "coverage": coverage,
            "schedule": schedule,
            "continue": False,
        }
    finally:
        rt.close_session()
