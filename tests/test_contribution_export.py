"""Tests for the apatch -> HC contribution pipe and owner sync path."""
import json
import os

from apatch import contribution_export as X


def _write_event(store, key_id, event_id):
    d = os.path.join(store, key_id)
    os.makedirs(d, exist_ok=True)
    ev = {"schema_version": 2, "kind": "contribution", "event_id": event_id,
          "idempotency_key": event_id, "avatar_id": key_id, "source": "apatch",
          "trust_level": "attested", "identity": {"key_id": key_id},
          "project": {"id": "p"}, "session": {"intent": "x"}, "volume": {"ops": 1},
          "proof_ref": {"op_ids": ["op_0"], "head": "op_0"}, "signature": "SIG"}
    with open(os.path.join(d, event_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump(ev, fh)
    return ev


def test_r1_export_copies_events(tmp_path):
    store, outbox = tmp_path / "store", tmp_path / "outbox"
    _write_event(str(store), "kid1", "ev_a")
    _write_event(str(store), "kid1", "ev_b")
    new = X.export_pending(str(store), str(outbox))
    assert sorted(new) == ["ev_a", "ev_b"]
    assert {e["event_id"] for e in X.outbox_events(str(outbox))} == {"ev_a", "ev_b"}


def test_r2_export_is_idempotent(tmp_path):
    store, outbox = tmp_path / "store", tmp_path / "outbox"
    _write_event(str(store), "kid1", "ev_a")
    assert X.export_pending(str(store), str(outbox)) == ["ev_a"]
    # second run hands off nothing new — no duplicates
    assert X.export_pending(str(store), str(outbox)) == []
    assert len(X.outbox_events(str(outbox))) == 1


def test_r3_export_preserves_content(tmp_path):
    store, outbox = tmp_path / "store", tmp_path / "outbox"
    src = _write_event(str(store), "kid1", "ev_a")
    X.export_pending(str(store), str(outbox))
    got = X.outbox_events(str(outbox))[0]
    assert got["proof_ref"] == src["proof_ref"]
    assert got["avatar_id"] == "kid1" and got["signature"] == "SIG"


def test_r4_empty_store_is_noop(tmp_path):
    outbox = tmp_path / "outbox"
    assert X.export_pending(str(tmp_path / "nope"), str(outbox)) == []


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.posts = []

    def post(self, url, *, headers, json):
        self.posts.append({"url": url, "headers": headers, "json": json})
        return _Response(
            {
                "status": "ready",
                "accepted": len(json["events"]),
                "duplicates": 0,
                "failed": [],
            }
        )


def test_sync_to_trustchain_avatar_posts_pending_events_and_receipts(tmp_path):
    store, receipts = tmp_path / "store", tmp_path / "receipts"
    _write_event(str(store), "kid1", "ev_a")
    client = _Client()

    result = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        http_client=client,
    )
    second = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        http_client=client,
    )

    assert result["ok"] is True
    assert result["attempted"] == 1
    assert result["accepted"] == 1
    assert client.posts[0]["url"] == "https://trust-chain.ai/api/avatar/contributions/upload"
    assert client.posts[0]["headers"]["Authorization"] == "Bearer tcav-sync-token"
    assert second["attempted"] == 0
    assert len(list(receipts.glob("*.json"))) == 1


def test_sync_to_trustchain_avatar_requires_platform_and_token(tmp_path):
    store = tmp_path / "store"
    _write_event(str(store), "kid1", "ev_a")

    no_platform = X.sync_to_trustchain_avatar(token="tcav-sync-token", store_dir=str(store))
    no_token = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="",
        store_dir=str(store),
    )

    assert no_platform["ok"] is False
    assert "platform" in no_platform["errors"][0].lower()
    assert no_token["ok"] is False
    assert "token" in no_token["errors"][0].lower()


def test_sync_to_trustchain_avatar_dry_run_counts_pending(tmp_path):
    store = tmp_path / "store"
    _write_event(str(store), "kid1", "ev_a")

    result = X.sync_to_trustchain_avatar(store_dir=str(store), dry_run=True)

    assert result["ok"] is True
    assert result["pending"] == 1
    assert result["would_attempt"] == 1


def test_export_can_be_scoped_to_one_avatar_identity(tmp_path):
    store, outbox = tmp_path / "store", tmp_path / "outbox"
    _write_event(str(store), "kid1", "ev_owned")
    _write_event(str(store), "kid2", "ev_foreign")

    exported = X.export_pending(
        str(store), str(outbox), avatar_id="kid1"
    )

    assert exported == ["ev_owned"]
    assert [event["avatar_id"] for event in X.outbox_events(str(outbox))] == [
        "kid1"
    ]


def test_identity_key_scopes_legacy_event_without_top_level_avatar_id(tmp_path):
    store, outbox, receipts = (
        tmp_path / "store",
        tmp_path / "outbox",
        tmp_path / "receipts",
    )
    event = _write_event(str(store), "kid1", "ev_legacy")
    event.pop("avatar_id")
    path = store / "kid1" / "ev_legacy.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    client = _Client()

    exported = X.export_pending(
        str(store),
        str(outbox),
        avatar_id="kid1",
    )
    synced = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        http_client=client,
    )

    assert exported == ["ev_legacy"]
    assert synced["accepted"] == 1
    upload = next(
        item for item in client.posts
        if item["url"].endswith("/contributions/upload")
    )
    assert upload["json"]["events"][0]["identity"]["key_id"] == "kid1"


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.posts = []
        self.server_events = set()
        self.unverified_events = set()

    def post(self, url, *, headers, json):
        self.posts.append({"url": url, "headers": headers, "json": json})
        if url.endswith("/contributions/reconcile"):
            event_ids = json["event_ids"]
            present = [value for value in event_ids if value in self.server_events]
            missing = [value for value in event_ids if value not in self.server_events]
            verified = [
                value for value in present
                if value not in self.unverified_events
            ]
            unverified = [
                value for value in present
                if value in self.unverified_events
            ]
            return _Response({
                "available": True,
                "status": "ready",
                "avatar_id": json["avatar_id"],
                "present_event_ids": present,
                "verified_present_event_ids": verified,
                "unverified_present_event_ids": unverified,
                "missing_event_ids": missing,
                "conflict_event_ids": [],
            })
        uploaded = {event["event_id"] for event in json["events"]}
        self.server_events.update(uploaded)
        self.unverified_events.difference_update(uploaded)
        return _Response({
            "status": "ready",
            "accepted": len(json["events"]),
            "duplicates": 0,
            "failed": [],
        })


class _PartialClient(_Client):
    def __init__(self):
        super().__init__()
        self.upload_count = 0

    def post(self, url, *, headers, json):
        if url.endswith("/contributions/reconcile"):
            return super().post(url, headers=headers, json=json)
        self.posts.append({"url": url, "headers": headers, "json": json})
        self.upload_count += 1
        if self.upload_count == 1:
            failed = json["events"][0]["event_id"]
            self.server_events.update(
                event["event_id"] for event in json["events"][1:]
            )
            return _Response({
                "status": "partial_failure",
                "accepted": len(json["events"]) - 1,
                "duplicates": 0,
                "failed": [{"event_id": failed, "status": "tracker_rejected"}],
            })
        self.server_events.update(event["event_id"] for event in json["events"])
        return _Response({
            "status": "ready",
            "accepted": len(json["events"]),
            "duplicates": 0,
            "failed": [],
        })


class _TransientFailureClient(_Client):
    def post(self, url, *, headers, json):
        if url.endswith("/contributions/reconcile"):
            return super().post(url, headers=headers, json=json)
        self.posts.append({"url": url, "headers": headers, "json": json})
        failed = json["events"][0]["event_id"]
        return _Response({
            "status": "partial_failure",
            "accepted": len(json["events"]) - 1,
            "duplicates": 0,
            "failed": [{"event_id": failed, "status": "tracker_unavailable"}],
        })


def test_sync_to_trustchain_avatar_posts_pending_events_and_receipts(tmp_path):
    store, receipts = tmp_path / "store", tmp_path / "receipts"
    _write_event(str(store), "kid1", "ev_a")
    client = _Client()

    result = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        http_client=client,
    )
    second = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        http_client=client,
    )

    assert result["ok"] is True
    assert result["attempted"] == 1
    assert result["accepted"] == 1
    upload_posts = [
        item for item in client.posts
        if item["url"].endswith("/contributions/upload")
    ]
    assert upload_posts[0]["url"] == (
        "https://trust-chain.ai/api/avatar/contributions/upload"
    )
    assert upload_posts[0]["headers"]["Authorization"] == "Bearer tcav-sync-token"
    assert second["attempted"] == 0
    assert len(list(receipts.glob("*.json"))) == 1
    assert second["reconciliation"]["status"] == "in_sync"


def test_sync_repairs_server_loss_behind_existing_owner_receipt(tmp_path):
    store, receipts = tmp_path / "store", tmp_path / "receipts"
    _write_event(str(store), "kid1", "ev_a")
    client = _Client()
    first = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        http_client=client,
    )
    client.server_events.clear()

    repaired = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        http_client=client,
    )

    assert first["ok"] is True
    assert repaired["ok"] is True
    assert repaired["attempted"] == 1
    assert repaired["reconciliation"]["status"] == "repaired"
    assert repaired["reconciliation"]["repaired_count"] == 1


def test_sync_replays_owner_event_until_tracker_verifies_it(tmp_path):
    store, receipts = tmp_path / "store", tmp_path / "receipts"
    _write_event(str(store), "kid1", "ev_a")
    client = _Client()
    first = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        http_client=client,
    )
    client.unverified_events.add("ev_a")

    repaired = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        http_client=client,
    )

    assert first["ok"] is True
    assert repaired["ok"] is True
    assert repaired["attempted"] == 1
    assert repaired["reconciliation"]["status"] == "reverified"
    assert repaired["reconciliation"]["unverified_before_repair"] == 1
    assert repaired["reconciliation"]["reverified_count"] == 1
    assert repaired["reconciliation"]["remaining_unverified_count"] == 0


def test_sync_quarantines_permanent_rejection_and_continues_batches(tmp_path):
    store, receipts = tmp_path / "store", tmp_path / "receipts"
    for event_id in ("ev_a", "ev_b", "ev_c"):
        _write_event(str(store), "kid1", event_id)
    client = _PartialClient()

    result = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        limit=2,
        http_client=client,
    )

    assert result["attempted"] == 3
    assert result["accepted"] == 2
    assert result["quarantined"] == 1
    assert result["quarantined_total"] == 1
    assert result["pending"] == 0
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["rejections"] == [{
        "event_id": "ev_a",
        "status": "tracker_rejected",
        "disposition": "quarantined",
    }]
    assert client.upload_count == 2
    assert len(list(receipts.glob("*.json"))) == 3

    replay = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        limit=2,
        http_client=client,
    )
    assert replay["attempted"] == 0
    assert replay["quarantined_total"] == 1


def test_sync_retries_transient_tracker_failure(tmp_path):
    store, receipts = tmp_path / "store", tmp_path / "receipts"
    _write_event(str(store), "kid1", "ev_a")
    client = _TransientFailureClient()

    result = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="tcav-sync-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="kid1",
        http_client=client,
    )

    assert result["ok"] is False
    assert result["quarantined"] == 0
    assert result["pending"] == 1
    assert result["errors"] == [{
        "event_id": "ev_a",
        "status": "tracker_unavailable",
    }]
    assert list(receipts.glob("*.json")) == []


def test_sync_to_trustchain_avatar_requires_platform_and_token(tmp_path):
    store = tmp_path / "store"
    _write_event(str(store), "kid1", "ev_a")

    no_platform = X.sync_to_trustchain_avatar(
        token="tcav-sync-token",
        store_dir=str(store),
    )
    no_token = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="",
        store_dir=str(store),
    )

    assert no_platform["ok"] is False
    assert "platform" in no_platform["errors"][0].lower()
    assert no_token["ok"] is False
    assert "token" in no_token["errors"][0].lower()


def test_sync_to_trustchain_avatar_dry_run_counts_pending(tmp_path):
    store = tmp_path / "store"
    _write_event(str(store), "kid1", "ev_a")

    result = X.sync_to_trustchain_avatar(store_dir=str(store), dry_run=True)

    assert result["ok"] is True
    assert result["pending"] == 1
    assert result["would_attempt"] == 1
