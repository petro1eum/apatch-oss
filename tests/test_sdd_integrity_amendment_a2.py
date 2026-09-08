from __future__ import annotations

import os
from pathlib import Path

import pytest

from apatch import sdd_integrity
from apatch import tool_paths


def _envelope() -> dict:
    return {
        "schema": "apatch.sdd.task-envelope.v1",
        "requirement": "SPEC-X#R5",
        "contract_hash": "sha256:" + "1" * 64,
        "baseline_hash": "sha256:" + "2" * 64,
        "allowed_reads": ["src/**"],
        "allowed_writes": ["src/feature.py"],
        "allowed_symbols": ["feature.apply"],
        "forbidden_paths": ["tests/**"],
        "tools": ["apatch_apply_session"],
        "commands": [["python3", "-m", "pytest", "tests/test_feature.py", "-q"]],
        "network": [],
        "remote": [],
        "services": [],
        "budgets": {"files": 1, "insertions": 40, "deletions": 20, "seconds": 300},
        "checks": ["AC-1"],
        "rollback_owner": "session:self",
        "containment": "mediated_only",
    }


def test_hash_bound_task_envelope_rejects_content_tampering() -> None:
    sealed = sdd_integrity.validate_task_envelope(_envelope())
    tampered = {**sealed, "allowed_writes": ["src/other.py"]}

    with pytest.raises(sdd_integrity.SddContractError, match="hash does not match"):
        sdd_integrity.validate_task_envelope(tampered)


def test_verify_child_drops_only_foreign_apatch_pythonpath(tmp_path: Path) -> None:
    stale_apatch_root = tmp_path / "stale-apatch"
    (stale_apatch_root / "apatch").mkdir(parents=True)
    user_root = tmp_path / "user-library"
    user_root.mkdir()
    current_apatch_root = Path(tool_paths.__file__).resolve().parent.parent
    inherited = os.pathsep.join(
        (str(stale_apatch_root), str(user_root), str(current_apatch_root))
    )

    env = tool_paths.build_subprocess_env(
        str(tmp_path),
        base_env={"PATH": "/usr/bin", "PYTHONPATH": inherited},
    )

    assert env["PYTHONPATH"].split(os.pathsep) == [
        str(user_root),
        str(current_apatch_root),
    ]
