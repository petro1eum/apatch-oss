from pathlib import Path

import pytest

from tests import test_sdd_integrity_contract as frozen_gate


def test_portable_frozen_rfp_keeps_original_acceptance_gate():
    frozen_gate.test_rfp_044_owner_freeze_and_traceability_are_exact()


def test_original_gate_rejects_tampered_portable_rfp(tmp_path, monkeypatch):
    altered = tmp_path / "frozen.md"
    altered.write_bytes(frozen_gate.FROZEN_RFP_PATH.read_bytes() + b"\nchanged\n")
    monkeypatch.setattr(frozen_gate, "FROZEN_RFP_PATH", altered)
    with pytest.raises(AssertionError):
        frozen_gate.test_rfp_044_owner_freeze_and_traceability_are_exact()


def test_original_gate_rejects_missing_portable_rfp(tmp_path, monkeypatch):
    monkeypatch.setattr(frozen_gate, "FROZEN_RFP_PATH", tmp_path / "absent.md")
    with pytest.raises(FileNotFoundError):
        frozen_gate.test_rfp_044_owner_freeze_and_traceability_are_exact()
