import json

from click.testing import CliRunner

from apatch.cli import cli
from apatch.repair_map import load_repair_map, route

GOOD = {
    "schema_version": 1,
    "rules": [
        {
            "id": "route_to_neighbor_slug",
            "match": {"root_cause": ["other_slug_route"]},
            "action": "Fix routing data",
            "edit_surfaces": ["config/category_routing*.yaml"],
        },
        {
            "id": "top1_relevance",
            "match": {
                "root_cause": ["expected_code_not_top1"],
                "graph_primary_issue": ["top_result_signal_mismatch"],
            },
            "action": "Fix relevance data for the owning slug",
            "edit_surfaces": ["categories/<slug>/*_query_hints.json"],
            "forbidden": ["bare JDE literal in hints"],
            "contract_ref": "§12.4.1",
        },
        {
            "id": "manual_noise",
            "match": {"manual": ["erp order-line noise"]},
            "action": "Sanitizer data",
            "edit_surfaces": ["config/sanitizer*"],
        },
    ],
}


def _write_map(tmp_path, data):
    man = tmp_path / "manifests"
    man.mkdir(exist_ok=True)
    (man / "repair-map.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_load_valid_map(tmp_path):
    _write_map(tmp_path, GOOD)

    out = load_repair_map(str(tmp_path))

    assert out["present"] is True
    assert out["path"].endswith("repair-map.json")
    assert [r["id"] for r in out["rules"]] == ["route_to_neighbor_slug", "top1_relevance", "manual_noise"]
    assert out["errors"] == []


def test_load_absent_map(tmp_path):
    out = load_repair_map(str(tmp_path))

    assert out["present"] is False
    assert out["rules"] == []


def test_validation_collects_errors_and_keeps_valid_rules(tmp_path):
    _write_map(
        tmp_path,
        {
            "rules": [
                {"id": "ok_rule", "match": {"root_cause": ["x"]}, "action": "do", "edit_surfaces": ["a/*.json"]},
                {"id": "no_action", "match": {}, "edit_surfaces": ["a"]},
                {"id": "bad_key", "match": {"weird": ["y"]}, "action": "do", "edit_surfaces": ["a"]},
                {"id": "ok_rule", "match": {}, "action": "dup", "edit_surfaces": ["a"]},
            ]
        },
    )

    out = load_repair_map(str(tmp_path))

    assert [r["id"] for r in out["rules"]] == ["ok_rule"]
    joined = " ".join(out["errors"])
    assert "no_action: missing action" in joined
    assert "unknown match keys" in joined
    assert "duplicate id" in joined


def test_route_precedence_and_manual_exclusion(tmp_path):
    _write_map(tmp_path, GOOD)
    repair = load_repair_map(str(tmp_path))

    # root_cause hit outranks graph-only hit.
    routed = route(repair, {"root_cause": "expected_code_not_top1", "graph_primary_issue": "top_result_signal_mismatch"})
    assert routed[0]["rule_id"] == "top1_relevance"
    assert routed[0]["forbidden"] == ["bare JDE literal in hints"]

    # graph-only hit still routes.
    routed = route(repair, {"root_cause": "something_else", "graph_primary_issue": "top_result_signal_mismatch"})
    assert [r["rule_id"] for r in routed] == ["top1_relevance"]

    # other root cause routes to its own rule.
    routed = route(repair, {"root_cause": "other_slug_route", "graph_primary_issue": None})
    assert [r["rule_id"] for r in routed] == ["route_to_neighbor_slug"]

    # manual rows never match automatically.
    assert route(repair, {"root_cause": "erp order-line noise", "graph_primary_issue": None}) == []


def test_cli_valid_map_and_routing(tmp_path):
    _write_map(tmp_path, GOOD)

    result = CliRunner().invoke(
        cli,
        ["slug", "repair-map", "--target-dir", str(tmp_path), "--root-cause", "other_slug_route", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["routed"][0]["rule_id"] == "route_to_neighbor_slug"


def test_cli_missing_map_exits_nonzero(tmp_path):
    result = CliRunner().invoke(cli, ["slug", "repair-map", "--target-dir", str(tmp_path), "--json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False
