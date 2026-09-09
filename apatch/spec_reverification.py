"""File-bound re-verification within existing signed APatch attestations.

Internal snapshots bind a completed verification to bytes captured BEFORE it.
Historical coverage is still derived exclusively from signed ledger records.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any

SCHEMA = "apatch.file-reverification.v1"


class ReverificationError(ValueError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_files(files: Any) -> bool:
    return (
        isinstance(files, dict) and 0 < len(files) <= 10000
        and all(
            isinstance(path, str) and path
            and not PurePosixPath(path).is_absolute()
            and "\\" not in path and ".." not in PurePosixPath(path).parts
            and str(PurePosixPath(path)) == path
            and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)
            for path, sha in files.items()
        )
    )


def _identity(stat) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _measure(root: str, files: dict, identities: dict | None = None) -> dict:
    if not _valid_files(files):
        raise ReverificationError("historical file scope or hashes are incomplete")
    base = Path(root).resolve()
    result = {}
    for rel in sorted(files):
        path = base / rel
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(base) or not resolved.is_file():
                raise ReverificationError("file scope escapes workspace or is not a file")
            if any(parent.is_symlink() for parent in (path, *path.parents) if parent != base):
                raise ReverificationError("symlink in file scope")
            import os
            before = _identity(path.stat())
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                if _identity(os.fstat(stream.fileno())) != before:
                    raise ReverificationError("file changed while measuring")
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
                if _identity(os.fstat(stream.fileno())) != before:
                    raise ReverificationError("file changed while measuring")
            if _identity(path.stat()) != before or path.is_symlink():
                raise ReverificationError("file changed while measuring")
            result[rel] = digest.hexdigest()
            if identities is not None:
                identities[rel] = before
        except OSError as exc:
            raise ReverificationError("file scope is unavailable") from exc
    return result


def valid_signed_reverification(data, *, artifact, artifact_hash, reference, files, op_ids, mutation_history):
    """Validate a signed row against its preceding genuine scope, never disk."""
    return (
        isinstance(data, dict)
        and set(data) == {
            "schema", "artifact", "content_hash", "verify_sha256", "verified",
            "reference_attestation_op_id", "mutation_op_ids", "mutation_history_op_ids", "files",
        }
        and data["schema"] == SCHEMA and data["artifact"] == artifact
        and data["content_hash"] == artifact_hash
        and isinstance(data["verify_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", data["verify_sha256"]) is not None
        and data["verified"] is True
        and data["reference_attestation_op_id"] == reference
        and data["mutation_op_ids"] == op_ids and bool(op_ids)
        and data["mutation_history_op_ids"] == mutation_history and bool(mutation_history)
        and _valid_files(files) and _valid_files(data["files"])
        and set(data["files"]) == set(files)
    )


@dataclass(frozen=True)
class FileReverification:
    root: str
    spec: str
    captured: dict
    results: dict | None = None

    def verified(self, results: dict) -> "FileReverification":
        return FileReverification(self.root, self.spec, deepcopy(self.captured), dict(results))

    def payload(self, target_dir: str, artifacts: list) -> dict:
        from apatch.spec_coverage import spec_status_with_coverage

        if Path(target_dir).resolve() != Path(self.root).resolve() or self.results is None:
            raise ReverificationError("verification is not bound to this workspace")
        status = spec_status_with_coverage(self.root, spec=self.spec)
        if not status.get("ok"):
            raise ReverificationError("current requirement evidence unavailable")
        rows = {row["id"]: row for row in status.get("requirements", [])}
        from apatch.artifact import coerce_artifacts
        anchors = {
            "spec:" + item["id"]: item.get("content_hash")
            for item in coerce_artifacts(artifacts)
            if isinstance(item, dict) and item.get("kind") == "spec" and item.get("id")
        }
        proofs = {}
        for rid, capture in self.captured.items():
            key = "spec:" + self.spec + "#" + rid
            if key not in anchors:
                continue
            row = rows.get(rid, {})
            if self.results.get(rid) is not True:
                raise ReverificationError("verification did not complete green")
            identities = {}
            measured = _measure(self.root, capture["historical"], identities)
            if (
                anchors[key] != capture["content_hash"]
                or row.get("content_hash") != capture["content_hash"]
                or row.get("verify") != capture["verify"]
                or row.get("reference_attestation_op_id") != capture["reference"]
                or row.get("op_ids") != capture["op_ids"]
                or row.get("mutation_history_op_ids") != capture["mutation_history"]
                or row.get("file_hashes") != capture["historical"]
                or not row.get("reference_complete")
                or measured != capture["files"]
                or identities != capture["identities"]
            ):
                raise ReverificationError("requirement, provenance or files changed during verification")
            proofs[key] = {
                "schema": SCHEMA, "artifact": key,
                "content_hash": capture["content_hash"],
                "verify_sha256": _digest(capture["verify"]), "verified": True,
                "reference_attestation_op_id": capture["reference"],
                "mutation_op_ids": capture["op_ids"], "files": capture["files"],
                "mutation_history_op_ids": capture["mutation_history"],
            }
        if not proofs:
            raise ReverificationError("verification does not cover session artifacts")
        return proofs


def completed_verification(root, result, *, verify, session_id, timeout_sec=3600):
    """Wait for the actual result of this session's exact asynchronous verifier."""
    job_id = result.get("verify_job_id") or result.get("job_id")
    if not job_id:
        return result
    from apatch.verify_jobs import _load_job, job_path, poll_verify_job
    import time
    if not isinstance(job_id, str) or not re.fullmatch(r"vjob_[0-9]+_[0-9a-f]{8}", job_id):
        raise ReverificationError("invalid verification job")
    def bound_job():
        job = _load_job(job_path(root, job_id)) or {}
        if (
            not session_id or job.get("session_id") != session_id
            or job.get("verify_command") != verify
            or job.get("baseline", "off") != "off" or job.get("allowed_failures")
        ):
            raise ReverificationError("verification job is not bound to the exact session and command")
        return job
    bound_job()
    deadline = time.monotonic() + timeout_sec
    while True:
        result = poll_verify_job(root, job_id)
        state = result.get("verify_job_state")
        if state in {"passed", "failed"}:
            job = bound_job()
            if result.get("ok") and (state != "passed" or job.get("returncode") != 0):
                raise ReverificationError("verification job lacks a successful exit")
            return {k: v for k, v in result.items() if k not in {"verify_job_id", "job_id"}}
        if state != "running" or not result.get("ok"):
            raise ReverificationError("verification job unavailable")
        if time.monotonic() >= deadline:
            error = ReverificationError("verification job did not finish before timeout")
            error.verify_job_id = job_id
            raise error
        time.sleep(0.25)


def capture_reverification(
    root: str, spec: str, rows: list, *, allow_primary_declarations: bool = False,
) -> FileReverification | None:
    """Capture before running a verifier. Missing history never means fileless."""
    captured = {}
    for row in rows:
        if not row.get("has_mutations") and not row.get("files"):
            continue
        if allow_primary_declarations and row.get("state") == "in_progress" and not row.get("has_attestation"):
            # Preserve legacy primary declaration, never manufacture file freshness.
            # The file-backed release gate still requires complete signed evidence.
            continue
        if (
            not row.get("reference_complete") or not row.get("op_ids")
            or not row.get("mutation_history_op_ids")
            or row.get("mutation_history_op_ids") != row.get("reference_mutation_history_op_ids")
            or not row.get("reference_attestation_op_id")
            or not isinstance(row.get("verify"), str) or not row["verify"]
            or not row.get("content_hash")
        ):
            raise ReverificationError("complete original requirement evidence is required")
        historical = deepcopy(row.get("file_hashes"))
        identities = {}
        measured = _measure(root, historical, identities)
        captured[row["id"]] = {
            "content_hash": row["content_hash"], "verify": row["verify"],
            "reference": row["reference_attestation_op_id"],
            "op_ids": deepcopy(row["op_ids"]), "historical": historical,
            "mutation_history": deepcopy(row["mutation_history_op_ids"]),
            "files": measured, "identities": identities,
        }
    return FileReverification(str(Path(root).resolve()), spec, captured) if captured else None
