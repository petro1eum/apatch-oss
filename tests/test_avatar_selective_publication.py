from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path

import pytest

from apatch import governed_work as G
from apatch import governed_work_mcp as M
from apatch import contribution_export as X


ROOT = Path(__file__).resolve().parents[1]
RFP = ROOT / "docs" / "RFP-048-AVATAR-SELECTIVE-PUBLICATION.md"
SPEC = ROOT / "docs" / "specs" / "SPEC-AVATAR-SELECTIVE-PUBLICATION-1.md"


def test_r0_traceability_and_ownership() -> None:
    rfp = RFP.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    acceptance = re.findall(r"^\| (AVP-\d+) \| MUST \|", rfp, re.MULTILINE)
    mappings = re.findall(
        r"^\| (AVP-\d+) \| (R\d+) \| covered \|$",
        spec,
        re.MULTILINE,
    )
    assert acceptance == [f"AVP-{index}" for index in range(1, 9)]
    assert mappings == [
        (f"AVP-{index}", f"R{index}") for index in range(1, 9)
    ]
    assert len(set(acceptance)) == len(acceptance)
    assert len({rk for _acceptance, rk in mappings}) == len(mappings)
    assert "> **ownership mode:** strict" in spec
    assert spec.count("\nowns:") >= 9
    assert "TODO" not in spec
    assert "verify: true" not in spec


def _selection_fixture(monkeypatch):
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "1" * 32,
    }
    binding = {
        "binding_id": "tcpsb_" + "2" * 32,
        "authority": "platform",
    }
    selected = {
        "event_id": "event-selected",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "session": {"session_id": "session-selected"},
        "payload": "selected",
        "signature": "signed-selected",
    }
    unrelated = {
        "event_id": "event-unrelated",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "session": {"session_id": "session-unrelated"},
        "payload": "unrelated",
        "signature": "signed-unrelated",
    }
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "project_source_binding_hash": G.document_hash(binding),
        "contribution_event_refs": [
            {
                "event_id": selected["event_id"],
                "event_hash": G.document_hash(selected),
            }
        ],
    }
    timesheet = {
        "session_refs": [
            {"governed_session_id": selected["session"]["session_id"]}
        ]
    }
    monkeypatch.setattr(
        M,
        "_publication_documents",
        lambda _root, _binding_id: (change, bundle, timesheet),
    )
    monkeypatch.setattr(
        M.G,
        "load_project_source_binding",
        lambda _root, _binding_id: binding,
    )
    monkeypatch.setattr(
        M.G,
        "_load_source_bound_contributions",
        lambda *_args, **_kwargs: [selected],
    )
    monkeypatch.setattr(
        X,
        "iter_store_events",
        lambda _store=None: [selected, unrelated],
    )
    return change, binding, bundle, timesheet, selected, unrelated



def test_r2_preview_closed_stable_and_read_only(
    monkeypatch, tmp_path: Path
) -> None:
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "1" * 32,
    }
    binding = {
        "binding_id": "tcpsb_" + "2" * 32,
        "signature": {"value": "binding-secret"},
    }
    event = {
        "event_id": "event-selected",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "source": "must-not-leak",
        "signature": "event-secret",
    }
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "contribution_event_refs": [
            {
                "event_id": event["event_id"],
                "event_hash": G.document_hash(event),
            }
        ],
        "signature": {"value": "bundle-secret"},
    }
    monkeypatch.setattr(
        M,
        "_avatar_publication_documents",
        lambda *_args, **_kwargs: (change, binding, bundle, [event]),
    )
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    first = M.preview_avatar_contribution_publication(
        str(tmp_path),
        binding_id=binding["binding_id"],
    )
    second = M.preview_avatar_contribution_publication(
        str(tmp_path),
        binding_id=binding["binding_id"],
    )
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    body = {
        "schema": "apatch.avatar-share-plan.v1",
        "avatar_origin": "https://trust-chain.ai",
        "avatar_id": "avatar-owner",
        "subject_key_id": "subject-key",
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "1" * 32,
        "scope": "avatar.contribution_upload",
        "project_source_binding_ref": {
            "binding_id": binding["binding_id"],
            "binding_hash": G.document_hash(binding),
        },
        "evidence_bundle_ref": {
            "bundle_id": bundle["bundle_id"],
            "bundle_hash": G.document_hash(bundle),
        },
        "contribution_event_refs": bundle["contribution_event_refs"],
    }
    expected = {
        "ok": True,
        "operation": "preview_avatar_contributions",
        "plan": {**body, "plan_hash": G.value_hash(body)},
    }
    assert first == expected
    assert second == expected
    assert after == before
    encoded = repr(first)
    for forbidden in (
        "must-not-leak",
        "event-secret",
        "binding-secret",
        "bundle-secret",
        "signature",
        "token",
        "timesheet",
        "claimed_active_seconds",
        "price",
        "acceptance",
        "capability",
    ):
        assert forbidden not in encoded



def test_r3_recipient_allowlist_is_exact(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("APATCH_AVATAR_ALLOWED_ORIGINS", raising=False)
    assert M._canonical_avatar_origin("https://TRUST-CHAIN.AI:443/") == (
        "https://trust-chain.ai"
    )
    monkeypatch.setenv(
        "APATCH_AVATAR_ALLOWED_ORIGINS",
        "https://avatar.example:8443",
    )
    assert M._canonical_avatar_origin("https://AVATAR.EXAMPLE:8443/") == (
        "https://avatar.example:8443"
    )

    called = []

    def forbidden_selection(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("recipient must fail before local selection")

    monkeypatch.setattr(M, "_avatar_publication_documents", forbidden_selection)
    invalid = [
        "http://trust-chain.ai",
        "https://user:password@trust-chain.ai",
        "https://trust-chain.ai/private",
        "https://trust-chain.ai?token=x",
        "https://trust-chain.ai#fragment",
        "https://trust-chain.ai.evil.example",
        "https://eviltrust-chain.ai",
        "https://trust-chаin.ai",
        "https://avatar.example:bad",
        "https://unlisted.example",
    ]
    for origin in invalid:
        result = M.preview_avatar_contribution_publication(
            str(tmp_path),
            binding_id="tcpsb_" + "2" * 32,
            avatar_origin=origin,
        )
        assert result["ok"] is False, origin
        assert result["operation"] == "preview_avatar_contributions"
    assert called == []



def test_r4_publish_reconstructs_and_requires_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "1" * 32,
    }
    binding = {"binding_id": "tcpsb_" + "2" * 32}
    event = {
        "event_id": "event-selected",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "signature": "signed",
    }
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "contribution_event_refs": [
            {
                "event_id": event["event_id"],
                "event_hash": G.document_hash(event),
            }
        ],
    }
    plan = M._avatar_share_plan(
        avatar_origin="https://trust-chain.ai",
        change=change,
        binding=binding,
        bundle=bundle,
        events=[event],
    )
    monkeypatch.setattr(
        M,
        "_avatar_publication_documents",
        lambda *_args, **_kwargs: (change, binding, bundle, [event]),
    )
    deliveries = []

    def deliver(**kwargs):
        deliveries.append(kwargs)
        return {
            "ok": True,
            "attempted": 1,
            "accepted": 1,
            "duplicates": 0,
            "errors": [],
        }

    monkeypatch.setattr(X, "sync_to_trustchain_avatar", deliver)

    unknown = {**plan, "token": "must-not-pass"}
    changed = deepcopy(plan)
    changed["avatar_id"] = "avatar-other"
    changed_body = {key: value for key, value in changed.items() if key != "plan_hash"}
    changed["plan_hash"] = G.value_hash(changed_body)
    bad_hash = {**plan, "plan_hash": "sha256:" + "0" * 64}
    for candidate, confirmation in (
        (unknown, f"publish:{plan['plan_hash']}"),
        (changed, f"publish:{changed['plan_hash']}"),
        (bad_hash, f"publish:{bad_hash['plan_hash']}"),
        (plan, "publish:sha256:" + "0" * 64),
    ):
        result = M.publish_avatar_contributions(
            str(tmp_path),
            plan=candidate,
            confirmation=confirmation,
            token="owner-token",
        )
        assert result["ok"] is False
    assert deliveries == []

    accepted = M.publish_avatar_contributions(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        token="owner-token",
        contribution_store_dir=str(tmp_path / "events"),
        receipt_dir=str(tmp_path / "receipts"),
    )
    assert accepted["ok"] is True
    assert accepted["operation"] == "publish_avatar_contributions"
    assert accepted["plan_hash"] == plan["plan_hash"]
    assert accepted["contribution_event_refs"] == bundle[
        "contribution_event_refs"
    ]
    assert len(deliveries) == 1
    assert deliveries[0]["event_ids"] == ["event-selected"]
    assert deliveries[0]["token"] == "owner-token"



def test_r5_unrelated_event_never_crosses_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    import json

    store = tmp_path / "events"
    receipts = tmp_path / "receipts"
    store.mkdir()
    selected = {
        "event_id": "event-selected",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "signature": "signed-selected",
    }
    unrelated = {
        "event_id": "event-unrelated",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "signature": "signed-unrelated",
    }
    (store / "selected.json").write_text(
        json.dumps(selected),
        encoding="utf-8",
    )
    (store / "unrelated.json").write_text(
        json.dumps(unrelated),
        encoding="utf-8",
    )
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "1" * 32,
    }
    binding = {"binding_id": "tcpsb_" + "2" * 32}
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "contribution_event_refs": [
            {
                "event_id": selected["event_id"],
                "event_hash": G.document_hash(selected),
            }
        ],
    }
    plan = M._avatar_share_plan(
        avatar_origin="https://trust-chain.ai",
        change=change,
        binding=binding,
        bundle=bundle,
        events=[selected],
    )
    monkeypatch.setattr(
        M,
        "_avatar_publication_documents",
        lambda *_args, **_kwargs: (change, binding, bundle, [selected]),
    )

    class Response:
        status_code = 200
        text = ""

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Client:
        def __init__(self):
            self.posts = []
            self.present = set()

        def post(self, url, *, headers, json):
            self.posts.append({"url": url, "headers": headers, "json": json})
            if url.endswith("/contributions/reconcile"):
                ids = list(json["event_ids"])
                present = [event_id for event_id in ids if event_id in self.present]
                missing = [event_id for event_id in ids if event_id not in self.present]
                return Response(
                    {
                        "available": True,
                        "status": "ready",
                        "avatar_id": json["avatar_id"],
                        "present_event_ids": present,
                        "missing_event_ids": missing,
                        "conflict_event_ids": [],
                        "verified_present_event_ids": present,
                        "unverified_present_event_ids": [],
                    }
                )
            ids = [event["event_id"] for event in json["events"]]
            self.present.update(ids)
            return Response(
                {
                    "status": "ready",
                    "accepted": len(ids),
                    "duplicates": 0,
                    "failed": [],
                }
            )

    client = Client()
    result = M.publish_avatar_contributions(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        token="owner-token",
        contribution_store_dir=str(store),
        receipt_dir=str(receipts),
        http_client=client,
    )
    assert result["ok"] is True
    assert result["delivery"]["attempted"] == 1
    assert result["delivery"]["reconciliation"]["complete"] is True
    serialized_calls = repr(client.posts)
    assert "event-selected" in serialized_calls
    assert "event-unrelated" not in serialized_calls
    assert "event-unrelated" not in repr(result)
    receipt_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in receipts.glob("*.json")
    )
    assert "event-selected" in receipt_text
    assert "event-unrelated" not in receipt_text

    missing_client = Client()
    missing = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(tmp_path / "missing-receipts"),
        avatar_id="avatar-owner",
        event_ids=["event-missing"],
        http_client=missing_client,
    )
    assert missing["ok"] is False
    assert missing["attempted"] == 0
    assert missing_client.posts == []

    duplicate_dir = store / "duplicate"
    duplicate_dir.mkdir()
    (duplicate_dir / "selected.json").write_text(
        json.dumps(selected),
        encoding="utf-8",
    )
    duplicate_client = Client()
    duplicate = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(tmp_path / "duplicate-receipts"),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=duplicate_client,
    )
    assert duplicate["ok"] is False
    assert duplicate["attempted"] == 0
    assert duplicate_client.posts == []



def test_r6_delivery_idempotency_and_token_fail_closed(
    monkeypatch, tmp_path: Path
) -> None:
    import json

    store = tmp_path / "events"
    receipts = tmp_path / "receipts"
    store.mkdir()
    event = {
        "event_id": "event-selected",
        "avatar_id": "avatar-owner",
        "identity": {"key_id": "subject-key"},
        "signature": "signed-selected",
    }
    (store / "selected.json").write_text(json.dumps(event), encoding="utf-8")

    class Response:
        text = ""

        def __init__(self, status_code, body):
            self.status_code = status_code
            self.body = body

        def json(self):
            return self.body

    class Client:
        def __init__(self, upload_mode="valid"):
            self.upload_mode = upload_mode
            self.posts = []
            self.present = set()

        def post(self, url, *, headers, json):
            self.posts.append({"url": url, "headers": headers, "json": json})
            if url.endswith("/contributions/reconcile"):
                ids = list(json["event_ids"])
                present = [event_id for event_id in ids if event_id in self.present]
                missing = [event_id for event_id in ids if event_id not in self.present]
                return Response(
                    200,
                    {
                        "available": True,
                        "status": "ready",
                        "avatar_id": json["avatar_id"],
                        "present_event_ids": present,
                        "missing_event_ids": missing,
                        "conflict_event_ids": [],
                        "verified_present_event_ids": present,
                        "unverified_present_event_ids": [],
                    },
                )
            if self.upload_mode == "malformed":
                return Response(200, {"status": "unexpected"})
            ids = [item["event_id"] for item in json["events"]]
            self.present.update(ids)
            return Response(
                200,
                {
                    "status": "ready",
                    "accepted": len(ids),
                    "duplicates": 0,
                    "failed": [],
                },
            )

    client = Client()
    first = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=client,
    )
    second = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=client,
    )
    assert first["ok"] is True
    assert first["attempted"] == 1
    assert second["ok"] is True
    assert second["attempted"] == 0
    assert len(list(receipts.glob("*.json"))) == 1

    malformed_receipts = tmp_path / "malformed-receipts"
    malformed = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(malformed_receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=Client(upload_mode="malformed"),
    )
    assert malformed["ok"] is False
    assert list(malformed_receipts.glob("*.json")) == []

    monkeypatch.delenv("APATCH_AVATAR_TOKEN", raising=False)
    missing_token_receipts = tmp_path / "missing-token-receipts"
    missing_token = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="",
        store_dir=str(store),
        receipt_dir=str(missing_token_receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=Client(),
    )
    assert missing_token["ok"] is False
    assert list(missing_token_receipts.glob("*.json")) == []

    class RefusingClient(Client):
        def post(self, url, *, headers, json):
            self.posts.append({"url": url, "headers": headers, "json": json})
            return Response(409, {"detail": "hc_identity_unbound"})

    refused_receipts = tmp_path / "refused-receipts"
    refused = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(refused_receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=RefusingClient(),
    )
    assert refused["ok"] is False
    assert refused.get("remote_status") == "hc_identity_unbound"
    assert list(refused_receipts.glob("*.json")) == []

    class PartialClient(Client):
        def post(self, url, *, headers, json):
            self.posts.append({"url": url, "headers": headers, "json": json})
            if url.endswith("/contributions/reconcile"):
                ids = list(json["event_ids"])
                return Response(
                    200,
                    {
                        "available": True,
                        "status": "ready",
                        "avatar_id": json["avatar_id"],
                        "present_event_ids": [],
                        "missing_event_ids": ids,
                        "conflict_event_ids": [],
                        "verified_present_event_ids": [],
                        "unverified_present_event_ids": [],
                    },
                )
            return Response(
                200,
                {
                    "status": "partial_failure",
                    "accepted": 0,
                    "duplicates": 0,
                    "failed": [
                        {
                            "event_id": "event-selected",
                            "status": "temporary_unavailable",
                        }
                    ],
                },
            )

    partial_receipts = tmp_path / "partial-receipts"
    partial = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(partial_receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=PartialClient(),
    )
    assert partial["ok"] is False
    assert partial["pending"] == 1
    assert list(partial_receipts.glob("*.json")) == []

    class CredentialRefusingClient(Client):
        def __init__(self, status_code, detail):
            super().__init__()
            self.status_code = status_code
            self.detail = detail

        def post(self, url, *, headers, json):
            self.posts.append({"url": url, "headers": headers, "json": json})
            return Response(self.status_code, {"detail": self.detail})

    for status_code, detail in (
        (401, "token_expired"),
        (403, "token_owner_mismatch"),
    ):
        credential_receipts = tmp_path / f"{detail}-receipts"
        credential_failure = X.sync_to_trustchain_avatar(
            platform_url="https://trust-chain.ai",
            token="owner-token",
            store_dir=str(store),
            receipt_dir=str(credential_receipts),
            avatar_id="avatar-owner",
            event_ids=["event-selected"],
            http_client=CredentialRefusingClient(status_code, detail),
        )
        assert credential_failure["ok"] is False
        assert list(credential_receipts.glob("*.json")) == []

    class OfflineClient(Client):
        def post(self, url, *, headers, json):
            self.posts.append({"url": url, "headers": headers, "json": json})
            raise RuntimeError("offline while using owner-token")

    offline_receipts = tmp_path / "offline-receipts"
    offline = X.sync_to_trustchain_avatar(
        platform_url="https://trust-chain.ai",
        token="owner-token",
        store_dir=str(store),
        receipt_dir=str(offline_receipts),
        avatar_id="avatar-owner",
        event_ids=["event-selected"],
        http_client=OfflineClient(),
    )
    assert offline["ok"] is False
    assert "owner-token" not in repr(offline)
    assert "[redacted]" in repr(offline)
    assert list(offline_receipts.glob("*.json")) == []

    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*.json")
    )
    assert "owner-token" not in serialized
    assert "owner-token" not in repr(first)
    assert "owner-token" not in repr(malformed)
    assert "owner-token" not in repr(refused)


def test_r7_mcp_surface_and_wire_compatibility(
    monkeypatch, tmp_path: Path
) -> None:
    import inspect
    import sys

    pytest.importorskip("mcp")
    from apatch import conformance
    from apatch.mcp import server
    from apatch.mcp.profiles import PROFILE_COMPACT

    names = {
        "apatch_governed_work_preview_avatar",
        "apatch_governed_work_publish_avatar",
    }
    tools = server.mcp._tool_manager._tools
    assert names <= set(tools)
    assert names.isdisjoint(PROFILE_COMPACT)

    preview_properties = tools[
        "apatch_governed_work_preview_avatar"
    ].parameters["properties"]
    publish_properties = tools[
        "apatch_governed_work_publish_avatar"
    ].parameters["properties"]
    assert set(preview_properties) == {
        "binding_id",
        "avatar_origin",
        "contribution_store_dir",
        "target_dir",
    }
    assert set(publish_properties) == {
        "plan",
        "confirmation",
        "token",
        "contribution_store_dir",
        "receipt_dir",
        "target_dir",
    }
    assert "token" not in preview_properties
    assert publish_properties["token"]["writeOnly"] is True
    assert publish_properties["token"]["default"] is None

    calls = []

    def preview(
        target_dir,
        *,
        binding_id,
        avatar_origin,
        contribution_store_dir=None,
    ):
        calls.append(
            (
                "preview",
                target_dir,
                binding_id,
                avatar_origin,
                contribution_store_dir,
            )
        )
        return {"ok": True, "operation": "preview_avatar_contributions"}

    def publish(
        target_dir,
        *,
        plan,
        confirmation,
        token=None,
        contribution_store_dir=None,
        receipt_dir=None,
    ):
        calls.append(
            (
                "publish",
                target_dir,
                plan,
                confirmation,
                token,
                contribution_store_dir,
                receipt_dir,
            )
        )
        return {"ok": True, "operation": "publish_avatar_contributions"}

    monkeypatch.setattr(M, "preview_avatar_contribution_publication", preview)
    monkeypatch.setattr(M, "publish_avatar_contributions", publish)
    target = str(tmp_path)
    binding_id = "tcpsb_" + "2" * 32
    plan = {"plan_hash": "sha256:" + "3" * 64}
    preview_result = tools["apatch_governed_work_preview_avatar"].fn(
        binding_id=binding_id,
        avatar_origin="https://trust-chain.ai",
        contribution_store_dir="events",
        target_dir=target,
    )
    publish_result = tools["apatch_governed_work_publish_avatar"].fn(
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        token="owner-secret",
        contribution_store_dir="events",
        receipt_dir="receipts",
        target_dir=target,
    )
    assert preview_result == {
        "ok": True,
        "operation": "preview_avatar_contributions",
    }
    assert publish_result == {
        "ok": True,
        "operation": "publish_avatar_contributions",
    }
    assert "owner-secret" not in repr(preview_result)
    assert "owner-secret" not in repr(publish_result)
    assert calls == [
        (
            "preview",
            target,
            binding_id,
            "https://trust-chain.ai",
            "events",
        ),
        (
            "publish",
            target,
            plan,
            f"publish:{plan['plan_hash']}",
            "owner-secret",
            "events",
            "receipts",
        ),
    ]

    sync_parameters = inspect.signature(
        X.sync_to_trustchain_avatar
    ).parameters
    assert tuple(sync_parameters) == (
        "platform_url",
        "token",
        "store_dir",
        "receipt_dir",
        "limit",
        "timeout",
        "dry_run",
        "avatar_id",
        "event_ids",
        "http_client",
    )
    assert sync_parameters["event_ids"].default is None

    child = tmp_path / "verify_environment.py"
    child.write_text(
        "import os, sys\n"
        "sys.exit(1 if 'APATCH_MCP_TARGET_POLICY' in os.environ else 0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APATCH_MCP_TARGET_POLICY", "alias_only")
    ran, failures, broken, details = conformance.run_spec_verify(
        str(tmp_path),
        [{"id": "R7-env", "verify": f"{sys.executable} {child.name}"}],
        capture_details=True,
    )
    assert (ran, failures, broken, details) == (1, [], [], [])


def test_r8_cross_product_round_trip(
    monkeypatch, tmp_path: Path
) -> None:
    import base64
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    from apatch import avatar_delivery
    from apatch import avatar_evidence
    from apatch import contribution as C

    class Provider:
        def __init__(self):
            self.private = Ed25519PrivateKey.generate()

        def get_public_key(self):
            return self.private.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )

        def sign(self, payload):
            return self.private.sign(payload)

    actor = Provider()
    platform = Provider()
    actor_key_id = G.signer_key_id(actor)
    spec_path = tmp_path / "docs" / "specs" / "SPEC-ROUND-TRIP-1.md"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        "# SPEC-ROUND-TRIP-1 -- Selective Avatar publication\n\n"
        "## R1 Publish one exact fact\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    change = G.prepare_change(
        str(tmp_path),
        tenant_id="tenant-a",
        project_group_id="tcpg_" + "1" * 32,
        work_program_id="tcwp_" + "2" * 32,
        work_program_hash="sha256:" + "3" * 64,
        spec_id="SPEC-ROUND-TRIP-1",
        spec_path=str(spec_path),
        requirement_ids=["R1"],
        purpose="Selective local Avatar publication",
        issued_at="2026-09-24T00:00:00Z",
        key_provider=actor,
    )["change"]

    binding_body = {
        "schema": G.SOURCE_BINDING_SCHEMA,
        "binding_id": "tcpsb_" + "4" * 32,
        "tenant_id": change["tenant_id"],
        "project_group_id": change["project_group_id"],
        "source_kind": change["source_kind"],
        "work_program_id": change["work_program_id"],
        "work_program_hash": change["work_program_hash"],
        "context_release_id": change["context_release_id"],
        "context_release_manifest_hash": change[
            "context_release_manifest_hash"
        ],
        "execution_system": "apatch",
        "change_id": change["change_id"],
        "change_hash": G.document_hash(change),
        "spec_id": change["spec_id"],
        "spec_hash": change["spec_hash"],
        "requirement_refs": change["requirement_refs"],
        "actor_ref": "agent_binding:tcpgab_" + "5" * 32,
        "authority_version": 1,
        "issued_at": "2026-09-24T00:01:00Z",
    }
    binding = {
        **binding_body,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": G.signer_key_id(platform),
            "value": G._b64url_encode(
                platform.sign(
                    G._platform_signature_payload(
                        binding_body,
                        purpose=G.PLATFORM_SOURCE_BINDING_PURPOSE,
                    )
                )
            ),
        },
    }
    stored_binding = G.store_project_source_binding(
        str(tmp_path),
        binding,
        trusted_authority_keys={
            G.signer_key_id(platform): platform.get_public_key()
        },
        change=change,
    )
    assert stored_binding["stored"] is True

    binding_hash = G.document_hash(binding)
    identity = {
        "key_id": actor_key_id,
        "cert_fingerprint": "sha256:" + "6" * 64,
        "agent_id": "apatch-round-trip",
        "subject_type": "agent",
        "public_key": base64.b64encode(
            actor.get_public_key()
        ).decode("ascii"),
        "ca": "platform",
        "trust_level": "attested",
    }
    project = {
        "id": "project-round-trip",
        "name": "round-trip",
        "remote": None,
    }
    binding_artifact = {
        "kind": "project-source-binding",
        "id": binding["binding_id"],
        "content_hash": binding_hash,
    }

    def signed_event(
        session_id: str,
        *,
        operation_id: str,
        started_at: str,
        ended_at: str,
        created_at: str,
    ):
        event = C.build_event(
            {
                "session_id": session_id,
                "intent": "local private intent",
                "artifacts": [binding_artifact],
                "started_at": started_at,
                "ended_at": ended_at,
            },
            target_dir=str(tmp_path),
            identity=identity,
            project=project,
            ledger_rows=[
                {
                    "id": operation_id,
                    "timestamp": ended_at,
                    "payload": {
                        "governed_session_id": session_id,
                        "files": {"src/result.py": {}},
                        "insertions": 3,
                        "deletions": 1,
                    },
                }
            ],
            created_at=created_at,
        )
        C.sign_event(event, actor)
        return event.to_dict()

    selected = signed_event(
        "session-selected",
        operation_id="op-selected",
        started_at="2026-09-24T00:02:00Z",
        ended_at="2026-09-24T00:12:00Z",
        created_at="2026-09-24T00:12:00Z",
    )
    unrelated = signed_event(
        "session-unrelated",
        operation_id="op-unrelated",
        started_at="2026-09-24T00:13:00Z",
        ended_at="2026-09-24T00:23:00Z",
        created_at="2026-09-24T00:23:00Z",
    )
    event_store = tmp_path / "contributions" / actor_key_id
    event_store.mkdir(parents=True)
    for event in (selected, unrelated):
        (event_store / f"{event['event_id']}.json").write_bytes(
            G.canonical_bytes(event)
        )

    verified = G._load_source_bound_contributions(
        str(tmp_path),
        session_ids={"session-selected", "session-unrelated"},
        binding=binding,
        store_dir=str(tmp_path / "contributions"),
        key_provider=actor,
    )
    assert {event["event_id"] for event in verified} == {
        selected["event_id"],
        unrelated["event_id"],
    }

    timesheet = G.build_timesheet_draft(
        str(tmp_path),
        binding=binding,
        sessions=[
            {
                "governed_session_id": "session-selected",
                "started_at": "2026-09-24T00:02:00Z",
                "ended_at": "2026-09-24T00:12:00Z",
            }
        ],
        contribution_events=[selected],
        issued_at="2026-09-24T00:24:00Z",
        key_provider=actor,
    )["document"]
    requirement_ref = change["requirement_refs"][0]
    bundle = G.build_work_evidence_bundle(
        str(tmp_path),
        change=change,
        binding=binding,
        attestation_facts=[
            {
                "spec_id": change["spec_id"],
                "requirement_id": requirement_ref["requirement_id"],
                "requirement_hash": requirement_ref["requirement_hash"],
                "attestation_id": "att-round-trip-r1",
                "attestation_hash": "sha256:" + "7" * 64,
                "outcome": "passed",
                "project_source_binding_id": binding["binding_id"],
                "project_source_binding_hash": binding_hash,
                "governed_session_id": "session-selected",
            }
        ],
        contribution_events=[selected],
        timesheet=timesheet,
        issued_at="2026-09-24T00:25:00Z",
        key_provider=actor,
    )["document"]
    assert bundle["contribution_event_refs"] == [
        {
            "event_id": selected["event_id"],
            "event_hash": G.document_hash(selected),
        }
    ]

    monkeypatch.setattr(
        G,
        "_load_local_provider",
        lambda _root, key_provider=None: key_provider or actor,
    )
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unrelated product pipeline must not be called")

    monkeypatch.setattr(M.D, "queue_evidence_admission", forbidden)
    monkeypatch.setattr(G, "build_timesheet_draft", forbidden)
    monkeypatch.setattr(G, "build_work_evidence_bundle", forbidden)
    monkeypatch.setattr(X, "export_pending", forbidden)
    monkeypatch.setattr(avatar_delivery, "queue_current_evidence", forbidden)
    monkeypatch.setattr(avatar_evidence, "build_evidence_bundle", forbidden)

    rejected_origin = M.preview_avatar_contribution_publication(
        str(tmp_path),
        binding_id=binding["binding_id"],
        avatar_origin="https://trust-chain.ai.evil.example",
        contribution_store_dir=str(tmp_path / "contributions"),
    )
    assert rejected_origin["ok"] is False

    preview = M.preview_avatar_contribution_publication(
        str(tmp_path),
        binding_id=binding["binding_id"],
        contribution_store_dir=str(tmp_path / "contributions"),
    )
    assert preview["ok"] is True
    plan = preview["plan"]
    assert plan["contribution_event_refs"] == bundle[
        "contribution_event_refs"
    ]

    class Response:
        text = ""

        def __init__(self, body, status_code=200):
            self.body = body
            self.status_code = status_code

        def json(self):
            return self.body

    class Client:
        def __init__(self):
            self.posts = []
            self.present = set()

        def post(self, url, *, headers, json):
            self.posts.append(
                {"url": url, "headers": headers, "json": json}
            )
            if url.endswith("/contributions/reconcile"):
                requested = list(json["event_ids"])
                present = [
                    event_id
                    for event_id in requested
                    if event_id in self.present
                ]
                missing = [
                    event_id
                    for event_id in requested
                    if event_id not in self.present
                ]
                return Response(
                    {
                        "available": True,
                        "status": "ready",
                        "avatar_id": json["avatar_id"],
                        "present_event_ids": present,
                        "missing_event_ids": missing,
                        "conflict_event_ids": [],
                        "verified_present_event_ids": present,
                        "unverified_present_event_ids": [],
                    }
                )
            uploaded = [event["event_id"] for event in json["events"]]
            self.present.update(uploaded)
            return Response(
                {
                    "status": "ready",
                    "accepted": len(uploaded),
                    "duplicates": 0,
                    "failed": [],
                }
            )

    no_network = Client()
    tampered = deepcopy(plan)
    tampered["contribution_event_refs"][0]["event_hash"] = (
        "sha256:" + "8" * 64
    )
    tampered_body = {
        key: value
        for key, value in tampered.items()
        if key != "plan_hash"
    }
    tampered["plan_hash"] = G.value_hash(tampered_body)
    rejected_tamper = M.publish_avatar_contributions(
        str(tmp_path),
        plan=tampered,
        confirmation=f"publish:{tampered['plan_hash']}",
        token="owner-secret",
        contribution_store_dir=str(tmp_path / "contributions"),
        receipt_dir=str(tmp_path / "avatar-receipts"),
        http_client=no_network,
    )
    assert rejected_tamper["ok"] is False
    assert no_network.posts == []

    monkeypatch.delenv("APATCH_AVATAR_TOKEN", raising=False)
    rejected_token = M.publish_avatar_contributions(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        token="",
        contribution_store_dir=str(tmp_path / "contributions"),
        receipt_dir=str(tmp_path / "avatar-receipts"),
        http_client=no_network,
    )
    assert rejected_token["ok"] is False
    assert no_network.posts == []

    client = Client()
    first = M.publish_avatar_contributions(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        token="owner-secret",
        contribution_store_dir=str(tmp_path / "contributions"),
        receipt_dir=str(tmp_path / "avatar-receipts"),
        http_client=client,
    )
    replay = M.publish_avatar_contributions(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        token="owner-secret",
        contribution_store_dir=str(tmp_path / "contributions"),
        receipt_dir=str(tmp_path / "avatar-receipts"),
        http_client=client,
    )
    assert first["ok"] is True
    assert first["delivery"]["attempted"] == 1
    assert first["delivery"]["reconciliation"]["complete"] is True
    assert replay["ok"] is True
    assert replay["delivery"]["attempted"] == 0
    upload_calls = [
        call
        for call in client.posts
        if call["url"].endswith("/contributions/upload")
    ]
    assert len(upload_calls) == 1
    assert [
        event["event_id"]
        for event in upload_calls[0]["json"]["events"]
    ] == [selected["event_id"]]
    assert unrelated["event_id"] not in repr(client.posts)
    assert unrelated["event_id"] not in repr(first)
    assert unrelated["event_id"] not in repr(replay)

    receipt_files = list((tmp_path / "avatar-receipts").glob("*.json"))
    assert len(receipt_files) == 1
    receipt_text = receipt_files[0].read_text(encoding="utf-8")
    assert selected["event_id"] in receipt_text
    assert unrelated["event_id"] not in receipt_text
    assert "owner-secret" not in receipt_text
    assert "owner-secret" not in repr(first)
    assert "owner-secret" not in repr(replay)

    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
        and "avatar-receipts" not in path.relative_to(tmp_path).parts
    }
    assert after == before
    governed_root = G.governed_work_root(str(tmp_path))
    outbox = governed_root / "outbox"
    assert not outbox.exists() or not list(outbox.glob("*.json"))
    public_surface = repr(
        {"preview": preview, "first": first, "replay": replay}
    )
    for forbidden_key in (
        "timesheet",
        "claimed_active_seconds",
        "price",
        "acceptance",
        "capability",
    ):
        assert forbidden_key not in public_surface


def test_r1_exact_bundle_selection(monkeypatch, tmp_path: Path) -> None:
    change, binding, bundle, _timesheet, selected, unrelated = _selection_fixture(
        monkeypatch
    )
    resolved = M._avatar_publication_documents(
        str(tmp_path),
        binding["binding_id"],
        contribution_store_dir=str(tmp_path / "events"),
    )
    assert resolved == (change, binding, bundle, [selected])
    assert unrelated not in resolved[3]

    monkeypatch.setattr(X, "iter_store_events", lambda _store=None: [unrelated])
    with pytest.raises(G.GovernedWorkError, match="exactly once"):
        M._avatar_publication_documents(str(tmp_path), binding["binding_id"])

    monkeypatch.setattr(
        X,
        "iter_store_events",
        lambda _store=None: [selected, deepcopy(selected), unrelated],
    )
    with pytest.raises(G.GovernedWorkError, match="exactly once"):
        M._avatar_publication_documents(str(tmp_path), binding["binding_id"])

    drifted = deepcopy(selected)
    drifted["payload"] = "tampered"
    monkeypatch.setattr(X, "iter_store_events", lambda _store=None: [drifted])
    with pytest.raises(G.GovernedWorkError, match="hash differs"):
        M._avatar_publication_documents(str(tmp_path), binding["binding_id"])

    monkeypatch.setattr(
        M.G,
        "_load_source_bound_contributions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            G.GovernedWorkError("ContributionEvent signature verification failed")
        ),
    )
    with pytest.raises(G.GovernedWorkError, match="signature verification failed"):
        M._avatar_publication_documents(str(tmp_path), binding["binding_id"])

    monkeypatch.setattr(
        M.G,
        "_load_source_bound_contributions",
        lambda *_args, **_kwargs: [selected],
    )
    wrong_binding = {**binding, "authority": "different"}
    monkeypatch.setattr(
        M.G,
        "load_project_source_binding",
        lambda _root, _binding_id: wrong_binding,
    )
    with pytest.raises(G.GovernedWorkError, match="Binding selection differ"):
        M._avatar_publication_documents(str(tmp_path), binding["binding_id"])

    second = {
        **unrelated,
        "avatar_id": "avatar-other",
        "identity": {"key_id": "other-subject"},
    }
    bundle["contribution_event_refs"].append(
        {"event_id": second["event_id"], "event_hash": G.document_hash(second)}
    )
    _timesheet["session_refs"].append(
        {"governed_session_id": second["session"]["session_id"]}
    )
    monkeypatch.setattr(
        M.G,
        "load_project_source_binding",
        lambda _root, _binding_id: binding,
    )
    monkeypatch.setattr(
        M.G,
        "_load_source_bound_contributions",
        lambda *_args, **_kwargs: [selected, second],
    )
    monkeypatch.setattr(X, "iter_store_events", lambda _store=None: [selected, second])
    with pytest.raises(G.GovernedWorkError, match="one Avatar identity"):
        M._avatar_publication_documents(str(tmp_path), binding["binding_id"])
