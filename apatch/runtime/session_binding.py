"""Opaque governed-session capabilities and immutable patch-log contracts."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from apatch.runtime.errors import SessionBindingError


@dataclass(frozen=True)
class SessionBinding:
    session_id: str
    lane_id: str
    revision: int
    session_token_hash: str


def _rollback_rejection(
    message: str,
    *,
    error_type: str,
    expected: Optional[str] = None,
    actual: Optional[str] = None,
) -> SessionBindingError:
    return SessionBindingError(
        message,
        error_type=error_type,
        expected=expected,
        actual=actual,
    )


def _safe_checkpoint_id(raw: Optional[str]) -> Optional[str]:
    checkpoint = str(raw or "").strip()
    if not checkpoint:
        return None
    if checkpoint in {".", ".."} or os.path.basename(checkpoint) != checkpoint:
        raise _rollback_rejection(
            "Rollback checkpoint must be a workspace-local checkpoint id.",
            error_type="ROLLBACK_CHECKPOINT_MISMATCH",
            actual=checkpoint,
        )
    return checkpoint


def resolve_governed_rollback(
    target_dir: str,
    binding: SessionBinding,
    *,
    requested_checkpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve one rollback checkpoint owned by the exact governed session.

    A governed rollback must never use the workspace-wide "latest backup"
    fallback. Older apply-session files are accepted as migration evidence, but
    only from the lane selected by the validated session capability.
    """
    from apatch.lane import lane_state_path_for
    from apatch.runtime.atomic_io import (
        atomic_write_json,
        exclusive_file_lock,
        read_json_file,
    )

    root = os.path.abspath(target_dir)
    session_id = binding.session_id
    state_path = lane_state_path_for(root, binding.lane_id, "session_state.json")
    state = read_json_file(state_path, {})
    actual_session = str(state.get("session_id") or "")
    if actual_session != session_id:
        raise _rollback_rejection(
            "Rollback capability no longer owns the selected lane.",
            error_type="SESSION_MISMATCH",
            expected=session_id,
            actual=actual_session or None,
        )

    apply_path = lane_state_path_for(root, binding.lane_id, "apply_session.json")
    apply_state = read_json_file(apply_path, {})
    apply_owner = str(
        apply_state.get("governed_session_id") or apply_state.get("session_id") or ""
    )
    apply_candidates: List[str] = []
    if apply_state and apply_owner == session_id:
        for value in list(apply_state.get("checkpoints") or []) + [
            apply_state.get("last_checkpoint"),
            apply_state.get("checkpoint"),
        ]:
            checkpoint = _safe_checkpoint_id(value)
            if checkpoint and checkpoint not in apply_candidates:
                apply_candidates.append(checkpoint)

    backups_dir = os.path.join(root, ".apatch", "backups")
    owned_metadata: List[tuple[str, str]] = []
    if os.path.isdir(backups_dir):
        for checkpoint in os.listdir(backups_dir):
            safe_checkpoint = _safe_checkpoint_id(checkpoint)
            if not safe_checkpoint:
                continue
            metadata_path = os.path.join(backups_dir, safe_checkpoint, "metadata.json")
            metadata = read_json_file(metadata_path, {})
            if str(metadata.get("governed_session_id") or "") == session_id:
                owned_metadata.append(
                    (str(metadata.get("timestamp") or ""), safe_checkpoint)
                )
    owned_metadata.sort()
    metadata_candidates = [checkpoint for _ts, checkpoint in owned_metadata]
    owned = set(apply_candidates) | set(metadata_candidates)

    requested = _safe_checkpoint_id(requested_checkpoint)
    if requested:
        if requested not in owned:
            raise _rollback_rejection(
                "Rollback checkpoint is not owned by the supplied governed session capability.",
                error_type="ROLLBACK_CHECKPOINT_MISMATCH",
                expected=session_id,
                actual=requested,
            )
        checkpoint = requested
    elif apply_candidates:
        checkpoint = apply_candidates[-1]
    elif metadata_candidates:
        checkpoint = metadata_candidates[-1]
    else:
        raise _rollback_rejection(
            "The supplied governed session has no rollback checkpoint.",
            error_type="ROLLBACK_CHECKPOINT_MISSING",
            expected=session_id,
        )

    metadata_path = os.path.join(backups_dir, checkpoint, "metadata.json")
    metadata = read_json_file(metadata_path, {})
    metadata_owner = str(metadata.get("governed_session_id") or "")
    if metadata_owner and metadata_owner != session_id:
        raise _rollback_rejection(
            "Rollback backup metadata belongs to another governed session.",
            error_type="ROLLBACK_CHECKPOINT_MISMATCH",
            expected=session_id,
            actual=metadata_owner,
        )
    if metadata and not metadata_owner:
        if checkpoint not in apply_candidates:
            raise _rollback_rejection(
                "Rollback backup has no governed ownership evidence.",
                error_type="ROLLBACK_CHECKPOINT_MISMATCH",
                expected=session_id,
                actual=checkpoint,
            )
        with exclusive_file_lock(metadata_path):
            current = read_json_file(metadata_path, {})
            current_owner = str(current.get("governed_session_id") or "")
            if current_owner and current_owner != session_id:
                raise _rollback_rejection(
                    "Rollback backup ownership changed during resolution.",
                    error_type="ROLLBACK_CHECKPOINT_MISMATCH",
                    expected=session_id,
                    actual=current_owner,
                )
            current["governed_session_id"] = session_id
            atomic_write_json(metadata_path, current)
            metadata = current

    paths: List[str] = []
    for row in metadata.get("backups") or []:
        if not isinstance(row, dict) or not row.get("original_file"):
            continue
        absolute = os.path.abspath(str(row["original_file"]))
        try:
            if os.path.commonpath([root, absolute]) != root:
                raise ValueError
        except ValueError as exc:
            raise _rollback_rejection(
                "Rollback backup references a path outside the workspace.",
                error_type="ROLLBACK_CHECKPOINT_MISMATCH",
                expected=root,
                actual=absolute,
            ) from exc
        paths.append(os.path.relpath(absolute, root))

    return {
        "checkpoint": checkpoint,
        "governed_session_id": session_id,
        "lane_id": binding.lane_id,
        "paths": list(dict.fromkeys(paths)),
        "metadata_path": metadata_path,
    }


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_session_binding(
    target_dir: str,
    *,
    expected_session_id: Optional[str] = None,
    session_token: Optional[str] = None,
    require_capability: bool = False,
    operation: str = "governed operation",
) -> Optional[SessionBinding]:
    """Resolve one active session and validate its unpersisted capability token."""
    from apatch.lane import lane_state_path_for
    from apatch.lane_context import (
        active_lane_ids,
        bind_governed_session_id,
        resolve_lane_for_session,
    )
    from apatch.runtime.atomic_io import read_json_file

    root = os.path.abspath(target_dir)
    lane_id: Optional[str] = None

    if expected_session_id:
        lane_id, error = resolve_lane_for_session(
            root, expected_session_id, active_only=False
        )
        if error:
            raise SessionBindingError(
                error,
                error_type="SESSION_MISMATCH",
                expected=expected_session_id,
            )
    else:
        active = active_lane_ids(root)
        if len(active) > 1:
            raise SessionBindingError(
                "Multiple governed sessions are active; provide governed_session_id and session_token.",
                error_type="SESSION_AMBIGUOUS",
            )
        if active:
            lane_id = active[0]

    registered_lane = lane_id is not None
    if lane_id is None:
        from apatch.lane import resolve_lane

        lane_id = resolve_lane(root).lane_id

    state_path = lane_state_path_for(root, lane_id, "session_state.json")
    raw = read_json_file(state_path, {})
    actual_id = str(raw.get("session_id") or "")
    active = bool(actual_id and raw.get("intent") and not raw.get("ended_at"))

    if not active:
        if expected_session_id:
            raise SessionBindingError(
                "The requested governed session is not active.",
                error_type="SESSION_MISMATCH",
                expected=expected_session_id,
                actual=actual_id or None,
            )
        if require_capability:
            return None
        return None

    if expected_session_id and actual_id != expected_session_id:
        raise SessionBindingError(
            "The active governed session was replaced.",
            error_type="SESSION_MISMATCH",
            expected=expected_session_id,
            actual=actual_id,
        )

    token_hash = str(raw.get("session_token_hash") or "")
    if token_hash and require_capability:
        if not expected_session_id or not session_token:
            raise SessionBindingError(
                "This governed session requires governed_session_id and session_token.",
                error_type="SESSION_BINDING_REQUIRED",
                expected=actual_id,
            )
    if session_token:
        if not token_hash or not hmac.compare_digest(
            hash_session_token(session_token), token_hash
        ):
            raise SessionBindingError(
                "The governed session token is invalid.",
                error_type="SESSION_TOKEN_MISMATCH",
                expected=actual_id,
                actual=actual_id,
            )

    # Registry-less states predate capabilities; keep their path resolution
    # local instead of binding an id that cannot resolve through the registry.
    if registered_lane:
        bind_governed_session_id(actual_id, root=root)
    return SessionBinding(
        session_id=actual_id,
        lane_id=lane_id,
        revision=int(raw.get("revision") or 0),
        session_token_hash=token_hash,
    )


def patch_log_contract_exists(target_dir: str, logs_path: str) -> bool:
    from apatch.artifact_governance import load_registry

    root = os.path.abspath(target_dir)
    abs_path = logs_path if os.path.isabs(logs_path) else os.path.join(root, logs_path)
    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    return rel in load_registry(root)


def validate_patch_log_contract(
    target_dir: str,
    logs_path: str,
    *,
    expected_session_id: str,
) -> dict:
    """Reject a foreign or modified patch log before any project mutation."""
    from apatch.artifact_governance import load_registry

    root = os.path.abspath(target_dir)
    abs_path = logs_path if os.path.isabs(logs_path) else os.path.join(root, logs_path)
    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    entry = load_registry(root).get(rel)
    if entry is None:
        raise SessionBindingError(
            "Patch log is not registered to a governed session.",
            error_type="PATCH_LOG_UNREGISTERED",
            expected=expected_session_id,
        )
    lineage = entry.lineage or {}
    owner = str(lineage.get("governed_session_id") or "")
    if owner != expected_session_id:
        raise SessionBindingError(
            "Patch log belongs to a different governed session.",
            error_type="PATCH_LOG_OWNER_MISMATCH",
            expected=expected_session_id,
            actual=owner or None,
        )
    expected_digest = str(lineage.get("content_sha256") or "")
    if not expected_digest:
        raise SessionBindingError(
            "Patch log has no immutable content digest.",
            error_type="PATCH_LOG_DIGEST_MISSING",
            expected=expected_session_id,
        )
    if not os.path.isfile(abs_path):
        raise SessionBindingError(
            "Patch log no longer exists.",
            error_type="PATCH_LOG_MISSING",
            expected=expected_session_id,
        )
    actual_digest = file_sha256(abs_path)
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise SessionBindingError(
            "Patch log content changed after generation.",
            error_type="PATCH_LOG_DIGEST_MISMATCH",
            expected=expected_digest,
            actual=actual_digest,
        )
    return {
        "ok": True,
        "path": rel,
        "owner_session_id": owner,
        "content_sha256": actual_digest,
    }


def assert_patch_log_write_owner(
    target_dir: str,
    logs_path: str,
    *,
    expected_session_id: Optional[str],
) -> None:
    """Prevent generation from overwriting another session's registered log."""
    if not expected_session_id:
        return
    from apatch.artifact_governance import load_registry

    root = os.path.abspath(target_dir)
    abs_path = logs_path if os.path.isabs(logs_path) else os.path.join(root, logs_path)
    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    entry = load_registry(root).get(rel)
    if entry is None:
        if os.path.exists(abs_path):
            raise SessionBindingError(
                "Existing patch log has no governed owner.",
                error_type="PATCH_LOG_UNOWNED",
                expected=expected_session_id,
            )
        return
    owner = str((entry.lineage or {}).get("governed_session_id") or "")
    if owner and owner != expected_session_id:
        raise SessionBindingError(
            "Refusing to overwrite another session's patch log.",
            error_type="PATCH_LOG_OWNER_MISMATCH",
            expected=expected_session_id,
            actual=owner,
        )
