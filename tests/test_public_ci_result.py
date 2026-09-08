import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_public_conformance.py"


def _accepted(value):
    spec = importlib.util.spec_from_file_location("public_conformance_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.accepted(value)


def _passed():
    return dict(enabled=True, ok=True, contract_holds=True, gate="passed", gated=91, verified_live=91)


def test_public_ci_accepts_real_live_success_with_explicit_advisories():
    result = _passed()
    result["unproven"] = ["SPEC-EXTERNAL-1"]
    assert _accepted(result)


@pytest.mark.parametrize("change", [
    {"ok": False, "contract_holds": False, "gate": "failed"},
    {"enabled": False, "gate": "skipped"},
    {"gated": 0},
    {"verified_live": 0},
    {"ok": "true"},
])
def test_public_ci_rejects_failed_disabled_or_empty_check(change):
    assert not _accepted({**_passed(), **change})


def test_public_ci_returns_nonzero_for_semantic_failure(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({**_passed(), "contract_holds": False, "gate": "failed"}))
    process = subprocess.run([sys.executable, str(SCRIPT), str(result)], capture_output=True)
    assert process.returncode != 0
