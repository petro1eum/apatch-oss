"""Click surfaces for the open local APatch Extension Host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import click

from apatch.extensions.authoring import pin_local_extension, scaffold_local_extension
from apatch.extensions.catalog import DEFAULT_WORKSPACE_LOCK
from apatch.extensions.host import (
    inspect_extension,
    list_extensions,
    run_extension_tool,
    validate_extensions,
)


@click.group("extension")
def extension_group() -> None:
    """Inspect and run explicitly pinned local extensions."""


def _emit(result: Dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        if "extensions" in result:
            for item in result.get("extensions") or []:
                click.echo(
                    "{id}@{version} [{state}]".format(
                        id=item["id"], version=item["version"],
                        state="enabled" if item.get("enabled", True) else "disabled",
                    )
                )
        elif "extension" in result:
            item = result["extension"]
            click.echo("{}@{}".format(item["id"], item["version"]))
        else:
            click.echo("ok")
    else:
        click.echo("{}: {}".format(
            result.get("error_type") or "EXTENSION_ERROR",
            result.get("error") or "extension operation failed",
        ), err=True)
    if not result.get("ok"):
        raise click.ClickException(result.get("error") or result.get("error_type") or "extension error")


@extension_group.command("list")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--lock-path", default=DEFAULT_WORKSPACE_LOCK, show_default=True)
@click.option("--enabled-only", is_flag=True, help="Hide disabled lock entries.")
@click.option("--json", "as_json", is_flag=True)
def extension_list_cmd(target_dir: str, lock_path: str, enabled_only: bool, as_json: bool) -> None:
    """List digest-verified extensions from the explicit lock."""
    _emit(list_extensions(
        target_dir, lock_path=lock_path, include_disabled=not enabled_only
    ), as_json=as_json)


@extension_group.command("inspect")
@click.argument("extension_id")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--lock-path", default=DEFAULT_WORKSPACE_LOCK, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def extension_inspect_cmd(extension_id: str, target_dir: str, lock_path: str, as_json: bool) -> None:
    """Inspect one extension without executing it."""
    _emit(inspect_extension(target_dir, extension_id, lock_path=lock_path), as_json=as_json)


@extension_group.command("validate")
@click.argument("extension_id", required=False, default="")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--lock-path", default=DEFAULT_WORKSPACE_LOCK, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def extension_validate_cmd(extension_id: str, target_dir: str, lock_path: str, as_json: bool) -> None:
    """Revalidate lock, manifest, and artifact digests."""
    _emit(validate_extensions(target_dir, extension_id, lock_path=lock_path), as_json=as_json)


def _emit_authoring(call: Any, *, as_json: bool) -> None:
    try:
        result = call()
    except Exception as exc:
        from apatch.extensions.schema import ExtensionContractError
        if not isinstance(exc, ExtensionContractError):
            raise
        result = exc.to_result()
    _emit(result, as_json=as_json)


@extension_group.command("init")
@click.argument("extension_id")
@click.option("--tool-id", default="run", show_default=True)
@click.option("--owner", required=True)
@click.option("--license", "license_id", default="LicenseRef-Private", show_default=True)
@click.option("--authority", type=click.Choice(["read_only", "proposal"]), default="read_only")
@click.option("--exportable/--private", default=False)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def extension_init_cmd(
    extension_id: str, tool_id: str, owner: str, license_id: str,
    authority: str, exportable: bool, target_dir: str, as_json: bool,
) -> None:
    """Scaffold a user-owned local extension without pinning it."""
    _emit_authoring(lambda: scaffold_local_extension(
        target_dir, extension_id, tool_id=tool_id, owner=owner,
        license_id=license_id, authority=authority, exportable=exportable,
    ), as_json=as_json)


@extension_group.command("pin")
@click.argument("manifest_path", type=click.Path(dir_okay=False))
@click.option("--source", type=click.Choice(["personal", "workspace"]), default="personal")
@click.option("--grant", "grants", multiple=True)
@click.option("--disabled", is_flag=True)
@click.option("--replace", is_flag=True, help="Explicitly replace a changed pin.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--lock-path", default=DEFAULT_WORKSPACE_LOCK, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def extension_pin_cmd(
    manifest_path: str, source: str, grants: tuple[str, ...], disabled: bool,
    replace: bool, target_dir: str, lock_path: str, as_json: bool,
) -> None:
    """Verify and explicitly pin a local extension manifest."""
    _emit_authoring(lambda: pin_local_extension(
        target_dir, manifest_path, source=source, grants=grants,
        enabled=not disabled, replace=replace, lock_path=lock_path,
    ), as_json=as_json)


@extension_group.command("run")
@click.argument("tool_id")
@click.option("--arguments-json", default="", help="One JSON object passed to the extension.")
@click.option("--arguments-file", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--lock-path", default=DEFAULT_WORKSPACE_LOCK, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def extension_run_cmd(
    tool_id: str, arguments_json: str, arguments_file: Optional[str],
    target_dir: str, lock_path: str, as_json: bool,
) -> None:
    """Run one enabled extension tool through the bounded JSON protocol."""
    if arguments_json and arguments_file:
        raise click.UsageError("choose exactly one of --arguments-json or --arguments-file")
    raw = Path(arguments_file).read_text(encoding="utf-8") if arguments_file else arguments_json
    try:
        arguments: Any = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise click.UsageError("arguments must be one JSON value") from exc
    _emit(run_extension_tool(
        target_dir, tool_id, arguments, lock_path=lock_path
    ), as_json=as_json)
