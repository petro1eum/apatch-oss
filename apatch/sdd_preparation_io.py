"""Generic exact preparation I/O; no historical migration or enrollment authority."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from apatch.sdd_integrity import SddContractError
from apatch.strict_existing_signer import ExistingSignerBinding, ExistingSignerRefused
from apatch.runtime.atomic_io import exclusive_file_lock

MAX_BYTES = 1048576
PENDING = ".apatch/sdd/draft-amendment-pending.json"


def _path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise SddContractError("an exact relative path is required")
    parts = Path(relative).parts
    if Path(relative).is_absolute() or ".." in parts or any(c in relative for c in "*?[]") or str(Path(relative)) != relative:
        raise SddContractError("scope must contain exact canonical relative paths")
    path = Path(root)
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise SddContractError("symbolic links are not authoring scope")
    return path


def _read(root, relative):
    try:
        value = json.loads(_path(root, relative).read_text())
    except (OSError, ValueError) as exc:
        raise SddContractError("authoring authority is unavailable") from exc
    if not isinstance(value, dict):
        raise SddContractError("authoring document must be an object")
    return value


def _hash(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _capture(root, relative, *, optional=False):
    path = _path(root, relative)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if optional:
            return None
        raise SddContractError("required preparation input is absent") from None
    try:
        before = os.fstat(fd)
        import stat
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
            raise SddContractError("preparation file role refused")
        data = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        named = os.lstat(path)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_uid, s.st_gid, s.st_nlink)
        if len(data) != before.st_size or identity(before) != identity(after) or identity(after) != identity(named):
            raise SddContractError("preparation file changed during capture")
        return data
    finally:
        os.close(fd)


@contextmanager
def freeze_lock(root):
    target = _path(root, ".apatch/sdd/successor-activation")
    target.parent.mkdir(parents=True, exist_ok=True)
    _path(root, ".apatch/sdd/successor-activation.lock")
    with exclusive_file_lock(str(target)):
        yield


def assert_can_freeze(root, *, own_review=None):
    from apatch.runtime.finalization import load_finalization
    for path in [Path(root) / ".apatch/session_state.json", *Path(root).glob(".apatch/lanes/*/session_state.json")]:
        if path.exists():
            state = _read(root, path.relative_to(root).as_posix())
            sid = state.get("session_id")
            if not sid:
                continue
            finalized = load_finalization(str(root), sid)
            if finalized is not None:
                if finalized.get("status") == "complete":
                    continue
                raise SddContractError("finish active governed sessions before owner repair")
            # The bounded native finalization journal may evict a completed
            # session. A present incomplete transaction never uses this fallback.
            from datetime import datetime, timezone
            try:
                started = datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
                ended = datetime.fromisoformat(state["ended_at"].replace("Z", "+00:00"))
                closed = (started.tzinfo is not None and ended.tzinfo is not None
                          and started <= ended <= datetime.now(timezone.utc))
            except (KeyError, TypeError, AttributeError, ValueError):
                closed = False
            if not closed:
                raise SddContractError("finish active governed sessions before owner repair")
    for name in [".apatch/sdd/judge-amendment-pending.json", PENDING]:
        path = _path(root, name)
        if path.exists() and (name != PENDING or _read(root, name).get("review_hash") != own_review):
            raise SddContractError("another owner transaction is incomplete")


def _identity(root):
    from apatch.trust_identity import load_local_identity
    identity = load_local_identity(str(root))
    if identity is None or identity.key_provider is None:
        raise ExistingSignerRefused("EXISTING_SIGNER_UNAVAILABLE")
    public = identity.key_provider.get_public_key()
    binding = ExistingSignerBinding(identity.agent_id, hashlib.sha256(public).hexdigest())
    binding.resolve(str(root))
    return binding, public


def _immutable(root, relative, document):
    path = _path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    _path(root, relative)
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".successor-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(document, stream, sort_keys=True, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _read(root, relative) != document:
                raise SddContractError("immutable successor history conflicts")
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
