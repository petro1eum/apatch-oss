"""trust-chain.ai refuses an owner it cannot resolve; the lane stops and says so.

The Avatar BFF resolves the caller's HC professional on every owner operation
(avatar_bff 1.0.2, block identity_gate): 409 means the person must connect or
reactivate their HC profile, 503 means the site could not answer this time.
Before this, both arrived as an opaque `TrustChain HTTP 409: ...` string and the
outbox was retried forever with nothing to act on.
"""
from __future__ import annotations

import json
import os

import pytest

from apatch import avatar_delivery, contribution_export, outcome_delivery, taxonomy_delivery
from apatch.avatar_identity_gate import (
    IDENTITY_GATE_BLOCKING,
    IDENTITY_GATE_RETRYABLE,
    identity_gate_refusal,
)

BASE = "https://trust-chain.ai"
TOKEN = "tcav_owner_sync_token"
AVATAR = "b2" * 16


class _Response:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _RefusingBff:
    """Every owner route answers the same refusal."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.calls: list = []

    def _answer(self, url: str) -> _Response:
        self.calls.append(url)
        return _Response(self.status_code, {"detail": self.detail})

    def get(self, url, *, headers=None, params=None):
        return self._answer(url)

    def post(self, url, *, json=None, headers=None):
        return self._answer(url)


def _event(store: str, event_id: str) -> None:
    directory = os.path.join(store, AVATAR)
    os.makedirs(directory, exist_ok=True)
    event = {
        "schema_version": 2, "kind": "contribution", "event_id": event_id,
        "idempotency_key": event_id, "avatar_id": AVATAR, "source": "apatch",
        "trust_level": "attested", "identity": {"key_id": AVATAR},
        "project": {"id": "p"}, "session": {"intent": "x"}, "volume": {"ops": 1},
        "proof_ref": {"op_ids": ["op_0"], "head": "op_0"}, "signature": "SIG",
    }
    with open(os.path.join(directory, event_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump(event, fh)


@pytest.mark.parametrize("status", IDENTITY_GATE_BLOCKING)
def test_a_refused_owner_is_read_as_blocking(status):
    refusal = identity_gate_refusal(_Response(409, {"detail": status}))
    assert refusal == {"status": status, "retryable": False, "status_code": 409}


@pytest.mark.parametrize("status", IDENTITY_GATE_RETRYABLE)
def test_an_unresolved_owner_is_read_as_retryable(status):
    refusal = identity_gate_refusal(_Response(503, {"detail": status}))
    assert refusal == {"status": status, "retryable": True, "status_code": 503}


@pytest.mark.parametrize("response", [
    _Response(409, {"detail": "review_conflict"}),      # the same code, another meaning
    _Response(409, {"status": "hc_identity_unbound"}),  # not the detail field
    _Response(503, {"detail": "hc_identity_unbound"}),  # blocking status, wrong code
    _Response(409, None),                              # no body at all
    _Response(200, {"status": "ready"}),
    _Response(409, ValueError("not json")),
])
def test_nothing_else_is_mistaken_for_the_gate(response):
    assert identity_gate_refusal(response) is None


def test_the_pulls_report_the_refusal_instead_of_a_generic_rejection(tmp_path):
    bff = _RefusingBff(409, "hc_identity_unbound")
    taxonomy = taxonomy_delivery.pull_taxonomy_decisions_from_platform(
        platform_url=BASE, avatar_token=TOKEN, avatar_id=AVATAR,
        store_dir=str(tmp_path / "taxonomy"), http_client=bff,
        trusted_public_keys=["pinned-tracker-key"],
    )
    outcomes = outcome_delivery.pull_outcome_attestations_from_platform(
        platform_url=BASE, avatar_token=TOKEN, avatar_id=AVATAR,
        store_dir=str(tmp_path / "outcomes"), http_client=bff, trusted_issuers=[],
    )
    for result in (taxonomy, outcomes):
        assert result["ok"] is False
        assert result["status"] == "hc_identity_unbound"
        assert result["retryable"] is False
        assert result["errors"][0]["action_required"] == "connect_hc_profile"
        assert result["stored"] == 0


def test_the_upload_stops_and_keeps_every_event(tmp_path):
    store = str(tmp_path / "contributions")
    _event(store, "ev_1")
    _event(store, "ev_2")
    bff = _RefusingBff(409, "hc_identity_inactive")
    result = contribution_export.sync_to_trustchain_avatar(
        platform_url=BASE, token=TOKEN, store_dir=store,
        receipt_dir=str(tmp_path / "receipts"), avatar_id=AVATAR, http_client=bff,
    )
    assert result["ok"] is False
    assert result["remote_status"] == "hc_identity_inactive"
    assert result["retryable"] is False
    assert result["accepted"] == 0
    assert result["quarantined"] == 0
    receipts = tmp_path / "receipts"
    assert list(receipts.rglob("*.json")) == [] if receipts.exists() else True
    assert len(list((tmp_path / "contributions" / AVATAR).glob("*.json"))) == 2


def test_the_evidence_lane_keeps_its_bundles_pending(tmp_path, monkeypatch):
    bundle = {
        "schema_version": 2, "kind": "capability_evidence", "bundle_id": "c" * 40,
        "avatar_id": AVATAR, "source": "apatch", "trust_level": "attested",
        "generated_at": "2026-09-14T12:00:00+00:00", "evidence_scope": {"event_count": 1},
        "episodes": [{"episode_id": "episode-1"}], "capability_estimates": [],
        "exclusions": {"count": 0, "reasons": {}}, "signature": {"value": "signed"},
    }
    monkeypatch.setattr("apatch.avatar_evidence.build_evidence_bundle", lambda *_a, **_k: bundle)
    outbox = str(tmp_path / "evidence-outbox")
    avatar_delivery.queue_current_evidence(str(tmp_path), outbox_dir=outbox)
    bff = _RefusingBff(409, "hc_identity_unbound")
    result = avatar_delivery.deliver_pending_evidence_to_platform(
        platform_url=BASE, avatar_token=TOKEN, outbox_dir=outbox, http_client=bff,
    )
    assert result["ok"] is False
    assert result["status"] == "hc_identity_unbound"
    assert result["retryable"] is False
    assert result["delivered"] == 0
    assert result["pending"] == 1
    assert result["errors"][0]["action_required"] == "connect_hc_profile"


def test_a_retryable_refusal_is_not_reported_as_an_action_for_the_person(tmp_path):
    bff = _RefusingBff(503, "identity_unavailable")
    outcomes = outcome_delivery.pull_outcome_attestations_from_platform(
        platform_url=BASE, avatar_token=TOKEN, avatar_id=AVATAR,
        store_dir=str(tmp_path / "outcomes"), http_client=bff, trusted_issuers=[],
    )
    assert outcomes["status"] == "identity_unavailable"
    assert outcomes["retryable"] is True
    assert outcomes["errors"][0]["action_required"] == "retry_later"
