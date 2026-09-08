"""Explicit, read-only discovery for local APatch extensions."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping

from apatch.extensions.schema import (
    ExtensionContractError,
    validate_lock,
    verify_lock_entry,
)

DEFAULT_WORKSPACE_LOCK = ".apatch/extensions.lock.json"


def _fail(code: str, message: str, *, path: str = "") -> None:
    raise ExtensionContractError(code, message, path=path)


def _explicit_path(root: Path, relative: str, *, allow_missing: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _fail("EXTENSION_LOCK_PATH_INVALID", "lock path must be a non-empty POSIX relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("EXTENSION_LOCK_PATH_INVALID", "lock path must stay inside the workspace", path=relative)

    root_resolved = root.resolve()
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            _fail("EXTENSION_LOCK_PATH_INVALID", "lock path cannot contain symlinks", path=relative)
    if allow_missing and not candidate.exists():
        parent = candidate.parent.resolve()
        try:
            common = os.path.commonpath([str(root_resolved), str(parent)])
        except ValueError:
            common = ""
        if common != str(root_resolved):
            _fail("EXTENSION_LOCK_PATH_INVALID", "lock path escapes workspace", path=relative)
        return candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        _fail("EXTENSION_LOCK_READ_FAILED", "cannot resolve lock: " + str(exc), path=relative)
    try:
        common = os.path.commonpath([str(root_resolved), str(resolved)])
    except ValueError:
        common = ""
    if common != str(root_resolved) or not resolved.is_file():
        _fail("EXTENSION_LOCK_PATH_INVALID", "lock must be a regular workspace file", path=relative)
    return resolved


def load_explicit_lock(
    workspace: str | os.PathLike[str],
    *,
    lock_path: str = DEFAULT_WORKSPACE_LOCK,
) -> Dict[str, Any]:
    """Load exactly one governed workspace lock; absence means an empty catalog."""

    root = Path(workspace).resolve()
    path = _explicit_path(root, lock_path, allow_missing=True)
    if not path.exists():
        return {
            "schema_version": 1,
            "extensions": [],
            "_lock_path": str(path),
            "_lock_present": False,
        }
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("EXTENSION_LOCK_READ_FAILED", "cannot read extension lock: " + str(exc), path=lock_path)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("EXTENSION_LOCK_JSON_INVALID", "extension lock must be one UTF-8 JSON object", path=lock_path)
    normalized = validate_lock(parsed)
    normalized["_lock_path"] = str(path)
    normalized["_lock_present"] = True
    return normalized


def _public_record(verified: Mapping[str, Any]) -> Dict[str, Any]:
    manifest = verified["manifest"]
    lock_entry = verified["lock_entry"]
    return {
        "id": manifest["id"],
        "version": manifest["version"],
        "title": manifest.get("title") or manifest["id"],
        "description": manifest.get("description") or "",
        "source": lock_entry["source"],
        "enabled": lock_entry["enabled"],
        "license": manifest["license"],
        "owner": dict(manifest["owner"]),
        "exportable": manifest["exportable"],
        "notices": list(manifest["notices"]),
        "capabilities_requested": list(manifest["capabilities"]),
        "grants": list(lock_entry["grants"]),
        "manifest_path": lock_entry["manifest_path"],
        "manifest_sha256": verified["manifest_sha256"],
        "package_sha256": verified["package_sha256"],
        "tools": [
            {
                "id": tool["id"],
                "full_id": tool["full_id"],
                "title": tool["title"],
                "description": tool["description"],
                "authority": tool["authority"],
                "timeout_sec": tool["timeout_sec"],
                "max_output_bytes": tool["max_output_bytes"],
                "work_asset_ref": tool.get("work_asset_ref"),
            }
            for tool in manifest["tools"]
        ],
    }


class ExtensionCatalog:
    """Digest-verified catalog backed only by an explicit lock file."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        lock_path: str = DEFAULT_WORKSPACE_LOCK,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.lock_path = lock_path

    def _verified(self) -> list[Dict[str, Any]]:
        lock = load_explicit_lock(self.workspace, lock_path=self.lock_path)
        verified: list[Dict[str, Any]] = []
        tool_ids: set[str] = set()
        for entry in lock["extensions"]:
            item = verify_lock_entry(self.workspace, entry)
            for tool in item["manifest"]["tools"]:
                full_id = tool["full_id"]
                if full_id in tool_ids:
                    _fail("EXTENSION_TOOL_COLLISION", "duplicate logical tool id", path=full_id)
                tool_ids.add(full_id)
            verified.append(item)
        return verified

    def list(self, *, include_disabled: bool = True) -> Dict[str, Any]:
        records = [
            _public_record(item)
            for item in self._verified()
            if include_disabled or item["lock_entry"]["enabled"]
        ]
        return {
            "ok": True,
            "workspace": str(self.workspace),
            "lock_path": self.lock_path,
            "count": len(records),
            "extensions": records,
        }

    def inspect(self, extension_id: str) -> Dict[str, Any]:
        for item in self._verified():
            if item["manifest"]["id"] == extension_id:
                return {"ok": True, "extension": _public_record(item)}
        _fail("EXTENSION_NOT_FOUND", "extension is not present in the explicit lock", path=extension_id)

    def validate(self, extension_id: str = "") -> Dict[str, Any]:
        verified = self._verified()
        if extension_id:
            verified = [item for item in verified if item["manifest"]["id"] == extension_id]
            if not verified:
                _fail("EXTENSION_NOT_FOUND", "extension is not present in the explicit lock", path=extension_id)
        return {
            "ok": True,
            "count": len(verified),
            "extensions": [
                {
                    "id": item["manifest"]["id"],
                    "version": item["manifest"]["version"],
                    "manifest_sha256": item["manifest_sha256"],
                    "package_sha256": item["package_sha256"],
                    "valid": True,
                }
                for item in verified
            ],
        }

    def resolve_tool(self, full_tool_id: str) -> Dict[str, Any]:
        for item in self._verified():
            if not item["lock_entry"]["enabled"]:
                continue
            for tool in item["manifest"]["tools"]:
                if tool["full_id"] == full_tool_id:
                    return {**item, "tool": tool}
        _fail("EXTENSION_TOOL_NOT_FOUND", "enabled tool is not present in the explicit lock", path=full_tool_id)
