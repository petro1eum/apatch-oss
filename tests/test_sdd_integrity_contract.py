from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RFP_PATH = ROOT / "docs/RFP-044-sdd-integrity-adoption.md"
SPEC_PATH = ROOT / "docs/specs/SPEC-SDD-INTEGRITY-1.md"
FREEZE_PATH = ROOT / "docs/contracts/RFP-044-owner-freeze.json"
FROZEN_RFP_PATH = ROOT / "tests/fixtures/public_contracts/RFP-044-owner-frozen.md"
EXPECTED_RFP_HASH = "deb673de57fef35d0f2369581641164c11645bc8d9a8232dab3abf172ce743ae"
EXPECTED_RFP_COMMIT = "381f9acb402af188636db631e9a8e4d701f208ca"
EXPECTED_IDS = [f"CORE-SDD-{index}" for index in range(1, 12)]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_rfp_044_owner_freeze_and_traceability_are_exact() -> None:
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    frozen = FROZEN_RFP_PATH.read_bytes()
    current = RFP_PATH.read_bytes()
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert freeze["schema"] == "apatch.sdd.owner-rfp-freeze.v1"
    assert freeze["source_commit"] == EXPECTED_RFP_COMMIT
    assert freeze["source_hash"] == f"sha256:{EXPECTED_RFP_HASH}"
    assert freeze["acceptance_ids"] == EXPECTED_IDS
    assert freeze["source_implementation_allowed"] is False
    assert freeze["upstream"]["source_hash"] == (
        "sha256:cdfad4871f81abb6c26d762ab23cf4350aa07eed7d3d69f7a0a230538913ea1d"
    )
    assert _sha256(frozen) == EXPECTED_RFP_HASH
    assert _sha256(current) == EXPECTED_RFP_HASH

    mappings = re.findall(r"^\| (CORE-SDD-\d+) \| (R\d+) \| covered \|$", spec, re.MULTILINE)
    assert mappings == [
        (acceptance_id, f"R{index}")
        for index, acceptance_id in enumerate(EXPECTED_IDS, 1)
    ]
    commands = re.findall(r"^\(verify: ([^)]+)\)$", spec, re.MULTILINE)
    assert len(commands) == 14
    assert "## R12 Mandatory strict profile admission" in spec
    assert "## R13 Fixed-purpose MCP implementation binding" in spec
    assert all("TODO" not in command and "::test_" in command for command in commands)
