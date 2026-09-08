"""Multi-file strip manifest loading (R29)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from apatch.strip import StripSpec, load_strip_manifest


@dataclass
class ManifestFileEntry:
    path: str
    strips: List[StripSpec] = field(default_factory=list)


@dataclass
class ManifestBundle:
    files: List[ManifestFileEntry] = field(default_factory=list)
    verify_command: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_multi(self) -> bool:
        return len(self.files) > 1

    def single_specs(self) -> List[StripSpec]:
        if self.files:
            return self.files[0].strips
        return []


def _spec_from_dict(item: dict) -> StripSpec:
    return StripSpec(
        start=item["start"],
        until=item["until"],
        replace=item.get("replace", ""),
        label=item.get("label", ""),
        export=item.get("export", ""),
        register=item.get("register", ""),
        target_module=item.get("target_module", ""),
        module_kind=item.get("module_kind", ""),
        parent_import=item.get("parent_import", ""),
        parent_wire=item.get("parent_wire", ""),
        verify_command=item.get("verify_command", ""),
        exclude_from_compile=list(item.get("exclude_from_compile", []) or []),
        route_from=item.get("route_from", ""),
        route_to=item.get("route_to", ""),
    )


def _load_raw(manifest_path: Union[str, Path]) -> Any:
    path = Path(manifest_path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:
            raise RuntimeError("PyYAML required for YAML manifests: pip install pyyaml") from e
        return yaml.safe_load(raw)
    return json.loads(raw)


def load_manifest_bundle(
    manifest_path: Union[str, Path],
    *,
    default_file: Optional[str] = None,
) -> ManifestBundle:
    """
    Load v1 `{strips: [...]}` or v2 `{files: [{path, strips}], verify_command}` manifests.
    """
    data = _load_raw(manifest_path)
    verify_command = ""
    raw_dict: Dict[str, Any] = data if isinstance(data, dict) else {}

    if isinstance(data, dict) and "files" in data:
        verify_command = str(data.get("verify_command", "") or "")
        entries: List[ManifestFileEntry] = []
        for item in data["files"]:
            if not isinstance(item, dict) or "path" not in item:
                raise ValueError("each files[] entry must have path and strips")
            strips_raw = item.get("strips", item.get("blocks", []))
            entries.append(
                ManifestFileEntry(
                    path=item["path"],
                    strips=[_spec_from_dict(s) for s in strips_raw],
                )
            )
        return ManifestBundle(files=entries, verify_command=verify_command, raw=raw_dict)

    # Legacy: top-level strips list or dict with strips key — single file
    specs = load_strip_manifest(manifest_path)
    file_path = default_file or raw_dict.get("file") or raw_dict.get("path") or ""
    if not file_path:
        raise ValueError("single-file manifest requires --file or manifest.file/path")
    if isinstance(data, dict):
        verify_command = str(data.get("verify_command", "") or "")
    return ManifestBundle(
        files=[ManifestFileEntry(path=file_path, strips=specs)],
        verify_command=verify_command,
        raw=raw_dict,
    )
