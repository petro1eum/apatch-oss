"""Strict schema and digest validation for APatch local extensions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Sequence

EXTENSION_PROTOCOL = "apatch.extension.v1"
SCHEMA_VERSION = 1
MIN_TIMEOUT_SEC = 1
MAX_TIMEOUT_SEC = 120
MIN_OUTPUT_BYTES = 256
MAX_OUTPUT_BYTES = 1024 * 1024

_ID_RE = re.compile(
    r"^(?!apatch\.)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_MAX_EXTENSION_ID_LENGTH = 253
_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][A-Za-z0-9_*-]+)*$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_LICENSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+()/:_-]{0,191}$")
_WORK_ASSET_REF_RE = re.compile(r"^work_asset:[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")

_MANIFEST_REQUIRED = frozenset(
    {
        "schema_version",
        "id",
        "version",
        "api_range",
        "owner",
        "license",
        "exportable",
        "notices",
        "capabilities",
        "artifacts",
        "package_sha256",
        "tools",
    }
)
_MANIFEST_OPTIONAL = frozenset({"title", "description"})
_TOOL_REQUIRED = frozenset(
    {
        "id",
        "title",
        "description",
        "authority",
        "input_schema",
        "output_schema",
        "runtime",
        "entrypoint",
        "argv",
        "timeout_sec",
        "max_output_bytes",
        "pass_env",
    }
)
_TOOL_OPTIONAL = frozenset({"work_asset_ref"})
_LOCK_REQUIRED = frozenset({"schema_version", "extensions"})
_LOCK_ENTRY_REQUIRED = frozenset(
    {
        "id",
        "version",
        "source",
        "manifest_path",
        "manifest_sha256",
        "package_sha256",
        "license",
        "enabled",
        "grants",
    }
)
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "description",
    }
)
_JSON_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_RESERVED_EXTENSION_PREFIXES = ("apatch.", "apatch_", "apatch-")


class ExtensionContractError(ValueError):
    """Typed contract failure suitable for stable CLI/MCP DTOs."""

    def __init__(self, code: str, message: str, *, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.path = path

    def to_result(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": False,
            "error_type": self.code,
            "error": str(self),
        }
        if self.path:
            result["path"] = self.path
        return result


def _fail(code: str, message: str, *, path: str = "") -> None:
    raise ExtensionContractError(code, message, path=path)


def _mapping(value: Any, *, code: str, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code, "expected an object", path=path)
    return value


def _strict_keys(
    value: Mapping[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    code: str,
    path: str,
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        _fail(code, "missing fields: " + ", ".join(missing), path=path)
    if unknown:
        _fail(code, "unknown fields: " + ", ".join(unknown), path=path)


def _nonempty_string(value: Any, *, code: str, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code, "expected a non-empty string", path=path)
    return value.strip()


def _extension_id(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("EXTENSION_ID_INVALID", "expected a non-empty string", path=path)
    text = value
    if (
        len(text) > _MAX_EXTENSION_ID_LENGTH
        or not _ID_RE.fullmatch(text)
        or text.startswith(_RESERVED_EXTENSION_PREFIXES)
    ):
        _fail(
            "EXTENSION_ID_INVALID",
            "id must contain at least three lowercase reverse-DNS labels",
            path=path,
        )
    return text


def _string_list(value: Any, *, code: str, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail(code, "expected a list of non-empty strings", path=path)
    if len(value) != len(set(value)):
        _fail(code, "duplicate values are not allowed", path=path)
    return list(value)


def _semver(value: Any, *, code: str, path: str) -> str:
    text = _nonempty_string(value, code=code, path=path)
    if not _SEMVER_RE.fullmatch(text):
        _fail(code, "expected strict semantic version", path=path)
    return text


def _semver_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        _fail("EXTENSION_SEMVER_INVALID", "expected strict semantic version")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _assert_api_compatible(manifest: Mapping[str, Any]) -> None:
    from apatch import __version__

    current = _semver_tuple(__version__)
    minimum = _semver_tuple(str(manifest["api_range"]["min"]))
    maximum = _semver_tuple(str(manifest["api_range"]["max"]))
    if current < minimum or current > maximum:
        _fail(
            "EXTENSION_API_VERSION_UNSUPPORTED",
            "running APatch version is outside the extension api_range",
            path="manifest.api_range",
        )


def _sha256(value: Any, *, code: str, path: str) -> str:
    text = _nonempty_string(value, code=code, path=path)
    if not _SHA256_RE.fullmatch(text):
        _fail(code, "expected lowercase SHA-256 hex digest", path=path)
    return text


def _relative_path(value: Any, *, code: str, path: str) -> str:
    text = _nonempty_string(value, code=code, path=path)
    if "\\" in text:
        _fail(code, "path must use POSIX separators", path=path)
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail(code, "path must be relative and contained", path=path)
    return pure.as_posix()


def _validate_json_schema(value: Any, *, path: str) -> Dict[str, Any]:
    schema = _mapping(value, code="EXTENSION_SCHEMA_INVALID", path=path)
    unknown = sorted(set(schema) - _SCHEMA_KEYS)
    if unknown:
        _fail(
            "EXTENSION_SCHEMA_KEYWORD_UNSUPPORTED",
            "unsupported schema keywords: " + ", ".join(unknown),
            path=path,
        )
    schema_type = schema.get("type")
    if schema_type not in _JSON_TYPES:
        _fail("EXTENSION_SCHEMA_INVALID", "schema requires one supported type", path=path)
    if "description" in schema and not isinstance(schema["description"], str):
        _fail("EXTENSION_SCHEMA_INVALID", "description must be a string", path=path + ".description")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            _fail("EXTENSION_SCHEMA_INVALID", "enum must be a non-empty list", path=path + ".enum")
        serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in enum]
        if len(serialized) != len(set(serialized)):
            _fail("EXTENSION_SCHEMA_INVALID", "enum values must be unique", path=path + ".enum")
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            _fail("EXTENSION_SCHEMA_INVALID", key + " must be numeric", path=path + "." + key)
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (
            isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0
        ):
            _fail("EXTENSION_SCHEMA_INVALID", key + " must be a non-negative integer", path=path + "." + key)
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        _fail("EXTENSION_SCHEMA_INVALID", "minimum exceeds maximum", path=path)
    for low, high in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if low in schema and high in schema and schema[low] > schema[high]:
            _fail("EXTENSION_SCHEMA_INVALID", low + " exceeds " + high, path=path)

    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            _fail("EXTENSION_SCHEMA_INVALID", "properties must be an object", path=path + ".properties")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            _fail("EXTENSION_SCHEMA_INVALID", "required must be a string list", path=path + ".required")
        if len(required) != len(set(required)) or any(item not in properties for item in required):
            _fail("EXTENSION_SCHEMA_INVALID", "required fields must be unique declared properties", path=path + ".required")
        additional = schema.get("additionalProperties", False)
        if not isinstance(additional, bool):
            _fail("EXTENSION_SCHEMA_INVALID", "additionalProperties must be boolean", path=path)
        for name, child in properties.items():
            if not isinstance(name, str) or not name:
                _fail("EXTENSION_SCHEMA_INVALID", "property names must be non-empty strings", path=path)
            _validate_json_schema(child, path=path + ".properties." + name)
    elif any(key in schema for key in ("properties", "required", "additionalProperties")):
        _fail("EXTENSION_SCHEMA_INVALID", "object keywords require type=object", path=path)

    if schema_type == "array":
        if "items" not in schema:
            _fail("EXTENSION_SCHEMA_INVALID", "array schema requires items", path=path)
        _validate_json_schema(schema["items"], path=path + ".items")
    elif "items" in schema:
        _fail("EXTENSION_SCHEMA_INVALID", "items requires type=array", path=path)

    return dict(schema)


def validate_json_value(value: Any, schema_value: Any, *, path: str = "$") -> Any:
    """Validate a JSON-compatible value against the supported schema subset."""

    schema = _validate_json_schema(schema_value, path=path + ".schema")
    expected = schema["type"]
    matches = {
        "object": lambda item: isinstance(item, Mapping),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }[expected](value)
    if not matches:
        _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "expected type " + expected, path=path)

    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if "const" in schema and encoded != json.dumps(schema["const"], sort_keys=True, ensure_ascii=False):
        _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "value differs from const", path=path)
    if "enum" in schema:
        allowed = {
            json.dumps(item, sort_keys=True, ensure_ascii=False) for item in schema["enum"]
        }
        if encoded not in allowed:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "value is outside enum", path=path)

    if expected == "object":
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "missing required property", path=path + "." + required)
        if schema.get("additionalProperties", False) is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "unknown properties: " + ", ".join(unknown), path=path)
        for name, child in properties.items():
            if name in value:
                validate_json_value(value[name], child, path=path + "." + name)
    elif expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "array is shorter than minItems", path=path)
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "array is longer than maxItems", path=path)
        for index, item in enumerate(value):
            validate_json_value(item, schema["items"], path=f"{path}[{index}]")
    elif expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "string is shorter than minLength", path=path)
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "string is longer than maxLength", path=path)
    elif expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "number is below minimum", path=path)
        if "maximum" in schema and value > schema["maximum"]:
            _fail("EXTENSION_VALUE_SCHEMA_MISMATCH", "number is above maximum", path=path)
    return value


def validate_manifest(value: Any) -> Dict[str, Any]:
    """Validate and normalize one extension manifest without executing code."""

    manifest = _mapping(value, code="EXTENSION_MANIFEST_INVALID", path="manifest")
    _strict_keys(
        manifest,
        required=_MANIFEST_REQUIRED,
        optional=_MANIFEST_OPTIONAL,
        code="EXTENSION_MANIFEST_INVALID",
        path="manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        _fail("EXTENSION_SCHEMA_VERSION_UNSUPPORTED", "unsupported manifest schema_version", path="manifest.schema_version")

    manifest_title = None
    if "title" in manifest:
        manifest_title = _nonempty_string(
            manifest["title"], code="EXTENSION_TITLE_INVALID", path="manifest.title"
        )
    manifest_description = manifest.get("description")
    if "description" in manifest and not isinstance(manifest_description, str):
        _fail(
            "EXTENSION_DESCRIPTION_INVALID",
            "description must be a string",
            path="manifest.description",
        )

    extension_id = _extension_id(manifest["id"], path="manifest.id")
    version = _semver(manifest["version"], code="EXTENSION_VERSION_INVALID", path="manifest.version")

    api_range = _mapping(manifest["api_range"], code="EXTENSION_API_RANGE_INVALID", path="manifest.api_range")
    _strict_keys(
        api_range,
        required={"min", "max"},
        code="EXTENSION_API_RANGE_INVALID",
        path="manifest.api_range",
    )
    api_min = _semver(api_range["min"], code="EXTENSION_API_RANGE_INVALID", path="manifest.api_range.min")
    api_max = _semver(api_range["max"], code="EXTENSION_API_RANGE_INVALID", path="manifest.api_range.max")
    if _semver_tuple(api_min) > _semver_tuple(api_max):
        _fail("EXTENSION_API_RANGE_INVALID", "api_range min exceeds max", path="manifest.api_range")

    owner = _mapping(manifest["owner"], code="EXTENSION_OWNER_INVALID", path="manifest.owner")
    _strict_keys(owner, required={"name"}, optional={"contact"}, code="EXTENSION_OWNER_INVALID", path="manifest.owner")
    _nonempty_string(owner["name"], code="EXTENSION_OWNER_INVALID", path="manifest.owner.name")
    if "contact" in owner:
        _nonempty_string(owner["contact"], code="EXTENSION_OWNER_INVALID", path="manifest.owner.contact")

    license_name = _nonempty_string(manifest["license"], code="EXTENSION_LICENSE_INVALID", path="manifest.license")
    if not _LICENSE_RE.fullmatch(license_name):
        _fail("EXTENSION_LICENSE_INVALID", "license must be an SPDX expression or LicenseRef", path="manifest.license")
    if not isinstance(manifest["exportable"], bool):
        _fail("EXTENSION_EXPORTABLE_INVALID", "exportable must be boolean", path="manifest.exportable")
    _string_list(manifest["notices"], code="EXTENSION_NOTICES_INVALID", path="manifest.notices")

    capabilities = _string_list(
        manifest["capabilities"], code="EXTENSION_CAPABILITIES_INVALID", path="manifest.capabilities"
    )
    for capability in capabilities:
        if not _CAPABILITY_RE.fullmatch(capability):
            _fail("EXTENSION_CAPABILITIES_INVALID", "invalid capability name", path="manifest.capabilities")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        _fail("EXTENSION_ARTIFACTS_INVALID", "artifacts must be a non-empty list", path="manifest.artifacts")
    artifact_paths: set[str] = set()
    normalized_artifacts: list[Dict[str, Any]] = []
    for index, raw in enumerate(artifacts):
        item_path = f"manifest.artifacts[{index}]"
        artifact = _mapping(raw, code="EXTENSION_ARTIFACTS_INVALID", path=item_path)
        _strict_keys(artifact, required={"path", "sha256"}, code="EXTENSION_ARTIFACTS_INVALID", path=item_path)
        rel = _relative_path(artifact["path"], code="EXTENSION_ARTIFACT_PATH_INVALID", path=item_path + ".path")
        if rel in artifact_paths:
            _fail("EXTENSION_ARTIFACTS_INVALID", "duplicate artifact path", path=item_path)
        artifact_paths.add(rel)
        normalized_artifacts.append(
            {"path": rel, "sha256": _sha256(artifact["sha256"], code="EXTENSION_ARTIFACT_DIGEST_INVALID", path=item_path + ".sha256")}
        )
    package_sha = _sha256(
        manifest["package_sha256"], code="EXTENSION_PACKAGE_DIGEST_INVALID", path="manifest.package_sha256"
    )

    tools = manifest["tools"]
    if not isinstance(tools, list) or not tools:
        _fail("EXTENSION_TOOLS_INVALID", "tools must be a non-empty list", path="manifest.tools")
    tool_ids: set[str] = set()
    normalized_tools: list[Dict[str, Any]] = []
    for index, raw in enumerate(tools):
        tool_path = f"manifest.tools[{index}]"
        tool = _mapping(raw, code="EXTENSION_TOOL_INVALID", path=tool_path)
        _strict_keys(tool, required=_TOOL_REQUIRED, optional=_TOOL_OPTIONAL, code="EXTENSION_TOOL_INVALID", path=tool_path)
        tool_id = _nonempty_string(tool["id"], code="EXTENSION_TOOL_ID_INVALID", path=tool_path + ".id")
        if not _TOOL_ID_RE.fullmatch(tool_id) or tool_id.startswith("apatch"):
            _fail("EXTENSION_TOOL_ID_INVALID", "invalid or reserved tool id", path=tool_path + ".id")
        if tool_id in tool_ids:
            _fail("EXTENSION_TOOL_DUPLICATE", "duplicate tool id", path=tool_path + ".id")
        tool_ids.add(tool_id)
        tool_title = _nonempty_string(
            tool["title"], code="EXTENSION_TOOL_TITLE_INVALID", path=tool_path + ".title"
        )
        tool_description = _nonempty_string(
            tool["description"],
            code="EXTENSION_TOOL_DESCRIPTION_INVALID",
            path=tool_path + ".description",
        )
        authority = tool["authority"]
        if authority not in {"read_only", "proposal"}:
            _fail("EXTENSION_AUTHORITY_INVALID", "authority must be read_only or proposal", path=tool_path + ".authority")
        runtime = tool["runtime"]
        if runtime not in {"python", "executable"}:
            _fail("EXTENSION_RUNTIME_INVALID", "runtime must be python or executable", path=tool_path + ".runtime")
        entrypoint = _relative_path(tool["entrypoint"], code="EXTENSION_ENTRYPOINT_INVALID", path=tool_path + ".entrypoint")
        if entrypoint not in artifact_paths:
            _fail("EXTENSION_ENTRYPOINT_INVALID", "entrypoint must be a pinned artifact", path=tool_path + ".entrypoint")
        argv = _string_list(tool["argv"], code="EXTENSION_ARGV_INVALID", path=tool_path + ".argv")
        if any("\x00" in arg for arg in argv):
            _fail("EXTENSION_ARGV_INVALID", "argv cannot contain NUL", path=tool_path + ".argv")
        timeout = tool["timeout_sec"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not MIN_TIMEOUT_SEC <= timeout <= MAX_TIMEOUT_SEC:
            _fail("EXTENSION_TIMEOUT_INVALID", f"timeout_sec must be {MIN_TIMEOUT_SEC}..{MAX_TIMEOUT_SEC}", path=tool_path + ".timeout_sec")
        max_output = tool["max_output_bytes"]
        if isinstance(max_output, bool) or not isinstance(max_output, int) or not MIN_OUTPUT_BYTES <= max_output <= MAX_OUTPUT_BYTES:
            _fail("EXTENSION_OUTPUT_BOUND_INVALID", f"max_output_bytes must be {MIN_OUTPUT_BYTES}..{MAX_OUTPUT_BYTES}", path=tool_path + ".max_output_bytes")
        pass_env = _string_list(tool["pass_env"], code="EXTENSION_ENV_INVALID", path=tool_path + ".pass_env")
        for env_name in pass_env:
            if not _ENV_RE.fullmatch(env_name):
                _fail("EXTENSION_ENV_INVALID", "invalid environment variable name", path=tool_path + ".pass_env")
            if "env:" + env_name not in capabilities:
                _fail("EXTENSION_ENV_UNDECLARED", "pass_env requires matching env capability", path=tool_path + ".pass_env")
        normalized = dict(tool)
        normalized.update(
            {
                "id": tool_id,
                "title": tool_title,
                "description": tool_description,
                "authority": authority,
                "runtime": runtime,
                "entrypoint": entrypoint,
                "argv": argv,
                "timeout_sec": timeout,
                "max_output_bytes": max_output,
                "pass_env": pass_env,
                "input_schema": _validate_json_schema(tool["input_schema"], path=tool_path + ".input_schema"),
                "output_schema": _validate_json_schema(tool["output_schema"], path=tool_path + ".output_schema"),
                "full_id": extension_id + "/" + tool_id,
            }
        )
        if "work_asset_ref" in tool:
            work_asset_ref = _nonempty_string(
                tool["work_asset_ref"], code="EXTENSION_WORK_ASSET_REF_INVALID", path=tool_path + ".work_asset_ref"
            )
            if not _WORK_ASSET_REF_RE.fullmatch(work_asset_ref):
                _fail(
                    "EXTENSION_WORK_ASSET_REF_INVALID",
                    "work_asset_ref must be one bounded opaque work_asset reference",
                    path=tool_path + ".work_asset_ref",
                )
            normalized["work_asset_ref"] = work_asset_ref
        normalized_tools.append(normalized)

    normalized_manifest = dict(manifest)
    if manifest_title is not None:
        normalized_manifest["title"] = manifest_title
    if manifest_description is not None:
        normalized_manifest["description"] = manifest_description
    normalized_manifest.update(
        {
            "id": extension_id,
            "version": version,
            "api_range": {"min": api_min, "max": api_max},
            "owner": dict(owner),
            "license": license_name,
            "capabilities": capabilities,
            "artifacts": normalized_artifacts,
            "package_sha256": package_sha,
            "tools": normalized_tools,
        }
    )
    return normalized_manifest


def validate_lock(value: Any) -> Dict[str, Any]:
    """Validate an explicit extension lock without filesystem discovery."""

    lock = _mapping(value, code="EXTENSION_LOCK_INVALID", path="lock")
    _strict_keys(lock, required=_LOCK_REQUIRED, code="EXTENSION_LOCK_INVALID", path="lock")
    if lock["schema_version"] != SCHEMA_VERSION:
        _fail("EXTENSION_LOCK_VERSION_UNSUPPORTED", "unsupported lock schema_version", path="lock.schema_version")
    entries = lock["extensions"]
    if not isinstance(entries, list):
        _fail("EXTENSION_LOCK_INVALID", "extensions must be a list", path="lock.extensions")
    seen: set[str] = set()
    normalized: list[Dict[str, Any]] = []
    for index, raw in enumerate(entries):
        entry_path = f"lock.extensions[{index}]"
        entry = _mapping(raw, code="EXTENSION_LOCK_ENTRY_INVALID", path=entry_path)
        _strict_keys(entry, required=_LOCK_ENTRY_REQUIRED, code="EXTENSION_LOCK_ENTRY_INVALID", path=entry_path)
        extension_id = _extension_id(entry["id"], path=entry_path + ".id")
        if extension_id in seen:
            _fail("EXTENSION_LOCK_DUPLICATE", "duplicate extension id", path=entry_path + ".id")
        seen.add(extension_id)
        source = entry["source"]
        if source not in {"workspace", "bundled", "personal"}:
            _fail("EXTENSION_SOURCE_INVALID", "source must be workspace, bundled, or personal", path=entry_path + ".source")
        if not isinstance(entry["enabled"], bool):
            _fail("EXTENSION_LOCK_ENTRY_INVALID", "enabled must be boolean", path=entry_path + ".enabled")
        grants = _string_list(entry["grants"], code="EXTENSION_GRANTS_INVALID", path=entry_path + ".grants")
        for grant in grants:
            if not _CAPABILITY_RE.fullmatch(grant):
                _fail("EXTENSION_GRANTS_INVALID", "invalid grant name", path=entry_path + ".grants")
        normalized.append(
            {
                **dict(entry),
                "id": extension_id,
                "version": _semver(entry["version"], code="EXTENSION_VERSION_INVALID", path=entry_path + ".version"),
                "manifest_path": _relative_path(entry["manifest_path"], code="EXTENSION_MANIFEST_PATH_INVALID", path=entry_path + ".manifest_path"),
                "manifest_sha256": _sha256(entry["manifest_sha256"], code="EXTENSION_MANIFEST_DIGEST_INVALID", path=entry_path + ".manifest_sha256"),
                "package_sha256": _sha256(entry["package_sha256"], code="EXTENSION_PACKAGE_DIGEST_INVALID", path=entry_path + ".package_sha256"),
                "license": _nonempty_string(entry["license"], code="EXTENSION_LICENSE_INVALID", path=entry_path + ".license"),
                "grants": grants,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "extensions": normalized}


def package_digest(artifacts: Sequence[Mapping[str, str]]) -> str:
    """Deterministic digest over normalized artifact paths and verified digests."""

    digest = hashlib.sha256()
    for artifact in sorted(artifacts, key=lambda item: item["path"]):
        rel = _relative_path(artifact["path"], code="EXTENSION_ARTIFACT_PATH_INVALID", path="artifact.path")
        sha = _sha256(artifact["sha256"], code="EXTENSION_ARTIFACT_DIGEST_INVALID", path="artifact.sha256")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha))
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _contained_file(base: Path, rel: str, *, code: str) -> Path:
    base_resolved = base.resolve()
    candidate = base
    for part in PurePosixPath(rel).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            _fail(code, "symlink artifacts are not allowed", path=rel)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        _fail(code, "artifact is missing: " + str(exc), path=rel)
    try:
        common = os.path.commonpath([str(base_resolved), str(resolved)])
    except ValueError:
        common = ""
    if common != str(base_resolved) or not resolved.is_file():
        _fail(code, "artifact escapes package root or is not a file", path=rel)
    return resolved


def verify_manifest_file(
    manifest_path: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
) -> Dict[str, Any]:
    """Load a manifest and verify its own and every declared artifact digest."""

    path = Path(manifest_path)
    if path.is_symlink():
        _fail("EXTENSION_MANIFEST_PATH_INVALID", "manifest symlinks are not allowed", path=str(path))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("EXTENSION_MANIFEST_READ_FAILED", "cannot read manifest: " + str(exc), path=str(path))
    manifest_sha = _sha256_bytes(raw)
    if expected_sha256 is not None and manifest_sha != _sha256(
        expected_sha256, code="EXTENSION_MANIFEST_DIGEST_INVALID", path=str(path)
    ):
        _fail("EXTENSION_MANIFEST_DIGEST_MISMATCH", "manifest digest does not match lock", path=str(path))
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("EXTENSION_MANIFEST_JSON_INVALID", "manifest must be one UTF-8 JSON object", path=str(path))
    manifest = validate_manifest(parsed)
    _assert_api_compatible(manifest)
    root = path.parent
    verified_artifacts: list[Dict[str, Any]] = []
    for artifact in manifest["artifacts"]:
        artifact_path = _contained_file(root, artifact["path"], code="EXTENSION_ARTIFACT_PATH_INVALID")
        actual = _sha256_bytes(artifact_path.read_bytes())
        if actual != artifact["sha256"]:
            _fail("EXTENSION_ARTIFACT_DIGEST_MISMATCH", "artifact digest does not match manifest", path=artifact["path"])
        verified_artifacts.append({**artifact, "absolute_path": str(artifact_path)})
    actual_package = package_digest(manifest["artifacts"])
    if actual_package != manifest["package_sha256"]:
        _fail("EXTENSION_PACKAGE_DIGEST_MISMATCH", "package digest does not match manifest", path=str(path))
    return {
        "ok": True,
        "manifest": manifest,
        "manifest_path": str(path.resolve()),
        "manifest_sha256": manifest_sha,
        "package_sha256": actual_package,
        "artifacts": verified_artifacts,
    }


def verify_lock_entry(
    root: str | os.PathLike[str],
    entry: Mapping[str, Any],
) -> Dict[str, Any]:
    """Verify one already-validated explicit lock entry against local files."""

    normalized_lock = validate_lock({"schema_version": SCHEMA_VERSION, "extensions": [dict(entry)]})
    lock_entry = normalized_lock["extensions"][0]
    manifest_rel = lock_entry["manifest_path"]
    manifest_path = _contained_file(Path(root), manifest_rel, code="EXTENSION_MANIFEST_PATH_INVALID")
    verified = verify_manifest_file(manifest_path, expected_sha256=lock_entry["manifest_sha256"])
    manifest = verified["manifest"]
    comparisons = {
        "id": manifest["id"],
        "version": manifest["version"],
        "package_sha256": manifest["package_sha256"],
        "license": manifest["license"],
    }
    for key, actual in comparisons.items():
        if lock_entry[key] != actual:
            _fail("EXTENSION_LOCK_MANIFEST_MISMATCH", key + " differs between lock and manifest", path=manifest_rel)
    undeclared = sorted(set(lock_entry["grants"]) - set(manifest["capabilities"]))
    if undeclared:
        _fail("EXTENSION_GRANT_UNREQUESTED", "lock grants capabilities not requested by manifest", path=manifest_rel)
    return {**verified, "lock_entry": lock_entry}
