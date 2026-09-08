"""Explicit offline authoring helpers for local APatch extensions."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable

from apatch.extensions.catalog import DEFAULT_WORKSPACE_LOCK, load_explicit_lock
from apatch.extensions.schema import (
    EXTENSION_PROTOCOL,
    ExtensionContractError,
    package_digest,
    validate_lock,
    validate_manifest,
    verify_lock_entry,
    verify_manifest_file,
)
from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock


def _fail(code: str, message: str, *, path: str = "") -> None:
    raise ExtensionContractError(code, message, path=path)


def _workspace_root(workspace: str | os.PathLike[str]) -> Path:
    try:
        root = Path(workspace).resolve(strict=True)
    except OSError as exc:
        _fail("EXTENSION_WORKSPACE_INVALID", "workspace cannot be resolved: " + str(exc))
    if not root.is_dir():
        _fail("EXTENSION_WORKSPACE_INVALID", "workspace must be a directory")
    return root


def _relative_workspace_file(root: Path, path_value: str | os.PathLike[str]) -> tuple[Path, str]:
    raw = Path(path_value)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        _fail("EXTENSION_MANIFEST_PATH_INVALID", "manifest must be a workspace file: " + str(exc))
    check = root
    for part in relative.parts:
        check = check / part
        if check.is_symlink():
            _fail("EXTENSION_MANIFEST_PATH_INVALID", "manifest path cannot contain symlinks", path=relative.as_posix())
    if not resolved.is_file():
        _fail("EXTENSION_MANIFEST_PATH_INVALID", "manifest must be a regular file", path=relative.as_posix())
    return resolved, relative.as_posix()


def _lock_target(root: Path, lock_path: str) -> Path:
    if not isinstance(lock_path, str) or not lock_path or "\\" in lock_path:
        _fail("EXTENSION_LOCK_PATH_INVALID", "lock path must be a POSIX workspace path")
    pure = PurePosixPath(lock_path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("EXTENSION_LOCK_PATH_INVALID", "lock path must stay inside workspace", path=lock_path)
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        if candidate.exists() and candidate.is_symlink():
            _fail("EXTENSION_LOCK_PATH_INVALID", "lock path cannot contain symlinks", path=lock_path)
    return candidate


def _runner_source() -> str:
    return (
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "json.dump({'protocol': 'apatch.extension.v1', "
        "'request_id': request['request_id'], 'ok': True, "
        "'result': request['arguments']}, sys.stdout)\n"
    )


def scaffold_local_extension(
    workspace: str | os.PathLike[str],
    extension_id: str,
    *,
    tool_id: str = "run",
    owner: str,
    license_id: str = "LicenseRef-Private",
    authority: str = "read_only",
    exportable: bool = False,
) -> Dict[str, Any]:
    """Create one deterministic local package; pinning remains a separate action."""

    root = _workspace_root(workspace)
    runner_raw = _runner_source().encode("utf-8")
    artifacts = [{"path": "runner.py", "sha256": hashlib.sha256(runner_raw).hexdigest()}]
    closed_object = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    manifest = {
        "schema_version": 1,
        "id": extension_id,
        "version": "0.1.0",
        "api_range": {"min": "0.8.28", "max": "0.8.99"},
        "owner": {"name": owner},
        "license": license_id,
        "exportable": exportable,
        "notices": ["Owner retains the rights declared by this manifest."],
        "capabilities": [],
        "artifacts": artifacts,
        "package_sha256": package_digest(artifacts),
        "tools": [
            {
                "id": tool_id,
                "title": tool_id.replace("-", " ").replace("_", " ").title(),
                "description": "User-owned local extension tool.",
                "authority": authority,
                "input_schema": dict(closed_object),
                "output_schema": dict(closed_object),
                "runtime": "python",
                "entrypoint": "runner.py",
                "argv": [],
                "timeout_sec": 10,
                "max_output_bytes": 65536,
                "pass_env": [],
            }
        ],
    }
    validate_manifest(manifest)
    extensions_root = root / "extensions"
    if extensions_root.exists() and extensions_root.is_symlink():
        _fail("EXTENSION_AUTHORING_PATH_INVALID", "extensions directory cannot be a symlink")
    extensions_root.mkdir(parents=True, exist_ok=True)
    target = extensions_root / extension_id
    if target.exists() or target.is_symlink():
        _fail("EXTENSION_ALREADY_EXISTS", "extension directory already exists", path=target.relative_to(root).as_posix())

    temporary = Path(tempfile.mkdtemp(prefix=".apatch-extension-", dir=str(extensions_root)))
    try:
        (temporary / "runner.py").write_bytes(runner_raw)
        atomic_write_json(str(temporary / "apatch-extension.json"), manifest)
        verify_manifest_file(temporary / "apatch-extension.json")
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    manifest_rel = (target / "apatch-extension.json").relative_to(root).as_posix()
    return {
        "ok": True,
        "protocol": EXTENSION_PROTOCOL,
        "extension_id": extension_id,
        "tool_id": extension_id + "/" + tool_id,
        "manifest_path": manifest_rel,
        "pinned": False,
        "next": "apatch extension pin " + manifest_rel,
    }


def pin_local_extension(
    workspace: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    *,
    source: str = "personal",
    grants: Iterable[str] = (),
    enabled: bool = True,
    replace: bool = False,
    lock_path: str = DEFAULT_WORKSPACE_LOCK,
) -> Dict[str, Any]:
    """Explicitly add or repin one verified package in the workspace lock."""

    root = _workspace_root(workspace)
    manifest_file, manifest_rel = _relative_workspace_file(root, manifest_path)
    verified = verify_manifest_file(manifest_file)
    manifest = verified["manifest"]
    entry = {
        "id": manifest["id"],
        "version": manifest["version"],
        "source": source,
        "manifest_path": manifest_rel,
        "manifest_sha256": verified["manifest_sha256"],
        "package_sha256": verified["package_sha256"],
        "license": manifest["license"],
        "enabled": enabled,
        "grants": list(grants),
    }
    lock_file = _lock_target(root, lock_path)
    with exclusive_file_lock(str(lock_file)):
        current = load_explicit_lock(root, lock_path=lock_path)
        entries = [dict(item) for item in current["extensions"]]
        old = next((item for item in entries if item["id"] == manifest["id"]), None)
        if old is not None and old != entry and not replace:
            _fail(
                "EXTENSION_REPIN_REQUIRED",
                "extension is already pinned differently; repeat with explicit replace",
                path=manifest["id"],
            )
        if old == entry:
            changed = False
        else:
            entries = [item for item in entries if item["id"] != manifest["id"]]
            entries.append(entry)
            entries.sort(key=lambda item: item["id"])
            changed = True
        normalized = validate_lock({"schema_version": 1, "extensions": entries})
        verify_lock_entry(root, entry)
        if changed:
            atomic_write_json(str(lock_file), normalized)
    return {
        "ok": True,
        "extension_id": manifest["id"],
        "manifest_path": manifest_rel,
        "lock_path": lock_path,
        "source": source,
        "enabled": enabled,
        "grants": list(entry["grants"]),
        "changed": changed,
    }
