from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import contribution as C
from apatch import governed_work as G
from apatch import governed_work_delivery as D


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


def contribution_event():
    return C.build_event(
        {
            "session_id": "apatch_sess_compat",
            "intent": "private",
            "artifacts": ["spec:SPEC-COMPAT-1#R1"],
            "started_at": "2026-08-30T00:00:00+00:00",
            "ended_at": "2026-08-30T00:10:00+00:00",
        },
        identity={
            "key_id": "k" * 32,
            "agent_id": "tester",
            "ca": "legacy",
            "trust_level": "claimed",
        },
        project={"id": "project-1", "name": "demo", "remote": None},
        ledger_rows=[
            {
                "id": "op_1",
                "timestamp": "2026-08-30T00:01:00+00:00",
                "payload": {
                    "governed_session_id": "apatch_sess_compat",
                    "files": {"a.py": {}},
                    "insertions": 2,
                    "deletions": 1,
                },
            }
        ],
        created_at="2026-08-30T00:10:00+00:00",
    ).to_dict()


def test_contribution_event_wire_bytes_keep_the_existing_golden_hash():
    event = contribution_event()
    canonical = C._canonical(event).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == (
        "e4ab24aa3ccbc071d9e1d723350bedc4f8aa38f48cdbebc8d2b9bce8a95573c2"
    )
    assert event["schema_version"] == 3
    assert not {
        "change_id",
        "project_source_binding_id",
        "work_release_id",
        "workspace_contribution_binding_id",
        "timesheet_decision_id",
    }.intersection(event)


def test_timesheet_references_contribution_event_without_mutating_it(tmp_path):
    event = contribution_event()
    before = C._canonical(event)
    provider = Provider()
    binding = {
        "binding_id": "tcpsb_" + "1" * 32,
        "schema": G.SOURCE_BINDING_SCHEMA,
        "opaque": "binding bytes remain Platform-owned",
    }
    result = G.build_timesheet_draft(
        str(tmp_path),
        binding=binding,
        sessions=[
            {
                "governed_session_id": "apatch_sess_compat",
                "started_at": "2026-08-30T00:00:00Z",
                "ended_at": "2026-08-30T00:10:00Z",
            }
        ],
        contribution_events=[event],
        issued_at="2026-08-30T00:11:00Z",
        key_provider=provider,
        store=False,
    )
    assert C._canonical(event) == before
    assert result["document"]["contribution_event_refs"] == [
        {
            "event_id": event["event_id"],
            "event_hash": G.document_hash(event),
        }
    ]
    assert "project_source_binding_id" not in event


def test_local_activity_and_work_release_do_not_infer_platform_decisions(tmp_path):
    root = tmp_path / ".apatch" / "governed_work"
    for name in ("changes", "bindings", "timesheets", "evidence"):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "one.json").write_text("{}", encoding="utf-8")

    local = G.local_governed_work_status(str(tmp_path))
    assert local["changes"] == 1
    assert local["source_bindings"] == 1
    assert local["timesheet_drafts"] == 1
    assert local["evidence_bundles"] == 1
    assert local["collective_acceptance"] == "unknown"
    assert local["source_verified"] == "local_only"
    assert local["contribution_bound"] == "unknown"
    assert local["timesheet_accepted"] == "unknown"

    projection = D.validate_status_projection(
        {
            "schema": "trustchain.governed-work-status.v1",
            "tenant_id": "tenant-a",
            "project_group_id": "tcpg_" + "2" * 32,
            "work_program_id": "tcwp_" + "3" * 32,
            "work_release_id": "tcwr_" + "4" * 32,
            "collective_acceptance": "accepted",
            "source_verified": "unbound",
            "contribution_bound": "unbound",
            "timesheet_accepted": "not_submitted",
            "authority_version": 1,
            "revocation_version": 0,
            "projection_cursor": 1,
        }
    )
    assert projection["collective_acceptance"] == "accepted"
    assert projection["source_verified"] == "unbound"
    assert projection["contribution_bound"] == "unbound"
    assert projection["timesheet_accepted"] == "not_submitted"


def test_platform_c956a323_receipt_fixture_is_byte_exact_and_verified():
    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "governed_work"
        / "platform-c956a323-receipt.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["platform_commit"] == (
        "c956a3237177f294f0d8a86e50949d1f69681638"
    )
    assert fixture["canonical_contract_sha256"] == (
        "f2a237c4241c364be85733a22162d5193849abdf4738bf5263690506eedc797e"
    )
    assert fixture["service_sha256"] == (
        "efc0c013becef29700d74d336ca7b345d536b543f64daec38839260ed7026a6d"
    )
    assert fixture["router_sha256"] == (
        "bb03badc12b0c1bca62d8e8ce536479e5cd3b0c5f964f121e44345d953be0561"
    )
    assert (
        fixture["receipt_signing_purpose"]
        == G.PLATFORM_EVIDENCE_ADMISSION_PURPOSE
    )
    receipt = fixture["receipt"]
    keys = {
        receipt["signature"]["key_id"]: fixture["authority_public_key"],
    }
    assert D.validate_admission_receipt(
        receipt,
        trusted_authority_keys=keys,
    ) == receipt

    tampered = copy.deepcopy(receipt)
    tampered["projection_cursor"] += 1
    with pytest.raises(G.GovernedWorkError, match="signature verification"):
        D.validate_admission_receipt(
            tampered,
            trusted_authority_keys=keys,
        )
