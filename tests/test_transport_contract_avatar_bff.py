"""The owner lane is pinned to the shared Avatar BFF transport contract.

The bundled ``avatar_contract.transport`` ships ``avatar_bff`` v1: the routes a local apatch
calls on trust-chain.ai with its owner-scoped Avatar token, the order the lane
runs in, and the upload failure statuses that quarantine an event. Each apatch
consumer route is exercised through the real delivery module against a
recording fake BFF that answers with contract-shaped bodies; the URL apatch
builds must be the platform base plus exactly the contract path, with the
contract method.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import pytest

# The transport contract ships inside the avatar-contract package APatch bundles
# (apatch._vendor.avatar_contract, pinned by UPSTREAM.json); it is always present.
from apatch._vendor.avatar_contract.transport import (
    assert_response_shape,
    load_transport_contract,
    route_method,
    route_path,
)

from apatch import avatar_delivery, contribution_export, outcome_delivery, taxonomy_delivery

BASE = "https://trust-chain.ai"
TOKEN = "tcav_owner_sync_token"
AVATAR = "a1" * 16
APATCH_ROUTES = (
    "taxonomy_decisions.pull",
    "outcomes.pull",
    "contributions.upload",
    "contributions.reconcile",
    "evidence.upload",
)


@pytest.fixture(scope="module")
def contract() -> dict:
    return load_transport_contract("avatar_bff")


class _Response:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


class _RecordingBff:
    """A fake Avatar BFF: records every call, answers only contract routes."""

    def __init__(self, contract: dict, avatar_id: str) -> None:
        self.contract = contract
        self.avatar_id = avatar_id
        self.calls: list = []  # (method, url, headers, params_or_body)
        self.present: set = set()

    def _route(self, method: str, url: str) -> str:
        path = urlsplit(url).path
        for route in self.contract["routes"]:
            if route.get("consumer") != "apatch":
                continue
            if route["method"] == method and route["path"] == path:
                return route["id"]
        raise AssertionError(f"apatch called {method} {url}, which is not an apatch route of the contract")

    def _answer(self, route_id: str, body: dict) -> _Response:
        assert_response_shape(self.contract, route_id, body)
        return _Response(body)

    def get(self, url, *, headers=None, params=None):
        self.calls.append(("GET", url, dict(headers or {}), params))
        route_id = self._route("GET", url)
        if route_id == "taxonomy_decisions.pull":
            body = {"available": True, "status": "ready", "avatar_id": self.avatar_id, "decisions": [], "count": 0}
        else:
            body = {"available": True, "status": "ready", "avatar_id": self.avatar_id, "attestations": [], "count": 0}
        return self._answer(route_id, body)

    def post(self, url, *, json=None, headers=None):
        self.calls.append(("POST", url, dict(headers or {}), json))
        route_id = self._route("POST", url)
        if route_id == "contributions.upload":
            event_ids = [str(event["event_id"]) for event in json["events"]]
            self.present.update(event_ids)
            body = {
                "available": True, "status": "ready", "principal_key_id": self.avatar_id,
                "accepted": len(event_ids), "duplicates": 0, "failed": [],
            }
        elif route_id == "contributions.reconcile":
            requested = [str(value) for value in json["event_ids"]]
            body = {
                "available": True, "status": "ready", "avatar_id": json["avatar_id"],
                "present_event_ids": [value for value in requested if value in self.present],
                "missing_event_ids": [value for value in requested if value not in self.present],
                "conflict_event_ids": [],
            }
        else:
            body = {
                "available": True, "status": "ready", "accepted": True,
                "principal_key_id": self.avatar_id, "bundle_id": json["evidence"]["bundle_id"],
                "stored": True, "duplicate": False, "signature_verified": True,
            }
        return self._answer(route_id, body)

    def route_ids(self) -> list:
        return [self._route(method, url) for method, url, _headers, _body in self.calls]


def _write_event(store: str, avatar_id: str, event_id: str) -> None:
    directory = os.path.join(store, avatar_id)
    os.makedirs(directory, exist_ok=True)
    event = {
        "schema_version": 2, "kind": "contribution", "event_id": event_id,
        "idempotency_key": event_id, "avatar_id": avatar_id, "source": "apatch",
        "trust_level": "attested", "identity": {"key_id": avatar_id},
        "project": {"id": "p"}, "session": {"intent": "x"}, "volume": {"ops": 1},
        "proof_ref": {"op_ids": ["op_0"], "head": "op_0"}, "signature": "SIG",
    }
    with open(os.path.join(directory, event_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump(event, fh)


def _bundle() -> dict:
    return {
        "schema_version": 2, "kind": "capability_evidence", "bundle_id": "b" * 40,
        "avatar_id": AVATAR, "source": "apatch", "trust_level": "attested",
        "generated_at": "2026-09-06T12:00:00+00:00", "evidence_scope": {"event_count": 1},
        "episodes": [{"episode_id": "episode-1"}], "capability_estimates": [],
        "exclusions": {"count": 0, "reasons": {}}, "signature": {"value": "signed"},
    }


def _expected(contract: dict, route_id: str) -> tuple:
    return route_method(contract, route_id), BASE + route_path(contract, route_id)


def test_contract_names_exactly_the_apatch_consumer_routes(contract):
    apatch_routes = [route["id"] for route in contract["routes"] if route.get("consumer") == "apatch"]
    assert apatch_routes == list(APATCH_ROUTES)
    for route_id in APATCH_ROUTES:
        assert route_path(contract, route_id).startswith("/api/avatar/")
        assert route_method(contract, route_id) in {"GET", "POST"}
    assert contract["auth"]["apatch"].startswith("Authorization: Bearer <tcav_")


def test_each_apatch_route_is_the_url_the_delivery_module_builds(contract, tmp_path, monkeypatch):
    bff = _RecordingBff(contract, AVATAR)
    platform_url = BASE + "/"  # apatch strips the slash; the contract path is joined exactly

    taxonomy = taxonomy_delivery.pull_taxonomy_decisions_from_platform(
        platform_url=platform_url, avatar_token=TOKEN, avatar_id=AVATAR,
        store_dir=str(tmp_path / "taxonomy"), http_client=bff,
        trusted_public_keys=["pinned-tracker-key"],
    )
    outcomes = outcome_delivery.pull_outcome_attestations_from_platform(
        platform_url=platform_url, avatar_token=TOKEN, avatar_id=AVATAR,
        store_dir=str(tmp_path / "outcomes"), http_client=bff, trusted_issuers=[],
    )
    store = str(tmp_path / "contributions")
    _write_event(store, AVATAR, "ev_1")
    contributions = contribution_export.sync_to_trustchain_avatar(
        platform_url=platform_url, token=TOKEN, store_dir=store,
        receipt_dir=str(tmp_path / "receipts"), avatar_id=AVATAR, http_client=bff,
    )
    monkeypatch.setattr("apatch.avatar_evidence.build_evidence_bundle", lambda *_a, **_k: _bundle())
    outbox = str(tmp_path / "evidence-outbox")
    avatar_delivery.queue_current_evidence(str(tmp_path), outbox_dir=outbox)
    evidence = avatar_delivery.deliver_pending_evidence_to_platform(
        platform_url=platform_url, avatar_token=TOKEN, outbox_dir=outbox, http_client=bff,
    )

    assert taxonomy["ok"] and outcomes["ok"] and contributions["ok"] and evidence["ok"], (
        taxonomy, outcomes, contributions, evidence,
    )
    observed = {}
    for (method, url, headers, params), route_id in zip(bff.calls, bff.route_ids()):
        observed.setdefault(route_id, set()).add((method, url))
        assert headers.get("Authorization") == f"Bearer {TOKEN}", route_id
        if method == "GET":
            assert params is None, f"{route_id} declares no query parameters"
    assert set(observed) == set(APATCH_ROUTES)
    for route_id in APATCH_ROUTES:
        assert observed[route_id] == {_expected(contract, route_id)}, route_id


def test_owner_lane_order_matches_sync_avatar_state(contract, tmp_path, monkeypatch):
    bff = _RecordingBff(contract, AVATAR)
    store = str(tmp_path / "contributions")
    _write_event(store, AVATAR, "ev_lane")
    monkeypatch.setenv("APATCH_CONTRIB_STORE", store)
    monkeypatch.setenv("APATCH_AVATAR_SYNC_RECEIPTS", str(tmp_path / "receipts"))
    monkeypatch.setenv("APATCH_TRUSTED_TAXONOMY_PUBLIC_KEYS", "pinned-tracker-key")
    monkeypatch.setattr(
        avatar_delivery, "tracker_config_from_env",
        lambda: {"platform_url": BASE, "avatar_token": TOKEN, "base_url": "", "service_token": ""},
    )
    monkeypatch.setattr("apatch.contribution.resolve_identity", lambda _target: {"key_id": AVATAR})
    monkeypatch.setattr("apatch.avatar_evidence.build_evidence_bundle", lambda *_a, **_k: _bundle())

    result = avatar_delivery.sync_avatar_state(
        str(tmp_path),
        evidence_outbox_dir=str(tmp_path / "evidence-outbox"),
        outcome_store_dir=str(tmp_path / "outcomes"),
        taxonomy_store_dir=str(tmp_path / "taxonomy"),
        http_client=bff,
    )

    assert result["transport"] == "trustchain_avatar"
    assert result["complete"] is True, result
    executed = bff.route_ids()
    # The literal call sequence: reconciliation brackets the upload (find what
    # to replay, then confirm), so each lane step settles in this order.
    assert executed == [
        "taxonomy_decisions.pull",
        "outcomes.pull",
        "contributions.reconcile",
        "contributions.upload",
        "contributions.reconcile",
        "evidence.upload",
    ]
    settled = sorted(set(executed), key=lambda route_id: len(executed) - 1 - executed[::-1].index(route_id))
    assert settled == contract["owner_lane_order"]
    assert executed[:2] == contract["owner_lane_order"][:2]
    assert executed[-1] == contract["owner_lane_order"][-1]


def test_permanent_rejection_statuses_match_the_contract(contract):
    assert set(contract["permanent_rejection_statuses"]) == contribution_export._PERMANENT_REJECTION_STATUSES


def test_the_client_reads_the_identity_gate_the_contract_declares(contract):
    """apatch's own constants are the contract's identity_gate, or this fails."""
    from apatch.avatar_identity_gate import (
        IDENTITY_GATE_BLOCKING,
        IDENTITY_GATE_RETRYABLE,
        IDENTITY_GATE_STATUSES,
    )

    gate = contract["identity_gate"]
    assert set(IDENTITY_GATE_BLOCKING) == set(gate["http"]["409"])
    assert set(IDENTITY_GATE_RETRYABLE) == set(gate["http"]["503"])
    assert set(IDENTITY_GATE_STATUSES) == set(gate["statuses"])
    assert set(gate["statuses"]) <= set(contract["not_ready_statuses"])
    # every route the owner lane calls is either gated or answers not-ready itself
    for route_id in APATCH_ROUTES:
        assert route_id in gate["gated_routes"] or route_id in gate["answering_routes"]
