from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import governed_work_mcp as M
from apatch import governed_work_transport as T


class Provider:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()

    def get_public_key(self):
        return self.private.public_key().public_bytes_raw()

    def sign(self, payload):
        return self.private.sign(payload)


TOOL_NAMES = {
    "apatch_governed_work_configure",
    "apatch_governed_work_transition_endpoint",
    "apatch_governed_work_prepare_change",
    "apatch_governed_work_store_binding",
    "apatch_governed_work_build_evidence",
    "apatch_governed_work_sync",
    "apatch_governed_work_retire_outbox",
    "apatch_governed_work_status",
}


def _tool(name):
    pytest.importorskip("mcp")
    from apatch.mcp import server as S

    return S.mcp._tool_manager._tools[name].fn


def test_full_catalog_exposes_governed_work_without_expanding_compact():
    pytest.importorskip("mcp")
    from apatch.mcp import server as S
    from apatch.mcp.profiles import PROFILE_COMPACT, allowed_tools

    assert TOOL_NAMES.issubset(S.mcp._tool_manager._tools)
    assert TOOL_NAMES.isdisjoint(PROFILE_COMPACT)
    assert allowed_tools("full") is None
    schemas = {
        name: S.mcp._tool_manager._tools[name].parameters for name in TOOL_NAMES
    }
    for schema in schemas.values():
        assert "service_token" not in schema["properties"]
    configure = schemas["apatch_governed_work_configure"]["properties"]
    assert "service_token_env" not in configure
    assert "service_request_key_id" not in configure


def test_mcp_operations_route_to_the_domain_service(monkeypatch, tmp_path):
    calls = []

    def fake(name):
        def invoke(target_dir, **kwargs):
            calls.append((name, target_dir, kwargs))
            return {"ok": True, "operation": name}
        return invoke

    monkeypatch.setattr(M, "configure_governed_work", fake("configure"))
    monkeypatch.setattr(
        M,
        "transition_governed_work_endpoint",
        fake("transition_endpoint"),
    )
    monkeypatch.setattr(M, "prepare_governed_change", fake("prepare_change"))
    monkeypatch.setattr(M, "store_governed_source_binding", fake("store_binding"))
    monkeypatch.setattr(M, "build_governed_evidence", fake("build_evidence"))
    monkeypatch.setattr(M, "sync_governed_work", fake("sync"))
    monkeypatch.setattr(
        M,
        "retire_governed_work_outbox",
        fake("retire_outbox"),
    )
    monkeypatch.setattr(M, "governed_work_status", fake("status"))

    target = str(tmp_path)
    assert _tool("apatch_governed_work_configure")(
        target_dir=target,
        platform_url="https://platform.example",
        client_id="apatch:test",
        binding_authority_keys={"platform": "public"},
    )["operation"] == "configure"
    assert _tool("apatch_governed_work_transition_endpoint")(
        target_dir=target,
        expected_platform_url="https://keys.trust-chain.ai",
        platform_url="https://clients.trust-chain.ai",
        idempotency_key="prod-surface-transition-20260831",
    )["operation"] == "transition_endpoint"
    assert _tool("apatch_governed_work_prepare_change")(
        target_dir=target,
        tenant_id="tenant",
        project_group_id="group",
        work_program_id="program",
        work_program_hash="hash",
        spec_id="SPEC-X",
        purpose="private",
    )["operation"] == "prepare_change"
    assert _tool("apatch_governed_work_store_binding")(
        target_dir=target,
        binding={"schema": "binding"},
    )["operation"] == "store_binding"
    assert _tool("apatch_governed_work_build_evidence")(
        target_dir=target,
        binding_id="binding",
    )["operation"] == "build_evidence"
    assert _tool("apatch_governed_work_sync")(
        target_dir=target,
    )["operation"] == "sync"
    assert _tool("apatch_governed_work_retire_outbox")(
        target_dir=target,
        entry_id="apgwo_old",
        superseded_by_entry_id="apgwo_new",
    )["operation"] == "retire_outbox"
    assert _tool("apatch_governed_work_status")(
        target_dir=target,
    )["operation"] == "status"
    assert [name for name, _target, _kwargs in calls] == [
        "configure",
        "transition_endpoint",
        "prepare_change",
        "store_binding",
        "build_evidence",
        "sync",
        "retire_outbox",
        "status",
    ]


class Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class Client:
    def __init__(self, body):
        self.body = body
        self.headers = None
        self.params = None

    def get(self, _url, params, headers):
        self.params = params
        self.headers = headers
        return Response(self.body)


def test_status_reads_four_independent_platform_states(monkeypatch, tmp_path):
    group_id = "tcpg_" + "1" * 32
    program_id = "tcwp_" + "2" * 32
    provider = Provider()
    configured = M.configure_governed_work(
        str(tmp_path),
        platform_url="https://platform.example",
        client_id="apatch:test",
        binding_authority_keys={"platform": "public-key"},
        request_key_provider=provider,
    )
    assert configured["public_registration"]["service_request_key_id"] == (
        T.service_request_key_id(provider)
    )
    projection = {
        "schema": "trustchain.governed-work-status.v1",
        "tenant_id": "tenant-a",
        "project_group_id": group_id,
        "work_program_id": program_id,
        "work_release_id": None,
        "collective_acceptance": "accepted",
        "source_verified": "verified",
        "contribution_bound": "unbound",
        "timesheet_accepted": "not_submitted",
        "authority_version": 2,
        "revocation_version": 0,
        "projection_cursor": 7,
    }
    client = Client(projection)
    result = M.governed_work_status(
        str(tmp_path),
        tenant_id="tenant-a",
        project_group_id=group_id,
        work_program_id=program_id,
        refresh_platform=True,
        http_client=client,
        request_key_provider=provider,
    )
    assert result["ok"] is True
    assert result["platform"] == {
        "status": "available",
        "projection": projection,
    }
    states = result["platform"]["projection"]
    assert states["collective_acceptance"] == "accepted"
    assert states["source_verified"] == "verified"
    assert states["contribution_bound"] == "unbound"
    assert states["timesheet_accepted"] == "not_submitted"
    assert "X-Service-Token" not in client.headers
    assert client.headers["X-TC-Service-Key-Id"] == T.service_request_key_id(provider)
    assert client.params == {
        "subject": "apatch:test",
        "tenant_id": "tenant-a",
    }
    assert "signature" not in str(result).casefold()
