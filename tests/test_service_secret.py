"""Purpose-bound TrustChain Secrets resolution for Avatar sync."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from apatch.service_secret import (
    PURPOSE,
    SECRET_NAME,
    SERVICE_ID,
    ServiceSecretResolutionError,
    resolve_hc_tracker_token,
)


def test_explicit_token_never_invokes_resolver():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("resolver must not run")

    assert resolve_hc_tracker_token("direct-token", runner=forbidden) == "direct-token"


def test_empty_broker_config_preserves_offline_queue_mode():
    assert resolve_hc_tracker_token(
        environment={"APATCH_AVATAR_SYNC_CONFIG": "/nonexistent/avatar-sync.json"}
    ) == ""


def test_durable_broker_config_invokes_exact_contract(tmp_path):
    resolver = tmp_path / "resolver"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver.chmod(0o700)
    config = tmp_path / "avatar-sync.json"
    config.write_text(json.dumps({
        "schema_version": 1,
        "tracker_base_url": "http://tracker:8040",
        "secret_binding": {
            "binding_id": "e3daf724-6d63-4d93-a99b-15fa713f23f8",
            "resolver_path": str(resolver),
            "service_id": SERVICE_ID,
            "purpose": PURPOSE,
            "secret_name": SECRET_NAME,
        },
    }), encoding="utf-8")
    config.chmod(0o600)
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"tracker-token", stderr=b"")

    assert resolve_hc_tracker_token(
        environment={"APATCH_AVATAR_SYNC_CONFIG": str(config)}, runner=runner
    ) == "tracker-token"
    assert calls == [[
        str(resolver),
        "e3daf724-6d63-4d93-a99b-15fa713f23f8",
        SERVICE_ID,
        PURPOSE,
        SECRET_NAME,
    ]]


def test_resolver_is_exact_argv_without_shell(tmp_path):
    resolver = tmp_path / "resolver"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver.chmod(0o700)
    binding_id = "e3daf724-6d63-4d93-a99b-15fa713f23f8"
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"tracker-token", stderr=b"")

    token = resolve_hc_tracker_token(
        environment={
            "APATCH_HC_SECRET_BINDING_ID": binding_id,
            "APATCH_HC_SECRET_RESOLVER_PATH": str(resolver),
        },
        runner=runner,
    )

    assert token == "tracker-token"
    assert calls[0][0] == [
        str(resolver), binding_id, SERVICE_ID, PURPOSE, SECRET_NAME
    ]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["stdout"] is subprocess.PIPE
    assert calls[0][1]["stderr"] is subprocess.PIPE


def test_resolution_failure_never_echoes_resolver_output(tmp_path):
    resolver = tmp_path / "resolver"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver.chmod(0o700)

    def runner(_argv, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=b"must-not-leak",
            stderr=b"also-must-not-leak",
        )

    with pytest.raises(ServiceSecretResolutionError) as raised:
        resolve_hc_tracker_token(
            environment={
                "APATCH_HC_SECRET_BINDING_ID":
                    "e3daf724-6d63-4d93-a99b-15fa713f23f8",
                "APATCH_HC_SECRET_RESOLVER_PATH": str(resolver),
            },
            runner=runner,
        )
    message = str(raised.value)
    assert "must-not-leak" not in message
    assert "also-must-not-leak" not in message


def test_timeout_drops_resolver_argv_from_exception_chain(tmp_path):
    resolver = tmp_path / "resolver"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver.chmod(0o700)

    def runner(argv, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv + ["must-not-escape"],
            timeout=60,
        )

    with pytest.raises(ServiceSecretResolutionError) as raised:
        resolve_hc_tracker_token(
            environment={
                "APATCH_HC_SECRET_BINDING_ID":
                    "e3daf724-6d63-4d93-a99b-15fa713f23f8",
                "APATCH_HC_SECRET_RESOLVER_PATH": str(resolver),
            },
            runner=runner,
        )

    assert str(raised.value) == "tracker_secret_resolution_failed"
    assert raised.value.__cause__ is None
    assert "must-not-escape" not in repr(raised.value)


def test_partial_or_invalid_binding_fails_closed(tmp_path):
    resolver = tmp_path / "resolver"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver.chmod(0o700)

    with pytest.raises(ServiceSecretResolutionError):
        resolve_hc_tracker_token(
            environment={"APATCH_HC_SECRET_BINDING_ID": "not-a-uuid"}
        )
    with pytest.raises(ServiceSecretResolutionError):
        resolve_hc_tracker_token(
            environment={
                "APATCH_HC_SECRET_BINDING_ID": "not-a-uuid",
                "APATCH_HC_SECRET_RESOLVER_PATH": str(resolver),
            }
        )
