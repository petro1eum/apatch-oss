"""Shared wire corpus: content seals only, no signatures or execution grants."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from apatch import sdd_integrity as sdd

CORPUS = json.loads((Path(__file__).parent / "fixtures/sdd_wire_v1.json").read_text())


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["name"])
def test_core_reads_wire_case_without_rewriting_sealed_input(case):
    original = copy.deepcopy(case)
    if case["valid"]:
        sdd.validate_native_snapshot(case["contract"], case["envelopes"])
        assert sdd.validate_task_envelope(case["envelopes"]["R1"]) == case["envelopes"]["R1"]
    else:
        with pytest.raises(sdd.SddContractError):
            sdd.validate_native_snapshot(case["contract"], case["envelopes"])
    assert case == original


def test_order_unicode_and_golden_bytes_preserve_existing_hash_algorithm():
    data = CORPUS["golden_canonical"]
    assert "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest() == CORPUS["golden_hash"]
    assert sdd.canonical_json(json.loads(data)) == data
    value = CORPUS["cases"][0]
    contract = dict(reversed(list(value["contract"].items())))
    envelope = dict(reversed(list(value["envelopes"]["R1"].items())))
    sdd.validate_native_snapshot(contract, {"R1": envelope})
    assert sdd.validate_task_envelope(envelope)["document_hash"] == envelope["document_hash"]


@pytest.mark.parametrize("case", [c for c in CORPUS["cases"] if c["valid"]], ids=lambda c: c["name"])
def test_current_producer_reproduces_supported_wire_examples_without_hash_changes(case):
    contract = sdd.freeze_contract({**case["contract"], "source_mutation_count": 0})
    assert contract == case["contract"]
    envelope = sdd.validate_task_envelope({k: v for k, v in case["envelopes"]["R1"].items() if k != "document_hash"})
    assert envelope == case["envelopes"]["R1"]
    sdd.validate_native_snapshot(contract, {"R1": envelope})


@pytest.mark.parametrize("seconds", ["900", True, False, None, 1.0, -1, 2**53])
def test_producer_rejects_invalid_budget_before_issuing_a_seal(seconds):
    value = copy.deepcopy(CORPUS["cases"][0]["envelopes"]["R1"])
    value.pop("document_hash")
    value["budgets"]["seconds"] = seconds
    with pytest.raises(sdd.SddContractError, match="budgets.seconds"):
        sdd.validate_task_envelope(value)


def test_unknown_contract_version_cannot_be_downgraded_by_producer_or_capability():
    value = copy.deepcopy(CORPUS["cases"][0]["contract"])
    value["schema"] = "apatch.sdd.verification-contract.v2"
    with pytest.raises(sdd.SddContractError, match="schema"):
        sdd.freeze_contract(value)
    with pytest.raises(sdd.SddContractError, match="schema"):
        sdd.issue_capability(value, CORPUS["cases"][0]["envelopes"]["R1"], actor_id="agent", role="implementation")
