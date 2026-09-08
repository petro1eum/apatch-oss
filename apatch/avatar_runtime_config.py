"""Durable, non-secret runtime configuration for Avatar synchronization."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


SYNC_SCHEMA_VERSION = 1
TRUST_POLICY_SCHEMA_VERSION = 1


class AvatarRuntimeConfigError(ValueError):
    """A local Avatar configuration is malformed or unsafe to consume."""


def default_sync_config_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    env = environment if environment is not None else os.environ
    override = str(env.get("APATCH_AVATAR_SYNC_CONFIG", "")).strip()
    return Path(override).expanduser() if override else Path.home() / ".trustchain" / "avatar_sync.json"


def default_trust_policy_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    env = environment if environment is not None else os.environ
    override = str(env.get("APATCH_AVATAR_TRUST_POLICY", "")).strip()
    return Path(override).expanduser() if override else Path.home() / ".trustchain" / "avatar_trust_policy.json"


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o022:
            raise AvatarRuntimeConfigError(f"{label}_permissions_invalid")
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except AvatarRuntimeConfigError:
        raise
    except (OSError, json.JSONDecodeError):
        raise AvatarRuntimeConfigError(f"{label}_invalid") from None
    if not isinstance(value, dict):
        raise AvatarRuntimeConfigError(f"{label}_invalid")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise AvatarRuntimeConfigError(f"{label}_invalid")


def load_avatar_sync_config(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load the Tracker URL and purpose-bound broker pointer, never a secret."""
    value = _read_object(default_sync_config_path(environment), label="avatar_sync_config")
    if not value:
        return {}
    _exact_keys(
        value,
        {"schema_version", "tracker_base_url", "secret_binding"},
        label="avatar_sync_config",
    )
    if value.get("schema_version") != SYNC_SCHEMA_VERSION:
        raise AvatarRuntimeConfigError("avatar_sync_config_version_unsupported")
    base_url = str(value.get("tracker_base_url") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise AvatarRuntimeConfigError("avatar_sync_config_url_invalid")
    binding = value.get("secret_binding")
    if not isinstance(binding, dict):
        raise AvatarRuntimeConfigError("avatar_sync_config_binding_invalid")
    _exact_keys(
        binding,
        {"binding_id", "resolver_path", "service_id", "purpose", "secret_name"},
        label="avatar_sync_config_binding",
    )
    normalized = {
        key: str(binding.get(key) or "").strip()
        for key in ("binding_id", "resolver_path", "service_id", "purpose", "secret_name")
    }
    if not all(normalized.values()):
        raise AvatarRuntimeConfigError("avatar_sync_config_binding_invalid")
    return {
        "schema_version": SYNC_SCHEMA_VERSION,
        "tracker_base_url": base_url,
        "secret_binding": normalized,
    }


def load_avatar_trust_policy(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load public trust anchors for Tracker decisions and counterparty outcomes."""
    value = _read_object(
        default_trust_policy_path(environment),
        label="avatar_trust_policy",
    )
    if not value:
        return {}
    _exact_keys(
        value,
        {"schema_version", "taxonomy_public_keys", "outcome_issuers"},
        label="avatar_trust_policy",
    )
    if value.get("schema_version") != TRUST_POLICY_SCHEMA_VERSION:
        raise AvatarRuntimeConfigError("avatar_trust_policy_version_unsupported")
    keys = value.get("taxonomy_public_keys")
    issuers = value.get("outcome_issuers")
    if not isinstance(keys, list) or not isinstance(issuers, list):
        raise AvatarRuntimeConfigError("avatar_trust_policy_invalid")
    normalized_keys = [str(key).strip() for key in keys]
    if any(not key for key in normalized_keys) or len(set(normalized_keys)) != len(normalized_keys):
        raise AvatarRuntimeConfigError("avatar_trust_policy_invalid")
    normalized_issuers: list[dict[str, Any]] = []
    for issuer in issuers:
        if not isinstance(issuer, dict):
            raise AvatarRuntimeConfigError("avatar_trust_policy_invalid")
        _exact_keys(
            issuer,
            {"public_key", "organization_id", "roles"},
            label="avatar_trust_policy",
        )
        roles = issuer.get("roles")
        if not isinstance(roles, list):
            raise AvatarRuntimeConfigError("avatar_trust_policy_invalid")
        normalized = {
            "public_key": str(issuer.get("public_key") or "").strip(),
            "organization_id": str(issuer.get("organization_id") or "").strip(),
            "roles": [str(role).strip() for role in roles],
        }
        if (
            not normalized["public_key"]
            or not normalized["organization_id"]
            or not normalized["roles"]
            or any(not role for role in normalized["roles"])
        ):
            raise AvatarRuntimeConfigError("avatar_trust_policy_invalid")
        normalized_issuers.append(normalized)
    return {
        "schema_version": TRUST_POLICY_SCHEMA_VERSION,
        "taxonomy_public_keys": normalized_keys,
        "outcome_issuers": normalized_issuers,
    }
