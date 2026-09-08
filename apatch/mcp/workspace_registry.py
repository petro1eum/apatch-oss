"""Human-authorized registry for safe local MCP workspace roaming."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REGISTRY_VERSION = 1
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


class WorkspaceRegistryError(ValueError):
    """Structured local-workspace routing failure."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        recommended_action: str,
        alias: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.recommended_action = recommended_action
        self.alias = alias

    def to_result(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": False,
            "error": str(self),
            "error_type": self.error_type,
            "recoverable": True,
            "recommended_action": self.recommended_action,
        }
        if self.alias:
            result["alias"] = self.alias
        return result


def workspace_registry_path(path: Optional[str] = None) -> Path:
    explicit = path or os.environ.get("APATCH_WORKSPACE_REGISTRY")
    if explicit:
        return Path(explicit).expanduser().resolve()
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    ).expanduser()
    return (config_home / "apatch" / "workspaces.json").resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_fingerprint(root: Path) -> str:
    stat = root.stat()
    payload = json.dumps(
        {
            "realpath": str(root),
            "device": int(stat.st_dev),
            "inode": int(stat.st_ino),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_alias(alias: str) -> str:
    value = (alias or "").strip()
    if not _ALIAS_RE.fullmatch(value):
        raise WorkspaceRegistryError(
            "Invalid workspace alias {!r}; use lowercase letters, digits, dot, underscore, or dash.".format(
                value
            ),
            error_type="WORKSPACE_ALIAS_INVALID",
            recommended_action="Choose an alias such as agent, crm, or catalog.",
            alias=value or None,
        )
    return value


def _workspace_metadata(root_value: str, *, include_contract: bool = False) -> Dict[str, Any]:
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise WorkspaceRegistryError(
            "Workspace root does not exist: {}".format(root),
            error_type="WORKSPACE_NOT_READY",
            recommended_action="Choose an existing repository root.",
        )
    if not (root / ".git").exists():
        raise WorkspaceRegistryError(
            "Workspace is not a git repository: {}".format(root),
            error_type="WORKSPACE_NOT_READY",
            recommended_action="Register the git repository root, not a subdirectory.",
        )
    canonical = root / ".apatch" / "mcp.json"
    if not canonical.is_file():
        raise WorkspaceRegistryError(
            "Workspace has no canonical .apatch/mcp.json: {}".format(root),
            error_type="WORKSPACE_NOT_READY",
            recommended_action="Run 'apatch mcp sync --target-dir <root>' as a human, then register the alias.",
        )
    contract = root / "AGENTS.md"
    if not contract.is_file():
        raise WorkspaceRegistryError(
            "Workspace has no AGENTS.md contract: {}".format(root),
            error_type="WORKSPACE_NOT_READY",
            recommended_action="Add or refresh AGENTS.md before authorizing cross-workspace access.",
        )
    result: Dict[str, Any] = {
        "path": str(root),
        "root_fingerprint": _root_fingerprint(root),
        "canonical_mcp_path": str(canonical),
        "contract": {"path": str(contract), "sha256": _sha256(contract)},
    }
    if include_contract:
        result["contract"]["content"] = contract.read_text(encoding="utf-8")
    return result


def load_workspace_registry(path: Optional[str] = None) -> Dict[str, Any]:
    registry_path = workspace_registry_path(path)
    if not registry_path.exists():
        return {"version": REGISTRY_VERSION, "workspaces": {}}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceRegistryError(
            "Invalid workspace registry {}: {}".format(registry_path, exc),
            error_type="WORKSPACE_REGISTRY_INVALID",
            recommended_action="Repair or remove the registry, then re-register workspace aliases.",
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("workspaces"), dict):
        raise WorkspaceRegistryError(
            "Invalid workspace registry schema: {}".format(registry_path),
            error_type="WORKSPACE_REGISTRY_INVALID",
            recommended_action="Repair or remove the registry, then re-register workspace aliases.",
        )
    return data


def _save_workspace_registry(data: Dict[str, Any], path: Optional[str] = None) -> Path:
    registry_path = workspace_registry_path(path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = registry_path.with_name(
        "{}.tmp.{}".format(registry_path.name, os.getpid())
    )
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, registry_path)
    return registry_path


def _entry_status(
    alias: str,
    entry: Dict[str, Any],
    *,
    include_contract: bool = False,
) -> Dict[str, Any]:
    path = str(entry.get("path") or "")
    try:
        current = _workspace_metadata(path, include_contract=include_contract)
    except WorkspaceRegistryError as exc:
        result = exc.to_result()
        result.update({"alias": alias, "path": path, "ready": False, "drift": []})
        return result

    drift = []
    if entry.get("root_fingerprint") != current["root_fingerprint"]:
        drift.append(
            {
                "type": "WORKSPACE_IDENTITY_DRIFT",
                "expected": entry.get("root_fingerprint"),
                "actual": current["root_fingerprint"],
            }
        )
    expected_contract = entry.get("contract_sha256")
    actual_contract = current["contract"]["sha256"]
    if expected_contract != actual_contract:
        drift.append(
            {
                "type": "WORKSPACE_CONTRACT_DRIFT",
                "expected": expected_contract,
                "actual": actual_contract,
            }
        )
    return {
        "ok": not drift,
        "alias": alias,
        "target_dir": "@{}".format(alias),
        "path": current["path"],
        "ready": not drift,
        "root_fingerprint": current["root_fingerprint"],
        "canonical_mcp_path": current["canonical_mcp_path"],
        "contract": current["contract"],
        "registered_at": entry.get("registered_at"),
        "drift": drift,
    }


def register_local_workspace(
    alias: str,
    root: str,
    *,
    force: bool = False,
    registry_path: Optional[str] = None,
) -> Dict[str, Any]:
    name = _normalize_alias(alias)
    metadata = _workspace_metadata(root)
    data = load_workspace_registry(registry_path)
    workspaces = data.setdefault("workspaces", {})
    existed = name in workspaces
    if existed and not force:
        raise WorkspaceRegistryError(
            "Workspace alias already exists: @{}".format(name),
            error_type="WORKSPACE_ALIAS_EXISTS",
            recommended_action="Use --force only after reviewing the new root and AGENTS.md.",
            alias=name,
        )
    workspaces[name] = {
        "path": metadata["path"],
        "root_fingerprint": metadata["root_fingerprint"],
        "contract_sha256": metadata["contract"]["sha256"],
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    data["version"] = REGISTRY_VERSION
    saved = _save_workspace_registry(data, registry_path)
    result = _entry_status(name, workspaces[name])
    result.update({"ok": True, "registry_path": str(saved), "replaced": existed})
    return result


def remove_local_workspace(
    alias: str,
    *,
    registry_path: Optional[str] = None,
) -> Dict[str, Any]:
    name = _normalize_alias(alias)
    data = load_workspace_registry(registry_path)
    workspaces = data.setdefault("workspaces", {})
    if name not in workspaces:
        raise WorkspaceRegistryError(
            "Unknown workspace alias: @{}".format(name),
            error_type="WORKSPACE_ALIAS_UNKNOWN",
            recommended_action="Run 'apatch workspace list' and choose a registered alias.",
            alias=name,
        )
    removed = workspaces.pop(name)
    saved = _save_workspace_registry(data, registry_path)
    return {
        "ok": True,
        "alias": name,
        "removed": True,
        "path": removed.get("path"),
        "registry_path": str(saved),
    }


def list_local_workspaces(
    *,
    include_contract: bool = False,
    registry_path: Optional[str] = None,
) -> Dict[str, Any]:
    data = load_workspace_registry(registry_path)
    rows = [
        _entry_status(alias, entry, include_contract=include_contract)
        for alias, entry in sorted(data.get("workspaces", {}).items())
        if isinstance(entry, dict)
    ]
    return {
        "ok": True,
        "registry_path": str(workspace_registry_path(registry_path)),
        "count": len(rows),
        "workspaces": rows,
    }


def inspect_local_workspace(
    alias: str,
    *,
    include_contract: bool = False,
    enforce_pins: bool = False,
    registry_path: Optional[str] = None,
) -> Dict[str, Any]:
    name = _normalize_alias(alias.lstrip("@"))
    data = load_workspace_registry(registry_path)
    entry = data.get("workspaces", {}).get(name)
    if not isinstance(entry, dict):
        raise WorkspaceRegistryError(
            "Unknown workspace alias: @{}".format(name),
            error_type="WORKSPACE_ALIAS_UNKNOWN",
            recommended_action="Ask a human to run 'apatch workspace add {} <root>'.".format(name),
            alias=name,
        )
    status = _entry_status(name, entry, include_contract=include_contract)
    if enforce_pins and not status.get("ready"):
        drift = status.get("drift") or []
        error_type = (
            drift[0].get("type")
            if drift
            else status.get("error_type", "WORKSPACE_NOT_READY")
        )
        raise WorkspaceRegistryError(
            "Workspace alias @{} is not authorized in its current state.".format(name),
            error_type=str(error_type),
            recommended_action=(
                "Review the root and AGENTS.md, then run "
                "'apatch workspace add {} {} --force' as a human."
            ).format(name, status.get("path") or "<root>"),
            alias=name,
        )
    return status


def resolve_local_workspace(alias: str) -> str:
    return str(inspect_local_workspace(alias, enforce_pins=True)["path"])


def local_roaming_status(
    effective_workspace: str,
    *,
    bound_workspace: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        listing = list_local_workspaces()
    except WorkspaceRegistryError as exc:
        result = exc.to_result()
        result.update(
            {
                "effective_workspace": str(Path(effective_workspace).resolve()),
                "bound_workspace": bound_workspace,
            }
        )
        return result
    effective = str(Path(effective_workspace).resolve())
    selected = next(
        (row for row in listing["workspaces"] if row.get("path") == effective),
        None,
    )
    return {
        "ok": True,
        "enabled": bool(listing["count"]),
        "target_policy": os.environ.get("APATCH_MCP_TARGET_POLICY", "legacy"),
        "registry_path": listing["registry_path"],
        "registered_aliases": [row["alias"] for row in listing["workspaces"]],
        "bound_workspace": bound_workspace,
        "effective_workspace": effective,
        "effective_alias": selected.get("alias") if selected else None,
        "effective_target_dir": "@{}".format(selected["alias"]) if selected else ".",
    }
