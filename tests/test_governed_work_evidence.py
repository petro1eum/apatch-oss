from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import contribution as C
from apatch import governed_work as G


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


def make_change(root: Path, actor: Provider):
    spec = root / "docs/specs/SPEC-WORK-1.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "# SPEC-WORK-1 -- Work\n\n"
        "## R1 Exact\n\n(verify: true)\n\n"
        "## R2 Safe\n\n(verify: true)\n",
        encoding="utf-8",
    )
    return G.prepare_change(
        str(root),
        tenant_id="tenant-a",
        project_group_id="tcpg_" + "1" * 32,
        work_program_id="tcwp_" + "2" * 32,
        work_program_hash="sha256:" + "3" * 64,
        spec_id="SPEC-WORK-1",
        spec_path=str(spec),
        requirement_ids=["R1", "R2"],
        purpose="Exact private purpose",
        issued_at="2026-08-30T00:00:00Z",
        key_provider=actor,
    )["change"]


def make_binding(local_change, platform: Provider):
    body = {
        "schema": G.SOURCE_BINDING_SCHEMA,
        "binding_id": "tcpsb_" + "4" * 32,
        "tenant_id": local_change["tenant_id"],
        "project_group_id": local_change["project_group_id"],
        "source_kind": local_change["source_kind"],
        "work_program_id": local_change["work_program_id"],
        "work_program_hash": local_change["work_program_hash"],
        "context_release_id": None,
        "context_release_manifest_hash": None,
        "execution_system": "apatch",
        "change_id": local_change["change_id"],
        "change_hash": G.document_hash(local_change),
        "spec_id": local_change["spec_id"],
        "spec_hash": local_change["spec_hash"],
        "requirement_refs": local_change["requirement_refs"],
        "actor_ref": "agent_binding:tcpgab_" + "5" * 32,
        "authority_version": 1,
        "issued_at": "2026-08-30T00:01:00Z",
    }
    return G.sign_document(
        body, platform, key_id=G.signer_key_id(platform)
    )


def make_event(session_id: str, *, suffix: str):
    return {
        "schema_version": 3,
        "kind": "fact",
        "event_id": f"event-{suffix}",
        "source": "apatch",
        "trust_level": "attested",
        "idempotency_key": f"event-{suffix}",
        "avatar_id": "actor",
        "identity": {"key_id": "actor"},
        "project": {"id": "project"},
        "session": {
            "session_id": session_id,
            "started_at": "2026-08-30T00:00:00Z",
            "ended_at": "2026-08-30T00:10:00Z",
            "duration_sec": 600,
            "active_sec": 540,
            "artifacts": [],
            "intent": "",
        },
        "volume": {"ops": 1, "files_touched": 1, "insertions": 1, "deletions": 0},
        "proof_ref": {"op_ids": ["op-1"], "head": "op-1", "committed_at": 1},
        "payload": None,
        "created_at": "2026-08-30T00:10:00Z",
        "signature": "existing-contribution-signature",
    }


def make_facts(binding):
    return [
        {
            "spec_id": binding["spec_id"],
            "requirement_id": ref["requirement_id"],
            "requirement_hash": ref["requirement_hash"],
            "attestation_id": f"att-{ref['requirement_id']}",
            "attestation_hash": "sha256:" + (
                "6" if ref["requirement_id"] == "R1" else "7"
            ) * 64,
            "outcome": "passed",
            "project_source_binding_id": binding["binding_id"],
            "project_source_binding_hash": G.document_hash(binding),
            "governed_session_id": f"session-{ref['requirement_id']}",
        }
        for ref in binding["requirement_refs"]
    ]


def build_all(tmp_path):
    actor, platform = Provider(), Provider()
    change = make_change(tmp_path, actor)
    binding = make_binding(change, platform)
    sessions = [
        {
            "governed_session_id": "session-R1",
            "started_at": "2026-08-30T00:00:00Z",
            "ended_at": "2026-08-30T00:10:00Z",
        },
        {
            "governed_session_id": "session-R2",
            "started_at": "2026-08-30T00:20:00Z",
            "ended_at": "2026-08-30T00:30:00Z",
        },
    ]
    events = [
        make_event("session-R1", suffix="r1"),
        make_event("session-R2", suffix="r2"),
    ]
    timesheet = G.build_timesheet_draft(
        str(tmp_path),
        binding=binding,
        sessions=sessions,
        contribution_events=events,
        issued_at="2026-08-30T00:31:00Z",
        key_provider=actor,
    )["document"]
    bundle = G.build_work_evidence_bundle(
        str(tmp_path),
        change=change,
        binding=binding,
        attestation_facts=make_facts(binding),
        contribution_events=events,
        timesheet=timesheet,
        issued_at="2026-08-30T00:32:00Z",
        key_provider=actor,
    )["document"]
    return actor, change, binding, events, timesheet, bundle


def test_bundle_is_exact_signed_and_source_bound(tmp_path):
    actor, _change, binding, events, timesheet, bundle = build_all(tmp_path)
    assert bundle["schema"] == G.EVIDENCE_SCHEMA
    assert bundle["project_source_binding_hash"] == G.document_hash(binding)
    assert bundle["timesheet_ref"]["timesheet_hash"] == G.document_hash(timesheet)
    assert bundle["contribution_event_refs"] == sorted(
        [
            {"event_id": event["event_id"], "event_hash": G.document_hash(event)}
            for event in events
        ],
        key=lambda item: item["event_id"],
    )
    assert {ref["outcome"] for ref in bundle["attestation_refs"]} == {"passed"}
    G.validate_work_evidence_bundle(
        bundle,
        trusted_actor_keys={
            G.signer_key_id(actor): actor.get_public_key()
        },
    )


def test_stale_or_unbound_attestation_is_rejected(tmp_path):
    actor, change, binding, events, timesheet, _bundle = build_all(tmp_path)
    stale = make_facts(binding)
    stale[0]["requirement_hash"] = "sha256:" + "f" * 16
    with pytest.raises(G.GovernedWorkError, match="stale attestation"):
        G.build_work_evidence_bundle(
            str(tmp_path),
            change=change,
            binding=binding,
            attestation_facts=stale,
            contribution_events=events,
            timesheet=timesheet,
            key_provider=actor,
            store=False,
        )
    unbound = make_facts(binding)
    unbound[0]["project_source_binding_hash"] = "sha256:" + "e" * 64
    with pytest.raises(G.GovernedWorkError, match="not bound"):
        G.build_work_evidence_bundle(
            str(tmp_path),
            change=change,
            binding=binding,
            attestation_facts=unbound,
            contribution_events=events,
            timesheet=timesheet,
            key_provider=actor,
            store=False,
        )


def test_source_bound_ledger_projection_requires_exact_artifacts(tmp_path):
    actor, change, binding, _events, _timesheet, _bundle = build_all(tmp_path)
    del actor, change
    binding_hash = G.document_hash(binding)
    refs = binding["requirement_refs"]
    rows = []
    for index, ref in enumerate(refs):
        rows.append(
            {
                "id": f"att-{index}",
                "tool_id": "apatch_attest",
                "timestamp": f"2026-08-30T00:0{index}:00Z",
                "signature": f"sig-{index}",
                "payload": {
                    "session_id": f"session-{ref['requirement_id']}",
                    "artifacts": [
                        {
                            "kind": "project-source-binding",
                            "id": binding["binding_id"],
                            "content_hash": binding_hash,
                        },
                        {
                            "kind": "spec",
                            "id": f"{binding['spec_id']}#{ref['requirement_id']}",
                            "content_hash": ref["requirement_hash"],
                        },
                    ],
                },
            }
        )
    facts = G.derive_source_bound_attestation_facts(rows, binding=binding)
    assert [fact["requirement_id"] for fact in facts] == ["R1", "R2"]
    rows[0]["payload"]["artifacts"] = rows[0]["payload"]["artifacts"][1:]
    with pytest.raises(G.GovernedWorkError, match="missing source-bound"):
        G.derive_source_bound_attestation_facts(rows, binding=binding)


def test_source_bound_legacy_event_uses_trusted_key_and_attest_time(tmp_path):
    actor, platform = Provider(), Provider()
    change = make_change(tmp_path, actor)
    binding = make_binding(change, platform)
    binding_hash = G.document_hash(binding)
    session_id = "session-R1"
    event = make_event(session_id, suffix="legacy")
    key_id = G.signer_key_id(actor)
    event["avatar_id"] = key_id
    event["identity"] = {
        "key_id": key_id,
        "ca": "platform",
        "trust_level": "attested",
    }
    event["session"]["started_at"] = "2026-08-30T00:00:00+00:00"
    event["session"]["ended_at"] = None
    event["session"]["artifacts"] = [
        {
            "kind": "project-source-binding",
            "id": binding["binding_id"],
            "content_hash": binding_hash,
        }
    ]
    event["created_at"] = "2026-08-30T00:11:00Z"
    unsigned = {key: value for key, value in event.items() if key != "signature"}
    event["signature"] = base64.b64encode(
        actor.sign(C._canonical(unsigned).encode("utf-8"))
    ).decode("ascii")

    store = tmp_path / "contributions" / key_id
    store.mkdir(parents=True)
    (store / f"{event['event_id']}.json").write_text(
        json.dumps(event),
        encoding="utf-8",
    )
    loaded = G._load_source_bound_contributions(
        str(tmp_path),
        session_ids={session_id},
        binding=binding,
        store_dir=str(tmp_path / "contributions"),
        key_provider=actor,
    )
    assert [item["event_id"] for item in loaded] == [event["event_id"]]
    assert G._source_bound_session_refs(loaded) == [
        {
            "governed_session_id": session_id,
            "started_at": "2026-08-30T00:00:00Z",
            "ended_at": "2026-08-30T00:11:00Z",
        }
    ]

    with pytest.raises(G.GovernedWorkError, match="signer key_id mismatch"):
        G._load_source_bound_contributions(
            str(tmp_path),
            session_ids={session_id},
            binding=binding,
            store_dir=str(tmp_path / "contributions"),
            key_provider=Provider(),
        )

    event["identity"]["public_key"] = base64.b64encode(
        platform.get_public_key()
    ).decode("ascii")
    with pytest.raises(G.GovernedWorkError, match="signer key_id mismatch"):
        G._contribution_public_key(str(tmp_path), event, key_provider=actor)


def test_timesheet_is_a_claim_not_an_acceptance(tmp_path):
    actor, _change, binding, _events, timesheet, _bundle = build_all(tmp_path)
    assert timesheet["schema"] == G.TIMESHEET_SCHEMA
    assert timesheet["claimed_active_seconds"] == 1080
    forbidden = {
        "accepted_active_seconds",
        "decision",
        "rate",
        "price",
        "salary",
        "invoice",
        "ownership",
        "settlement",
    }
    assert not forbidden.intersection(timesheet)
    G.validate_timesheet_draft(
        timesheet,
        trusted_actor_keys={
            G.signer_key_id(actor): actor.get_public_key()
        },
    )
    assert timesheet["project_source_binding_hash"] == G.document_hash(binding)


def test_timesheet_rejects_bool_seconds_and_inverted_period(tmp_path):
    _actor, _change, _binding, _events, timesheet, _bundle = build_all(tmp_path)
    bad_seconds = dict(timesheet)
    bad_seconds["claimed_active_seconds"] = True
    with pytest.raises(G.GovernedWorkError, match="claimed_active_seconds"):
        G.validate_timesheet_draft(bad_seconds)
    inverted = dict(timesheet)
    inverted["period_started_at"], inverted["period_ended_at"] = (
        inverted["period_ended_at"],
        inverted["period_started_at"],
    )
    with pytest.raises(G.GovernedWorkError, match="inverted"):
        G.validate_timesheet_draft(inverted)


def test_privacy_barrier_rejects_unknown_fields_and_paths(tmp_path):
    _actor, _change, _binding, _events, _timesheet, bundle = build_all(tmp_path)
    leaked = dict(bundle)
    leaked["prompt"] = "private"
    with pytest.raises(G.GovernedWorkError, match="keys mismatch"):
        G.validate_work_evidence_bundle(leaked)
    with pytest.raises(G.GovernedWorkError, match="filesystem path"):
        G.assert_privacy_safe({"safe": "/Users/person/private/repo"})
    serialized = G.canonical_bytes(bundle).decode("utf-8")
    for forbidden in (
        "Exact private purpose",
        "/Users/",
        "session_token",
        "source_code",
        "credentials",
    ):
        assert forbidden not in serialized
