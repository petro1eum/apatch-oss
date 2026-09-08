"""Fail-closed Git commit/push for exact attested TrustChain sessions."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from apatch.enforcement import file_sha256, verify_paths_notarized
from apatch.git_util import find_git_root, git_changed_files
from apatch.trustchain_helper import TrustChainHelper

_EXCLUDED_MUTATION_ACTIONS = frozenset(
    {"bootstrap", "checkpoint", "rollback_compensation"}
)


class CommitAttestedError(RuntimeError):
    """Typed fail-closed error for commit-attested."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        recommended_action: str,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.recommended_action = recommended_action
        self.details = dict(details or {})

    def to_result(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": False,
            "committed": False,
            "error_type": self.error_type,
            "message": self.message,
            "recoverable": True,
            "recommended_action": self.recommended_action,
        }
        result.update(self.details)
        return result


def _run_git(root: str, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommitAttestedError(
            "GIT_COMMAND_FAILED",
            "Git command could not be completed.",
            recommended_action="Check the local Git installation and repository state, then retry.",
        ) from exc


def _git_paths(root: str, args: Sequence[str]) -> List[str]:
    proc = _run_git(root, [*args, "-z", "--"])
    if proc.returncode != 0:
        raise CommitAttestedError(
            "GIT_COMMAND_FAILED",
            "Git could not enumerate repository paths.",
            recommended_action="Inspect the repository state and retry.",
        )
    return [
        part.decode("utf-8", errors="surrogateescape")
        for part in proc.stdout.split(b"\0")
        if part
    ]


def _ledger_entries(root: str) -> List[Dict[str, Any]]:
    return TrustChainHelper(root, auto_init=False).iter_ledger_entries()


def _timestamp_value(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _normalize_session_ids(
    governed_session_id: Optional[str],
    session_ids: Optional[Iterable[str]],
) -> List[str]:
    ordered: List[str] = []
    for raw in [governed_session_id, *(session_ids or [])]:
        value = str(raw or "").strip()
        if value and value not in ordered:
            ordered.append(value)
    if not ordered:
        raise CommitAttestedError(
            "ATTESTED_SESSION_REQUIRED",
            "At least one explicit governed session id is required.",
            recommended_action="Pass governed_session_id or session_ids from completed apatch sessions.",
        )
    return ordered


def _safe_rel_path(raw: Any) -> str:
    value = str(raw or "").replace("\\", "/").strip()
    path = PurePosixPath(value)
    if not value or value == "." or path.is_absolute() or ".." in path.parts:
        raise CommitAttestedError(
            "ATTESTED_PATH_INVALID",
            "A signed mutation contains an unsafe repository path.",
            recommended_action="Inspect the TrustChain entry; do not commit until its path is canonical.",
            details={"path": value},
        )
    return path.as_posix()


def _ledger_evidence_rank(row: Mapping[str, Any]) -> Tuple[int, int, int, int, int]:
    """Prefer the signed object envelope over an earlier summary row with the same op id."""

    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        return (0, 0, 0, int(bool(row.get("signature"))), 0)
    session_id = str(
        row.get("governed_session_id")
        or row.get("session_id")
        or payload.get("governed_session_id")
        or payload.get("session_id")
        or ""
    ).strip()
    files = payload.get("files")
    signed_file_hashes = 0
    if isinstance(files, Mapping):
        signed_file_hashes = sum(
            1
            for meta in files.values()
            if isinstance(meta, Mapping) and str(meta.get("sha256") or "").strip()
        )
    return (
        1,
        int(bool(session_id)),
        signed_file_hashes,
        int(bool(row.get("signature"))),
        len(payload),
    )


def _deduplicated_events(
    rows: Iterable[Mapping[str, Any]],
) -> List[Tuple[float, int, Mapping[str, Any]]]:
    best_by_identity: Dict[
        str, Tuple[Tuple[int, int, int, int, int], int, Mapping[str, Any]]
    ] = {}
    for index, row in enumerate(rows):
        identity = str(
            row.get("id")
            or row.get("signature")
            or row.get("object_path")
            or f"row:{index}"
        )
        rank = _ledger_evidence_rank(row)
        previous = best_by_identity.get(identity)
        if previous is None:
            best_by_identity[identity] = (rank, index, row)
            continue
        previous_rank, first_index, _previous_row = previous
        if rank > previous_rank:
            best_by_identity[identity] = (rank, first_index, row)

    events: List[Tuple[float, int, Mapping[str, Any]]] = []
    for _rank, first_index, row in best_by_identity.values():
        timestamp = _timestamp_value(row.get("timestamp"))
        events.append(
            (timestamp if timestamp is not None else float("inf"), first_index, row)
        )
    events.sort(key=lambda item: (item[0], item[1]))
    return events


def _attested_file_hashes(root: str, session_ids: Sequence[str]) -> Dict[str, str]:
    events = _deduplicated_events(_ledger_entries(root))
    all_mutations: List[Tuple[float, int, str, str]] = []

    for session_id in session_ids:
        mutations: List[Tuple[float, int, str, str]] = []
        attestations: List[float] = []
        session_event_without_time = False
        for timestamp, index, row in events:
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            row_session = str(
                row.get("governed_session_id")
                or row.get("session_id")
                or payload.get("governed_session_id")
                or payload.get("session_id")
                or ""
            ).strip()
            if row_session != session_id:
                continue
            if timestamp == float("inf"):
                session_event_without_time = True
                continue
            tool_id = str(row.get("tool_id") or "")
            signed = bool(row.get("signature"))
            if tool_id == "apatch_attest" and signed:
                attestations.append(timestamp)
                continue
            files = payload.get("files")
            action = str(payload.get("action") or "").strip()
            if (
                tool_id != "apatch"
                or not signed
                or not isinstance(files, Mapping)
                or action in _EXCLUDED_MUTATION_ACTIONS
            ):
                continue
            for raw_path, raw_meta in files.items():
                if not isinstance(raw_meta, Mapping):
                    continue
                expected_sha = str(raw_meta.get("sha256") or "").strip()
                if expected_sha:
                    mutations.append(
                        (timestamp, index, _safe_rel_path(raw_path), expected_sha)
                    )

        if session_event_without_time:
            raise CommitAttestedError(
                "LEDGER_ORDER_UNAVAILABLE",
                "A requested session has signed entries without an orderable timestamp.",
                recommended_action="Repair or re-attest that session before committing.",
                details={"session_id": session_id},
            )
        if not mutations:
            raise CommitAttestedError(
                "ATTESTED_MUTATION_MISSING",
                "The requested session has no signed file mutation.",
                recommended_action="Use a governed session that applied and notarized source changes.",
                details={"session_id": session_id},
            )
        if not attestations:
            raise CommitAttestedError(
                "ATTESTATION_MISSING",
                "The requested session has no signed attestation.",
                recommended_action="Verify and attest the exact session before committing.",
                details={"session_id": session_id},
            )
        if max(attestations) <= max(item[0] for item in mutations):
            raise CommitAttestedError(
                "ATTESTATION_PRECEDES_MUTATION",
                "The latest signed mutation is not covered by a later attestation.",
                recommended_action="Run verify and attest again for the exact session.",
                details={"session_id": session_id},
            )
        all_mutations.extend(mutations)

    selected: Dict[str, str] = {}
    for _timestamp, _index, rel_path, expected_sha in sorted(all_mutations):
        selected[rel_path] = expected_sha
    return selected


def _unstage_exact(root: str, paths: Sequence[str]) -> None:
    if paths:
        _run_git(root, ["reset", "-q", "HEAD", "--", *paths])


def commit_attested_workspace(
    target_dir: str = ".",
    *,
    governed_session_id: Optional[str] = None,
    session_ids: Optional[Sequence[str]] = None,
    message: str,
    push: bool = False,
    remote: str = "origin",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Commit only current files proven by explicit, subsequently attested sessions."""

    try:
        root = os.path.realpath(os.path.abspath(target_dir))
        git_root = find_git_root(root)
        if not git_root or os.path.realpath(git_root) != root:
            raise CommitAttestedError(
                "GIT_ROOT_REQUIRED",
                "commit-attested must target the repository root.",
                recommended_action="Pass the exact Git checkout root as target_dir.",
            )
        commit_message = str(message or "").strip()
        if not commit_message:
            raise CommitAttestedError(
                "GIT_COMMIT_MESSAGE_REQUIRED",
                "A non-empty commit message is required.",
                recommended_action="Pass a concise message describing the attested change.",
            )
        requested_sessions = _normalize_session_ids(governed_session_id, session_ids)
        signed_hashes = _attested_file_hashes(root, requested_sessions)

        pre_staged = _git_paths(root, ["diff", "--cached", "--name-only"])
        if pre_staged:
            raise CommitAttestedError(
                "GIT_INDEX_NOT_CLEAN",
                "The Git index already contains staged files.",
                recommended_action="Commit or unstage the existing index before commit-attested.",
                details={"staged_files": sorted(pre_staged)},
            )

        changed = set(git_changed_files(root, since="HEAD"))
        selected = sorted(path for path in signed_hashes if path in changed)
        if not selected:
            return {
                "ok": True,
                "committed": False,
                "pushed": False,
                "dry_run": dry_run,
                "status": "nothing_to_commit",
                "session_ids": requested_sessions,
                "files": [],
            }

        drift: List[Dict[str, str]] = []
        for rel_path in selected:
            abs_path = os.path.join(root, rel_path)
            if not os.path.isfile(abs_path):
                drift.append({"path": rel_path, "reason": "missing"})
                continue
            actual_sha = file_sha256(abs_path)
            expected_sha = signed_hashes[rel_path]
            if actual_sha != expected_sha:
                drift.append({
                    "path": rel_path,
                    "reason": "sha256_mismatch",
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                })
        if drift:
            raise CommitAttestedError(
                "ATTESTED_FILE_DRIFT",
                "One or more requested files no longer match their signed mutation.",
                recommended_action="Do not stage them; finish and attest the newer mutation session first.",
                details={"violations": drift},
            )

        proof = verify_paths_notarized(root, selected)
        if not proof.get("ok"):
            raise CommitAttestedError(
                "NOTARIZATION_FAILED",
                "Selected files do not match the notarized workspace index.",
                recommended_action="Rebuild/verify notarization and attest the exact current contents.",
                details={"violations": proof.get("violations") or []},
            )

        result: Dict[str, Any] = {
            "ok": True,
            "committed": False,
            "pushed": False,
            "dry_run": dry_run,
            "session_ids": requested_sessions,
            "files": selected,
        }
        if dry_run:
            result["status"] = "validated"
            return result

        staged = False
        try:
            add = _run_git(root, ["add", "--", *selected])
            if add.returncode != 0:
                raise CommitAttestedError(
                    "GIT_STAGE_FAILED",
                    "Git could not stage the exact attested file set.",
                    recommended_action="Inspect repository permissions and hooks, then retry.",
                )
            staged = True
            actual_staged = sorted(
                _git_paths(root, ["diff", "--cached", "--name-only"])
            )
            if actual_staged != selected:
                raise CommitAttestedError(
                    "GIT_STAGE_SCOPE_MISMATCH",
                    "The staged set differs from the exact attested file set.",
                    recommended_action="Inspect Git attributes/hooks; commit-attested has unstaged its paths.",
                    details={"expected_files": selected, "staged_files": actual_staged},
                )
            staged_proof = verify_paths_notarized(root, selected)
            if not staged_proof.get("ok"):
                raise CommitAttestedError(
                    "NOTARIZATION_FAILED",
                    "Staged files failed the final notarization check.",
                    recommended_action="Re-attest the current contents before committing.",
                    details={"violations": staged_proof.get("violations") or []},
                )
            commit = _run_git(root, ["commit", "-m", commit_message, "--", *selected])
            if commit.returncode != 0:
                raise CommitAttestedError(
                    "GIT_COMMIT_FAILED",
                    "Git rejected the exact attested commit.",
                    recommended_action="Inspect the repository commit hook output locally and retry.",
                    details={"exit_code": commit.returncode},
                )
            staged = False
        except CommitAttestedError:
            if staged:
                _unstage_exact(root, selected)
            raise

        commit_sha_proc = _run_git(root, ["rev-parse", "HEAD"])
        commit_sha = commit_sha_proc.stdout.decode("ascii", errors="replace").strip()
        result.update({"committed": True, "commit": commit_sha, "status": "committed"})
        if not push:
            return result

        branch_proc = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch_proc.stdout.decode("utf-8", errors="replace").strip()
        if branch_proc.returncode != 0 or not branch or branch == "HEAD":
            result.update({
                "ok": False,
                "error_type": "GIT_PUSH_FAILED",
                "message": "The commit was created, but the checkout has no pushable branch.",
                "recoverable": True,
                "recommended_action": "Attach the commit to a branch and push it explicitly.",
            })
            return result
        upstream = _run_git(
            root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        )
        if upstream.returncode == 0:
            pushed = _run_git(root, ["push"])
        else:
            remote_name = str(remote or "").strip()
            if not remote_name:
                result.update({
                    "ok": False,
                    "error_type": "GIT_PUSH_FAILED",
                    "message": "The commit was created, but no remote was supplied for the first push.",
                    "recoverable": True,
                    "recommended_action": "Pass remote='origin' or another configured remote.",
                })
                return result
            pushed = _run_git(root, ["push", "-u", remote_name, branch])
        if pushed.returncode != 0:
            result.update({
                "ok": False,
                "error_type": "GIT_PUSH_FAILED",
                "message": "The exact attested commit was created, but Git push failed.",
                "recoverable": True,
                "recommended_action": "Resolve remote authentication/connectivity and push the existing commit.",
                "branch": branch,
                "exit_code": pushed.returncode,
            })
            return result
        result.update({"pushed": True, "status": "pushed", "branch": branch})
        return result
    except CommitAttestedError as exc:
        return exc.to_result()
