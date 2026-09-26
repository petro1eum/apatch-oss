"""Exact existing-source intake tests."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from apatch.existing_source_intake import validate_existing_sources
from apatch.git_commit import commit_attested_workspace
from apatch.shared_maintenance import prepare_shared_maintenance, shared_maintenance_workspace


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _fixture(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    docs = root / "docs" / "specs"
    docs.mkdir(parents=True)
    (root / "manifests").mkdir()
    specs = ["SPEC-A", "SPEC-B"]
    requirements = {}
    originals = {}
    for sid in specs:
        rel = f"manifests/{sid}.run.json"
        (docs / f"{sid}.md").write_text(
            f"# {sid}\n> **apatch artifact:** `spec:{sid}`\n"
            f"## R1 Admit existing manifest\n(verify: test -f {rel})\n",
            encoding="utf-8",
        )
        content = f'{{"spec":"{sid}"}}\n'.encode()
        (root / rel).write_bytes(content)
        originals[rel] = content
        requirements[sid] = {"R1": {"needles": [{
            "action": "intake", "target_file": rel,
            "sha256": hashlib.sha256(content).hexdigest(),
        }]}}
    _git(root, "add", ".gitignore", "docs")
    _git(root, "commit", "-qm", "baseline")
    return root, specs, requirements, originals


def test_intake_preflight_accepts_only_exact_untracked_hashes(tmp_path):
    root, specs, requirements, originals = _fixture(tmp_path)
    needles = [requirements[sid]["R1"]["needles"][0] for sid in specs]
    partition = {f"spec:{sid}#R1": [f"manifests/{sid}.run.json"] for sid in specs}
    result = validate_existing_sources(str(root), needles, partition)
    assert result["ok"] is True
    assert result["target_count"] == 2
    assert all(meta["content_mutated"] is False for meta in result["files"].values())

    (root / "manifests/SPEC-A.run.json").write_text("drift\n")
    assert validate_existing_sources(str(root), needles, partition)["error_type"] == "SOURCE_INTAKE_HASH_DRIFT"
    (root / "manifests/SPEC-A.run.json").write_bytes(originals["manifests/SPEC-A.run.json"])
    _git(root, "add", "manifests/SPEC-A.run.json")
    assert validate_existing_sources(str(root), needles, partition)["error_type"] == "SOURCE_INTAKE_INDEX_DIRTY"


def test_intake_rejects_ignored_symlink_wrong_owner_and_mixed_action(tmp_path):
    root, specs, requirements, _originals = _fixture(tmp_path)
    needles = [requirements[sid]["R1"]["needles"][0] for sid in specs]
    partition = {f"spec:{sid}#R1": [f"manifests/{sid}.run.json"] for sid in specs}
    wrong = {
        "spec:SPEC-A#R1": ["manifests/SPEC-B.run.json"],
        "spec:SPEC-B#R1": ["manifests/SPEC-A.run.json"],
    }
    assert validate_existing_sources(str(root), needles, wrong)["error_type"] == "SOURCE_INTAKE_OWNER_MISMATCH"

    ignored = root / "ignored" / "entry.json"
    ignored.parent.mkdir()
    ignored.write_text("ignored\n")
    bad = dict(needles[0], target_file="ignored/entry.json", sha256=hashlib.sha256(ignored.read_bytes()).hexdigest())
    assert validate_existing_sources(str(root), [bad], {"spec:SPEC-A#R1": ["ignored/entry.json"]})["error_type"] == "SOURCE_INTAKE_NOT_UNTRACKED"

    link = root / "manifests" / "link.json"
    link.symlink_to(root / "manifests/SPEC-A.run.json")
    bad = dict(needles[0], target_file="manifests/link.json")
    assert validate_existing_sources(str(root), [bad], {"spec:SPEC-A#R1": ["manifests/link.json"]})["error_type"] == "SOURCE_INTAKE_UNSAFE_PATH"

    requirements["SPEC-B"]["R1"]["needles"][0] = {
        "action": "replace", "target_file": "manifests/SPEC-B.run.json",
        "find_text": "old", "replace_text": "new",
    }
    prepared = prepare_shared_maintenance(str(root), specs=specs, requirements=requirements)
    assert prepared["ok"] is False
    assert prepared["error_type"] == "SPEC_SHARED_MAINTENANCE_INVALID"


def test_variant_run_manifests_use_base_spec_owner(tmp_path):
    root, _specs, _requirements, _originals = _fixture(tmp_path)
    for suffix in ("v2", "attest", "virtual-b", "attest_tail"):
        rel = f"manifests/SPEC-A.{suffix}.run.json"
        data = b'{"spec":"SPEC-A"}\n'
        (root / rel).write_bytes(data)
        needle = {"action": "intake", "target_file": rel,
                  "sha256": hashlib.sha256(data).hexdigest()}
        assert validate_existing_sources(
            str(root), [needle], {"spec:SPEC-A#R1": [rel]}
        )["ok"] is True
        assert validate_existing_sources(
            str(root), [needle], {"spec:SPEC-B#R1": [rel]}
        )["error_type"] == "SOURCE_INTAKE_OWNER_MISMATCH"


def test_partitioned_intake_signs_unchanged_source_for_exact_commit(tmp_path):
    root, specs, requirements, originals = _fixture(tmp_path)
    prepared = prepare_shared_maintenance(str(root), specs=specs, requirements=requirements)
    assert prepared["ok"] is True
    assert prepared["mode"] == "source_intake"

    result = shared_maintenance_workspace(
        str(root), specs=specs, requirements=requirements,
        verify_jobs=1, verify_timeout=30,
    )
    assert result["ok"] is True, result
    assert result["source_content_mutated"] is False
    for rel, data in originals.items():
        assert (root / rel).read_bytes() == data

    proof = commit_attested_workspace(
        str(root), session_ids=[result["session_id"]],
        message="Admit exact existing sources", dry_run=True,
    )
    assert proof["ok"] is True, proof
    assert proof["status"] == "validated"
    assert proof["files"] == sorted(originals)


def test_single_spec_intake_uses_same_hash_owner_and_signed_commit(tmp_path, monkeypatch):
    from apatch.remote.worker import dispatch

    root, specs, requirements, originals = _fixture(tmp_path)
    spec = specs[0]
    monkeypatch.chdir(root)
    plan = {
        "single_source_intake": True,
        "specs": [spec],
        "requirements": {spec: requirements[spec]},
        "execution_mode": "shared_maintenance",
        "verify_jobs": 1,
        "verify_timeout": 30,
    }
    result = dispatch({
        "operation": "apatch_spec_run_multi",
        "arguments": {"plan": plan},
    })
    assert result["ok"] is True, result
    assert result["intaken"] == 1
    assert result["source_content_mutated"] is False
    rel = f"manifests/{spec}.run.json"
    assert (root / rel).read_bytes() == originals[rel]
    proof = commit_attested_workspace(
        str(root), session_ids=[result["session_id"]],
        message="Admit lone exact source", dry_run=True,
    )
    assert proof["ok"] is True, proof
    assert proof["files"] == [rel]


def test_older_execute_next_controller_reaches_same_single_file_intake(tmp_path, monkeypatch):
    from apatch.remote.worker import dispatch

    root, specs, requirements, originals = _fixture(tmp_path)
    spec = specs[0]
    monkeypatch.chdir(root)
    needle = requirements[spec]["R1"]["needles"][0]
    result = dispatch({
        "operation": "apatch_execute_next",
        "arguments": {"plan": {
            "execute_next": True,
            "single_source_intake": True,
            "spec": spec,
            "requirement": f"{spec}#R1",
            "needles": [needle],
        }},
    })
    assert result["ok"] is True, result
    assert result["intaken"] == 1
    assert result["source_content_mutated"] is False
    assert (root / needle["target_file"]).read_bytes() == originals[needle["target_file"]]


def test_single_spec_intake_rejects_replace_and_wrong_hash(tmp_path, monkeypatch):
    from apatch.remote.worker import dispatch

    root, specs, requirements, _originals = _fixture(tmp_path)
    spec = specs[0]
    monkeypatch.chdir(root)
    plan = {
        "single_source_intake": True,
        "specs": [spec],
        "requirements": {spec: requirements[spec]},
        "execution_mode": "shared_maintenance",
    }
    wrong_hash = dict(requirements[spec]["R1"]["needles"][0], sha256="0" * 64)
    plan["requirements"] = {spec: {"R1": {"needles": [wrong_hash]}}}
    rejected = dispatch({"operation": "apatch_spec_run_multi", "arguments": {"plan": plan}})
    assert rejected["error_type"] == "APPLY_FAILED", rejected
    assert rejected["error"] == "File differs from declared SHA-256."
    assert not (root / ".trustchain").exists()

    plan["requirements"] = {spec: {"R1": {"needles": [{
        "action": "replace", "target_file": f"manifests/{spec}.run.json",
        "find_text": "old", "replace_text": "new",
    }]}}}
    rejected = dispatch({"operation": "apatch_spec_run_multi", "arguments": {"plan": plan}})
    assert rejected["error_type"] == "SPEC_SHARED_MAINTENANCE_INVALID"


def test_full_multi_spec_orchestrator_accepts_intake_without_rewriting_bytes(tmp_path):
    from apatch.spec_run_multi import spec_run_multi_workspace

    root, specs, requirements, originals = _fixture(tmp_path)
    result = spec_run_multi_workspace(
        str(root),
        specs=specs,
        requirements=requirements,
        execution_mode="shared_maintenance",
        re_interference=False,
        cross_verify=False,
        verify_jobs=1,
        verify_timeout=30,
    )
    assert result["ok"] is True, result
    assert result["intaken"] == 2
    for rel, data in originals.items():
        assert (root / rel).read_bytes() == data
