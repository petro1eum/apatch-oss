"""Purpose-bound attended resolution of the HC Tracker service token.

The resolver is an executable capability, not a generic secret reader. The token
is captured once in process memory and is never written to argv, logs, files, or
Avatar payloads.
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from typing import Any, Mapping, Optional


SERVICE_ID = "apatch-avatar-sync"
PURPOSE = "avatar-evidence-sync"
SECRET_NAME = "trustchain-agent.hc-tracker-service-token"
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


class ServiceSecretResolutionError(RuntimeError):
    """TrustChain Secrets could not issue the purpose-bound Tracker token."""


def resolve_hc_tracker_token(
    explicit_token: Optional[str] = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
    runner: Any = None,
    timeout: float = 60.0,
) -> str:
    """Return an explicit token or resolve it through TrustChain Secrets.

    Empty configuration preserves the existing offline queue behavior. A partial
    or invalid broker configuration fails closed. The resolver is invoked without
    a shell and its stdout is never included in an error.
    """
    env = environment if environment is not None else os.environ
    direct = str(explicit_token or env.get("APATCH_HC_SERVICE_TOKEN", "")).strip()
    if direct:
        return direct

    binding_id = str(env.get("APATCH_HC_SECRET_BINDING_ID", "")).strip()
    resolver_path = str(env.get("APATCH_HC_SECRET_RESOLVER_PATH", "")).strip()
    if not binding_id and not resolver_path:
        from apatch.avatar_runtime_config import (
            AvatarRuntimeConfigError,
            load_avatar_sync_config,
        )

        try:
            config = load_avatar_sync_config(env)
        except AvatarRuntimeConfigError as exc:
            raise ServiceSecretResolutionError(str(exc)) from None
        binding = config.get("secret_binding") or {}
        if binding:
            if any(
                binding.get(field) != expected
                for field, expected in (
                    ("service_id", SERVICE_ID),
                    ("purpose", PURPOSE),
                    ("secret_name", SECRET_NAME),
                )
            ):
                raise ServiceSecretResolutionError("tracker_secret_contract_invalid")
            binding_id = str(binding.get("binding_id") or "").strip()
            resolver_path = str(binding.get("resolver_path") or "").strip()
    if not binding_id and not resolver_path:
        return ""
    if not binding_id or not resolver_path:
        raise ServiceSecretResolutionError("tracker_secret_broker_config_incomplete")
    try:
        uuid.UUID(binding_id)
    except ValueError:
        raise ServiceSecretResolutionError(
            "tracker_secret_binding_invalid"
        ) from None
    if (
        not os.path.isabs(resolver_path)
        or not os.path.isfile(resolver_path)
        or not os.access(resolver_path, os.X_OK)
    ):
        raise ServiceSecretResolutionError("tracker_secret_resolver_unavailable")
    if any(
        _SAFE_LABEL.fullmatch(value) is None
        for value in (SERVICE_ID, PURPOSE, SECRET_NAME)
    ):
        raise ServiceSecretResolutionError("tracker_secret_contract_invalid")

    execute = runner or subprocess.run
    try:
        completed = execute(
            [resolver_path, binding_id, SERVICE_ID, PURPOSE, SECRET_NAME],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ServiceSecretResolutionError(
            "tracker_secret_resolution_failed"
        ) from None
    if int(completed.returncode) != 0:
        raise ServiceSecretResolutionError("tracker_secret_resolution_denied")

    raw = bytes(completed.stdout or b"").strip()
    if not raw or len(raw) > 8192:
        raise ServiceSecretResolutionError("tracker_secret_value_invalid")
    try:
        token = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ServiceSecretResolutionError(
            "tracker_secret_value_invalid"
        ) from None
    if any(character.isspace() for character in token):
        raise ServiceSecretResolutionError("tracker_secret_value_invalid")
    return token
