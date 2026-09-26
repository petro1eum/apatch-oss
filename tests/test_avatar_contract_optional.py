"""Regression: APatch never imports a top-level ``avatar_contract`` module.

Owner decision 2026-09-26 (RFP-049, SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1 R2): the
canonical avatar-contract is bundled byte-identical as
``apatch._vendor.avatar_contract``. A separately installed ``avatar-contract``
distribution of any version (missing, stale or hostile) must neither be imported
nor change behaviour, and contribution building must work with the top-level
name unavailable. Earlier releases imported the top-level package lazily and
degraded to ``AVATAR_CONTRACT_UNAVAILABLE``; that peer model is retired.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_BLOCKED = r"""
import sys

class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == "avatar_contract" or name.startswith("avatar_contract."):
            raise ImportError("blocked: top-level avatar_contract must never be imported")
        return None

sys.meta_path.insert(0, _Block())
"""

_STALE = r"""
import sys, types

stale = types.ModuleType("avatar_contract")
stale.__file__ = "/tmp/stale/avatar_contract/__init__.py"

class ContributionEvent:  # a stale, incompatible external class
    def __init__(self, *a, **k):
        raise AssertionError("stale external avatar_contract was used")

stale.ContributionEvent = ContributionEvent
stale.TRUST_LEVELS = ("audit",)
stale.RECOMMENDED_ALLOWED_KEYS = ("schema_version",)
sys.modules["avatar_contract"] = stale
"""

_EXERCISE = r"""
import apatch.avatar_delivery as D
import apatch.avatar_evidence  # noqa: F401
import apatch.concept_verify  # noqa: F401
import apatch.contribution as C
import apatch.edge_lockstep as E
import apatch.episode  # noqa: F401
import apatch.outcome_delivery  # noqa: F401
import apatch.taxonomy_delivery  # noqa: F401
import apatch.timesheet as T
import apatch.work_asset_lifecycle  # noqa: F401
import apatch.work_asset_suggest  # noqa: F401
import apatch.work_assets  # noqa: F401
from apatch._vendor.avatar_contract import ContributionEvent as Bundled

assert {"attestation", "proof_ref", "methodology_tags"} <= T._ALLOWED_EVENT_KEYS
event = C.build_event({"session_id": "probe", "intent": "probe"}, ledger_rows=[])
assert isinstance(event, Bundled), type(event).__mro__
event.validate()
assert C.current_schema_version() == 3
assert E.contribution_lockstep(".")["status"] == "green"
report = D.avatar_runtime_compatibility()
assert report["ok"] is True and report["bundled"] is True, report
assert report["module_path"].endswith(("apatch/_vendor/avatar_contract/__init__.py",
                                       "apatch\\_vendor\\avatar_contract\\__init__.py")), report
leaked = sorted(name for name, module in sys.modules.items()
                if (name == "avatar_contract" or name.startswith("avatar_contract."))
                and getattr(module, "__file__", "") != "/tmp/stale/avatar_contract/__init__.py")
assert not leaked, leaked
print("OK")
"""


def _probe(prelude: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", prelude + _EXERCISE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_contribution_building_works_with_top_level_name_blocked(tmp_path):
    """The public-install scenario: no top-level ``avatar_contract`` is importable."""
    result = _probe(_BLOCKED, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_stale_external_module_is_ignored(tmp_path):
    """A stale external module already in ``sys.modules`` changes nothing."""
    result = _probe(_STALE, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def _top_level_contract_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []

    def named(value: str) -> bool:
        return value == "avatar_contract" or value.startswith("avatar_contract.")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if named(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and named(node.module):
                found.append(node.module)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            if (name in {"import_module", "__import__", "find_spec"}
                    and isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and named(first.value)):
                found.append(name + "(" + first.value + ")")
    return found


def test_apatch_never_imports_top_level_avatar_contract():
    """Static guard over every runtime module, including the vendored package."""
    offenders = {}
    for root_name in ("apatch", "apatch_search_workflows"):
        for path in sorted((ROOT / root_name).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found = _top_level_contract_imports(path)
            if found:
                offenders[path.relative_to(ROOT).as_posix()] = found
    assert not offenders, offenders
