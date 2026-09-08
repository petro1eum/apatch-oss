"""Batched ContributionEvent delivery and durable acknowledgements."""
from __future__ import annotations

import json


class _Response:
    status_code = 200

    def __init__(self, events):
        self.events = events

    def json(self):
        return {
            "accepted": len(self.events),
            "rejected": 0,
            "results": [{
                "accepted": True,
                "stored": True,
                "duplicate": False,
                "event_id": event["event_id"],
                "signature_verified": event.get("trust_level") == "attested",
            } for event in self.events],
        }


class _Client:
    def __init__(self):
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return _Response(json)


class _ReconciliationResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class _ReconciliationClient:
    def __init__(self, missing_ids, unverified_ids=(), missing_envelope_ids=()):
        self.missing_ids = set(missing_ids)
        self.unverified_ids = set(unverified_ids)
        self.missing_envelope_ids = set(missing_envelope_ids)
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        if url.endswith("/contribution-events/reconcile"):
            event_ids = json["event_ids"]
            missing = [value for value in event_ids if value in self.missing_ids]
            present = [value for value in event_ids if value not in self.missing_ids]
            verified = [
                value for value in present if value not in self.unverified_ids
            ]
            unverified = [
                value for value in present if value in self.unverified_ids
            ]
            return _ReconciliationResponse({
                "avatar_id": json["avatar_id"],
                "present_event_ids": present,
                "verified_present_event_ids": verified,
                "unverified_present_event_ids": unverified,
                "missing_event_ids": missing,
                "conflict_event_ids": [],
                "missing_envelope_event_ids": [
                    value for value in present
                    if value in self.missing_envelope_ids
                ],
            })
        for event in json:
            event_id = event["event_id"]
            self.missing_ids.discard(event_id)
            if event.get("trust_level") == "attested":
                self.unverified_ids.discard(event_id)
            self.missing_envelope_ids.discard(event_id)
        return _Response(json)


def _write_events(path, count):
    path.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        event = {
            "event_id": f"event-{index}",
            "avatar_id": "avatar-key",
            "trust_level": "attested",
        }
        (path / f"event-{index}.json").write_text(
            json.dumps(event), encoding="utf-8"
        )


def test_delivery_never_crosses_avatar_identity(tmp_path):
    from apatch.contribution_delivery import deliver_pending_contributions

    _write_events(tmp_path, 2)
    foreign = {
        "event_id": "foreign-event",
        "avatar_id": "foreign-key",
        "trust_level": "claimed",
    }
    (tmp_path / "foreign-event.json").write_text(
        json.dumps(foreign), encoding="utf-8"
    )
    client = _Client()

    result = deliver_pending_contributions(
        base_url="http://tracker:8040",
        outbox_dir=str(tmp_path),
        avatar_id="avatar-key",
        http_client=client,
    )

    assert result["delivered"] == 2
    assert [event["avatar_id"] for event in client.calls[0][1]] == [
        "avatar-key", "avatar-key"
    ]
    assert not (tmp_path / "foreign-event.ack.json").exists()


def test_batched_delivery_acknowledges_each_event_once(tmp_path):
    from apatch.contribution_delivery import deliver_pending_contributions

    _write_events(tmp_path, 5)
    client = _Client()

    first = deliver_pending_contributions(
        base_url="http://tracker:8040",
        service_token="secret",
        outbox_dir=str(tmp_path),
        batch_size=2,
        http_client=client,
    )
    second = deliver_pending_contributions(
        base_url="http://tracker:8040",
        service_token="secret",
        outbox_dir=str(tmp_path),
        batch_size=2,
        http_client=client,
    )

    assert first["delivered"] == 5
    assert first["pending"] == 0
    assert second["delivered"] == 0
    assert len(client.calls) == 3
    assert all(len(call[1]) <= 2 for call in client.calls)
    assert client.calls[0][0].endswith("/internal/contribution-events/batch")


def test_sync_repairs_server_loss_even_when_local_ack_exists(tmp_path):
    from apatch.contribution_delivery import sync_contributions

    _write_events(tmp_path, 3)
    for index in range(3):
        (tmp_path / f"event-{index}.ack.json").write_text(
            json.dumps({"event_id": f"event-{index}", "accepted": True}),
            encoding="utf-8",
        )
    client = _ReconciliationClient({"event-1"})

    result = sync_contributions(
        base_url="http://tracker:8040",
        service_token="secret",
        store_dir=str(tmp_path / "empty-store"),
        outbox_dir=str(tmp_path),
        avatar_id="avatar-key",
        http_client=client,
    )

    assert result["ok"] is True
    assert result["reconciliation"] == {
        "ok": True,
        "status": "repaired",
        "complete": True,
        "storage_complete": True,
        "envelope_complete": True,
        "verification_complete": True,
        "verification_classified": True,
        "requested_count": 3,
        "present_count": 3,
        "verified_present_count": 3,
        "unverified_present_count": 0,
        "missing_before_repair": 1,
        "unverified_before_repair": 0,
        "missing_envelopes_before_repair": 0,
        "repaired_count": 1,
        "reverified_count": 0,
        "repaired_envelope_count": 0,
        "remaining_missing_count": 0,
        "remaining_unverified_count": 0,
        "remaining_missing_envelope_count": 0,
        "conflict_count": 0,
        "conflict_event_ids": [],
        "errors": [],
    }
    assert result["delivery"]["delivered"] == 1
    assert result["delivery"]["reconciled_delivered"] == 1
    batch_calls = [call for call in client.calls if call[0].endswith("/batch")]
    assert [[event["event_id"] for event in call[1]] for call in batch_calls] == [
        ["event-1"]
    ]
    repaired_ack = json.loads(
        (tmp_path / "event-1.ack.json").read_text(encoding="utf-8")
    )
    assert repaired_ack["reconciled_replay"] is True
    assert repaired_ack["prior_acknowledged"] is True


def test_sync_replays_present_events_until_tracker_verifies_them(tmp_path):
    from apatch.contribution_delivery import sync_contributions

    _write_events(tmp_path, 2)
    for index in range(2):
        (tmp_path / f"event-{index}.ack.json").write_text(
            json.dumps({"event_id": f"event-{index}", "accepted": True}),
            encoding="utf-8",
        )
    client = _ReconciliationClient(set(), {"event-1"})

    result = sync_contributions(
        base_url="http://tracker:8040",
        service_token="secret",
        store_dir=str(tmp_path / "empty-store"),
        outbox_dir=str(tmp_path),
        avatar_id="avatar-key",
        http_client=client,
    )

    assert result["ok"] is True
    assert result["reconciliation"]["status"] == "reverified"
    assert result["reconciliation"]["unverified_before_repair"] == 1
    assert result["reconciliation"]["reverified_count"] == 1
    assert result["reconciliation"]["remaining_unverified_count"] == 0
    assert result["reconciliation"]["storage_complete"] is True
    assert result["reconciliation"]["verification_complete"] is True
    batch_calls = [call for call in client.calls if call[0].endswith("/batch")]
    assert [[event["event_id"] for event in call[1]] for call in batch_calls] == [
        ["event-1"]
    ]


def test_sync_replays_verified_event_to_restore_full_signed_envelope(tmp_path):
    from apatch.contribution_delivery import sync_contributions

    _write_events(tmp_path, 2)
    for index in range(2):
        (tmp_path / f"event-{index}.ack.json").write_text(
            json.dumps({"event_id": f"event-{index}", "accepted": True}),
            encoding="utf-8",
        )
    client = _ReconciliationClient(
        set(),
        missing_envelope_ids={"event-0"},
    )

    result = sync_contributions(
        base_url="http://tracker:8040",
        service_token="secret",
        store_dir=str(tmp_path / "empty-store"),
        outbox_dir=str(tmp_path),
        avatar_id="avatar-key",
        http_client=client,
    )

    assert result["ok"] is True
    assert result["reconciliation"]["status"] == "envelopes_repaired"
    assert result["reconciliation"]["missing_envelopes_before_repair"] == 1
    assert result["reconciliation"]["repaired_envelope_count"] == 1
    assert result["reconciliation"]["remaining_missing_envelope_count"] == 0
    batch_calls = [call for call in client.calls if call[0].endswith("/batch")]
    assert [[event["event_id"] for event in call[1]] for call in batch_calls] == [
        ["event-0"]
    ]


def test_sync_reconciles_v1_identity_scoped_event_without_avatar_id(tmp_path):
    from apatch.contribution_delivery import sync_contributions

    legacy = {
        "schema_version": 1,
        "kind": "contribution",
        "event_id": "legacy-event",
        "identity": {"key_id": "avatar-key"},
        "trust_level": "attested",
    }
    (tmp_path / "legacy-event.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )
    (tmp_path / "legacy-event.ack.json").write_text(
        json.dumps({"event_id": "legacy-event", "accepted": True}),
        encoding="utf-8",
    )
    client = _ReconciliationClient({"legacy-event"})

    result = sync_contributions(
        base_url="http://tracker:8040",
        store_dir=str(tmp_path / "empty-store"),
        outbox_dir=str(tmp_path),
        avatar_id="avatar-key",
        http_client=client,
    )

    assert result["ok"] is True
    assert result["reconciliation"]["status"] == "repaired"
    assert result["reconciliation"]["requested_count"] == 1
    assert result["delivery"]["delivered"] == 1


def test_sync_delivers_new_events_but_fails_closed_when_reconciliation_fails(
    tmp_path,
):
    from apatch.contribution_delivery import sync_contributions

    _write_events(tmp_path, 1)

    class BrokenReconciliationClient(_Client):
        def post(self, url, *, json, headers):
            if url.endswith("/contribution-events/reconcile"):
                return _ReconciliationResponse({"detail": "temporarily unavailable"})
            return super().post(url, json=json, headers=headers)

    client = BrokenReconciliationClient()
    result = sync_contributions(
        base_url="http://tracker:8040",
        store_dir=str(tmp_path / "empty-store"),
        outbox_dir=str(tmp_path),
        avatar_id="avatar-key",
        http_client=client,
    )

    assert result["ok"] is False
    assert result["delivery"]["delivered"] == 1
    assert result["delivery"]["pending"] == 0
    assert result["reconciliation"]["status"] == "reconciliation_failed"
    assert result["reconciliation"]["complete"] is False
