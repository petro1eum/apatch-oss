"""Stable workflow boundary for the local APatch Extension Host."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict

from apatch.extensions.catalog import DEFAULT_WORKSPACE_LOCK, ExtensionCatalog
from apatch.extensions.runner import run_verified_tool
from apatch.extensions.schema import ExtensionContractError


def _guard(call: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return call()
    except ExtensionContractError as exc:
        return exc.to_result()
    except OSError:
        return {
            "ok": False,
            "error_type": "EXTENSION_HOST_IO_FAILED",
            "error": "extension host could not access a declared local artifact",
        }


def list_extensions(
    workspace: str | os.PathLike[str],
    *,
    lock_path: str = DEFAULT_WORKSPACE_LOCK,
    include_disabled: bool = True,
) -> Dict[str, Any]:
    return _guard(lambda: ExtensionCatalog(
        workspace, lock_path=lock_path
    ).list(include_disabled=include_disabled))


def inspect_extension(
    workspace: str | os.PathLike[str],
    extension_id: str,
    *,
    lock_path: str = DEFAULT_WORKSPACE_LOCK,
) -> Dict[str, Any]:
    return _guard(lambda: ExtensionCatalog(
        workspace, lock_path=lock_path
    ).inspect(extension_id))


def validate_extensions(
    workspace: str | os.PathLike[str],
    extension_id: str = "",
    *,
    lock_path: str = DEFAULT_WORKSPACE_LOCK,
) -> Dict[str, Any]:
    return _guard(lambda: ExtensionCatalog(
        workspace, lock_path=lock_path
    ).validate(extension_id))


def run_extension_tool(
    workspace: str | os.PathLike[str],
    tool_id: str,
    arguments: Any,
    *,
    lock_path: str = DEFAULT_WORKSPACE_LOCK,
) -> Dict[str, Any]:
    def _run() -> Dict[str, Any]:
        verified = ExtensionCatalog(workspace, lock_path=lock_path).resolve_tool(tool_id)
        return run_verified_tool(verified, arguments, workspace=workspace)
    return _guard(_run)
