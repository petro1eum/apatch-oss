"""Durable Avatar runtime configuration stays public, strict and fail-closed."""
from __future__ import annotations

import json

import pytest

from apatch.avatar_runtime_config import (
    AvatarRuntimeConfigError,
    load_avatar_sync_config,
    load_avatar_trust_policy,
)


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def test_sync_config_contains_only_public_broker_pointer(tmp_path):
    path = tmp_path / "sync.json"
    _write(path, {
        "schema_version": 1,
        "tracker_base_url": "http://127.0.0.1:8040/",
        "secret_binding": {
            "binding_id": "e3daf724-6d63-4d93-a99b-15fa713f23f8",
            "resolver_path": "/Applications/TrustChain Secrets.app/Contents/MacOS/TrustChainSecretsResolver",
            "service_id": "apatch-avatar-sync",
            "purpose": "avatar-evidence-sync",
            "secret_name": "trustchain-agent.hc-tracker-service-token",
        },
    })

    config = load_avatar_sync_config({"APATCH_AVATAR_SYNC_CONFIG": str(path)})

    assert config["tracker_base_url"] == "http://127.0.0.1:8040"
    assert "service_token" not in config
    assert "value" not in config["secret_binding"]


def test_sync_config_rejects_unknown_fields_and_writable_file(tmp_path):
    path = tmp_path / "sync.json"
    value = {
        "schema_version": 1,
        "tracker_base_url": "http://tracker:8040",
        "secret_binding": {},
        "service_token": "must-never-be-stored",
    }
    _write(path, value)
    with pytest.raises(AvatarRuntimeConfigError, match="invalid"):
        load_avatar_sync_config({"APATCH_AVATAR_SYNC_CONFIG": str(path)})
    path.chmod(0o666)
    with pytest.raises(AvatarRuntimeConfigError, match="permissions"):
        load_avatar_sync_config({"APATCH_AVATAR_SYNC_CONFIG": str(path)})


def test_trust_policy_is_strict_public_governance(tmp_path):
    path = tmp_path / "trust.json"
    _write(path, {
        "schema_version": 1,
        "taxonomy_public_keys": ["tracker-public-key"],
        "outcome_issuers": [{
            "public_key": "counterparty-public-key",
            "organization_id": "company-1",
            "roles": ["work_counterparty"],
        }],
    })

    policy = load_avatar_trust_policy({"APATCH_AVATAR_TRUST_POLICY": str(path)})

    assert policy["taxonomy_public_keys"] == ["tracker-public-key"]
    assert policy["outcome_issuers"][0]["organization_id"] == "company-1"


def test_missing_configs_preserve_offline_mode(tmp_path):
    assert load_avatar_sync_config({
        "APATCH_AVATAR_SYNC_CONFIG": str(tmp_path / "missing-sync.json")
    }) == {}
    assert load_avatar_trust_policy({
        "APATCH_AVATAR_TRUST_POLICY": str(tmp_path / "missing-trust.json")
    }) == {}


def test_delivery_consumers_use_durable_trust_policy(monkeypatch, tmp_path):
    from apatch.outcome_delivery import trusted_outcome_issuers
    from apatch.taxonomy_delivery import trusted_taxonomy_public_keys

    path = tmp_path / "trust.json"
    _write(path, {
        "schema_version": 1,
        "taxonomy_public_keys": ["tracker-public-key"],
        "outcome_issuers": [{
            "public_key": "counterparty-public-key",
            "organization_id": "company-1",
            "roles": ["client_counterparty"],
        }],
    })
    monkeypatch.setenv("APATCH_AVATAR_TRUST_POLICY", str(path))
    monkeypatch.delenv("APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS", raising=False)
    monkeypatch.delenv("APATCH_TRUSTED_OUTCOME_ISSUERS", raising=False)

    assert trusted_taxonomy_public_keys() == ["tracker-public-key"]
    issuers = trusted_outcome_issuers()
    assert issuers[0]["organization_id"] == "company-1"
    assert issuers[0]["roles"] == {"client_counterparty"}
