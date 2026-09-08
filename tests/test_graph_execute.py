import json

import pytest

from apatch.graph_execute import (
    build_execution_graph,
    execute_graph,
    plan_graph,
    topological_order,
)
from apatch.workflows import WorkflowError


def test_topological_order_simple():
    order = topological_order(
        ["plan", "apply_session", "db_check"],
        [["plan", "apply_session"], ["apply_session", "db_check"]],
    )
    assert order.index("plan") < order.index("apply_session") < order.index("db_check")


def test_topological_order_cycle():
    with pytest.raises(WorkflowError):
        topological_order(["a", "b"], [["a", "b"], ["b", "a"]])


def test_build_execution_graph_from_manifest():
    manifest = {
        "phases": [
            {"action": "plan"},
            {"action": "apply"},
            {"action": "db_check"},
            {"action": "arch_check"},
        ]
    }
    graph = build_execution_graph(manifest)
    assert "plan" in graph["nodes"]
    assert "apply_session" in graph["nodes"]
    assert graph["order"].index("plan") < graph["order"].index("apply_session")
    assert graph["order"].index("apply_session") < graph["order"].index("db_check")


def test_plan_graph_writes_file(tmp_path):
    manifest = tmp_path / "pipe.json"
    manifest.write_text(
        json.dumps({
            "kind": "engineering-pipeline",
            "patches_jsonl": "p.jsonl",
            "phases": [{"action": "plan"}, {"action": "apply"}],
        }),
        encoding="utf-8",
    )
    result = plan_graph(str(manifest), str(tmp_path))
    assert result["ok"] is True
    graph_file = tmp_path / ".apatch" / "execution_graph.json"
    assert graph_file.is_file()
    data = json.loads(graph_file.read_text(encoding="utf-8"))
    assert "apply_session" in data["order"]


def test_execute_graph_dry_run(tmp_path):
    manifest = tmp_path / "pipe.json"
    (tmp_path / "p.jsonl").write_text("", encoding="utf-8")
    manifest.write_text(
        json.dumps({
            "kind": "engineering-pipeline",
            "patches_jsonl": "p.jsonl",
            "phases": [
                {"action": "plan"},
                {"action": "apply"},
                {"action": "arch_check"},
            ],
        }),
        encoding="utf-8",
    )
    result = execute_graph(str(tmp_path), manifest_path=str(manifest), dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert len(result["node_results"]) >= 2
