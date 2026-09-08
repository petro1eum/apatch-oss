"""P4: extended project index (migrations, ADR) and TrustChain intent history."""

import json

from apatch.pipeline_run import run_engineering_pipeline
from apatch.trustchain_helper import TrustChainHelper
from apatch.workflows import (
    index_build_workspace,
    index_query_workspace,
    trustchain_intent_history_workspace,
)


def test_index_includes_migrations_and_adr(tmp_path):
    mig_dir = tmp_path / "alembic" / "versions"
    mig_dir.mkdir(parents=True)
    (mig_dir / "0001_init.py").write_text("revision = 'x'\n", encoding="utf-8")
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "ADR-001-user-model.md").write_text(
        "# User model refactor\n", encoding="utf-8"
    )
    built = index_build_workspace(str(tmp_path))
    assert built["version"] == 2
    assert any("0001_init.py" in p for p in built["migration_files"])
    assert built["adr_docs"]
    assert built["adr_docs"][0]["adr_id"] == "ADR-001"
    hit = index_query_workspace(str(tmp_path), "ADR-001")
    assert hit["adr_docs"]
    mig_hit = index_query_workspace(str(tmp_path), "0001")
    assert mig_hit["migration_files"]


def test_trustchain_intent_history(tmp_path):
    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    payload = {
        "value": {
            "tool_id": "apatch",
            "data": {
                "action": "engineering_pipeline",
                "intent": "refactor user model",
                "adr": "ADR-000",
                "manifest": "pipe.json",
            },
            "signature": "abc123sig",
            "id": "op-1",
        }
    }
    (tc_dir / "entry1.json").write_text(json.dumps(payload), encoding="utf-8")

    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    hist = helper.list_intent_history()
    assert hist["ok"] is True
    assert len(hist["entries"]) == 1
    assert hist["entries"][0]["adr"] == "ADR-000"

    filtered = trustchain_intent_history_workspace(str(tmp_path), query="user model")
    assert filtered["entries"]
    miss = trustchain_intent_history_workspace(str(tmp_path), query="nonexistent")
    assert miss["entries"] == []


def test_pipeline_trustchain_history_phase(tmp_path):
    tc_dir = tmp_path / ".trustchain" / "objects"
    tc_dir.mkdir(parents=True)
    (tmp_path / ".trustchain" / "HEAD").write_text("", encoding="utf-8")
    payload = {
        "value": {
            "tool_id": "apatch",
            "data": {
                "action": "engineering_pipeline",
                "intent": "rename billing service",
                "adr": "ADR-042",
                "manifest": "pipe.json",
            },
        }
    }
    (tc_dir / "prior.json").write_text(json.dumps(payload), encoding="utf-8")

    manifest = {
        "kind": "engineering-pipeline",
        "intent": "rename billing service",
        "phases": [
            {"name": "context", "action": "trustchain_history", "query": "billing"},
            {"name": "noop", "action": "index_build"},
        ],
    }
    mpath = tmp_path / "pipe.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_engineering_pipeline(str(mpath), str(tmp_path), dry_run=True)
    assert result["ok"] is True
    ctx = result["phases"][0]
    assert ctx["action"] == "trustchain_history"
    assert ctx["count"] >= 1
    assert ctx["latest"]["adr"] == "ADR-042"
