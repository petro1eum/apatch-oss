"""CA-6/A1 first slice: lost file evidence must never become a fresh result.

Synthetic ledger records exercise native projections, not signature validation.
They are confined to pytest temporary directories and never enter a real ledger.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from apatch.spec import parse_spec
from apatch.spec_coverage import coverage_rows, requirement_file_sets


def _fixture(tmp_path):
    spec = parse_spec("# SPEC-RECHECK-1\n\n## R1 Existing result\n\n(verify: true)\n")
    path = tmp_path / "result.txt"
    path.write_text("original\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    anchor = {"kind": "spec", "id": "SPEC-RECHECK-1#R1",
              "content_hash": spec.requirements[0].content_hash}
    entries = [
        {"id": "mutation-original", "tool_id": "apatch", "timestamp": "2026-09-08T00:00:01Z",
         "payload": {"action": "apply", "applied_patches": 1,
                     "governed_session_id": "original", "artifacts": [anchor],
                     "files": {"result.txt": {"sha256": digest}}}},
        {"id": "attestation-original", "tool_id": "apatch_attest", "timestamp": "2026-09-08T00:00:02Z",
         "payload": {"session_id": "original", "artifacts": [anchor]}},
    ]
    return spec, path, digest, entries


def _noop(entries, index=3):
    entries.append({"id": f"attestation-noop-{index}", "tool_id": "apatch_attest",
                    "timestamp": f"2026-09-08T00:00:{index:02d}Z",
                    "payload": {"session_id": f"noop-{index}",
                                "artifacts": deepcopy(entries[1]["payload"]["artifacts"]),
                                "covered_by": []}})


@pytest.mark.parametrize("change", ["unchanged", "modified", "deleted"])
def test_empty_rebind_never_claims_fresh_evidence(tmp_path, change):
    spec, path, digest, entries = _fixture(tmp_path)
    assert coverage_rows(spec, entries, str(tmp_path))[0]["state"] == "attested"
    if change == "modified":
        path.write_text("changed by another requirement\n", encoding="utf-8")
    elif change == "deleted":
        path.unlink()
    _noop(entries)
    saved = deepcopy(entries)

    row = coverage_rows(spec, entries, str(tmp_path))[0]

    assert row["state"] == "stale"
    assert row["stale"] is True
    assert row["evidence_status"] == "incomplete"
    assert row["file_hashes"] == {"result.txt": digest}
    assert row["reference_session_id"] == "original"
    assert row["session_id"] == "noop-3"
    assert row["stale_reason"] == ("evidence_incomplete" if change == "unchanged" else "file_drift")
    assert entries == saved


def test_repeated_empty_rebind_preserves_last_proven_file_set(tmp_path):
    spec, path, digest, entries = _fixture(tmp_path)
    _noop(entries)
    _noop(entries, 4)
    sets = requirement_file_sets(entries, spec.id)
    assert sets["R1"]["files"] == {"result.txt": digest}
    assert sets["R1"]["reference_session_id"] == "original"
    assert sets["R1"]["attestation_op_id"] == "attestation-noop-4"
    assert sets["R1"]["reference_attestation_op_id"] == "attestation-original"
    assert sets["R1"]["op_ids"] == ["mutation-original"]
    assert sets["R1"]["evidence_status"] == "incomplete"


def test_missing_historical_hashes_are_incomplete_not_reconstructed(tmp_path):
    spec, path, digest, entries = _fixture(tmp_path)
    entries[0]["payload"]["files"] = {"result.txt": {}}
    _noop(entries)
    row = coverage_rows(spec, entries, str(tmp_path))[0]
    assert row["state"] == "stale"
    assert row["evidence_status"] == "incomplete"
    assert row["stale_reason"] == "evidence_incomplete"
    assert row["file_hashes"] == {}
    assert row["files"] == []


def test_fileless_covered_by_legacy_requirement_is_not_invented_as_code(tmp_path):
    spec, path, digest, entries = _fixture(tmp_path)
    entries = [entries[1]]
    entries[0]["payload"]["covered_by"] = ["R2"]
    row = coverage_rows(spec, entries, str(tmp_path))[0]
    assert row["state"] == "attested"
    assert not row.get("file_hashes")


def test_partially_missing_hashes_do_not_qualify_remaining_subset(tmp_path):
    spec, path, digest, entries = _fixture(tmp_path)
    entries[0]["payload"]["files"]["missing-reference.txt"] = {}
    row = coverage_rows(spec, entries, str(tmp_path))[0]
    assert row["state"] == "stale"
    assert row["evidence_status"] == "incomplete"
    assert row["stale_reason"] == "evidence_incomplete"
    assert row["file_hashes"] == {"result.txt": digest}


def test_new_real_mutation_replaces_old_reference_and_detects_future_drift(tmp_path):
    spec, path, digest, entries = _fixture(tmp_path)
    _noop(entries)
    path.write_text("new verified result\n", encoding="utf-8")
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    mutation = deepcopy(entries[0])
    mutation.update(id="mutation-new", timestamp="2026-09-08T00:00:04Z")
    mutation["payload"].update(governed_session_id="new", files={"result.txt": {"sha256": current}})
    attestation = deepcopy(entries[1])
    attestation.update(id="attestation-new", timestamp="2026-09-08T00:00:05Z")
    attestation["payload"]["session_id"] = "new"
    entries.extend([mutation, attestation])
    row = coverage_rows(spec, entries, str(tmp_path))[0]
    assert row["state"] == "attested"
    assert row["file_hashes"] == {"result.txt": current}
    assert row["op_ids"] == ["mutation-new"]
    path.write_text("later drift\n", encoding="utf-8")
    assert coverage_rows(spec, entries, str(tmp_path))[0]["state"] == "stale"


def test_never_borrows_another_requirements_mutations(tmp_path):
    spec, path, digest, entries = _fixture(tmp_path)
    _noop(entries)
    foreign = deepcopy(entries[0])
    foreign.update(id="mutation-foreign", timestamp="2026-09-08T00:00:03Z")
    foreign["payload"].update(governed_session_id="noop-3",
                              artifacts=[{"kind": "spec", "id": "SPEC-RECHECK-1#R2"}],
                              files={"private.txt": {"sha256": "b" * 64}})
    entries.insert(2, foreign)
    row = coverage_rows(spec, entries, str(tmp_path))[0]
    assert row["state"] == "stale"
    assert row["file_hashes"] == {"result.txt": digest}
    assert "private.txt" not in row["files"]
