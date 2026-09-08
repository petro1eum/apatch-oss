"""Durable, idempotent delivery of Avatar evidence to HC Tracker."""
from __future__ import annotations

import copy
import json
import sys
from types import SimpleNamespace


def _bundle(bundle_id="a" * 40, generated_at="2026-07-12T12:00:00+00:00"):
    return {
        "schema_version": 2,
        "kind": "capability_evidence",
        "bundle_id": bundle_id,
        "avatar_id": "subject-key",
        "source": "apatch",
        "trust_level": "attested",
        "generated_at": generated_at,
        "evidence_scope": {"event_count": 2},
        "episodes": [{"episode_id": "episode-1"}],
        "capability_estimates": [{"estimate_id": "estimate-1"}],
        "exclusions": {"count": 0, "reasons": {}},
        "signature": {"value": "signed"},
    }


class _Response:
    status_code = 201

    def json(self):
        return {"accepted": True, "stored": True, "duplicate": False}


class _Client:
    def __init__(self):
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return _Response()


class _PlatformResponse:
    status_code = 200

    def json(self):
        return {
            "accepted": True,
            "status": "ready",
            "stored": True,
            "duplicate": False,
            "signature_verified": True,
        }


class _PlatformClient:
    def __init__(self):
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return _PlatformResponse()


def test_queue_deduplicates_same_semantic_snapshot(monkeypatch, tmp_path):
    from apatch.avatar_delivery import queue_current_evidence

    first = _bundle()
    second = copy.deepcopy(first)
    second["bundle_id"] = "b" * 40
    second["generated_at"] = "2026-07-12T12:01:00+00:00"
    second["signature"] = {"value": "different-signature"}
    bundles = iter((first, second))
    monkeypatch.setattr(
        "apatch.avatar_evidence.build_evidence_bundle",
        lambda *_a, **_k: next(bundles),
    )

    created = queue_current_evidence(".", outbox_dir=str(tmp_path))
    duplicate = queue_current_evidence(".", outbox_dir=str(tmp_path))

    assert created["queued"] is True
    assert duplicate["queued"] is False
    assert duplicate["duplicate_snapshot"] is True
    assert duplicate["bundle_id"] == first["bundle_id"]


def test_compilation_summary_separates_classification_acceptance_and_capability():
    from apatch.avatar_delivery import _compilation_summary

    bundle = _bundle()
    bundle["episodes"] = [{
        "episode_id": "episode-classified",
        "task": {"taxonomy_refs": [
            "soc:15-1252",
            "onet-task:10001",
            "onet-skill:2.B.3.e",
        ]},
        "review_package": {"status": "incomplete"},
        "outcome": {"basis": "technical_gate"},
        "eligible_for_capability": False,
    }, {
        "episode_id": "episode-accepted",
        "task": {"taxonomy_refs": ["apatch-spec:spec-avatar"]},
        "review_package": {"status": "ready"},
        "outcome": {"basis": "external_acceptance"},
        "eligible_for_capability": True,
    }]
    bundle["capability_estimates"] = [{
        "estimate_id": "estimate-internal",
        "taxonomy_refs": ["apatch-spec:spec-avatar"],
    }]

    summary = _compilation_summary(bundle)

    assert summary["detailed_taxonomy_episode_count"] == 1
    assert summary["counterparty_accepted_episode_count"] == 1
    assert summary["eligible_episode_count"] == 1
    assert summary["capability_count"] == 1
    assert summary["market_taxonomy_capability_count"] == 0
    assert summary["next_actions"] == [{
        "code": "prepare_public_review_package",
        "affected_count": 1,
    }, {
        "code": "classify_accepted_work_with_market_taxonomy",
        "affected_count": 1,
    }]


def test_delivery_persists_ack_and_does_not_resend(monkeypatch, tmp_path):
    from apatch.avatar_delivery import (
        deliver_pending_evidence,
        queue_current_evidence,
    )

    monkeypatch.setattr(
        "apatch.avatar_evidence.build_evidence_bundle",
        lambda *_a, **_k: _bundle(),
    )
    queue_current_evidence(".", outbox_dir=str(tmp_path))
    client = _Client()

    first = deliver_pending_evidence(
        base_url="http://tracker:8000",
        service_token="secret",
        outbox_dir=str(tmp_path),
        http_client=client,
    )
    second = deliver_pending_evidence(
        base_url="http://tracker:8000",
        service_token="secret",
        outbox_dir=str(tmp_path),
        http_client=client,
    )

    assert first == {
        "ok": True,
        "status": "delivered",
        "delivered": 1,
        "pending": 0,
        "errors": [],
    }
    assert second["delivered"] == 0
    assert len(client.calls) == 1
    assert client.calls[0][0].endswith("/api/v1/internal/capability-evidence")
    assert client.calls[0][2] == {"X-Service-Token": "secret"}


def test_offline_delivery_keeps_evidence_pending(monkeypatch, tmp_path):
    from apatch.avatar_delivery import (
        deliver_pending_evidence,
        queue_current_evidence,
    )

    monkeypatch.setattr(
        "apatch.avatar_evidence.build_evidence_bundle",
        lambda *_a, **_k: _bundle(),
    )
    queue_current_evidence(".", outbox_dir=str(tmp_path))

    result = deliver_pending_evidence(base_url="", outbox_dir=str(tmp_path))

    assert result["ok"] is True
    assert result["status"] == "queued_offline"
    assert result["pending"] == 1


def test_operational_sync_status_does_not_call_offline_queue_complete():
    from apatch.avatar_delivery import _with_operational_status

    result = _with_operational_status({
        "ok": True,
        "taxonomy": {"ok": True, "status": "tracker_unconfigured"},
        "outcomes": {"ok": True, "status": "tracker_unconfigured"},
        "evidence": {
            "ok": True,
            "delivery": {"ok": True, "status": "queued_offline", "pending": 7},
        },
        "contributions": {
            "ok": True,
            "delivery": {"ok": True, "status": "queued_offline", "pending": 11},
        },
    }, configured=False)

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["status"] == "tracker_unconfigured"
    assert result["pending_outbox_items"] == 18
    assert result["action_required"] == [
        "configure_tracker_or_trustchain_avatar"
    ]


def test_avatar_sync_runtime_check_fails_before_partial_contract_use(monkeypatch):
    from apatch.avatar_delivery import avatar_runtime_compatibility

    monkeypatch.setitem(
        sys.modules,
        "avatar_contract",
        SimpleNamespace(
            __file__="/tmp/stale/avatar_contract/__init__.py",
            ContributionEvent=object,
            TaxonomyDecision=object,
        ),
    )
    monkeypatch.setattr(
        "apatch.avatar_delivery.metadata.version",
        lambda _name: "0.4.0",
    )

    result = avatar_runtime_compatibility()

    assert result["ok"] is False
    assert result["status"] == "dependency_incompatible"
    assert result["installed_version"] == "0.4.0"
    assert result["module_path"].endswith("stale/avatar_contract/__init__.py")
    assert result["python_executable"]
    assert "OutcomeAttestation" in result["missing_symbols"]
    assert result["action_required"] == ["upgrade_avatar_contract"]
    assert result["restart_required"] is True


def test_avatar_runtime_reports_logical_venv_interpreter(monkeypatch, tmp_path):
    from apatch.avatar_delivery import (
        AVATAR_RUNTIME_REQUIRED_SYMBOLS,
        avatar_runtime_compatibility,
    )

    resolved = tmp_path / "python3.14"
    resolved.write_text("", encoding="utf-8")
    logical = tmp_path / "venv" / "bin" / "python"
    logical.parent.mkdir(parents=True)
    logical.symlink_to(resolved)

    contract = SimpleNamespace(__file__="/tmp/avatar_contract/__init__.py")
    for name in AVATAR_RUNTIME_REQUIRED_SYMBOLS:
        setattr(contract, name, object())
    monkeypatch.setitem(sys.modules, "avatar_contract", contract)
    monkeypatch.setattr(sys, "executable", str(logical))
    monkeypatch.setattr(
        "apatch.avatar_delivery.metadata.version",
        lambda _name: "0.6.0",
    )

    result = avatar_runtime_compatibility()

    assert result["ok"] is True
    assert result["python_executable"] == str(logical)
    assert result["python_executable_realpath"] == str(resolved)


def test_platform_delivery_uses_owner_token_and_persists_ack(monkeypatch, tmp_path):
    from apatch.avatar_delivery import (
        deliver_pending_evidence_to_platform,
        queue_current_evidence,
    )

    monkeypatch.setattr(
        "apatch.avatar_evidence.build_evidence_bundle",
        lambda *_a, **_k: _bundle(),
    )
    queue_current_evidence(".", outbox_dir=str(tmp_path))
    client = _PlatformClient()

    first = deliver_pending_evidence_to_platform(
        platform_url="https://trust-chain.ai",
        avatar_token="tcav-owner-token",
        outbox_dir=str(tmp_path),
        http_client=client,
    )
    second = deliver_pending_evidence_to_platform(
        platform_url="https://trust-chain.ai",
        avatar_token="tcav-owner-token",
        outbox_dir=str(tmp_path),
        http_client=client,
    )

    assert first["ok"] is True
    assert first["delivered"] == 1
    assert first["pending"] == 0
    assert second["delivered"] == 0
    assert client.calls[0][0] == "https://trust-chain.ai/api/avatar/evidence/upload"
    assert client.calls[0][1]["evidence"]["bundle_id"] == "a" * 40
    assert client.calls[0][2]["Authorization"] == "Bearer tcav-owner-token"


def test_direct_sync_resolves_broker_token_once(monkeypatch):
    import apatch.avatar_delivery as delivery
    import apatch.contribution as contribution
    import apatch.contribution_delivery as contribution_delivery
    import apatch.outcome_delivery as outcome_delivery
    import apatch.service_secret as service_secret
    import apatch.taxonomy_delivery as taxonomy_delivery

    observed = []
    monkeypatch.setattr(
        delivery,
        "tracker_config_from_env",
        lambda: {
            "platform_url": "",
            "avatar_token": "",
            "base_url": "http://tracker:8040",
            "service_token": "",
        },
    )
    monkeypatch.setattr(
        service_secret,
        "resolve_hc_tracker_token",
        lambda token: observed.append(("resolve", token)) or "broker-token",
    )
    monkeypatch.setattr(
        contribution,
        "resolve_identity",
        lambda _target: {"key_id": "avatar-key-1"},
    )
    monkeypatch.setattr(
        taxonomy_delivery,
        "pull_taxonomy_decisions",
        lambda **kwargs: observed.append(("taxonomy", kwargs["service_token"])) or {
            "ok": True, "status": "pulled", "received": 0, "stored": 0,
            "duplicates": 0, "errors": [],
        },
    )
    monkeypatch.setattr(
        outcome_delivery,
        "pull_outcome_attestations",
        lambda **kwargs: observed.append(("outcomes", kwargs["service_token"])) or {
            "ok": True, "status": "pulled", "received": 0, "stored": 0,
            "duplicates": 0, "errors": [],
        },
    )
    monkeypatch.setattr(
        delivery,
        "sync_avatar_evidence",
        lambda *_args, **kwargs: observed.append(
            ("evidence", kwargs["service_token"])
        ) or {
            "ok": True,
            "queue": {"avatar_id": "avatar-key-1"},
            "delivery": {"ok": True, "delivered": 1, "pending": 0, "errors": []},
        },
    )
    monkeypatch.setattr(
        contribution_delivery,
        "sync_contributions",
        lambda **kwargs: observed.append(
            ("contributions", kwargs["service_token"])
        ) or {
            "ok": True,
            "delivery": {"ok": True, "delivered": 1, "pending": 0, "errors": []},
        },
    )

    result = delivery.sync_avatar_state(".")

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["status"] == "synchronized"
    assert result["transport"] == "hc_tracker_internal"
    assert observed == [
        ("resolve", ""),
        ("taxonomy", "broker-token"),
        ("outcomes", "broker-token"),
        ("evidence", "broker-token"),
        ("contributions", "broker-token"),
    ]


def test_tracker_url_survives_restart_without_environment(monkeypatch, tmp_path):
    import apatch.avatar_delivery as delivery

    config = tmp_path / "avatar-sync.json"
    config.write_text(json.dumps({
        "schema_version": 1,
        "tracker_base_url": "http://127.0.0.1:8040",
        "secret_binding": {
            "binding_id": "e3daf724-6d63-4d93-a99b-15fa713f23f8",
            "resolver_path": "/Applications/TrustChain Secrets.app/Contents/MacOS/TrustChainSecretsResolver",
            "service_id": "apatch-avatar-sync",
            "purpose": "avatar-evidence-sync",
            "secret_name": "trustchain-agent.hc-tracker-service-token",
        },
    }), encoding="utf-8")
    config.chmod(0o600)
    monkeypatch.setenv("APATCH_AVATAR_SYNC_CONFIG", str(config))
    monkeypatch.delenv("APATCH_HC_TRACKER_URL", raising=False)
    monkeypatch.delenv("APATCH_HC_SERVICE_TOKEN", raising=False)

    resolved = delivery.tracker_config_from_env()

    assert resolved["base_url"] == "http://127.0.0.1:8040"
    assert resolved["service_token"] == ""


def test_invalid_durable_config_fails_before_sync_and_preserves_outbox(
    monkeypatch,
    tmp_path,
):
    import apatch.avatar_delivery as delivery

    config = tmp_path / "avatar-sync.json"
    config.write_text('{"schema_version":1,"service_token":"forbidden"}', encoding="utf-8")
    config.chmod(0o600)
    monkeypatch.setenv("APATCH_AVATAR_SYNC_CONFIG", str(config))

    assert delivery.sync_avatar_state(".") == {
        "ok": False,
        "status": "configuration_invalid",
        "error_code": "avatar_sync_config_invalid",
        "retryable": False,
        "outbox_preserved": True,
    }


def test_direct_sync_returns_sanitized_credential_failure(monkeypatch):
    import apatch.avatar_delivery as delivery
    import apatch.service_secret as service_secret

    monkeypatch.setattr(
        delivery,
        "tracker_config_from_env",
        lambda: {
            "platform_url": "",
            "avatar_token": "",
            "base_url": "http://tracker:8040",
            "service_token": "",
        },
    )

    def fail_resolution(_token):
        raise service_secret.ServiceSecretResolutionError(
            "tracker_secret_resolution_failed"
        )

    monkeypatch.setattr(
        service_secret,
        "resolve_hc_tracker_token",
        fail_resolution,
    )

    result = delivery.sync_avatar_state(".")

    assert result == {
        "ok": False,
        "status": "credential_unavailable",
        "error_code": "tracker_secret_resolution_failed",
        "retryable": True,
        "outbox_preserved": True,
    }


def test_avatar_sync_cli_credential_failure_is_sanitized(
    monkeypatch, tmp_path
):
    import apatch.avatar_delivery as delivery
    from apatch.cli import cli
    from click.testing import CliRunner

    failure = {
        "ok": False,
        "status": "credential_unavailable",
        "error_code": "tracker_secret_resolution_failed",
        "retryable": True,
        "outbox_preserved": True,
    }
    monkeypatch.setattr(
        delivery,
        "sync_avatar_state",
        lambda *_args, **_kwargs: failure,
    )

    result = CliRunner().invoke(
        cli,
        ["avatar", "sync", "--json", "--target-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == failure
    assert "Traceback" not in result.output
    assert "TrustChainSecretsResolver" not in result.output


def test_owner_sync_pulls_outcomes_before_contributions_and_evidence(monkeypatch):
    import apatch.avatar_delivery as delivery
    import apatch.contribution as contribution
    import apatch.contribution_export as contribution_export
    import apatch.outcome_delivery as outcome_delivery
    import apatch.taxonomy_delivery as taxonomy_delivery

    calls = []
    monkeypatch.setattr(
        delivery,
        "tracker_config_from_env",
        lambda: {
            "platform_url": "https://trust-chain.ai",
            "avatar_token": "tcav-token",
            "base_url": "",
            "service_token": "",
        },
    )
    monkeypatch.setattr(
        contribution,
        "resolve_identity",
        lambda _target: {"key_id": "avatar-key-1"},
    )
    monkeypatch.setattr(
        taxonomy_delivery,
        "pull_taxonomy_decisions_from_platform",
        lambda **_kwargs: calls.append("taxonomy") or {
            "ok": True,
            "status": "pulled",
            "received": 1,
            "stored": 1,
            "duplicates": 0,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        outcome_delivery,
        "pull_outcome_attestations_from_platform",
        lambda **_kwargs: calls.append("outcomes") or {
            "ok": True,
            "status": "pulled",
            "received": 1,
            "stored": 1,
            "duplicates": 0,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        contribution_export,
        "sync_to_trustchain_avatar",
        lambda **_kwargs: calls.append("contributions") or {
            "ok": True,
            "accepted": 2,
            "attempted": 3,
            "quarantined": 1,
            "quarantined_total": 4,
            "pending": 0,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        delivery,
        "sync_avatar_evidence",
        lambda *_args, **_kwargs: calls.append("evidence") or {
            "ok": True,
            "queue": {},
            "delivery": {"ok": True, "delivered": 1, "pending": 0, "errors": []},
        },
    )

    result = delivery.sync_avatar_state(".")

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["status"] == "synchronized"
    assert calls == ["taxonomy", "outcomes", "contributions", "evidence"]
    assert result["contributions"]["delivery"]["status"] == (
        "delivered_with_quarantine"
    )
    assert result["contributions"]["delivery"]["quarantined"] == 1
    assert result["contributions"]["delivery"]["quarantined_total"] == 4
