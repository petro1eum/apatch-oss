"""SPEC-HYGIENE-CORE + SPEC-HYGIENE-1 — artifact governance tests."""

from __future__ import annotations

import os
import stat
import time

import pytest

from apatch.artifact_governance import (
    ArtifactClass,
    ArtifactTier,
    InferenceSunsetPolicy,
    LineageContractError,
    RegistryEntry,
    acquire_run_lease,
    artifact_tier,
    build_doctor_hygiene,
    classify_unregistered,
    default_gc_allowed,
    gc_delete_allowed,
    gc_report,
    infer_class_for_path,
    is_static_replay_critical,
    load_registry,
    plan_gc_deletes,
    register_on_write,
    release_run_lease,
    save_inference_sunset_state,
    validate_lineage,
)
from apatch.doctor import run_doctor
from apatch.runtime.events import emit_domain_event
from apatch.session_state import save_session_state
from apatch.apply_session import save_session as save_apply_session
from apatch.spec_run import save_spec_run_state
from tests.test_hygiene1_helpers import provenance_path, read_jsonl, registry_artifacts_path


def test_static_replay_critical_closure():
    assert is_static_replay_critical(".trustchain/ledger/block_1.json")
    assert is_static_replay_critical(".apatch/events.jsonl")
    assert is_static_replay_critical(".apatch/session_state.json")
    assert is_static_replay_critical(".apatch/enforcement.json")
    assert is_static_replay_critical(".apatch/ledger/events.jsonl")
    assert is_static_replay_critical(".apatch/state/session.json")
    assert not is_static_replay_critical("patches.jsonl")
    assert not is_static_replay_critical(".apatch/backups/foo.bak")


def test_gc_allowed_gate():
    ok = RegistryEntry(
        path="patches.jsonl",
        registered=True,
        replay_critical=False,
        gc_allowed=True,
        run_lease_id=None,
    )
    assert gc_delete_allowed(ok, "patches.jsonl")
    blocked = RegistryEntry(
        path="patches.jsonl",
        registered=True,
        replay_critical=False,
        gc_allowed=False,
        run_lease_id=None,
    )
    assert not gc_delete_allowed(blocked, "patches.jsonl")
    assert not gc_delete_allowed(None, "patches.jsonl")
    assert not gc_delete_allowed(ok, ".apatch/session_state.json")


def test_gc_does_not_parse_run_state_files():
    registry = {
        "patches.jsonl": RegistryEntry(
            path="patches.jsonl",
            registered=True,
            gc_allowed=True,
        ),
    }
    with pytest.raises(ValueError, match="parse_run_state"):
        plan_gc_deletes(["patches.jsonl"], registry, parse_run_state=True)
    allowed = plan_gc_deletes(["patches.jsonl"], registry)
    assert allowed == {"patches.jsonl"}


def test_inference_sunset():
    pol = InferenceSunsetPolicy(max_governed_ops=100)
    assert not pol.should_block_governed(inferred_count=0, ops=100)
    assert not pol.should_block_governed(inferred_count=5, ops=99)
    assert pol.should_block_governed(inferred_count=3, ops=100)
    state = save_inference_sunset_state(42)
    assert state["governed_ops_since_inference"] == 42


def test_run_state_lease():
    entry = RegistryEntry(path=".apatch/apply_session.json", registered=True)
    acquire_run_lease(entry, "lease_abc")
    assert entry.run_lease_id == "lease_abc"
    assert entry.gc_allowed is False
    assert entry.replay_critical is True
    assert not gc_delete_allowed(entry, entry.path)
    release_run_lease(entry)
    assert entry.run_lease_id is None
    assert entry.gc_allowed is True
    assert gc_delete_allowed(entry, entry.path)


def test_lineage_required_on_register():
    validate_lineage({"created_by_tool": "apatch_generate", "reason": "batch"})
    with pytest.raises(LineageContractError):
        validate_lineage({"created_by_tool": "x"})
    with pytest.raises(LineageContractError):
        validate_lineage({"reason": "x"})


def test_lineage_dependency_blocks_delete():
    parent = RegistryEntry(
        path=".apatch/apply_session.json",
        registered=True,
        run_lease_id="lease_parent",
        gc_allowed=False,
    )
    child = RegistryEntry(
        path="patches.jsonl",
        registered=True,
        gc_allowed=True,
        depends_on=[".apatch/apply_session.json"],
    )
    reg = {
        ".apatch/apply_session.json": parent,
        "patches.jsonl": child,
    }
    assert not gc_delete_allowed(child, "patches.jsonl", reg)
    release_run_lease(parent)
    assert gc_delete_allowed(child, "patches.jsonl", reg)


def test_taxonomy_classes():
    assert artifact_tier(ArtifactClass.STATE.value) is ArtifactTier.CORE
    assert artifact_tier(ArtifactClass.LEDGER.value) is ArtifactTier.CORE
    assert artifact_tier(ArtifactClass.RUN_STATE.value) is ArtifactTier.MANAGED
    assert artifact_tier(ArtifactClass.EPHEMERAL.value) is ArtifactTier.TRANSIENT
    assert default_gc_allowed(ArtifactClass.EPHEMERAL.value) is True
    assert default_gc_allowed(ArtifactClass.STATE.value) is False
    for cls in ArtifactClass:
        assert artifact_tier(cls.value) in ArtifactTier


def test_register_persists_jsonl(tmp_path):
    register_on_write(
        str(tmp_path),
        ".apatch/session_state.json",
        class_name="STATE",
        created_by_tool="test",
        reason="unit",
    )
    rows = read_jsonl(registry_artifacts_path(str(tmp_path)))
    user_rows = [r for r in rows if r["path"] == ".apatch/session_state.json"]
    assert len(user_rows) == 1
    assert user_rows[0]["class_name"] == "STATE"
    assert user_rows[0]["lineage"]["provenance"] == "registered"
    reg = load_registry(str(tmp_path))
    assert ".apatch/session_state.json" in reg


def test_provenance_edge_append(tmp_path):
    register_on_write(
        str(tmp_path),
        "patches.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_generate",
        reason="batch",
    )
    prov = read_jsonl(provenance_path(str(tmp_path)))
    user_prov = [p for p in prov if p["path"] == "patches.jsonl"]
    assert len(user_prov) == 1
    assert user_prov[0]["lineage"]["created_by_tool"] == "apatch_generate"


def test_infer_scanner_closed_rules(tmp_path):
    (tmp_path / "patches-foo.jsonl").write_text("{}\n", encoding="utf-8")
    bench = tmp_path / ".apatch"
    bench.mkdir()
    (bench / "_bench1.jsonl").write_text("[]\n", encoding="utf-8")
    inferred = classify_unregistered(str(tmp_path))
    assert inferred["patches-foo.jsonl"]["class_name"] == "EPHEMERAL"
    assert inferred["patches-foo.jsonl"]["provenance"] == "inferred"
    assert inferred[".apatch/_bench1.jsonl"]["class_name"] == "GARBAGE"
    assert infer_class_for_path("patches-hygiene-core.jsonl") == "EPHEMERAL"


def test_gc_report_dry_run_no_mutation(tmp_path):
    (tmp_path / "patches-orphan.jsonl").write_text("{}\n", encoding="utf-8")
    apatch = tmp_path / ".apatch"
    apatch.mkdir()
    mystery = apatch / "mystery.dat"
    mystery.write_text("x", encoding="utf-8")

    def snap():
        return {
            p: (
                os.path.getmtime(p),
                os.path.getsize(p),
                stat.S_IMODE(os.stat(p).st_mode),
            )
            for p in (tmp_path / "patches-orphan.jsonl", mystery)
        }

    before = snap()
    report = gc_report(str(tmp_path))
    time.sleep(0.01)
    after = snap()
    assert before == after
    assert report["status"] == "degraded"
    assert report["recommendation"] == "apatch gc --dry-run"
    assert report["classified"]["EPHEMERAL"] >= 1
    assert report["classified"]["UNCLASSIFIED"] >= 1
    assert "classified" in report and "issues" in report


def test_doctor_hygiene_field(tmp_path):
    (tmp_path / "patches-test.jsonl").write_text("{}\n", encoding="utf-8")
    doc = run_doctor(str(tmp_path))
    hygiene = doc.get("hygiene")
    assert hygiene is not None
    assert hygiene["status"] == "degraded"
    assert hygiene["orphan_count"] >= 1
    assert hygiene["gc_recommendation"] == "apatch gc --dry-run"
    assert hygiene["layout"] in ("flat", "structured")
    empty = tmp_path / "empty_ws"
    empty.mkdir()
    assert build_doctor_hygiene(str(empty))["status"] == "clean"


def test_write_path_registry_coverage(tmp_path):
    ws = str(tmp_path)
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir()

    save_session_state(ws, {"phase": "idle", "session_id": "s1"})
    emit_domain_event(ws, "TestEvent", {"k": 1})
    save_apply_session(str(apatch_dir / "apply_session.json"), {"chunk_index": 0})
    save_spec_run_state(str(apatch_dir / "spec_run.json"), {"spec": "SPEC-X"})
    from apatch.project_index import build_project_index

    (tmp_path / "indexed.py").write_text("VALUE = 1\n", encoding="utf-8")
    build_project_index(ws)
    register_on_write(
        ws,
        "patches.jsonl",
        class_name="EPHEMERAL",
        created_by_tool="apatch_generate",
        reason="coverage",
    )

    art_rows = read_jsonl(registry_artifacts_path(ws))
    paths = {r["path"] for r in art_rows}
    assert ".apatch/session_state.json" in paths
    assert ".apatch/events.jsonl" in paths
    assert ".apatch/apply_session.json" in paths
    assert ".apatch/spec_run.json" in paths
    assert ".apatch/project_index.json" in paths
    assert "patches.jsonl" in paths
    for row in art_rows:
        assert row["lineage"]["provenance"] == "registered"
