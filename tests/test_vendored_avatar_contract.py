"""SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1 — the canonical Avatar contract ships inside APatch.

Owner decision 2026-09-26 (RFP-049): "include avatar_contract in APatch OSS". The
canonical MIT package is vendored byte-for-byte (imports rewritten only) as
``apatch._vendor.avatar_contract``; these tests prove identity, packaging and use
without any separately installed ``avatar_contract`` module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = "SPEC-OSS-BUNDLED-AVATAR-CONTRACT-1"
VENDOR = ROOT / "apatch" / "_vendor" / "avatar_contract"
PINNED_COMMIT = "44c8f9ada8a50fb8b7c94346a4103c09a19f15c2"


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vendor_script():
    return _load_script("vendor_avatar_contract", "scripts/vendor_avatar_contract.py")


def _upstream() -> dict:
    return json.loads((VENDOR / "UPSTREAM.json").read_text(encoding="utf-8"))


def test_r0_traceability_and_ownership():
    from apatch.rfp_coverage import rfp_spec_coverage_workspace
    from apatch.spec import parse_spec

    result = rfp_spec_coverage_workspace(str(ROOT), rfp="RFP-049", spec=SPEC)
    assert result["ok"] is True, result
    parsed = parse_spec((ROOT / "docs/specs" / (SPEC + ".md")).read_text(encoding="utf-8"))
    assert parsed.strict_ownership
    ids = [row.id for row in parsed.requirements]
    assert ids == ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]
    assert all(row.verify and row.owns for row in parsed.requirements)
    commands = [row.verify for row in parsed.requirements]
    assert len(set(commands)) == len(commands)
    assert all(command.startswith("python3 -m pytest tests/") for command in commands)
    assert not any(re.search(r"pytest\s+(-q\s+)?tests/?(\s|$)", c) for c in commands)


# --- R1 -------------------------------------------------------------------


def test_r1_vendored_copy_is_byte_identical_to_upstream():
    script = _vendor_script()
    upstream = _upstream()
    assert upstream["repository"] == "https://github.com/petro1eum/avatar-contract"
    assert upstream["commit"] == PINNED_COMMIT
    assert upstream["version"] == "0.7.2"
    assert upstream["license"] == "MIT"
    assert upstream["vendored_as"] == "apatch._vendor.avatar_contract"
    assert upstream["rewrite"]["from_import"] == {
        "pattern": r"^(\s*)from avatar_contract(\.| )",
        "replacement": r"\1from apatch._vendor.avatar_contract\2",
    }
    package = sorted(name for name in upstream["files"] if name.startswith("avatar_contract/"))
    assert len(package) == 17
    assert {"avatar_contract/__init__.py", "avatar_contract/transport/avatar_bff.v1.json",
            "avatar_contract/schema/contribution_event.v3.json"} <= set(package)
    assert set(upstream["files"]) == set(package) | {"LICENSE"}

    # Independent inverse rewrite (not only the script's own check).
    inverse = re.compile(rb"^(\s*)from apatch\._vendor\.avatar_contract(\.| )", re.M)
    for name, digest in upstream["files"].items():
        relative = "LICENSE" if name == "LICENSE" else name[len("avatar_contract/"):]
        content = (VENDOR / relative).read_bytes()
        original = inverse.sub(rb"\1from avatar_contract\2", content) if name.endswith(".py") else content
        assert hashlib.sha256(original).hexdigest() == digest, name
        if not name.endswith(".py"):
            assert b"apatch._vendor" not in content, name

    license_text = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License") and "Ed Cherednik" in license_text
    result = script.check()
    assert result["ok"] is True and result["commit"] == PINNED_COMMIT
    assert result["files"] == 18

    cli = subprocess.run([sys.executable, str(ROOT / "scripts/vendor_avatar_contract.py"), "--check"],
                         capture_output=True, text=True, timeout=60)
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert json.loads(cli.stdout)["ok"] is True


@pytest.mark.parametrize("case", [
    "changed_byte", "non_mechanical_edit", "extra_file", "missing_file",
    "changed_manifest_hash", "changed_rewrite_rule", "wrong_license", "symlink",
])
def test_r1_check_rejects_drifted_vendored_copy(tmp_path, case):
    script = _vendor_script()
    copy = tmp_path / "avatar_contract"
    shutil.copytree(VENDOR, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert script.check(copy)["ok"] is True
    manifest_path = copy / "UPSTREAM.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if case == "changed_byte":
        target = copy / "contribution_event.py"
        target.write_bytes(target.read_bytes().replace(b"SCHEMA_VERSION = 3", b"SCHEMA_VERSION = 4", 1))
    elif case == "non_mechanical_edit":
        target = copy / "__init__.py"
        target.write_bytes(target.read_bytes() + b"\nfrom apatch._vendor.avatar_contract import identity\n")
    elif case == "extra_file":
        (copy / "unreviewed.py").write_text("unreviewed = True\n")
    elif case == "missing_file":
        (copy / "transport" / "avatar_bff.v1.json").unlink()
    elif case == "changed_manifest_hash":
        manifest["files"]["avatar_contract/identity.py"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
    elif case == "changed_rewrite_rule":
        manifest["rewrite"]["from_import"]["replacement"] = r"\1from elsewhere\2"
        manifest_path.write_text(json.dumps(manifest))
    elif case == "wrong_license":
        manifest["license"] = "Proprietary"
        manifest_path.write_text(json.dumps(manifest))
    elif case == "symlink":
        (copy / "ownership.py").unlink()
        (copy / "ownership.py").symlink_to(VENDOR / "ownership.py")
    with pytest.raises(script.VendorError):
        script.check(copy)


# --- R4 -------------------------------------------------------------------


def test_r4_bundled_resources_load_through_importlib_resources():
    from importlib.resources import files

    from apatch._vendor.avatar_contract.transport import (
        TRANSPORT_CONTRACTS,
        contract_sha256,
        load_transport_contract,
    )

    upstream = _upstream()["files"]
    root = files("apatch._vendor.avatar_contract")
    for name in TRANSPORT_CONTRACTS:
        contract = load_transport_contract(name)
        assert contract["name"] == name
        assert contract_sha256(name) == upstream["avatar_contract/transport/" + name + ".v1.json"]
    for schema in ("contribution_event.v3.json", "capability_evidence.v2.json",
                   "work_review_package.v1.json"):
        raw = root.joinpath("schema").joinpath(schema).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == upstream["avatar_contract/schema/" + schema]
        assert isinstance(json.loads(raw), dict)
    assert root.joinpath("UPSTREAM.json").is_file() and root.joinpath("LICENSE").is_file()

    for relative in ("tests/test_transport_contract_avatar_bff.py", "tests/test_edge_lockstep.py",
                     "tests/test_contribution_event.py", "tests/test_concept_verify.py",
                     "tests/test_concept_status.py", "tests/test_concept_cli.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'importorskip("avatar_contract")' not in text, relative
        assert "import avatar_contract" not in text, relative


# --- R5 -------------------------------------------------------------------

_E2E_PROBE = r"""
import json
import sys


class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == "avatar_contract" or name.startswith("avatar_contract."):
            raise ImportError("blocked: top-level avatar_contract is not installed")
        return None


sys.meta_path.insert(0, _Block())

from apatch.runtime.runtime import MutationRuntime
from apatch.runtime.session import start_session
from apatch.session_state import PHASE_COMPLETE, load_session_state, save_session_state
from apatch.trustchain_helper import TrustChainHelper

workspace = sys.argv[1]
start_session(workspace, "bundled avatar contract end-to-end")
session_id = load_session_state(workspace)["session_id"]
chain = TrustChainHelper(workspace, auto_init=True)
assert chain.commit_action("apatch_apply_session", {
    "governed_session_id": session_id,
    "files": {"src/app.py": {}},
    "insertions": 3,
    "deletions": 1,
})
state = load_session_state(workspace)
state["phase"] = PHASE_COMPLETE
save_session_state(workspace, state)
result = MutationRuntime(workspace).attest(message="bundled avatar contract end-to-end")
print(json.dumps({
    "ok": result.get("ok"),
    "committed": result.get("committed"),
    "contribution_receipt": result.get("contribution_receipt"),
    "session_id": session_id,
    "top_level_modules": sorted(name for name in sys.modules
                                if name == "avatar_contract" or name.startswith("avatar_contract.")),
}))
"""


def test_r5_attest_emits_contribution_without_top_level_package(tmp_path):
    workspace, home, store = tmp_path / "workspace", tmp_path / "home", tmp_path / "store"
    workspace.mkdir()
    home.mkdir()
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
           if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), APATCH_CONTRIB_STORE=str(store),
               PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    probe = subprocess.run([sys.executable, "-c", _E2E_PROBE, str(workspace)], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=180)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    report = json.loads(probe.stdout.strip().splitlines()[-1])
    assert report["ok"] is True and report["committed"] is True
    receipt = report["contribution_receipt"]
    assert receipt["status"] == "emitted", receipt
    assert "AVATAR_CONTRACT_UNAVAILABLE" not in probe.stdout
    assert report["top_level_modules"] == []

    written = list(store.glob("*/*.json"))
    assert [path.stem for path in written] == [receipt["event_id"]]
    from apatch._vendor.avatar_contract import ContributionEvent

    event = ContributionEvent.from_wire(json.loads(written[0].read_text(encoding="utf-8")))
    event.validate()
    assert event.event_id == receipt["event_id"]
    assert event.kind == "fact" and event.schema_version == 3
    assert event.session.get("session_id") == report["session_id"]


# --- R6 -------------------------------------------------------------------


def test_r6_distribution_declares_bundled_contract():
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        import tomli as tomllib

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)
    data = project["tool"]["setuptools"]["package-data"]["apatch._vendor.avatar_contract"]
    assert set(data) == {"schema/*.json", "transport/*.json", "LICENSE", "UPSTREAM.json"}
    include = project["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "apatch.*" in include and "avatar_contract" not in include
    assert "avatar" not in project["project"]["optional-dependencies"]
    requirements = list(project["project"]["dependencies"])
    for extra in project["project"]["optional-dependencies"].values():
        requirements.extend(extra)
    assert not any("avatar" in item or " @ " in item or "git+" in item for item in requirements)
    assert "git+" not in text
    non_python = sorted(path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob("*")
                        if path.is_file() and path.suffix != ".py" and "__pycache__" not in path.parts)
    assert non_python == sorted(
        ["LICENSE", "UPSTREAM.json"]
        + ["schema/" + p.name for p in (VENDOR / "schema").glob("*.json")]
        + ["transport/" + p.name for p in (VENDOR / "transport").glob("*.json")]
    )


# --- R7 -------------------------------------------------------------------


def test_r7_qualification_inventory_has_no_avatar_waivers():
    qualify = _load_script("oss_qualification_bundled", "scripts/qualify_oss.py")
    raw = (ROOT / "docs/oss-verification-profiles.json").read_text(encoding="utf-8")
    inventory = qualify.load_inventory(ROOT / "docs/oss-verification-profiles.json")
    assert "avatar_contract" not in qualify.DEPENDENCIES
    assert "absent_peer_failures" not in inventory and "peer_requirements" not in inventory
    assert [row["dependency"] for row in inventory["existing_optional_skips"]] == ["tree_sitter_java"]
    assert [row["spec"] for row in inventory["unproven_external_specs"]] == ["SPEC-AVATAR-CONTRACT-1"]
    assert '"dependency": "avatar_contract"' not in raw
    vendored = {name for name in inventory["runtime_files"] if name.startswith("apatch/_vendor/")}
    on_disk = {path.relative_to(ROOT).as_posix() for path in (ROOT / "apatch/_vendor").rglob("*")
               if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    assert vendored == on_disk
    reintroduced = json.loads(raw)
    reintroduced["absent_peer_failures"] = []
    with pytest.raises(qualify.InventoryError):
        qualify.validate_inventory(reintroduced)
    assert qualify.bundled_contract_pin(ROOT) == PINNED_COMMIT
