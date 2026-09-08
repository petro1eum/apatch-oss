import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.reality import add_reality_record
from apatch.slug_cockpit import slug_cockpit_workspace


def _write_cockpit_fixture(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    rec_id = add_reality_record(
        str(tmp_path),
        id="REC-otvod-feedback",
        summary="Client query for otvod does not return the expected item",
        source="feedback",
        kind="bug",
    )
    (specs / "SPEC-OTVOD-1.md").write_text(
        f"""# SPEC-OTVOD-1

> **apatch artifact:** `spec:SPEC-OTVOD-1`

## R0 Data map (verify: python3 -c "print('catalog enrich otvod data')")

Map otvod semantic type to real catalog params.

## R1 Query-first live search gate (verify: python3 -c "print('query items otvod')")

discharges: {rec_id}
""",
        encoding="utf-8",
    )
    atomic = tmp_path / "atomic"
    atomic.mkdir()
    (atomic / "otvod_specs.json").write_text('{"otvod": ["sml"]}\n', encoding="utf-8")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "slug-intake.json").write_text(
        json.dumps(
            {
                "dictionary_roots": ["atomic"],
                "hooks": {
                    "data_audit": "python tools/audit_catalog.py {slug}",
                    "query_verify": "pytest tests/live/test_{slug}_queries.py",
                },
            }
        ),
        encoding="utf-8",
    )
    (manifests / "repair-map.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "top1_relevance",
                        "match": {
                            "root_cause": ["expected_code_not_top1"],
                            "graph_primary_issue": ["top_result_signal_mismatch"],
                        },
                        "action": "Fix relevance data for the owning slug",
                        "edit_surfaces": ["categories/<slug>/*_query_hints.json"],
                    },
                    {
                        "id": "honest_catalog_gap",
                        "match": {"root_cause": ["catalog_gap_zero_results_after_applied_filters"]},
                        "action": "Mark catalog_gap in the triage TSV",
                        "edit_surfaces": ["tests/regressions/<slug>_feedback_triage.tsv"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir(exist_ok=True)
    (apatch_dir / "conformance.json").write_text(
        json.dumps({"enabled": True, "contract": {"specs": ["SPEC-OTVOD-1"]}}),
        encoding="utf-8",
    )
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note",
                "fb_fixed\tui:1\tОтвод SML DN100 45\tdislike\tneeds_human_review\totvod\t\t\t\told",
                "fb_mismatch\tui:2\tОтвод стальной Ду20\tdislike\tneeds_human_review\totvod\t027-9999\t\t\told",
                "fb_gap\tui:3\tОтвод PP DN40 45\tdislike\tcatalog_gap\totvod\t\t\t\told",
                "fb_other\tui:4\tКоллектор с 2 отводами\tdislike\tother_slug\totvod\t\t\t\told",
                "fb_positive\tui:5\tОтвод PP DN50\tlike\tpositive_feedback\totvod\t\t\t\towner-positive",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (reg / "feedback_approved_contract.tsv").write_text(
        "fb_fixed\tОтвод SML DN100 45\tmust_find\totvod\tДу100х45гр\n",
        encoding="utf-8",
    )


def _fake_api(_api_url, query, _size, _timeout):
    graph = {
        "summary": {
            "category_slug": "otvod",
            "diagnosis_status": "green",
            "primary_issue": None,
            "applied_filter_count": 3,
            "total": 1,
        }
    }
    if query == "Отвод SML DN100 45":
        return {
            "items": [{"article": "027-1868", "name": "Отвод чуг SML Ду100х45гр"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if query == "Отвод стальной Ду20":
        red_graph = {
            "summary": {
                "category_slug": "otvod",
                "diagnosis_status": "red",
                "primary_issue": "expected_code_not_top1",
                "applied_filter_count": 4,
                "total": 1,
            }
        }
        return {
            "items": [{"article": "027-1157", "name": "Отвод ст Ду20"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": red_graph},
        }
    if query == "Отвод PP DN40 45":
        gap_graph = {
            "summary": {
                "category_slug": "otvod",
                "diagnosis_status": "red",
                "primary_issue": "zero_results_after_applied_filters",
                "applied_filter_count": 5,
                "total": 0,
            }
        }
        return {
            "items": [],
            "total": {"value": 0},
            "category": "otvod",
            "ai_escalation_reason": "l1_required_filter_catalog_gap",
            "debug": {"decision_graph": gap_graph},
        }
    if query == "Коллектор с 2 отводами":
        other_graph = {
            "summary": {
                "category_slug": "kollektor",
                "diagnosis_status": "warning",
                "primary_issue": "top_result_signal_mismatch",
                "applied_filter_count": 2,
                "total": 1,
            }
        }
        return {
            "items": [{"article": "127-1096", "name": "Коллектор лат 2в"}],
            "total": {"value": 1},
            "category": "kollektor",
            "debug": {"decision_graph": other_graph},
        }
    if query == "Отвод PP DN50":
        return {
            "items": [{"article": "027-5000", "name": "Отвод PP Ду50"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    raise AssertionError(query)


def test_slug_cockpit_combines_intake_feedback_and_actions(tmp_path):
    _write_cockpit_fixture(tmp_path)

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", api_func=_fake_api)

    assert out["ok"] is True
    assert out["health"]["state"] == "red"
    assert out["summary"]["specs"] == 1
    assert out["summary"]["feedback_rows"] == 5
    assert out["summary"]["non_fixed_feedback"] == 1
    assert out["summary"]["open_feedback"] == 1
    assert out["summary"]["classified_non_fixed"] == 3
    assert out["diagnosis"]["query_first_present"] is True
    assert out["diagnosis"]["data_model_present"] is True
    assert out["diagnosis"]["feedback_root_cause_counts"]["expected_code_not_top1"] == 1
    assert out["diagnosis"]["feedback_root_cause_counts"]["catalog_gap_zero_results_after_applied_filters"] == 1
    assert out["diagnosis"]["feedback_root_cause_counts"]["other_slug_route"] == 1
    assert out["diagnosis"]["graph_issue_counts"]["expected_code_not_top1"] == 1
    assert out["feedback"]["non_fixed"][0]["triage_id"] == "fb_mismatch"
    assert [row["triage_id"] for row in out["feedback"]["classified_non_fixed"]] == ["fb_gap", "fb_other", "fb_positive"]
    assert out["feedback"]["classified_non_fixed"][2]["owner_terminal_status"] is True
    assert out["feedback"]["non_fixed"][0]["top_jde"] == "027-1157"
    assert out["action_items"][0]["id"] == "feedback_not_fixed"
    assert "apatch slug close otvod" in " ".join(out["next_commands"])
    # Canonical fixture: the automatic vocabulary lint reports clean.
    assert out["feedback_lint"]["ok"] is True
    assert out["feedback_lint"]["clean"] is True
    assert out["summary"]["vocabulary_clean"] is True
    assert not [item for item in out["action_items"] if str(item["id"]).startswith("vocabulary_")]
    # Repair map routes the open row to its designated edit surface.
    assert out["repair_map"]["present"] is True
    assert out["repair_map"]["matched"] == 1
    assert out["repair_map"]["unmatched_causes"] == []
    assert out["feedback"]["non_fixed"][0]["repair"]["rule_id"] == "top1_relevance"
    not_fixed_item = next(item for item in out["action_items"] if item["id"] == "feedback_not_fixed")
    assert not_fixed_item["recommended_action"].startswith("[top1_relevance]")
    assert "categories/<slug>/*_query_hints.json" in not_fixed_item["recommended_action"]
    assert not [item for item in out["action_items"] if item["id"] == "repair_map_gap"]


def test_slug_cockpit_cli_json(tmp_path, monkeypatch):
    _write_cockpit_fixture(tmp_path)
    monkeypatch.setattr("apatch.slug_close._post_search_api", _fake_api)

    result = CliRunner().invoke(
        cli,
        ["slug", "cockpit", "otvod", "--target-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["health"]["state"] == "red"
    assert payload["feedback"]["non_fixed"][0]["root_cause"] == "expected_code_not_top1"


def test_slug_cockpit_does_not_warn_missing_reality_when_feedback_exists(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-OTVOD-1.md").write_text(
        """# SPEC-OTVOD-1

> **apatch artifact:** `spec:SPEC-OTVOD-1`

## R1 Query-first live search gate (verify: python3 -c "print('query items otvod')")
""",
        encoding="utf-8",
    )
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note",
                "fb_fixed\tui:1\tОтвод SML DN100 45\tdislike\tfixed\totvod\t\tДу100х45гр\t\told",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", api_func=_fake_api)

    assert out["summary"]["feedback_rows"] == 1
    assert out["required_gates"]["reality_coverage"]["passed"] is True
    assert "missing_reality" not in {gap["id"] for gap in out["gaps"]}
    assert "missing_reality" not in {item["id"] for item in out["action_items"]}


def test_slug_cockpit_flags_vocabulary_drift(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-OTVOD-1.md").write_text(
        """# SPEC-OTVOD-1

> **apatch artifact:** `spec:SPEC-OTVOD-1`

## R1 Query-first live search gate (verify: python3 -c "print('query items otvod')")
""",
        encoding="utf-8",
    )
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note",
                "fb_alias\tui:1\tОтвод PP DN50\tdislike\tneighbor_slug\totvod\t\t\t\told",
                "fb_junk\tui:2\tОтвод SML DN100 45\tdislike\ttotal_junk_status\totvod\t\tДу100х45гр\t\told",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", api_func=_fake_api)

    lint = out["feedback_lint"]
    assert lint["ok"] is True
    assert lint["clean"] is False
    assert lint["proposed_needles"] == 1
    ids = [item["id"] for item in out["action_items"]]
    assert "vocabulary_unknown_status" in ids
    assert "vocabulary_alias_status" in ids
    assert out["health"]["state"] == "red"
    assert out["summary"]["vocabulary_clean"] is False
    assert any("feedback-lint" in cmd for cmd in out["next_commands"])


def test_slug_cockpit_reports_repair_map_gap(tmp_path):
    _write_cockpit_fixture(tmp_path)
    # Replace the map with one that cannot route the open row's diagnosis.
    (tmp_path / "manifests" / "repair-map.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "unrelated",
                        "match": {"root_cause": ["some_other_cause"]},
                        "action": "n/a",
                        "edit_surfaces": ["x"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", api_func=_fake_api)

    assert out["repair_map"]["matched"] == 0
    assert out["repair_map"]["unmatched_causes"] == ["expected_code_not_top1"]
    gap = next(item for item in out["action_items"] if item["id"] == "repair_map_gap")
    assert "expected_code_not_top1" in gap["message"]
    assert "platform RFC" in gap["recommended_action"]


def test_slug_cockpit_absent_repair_map_is_silent(tmp_path):
    _write_cockpit_fixture(tmp_path)
    (tmp_path / "manifests" / "repair-map.json").unlink()

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", api_func=_fake_api)

    assert out["repair_map"]["present"] is False
    assert not [item for item in out["action_items"] if str(item["id"]).startswith("repair_map")]


def _configure_operational_status(tmp_path):
    path = tmp_path / "manifests" / "slug-intake.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["hooks"]["operational_status"] = "python3 tools/audit_slug.py {slug} --live --json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    triage = tmp_path / "tests" / "regressions" / "otvod_feedback_triage.tsv"
    lines = [
        line
        for line in triage.read_text(encoding="utf-8").splitlines()
        if not line.startswith("fb_mismatch\t")
    ]
    triage.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_slug_cockpit_accepts_evidence_red_exit_without_reopening_runtime(tmp_path, monkeypatch):
    _write_cockpit_fixture(tmp_path)
    _configure_operational_status(tmp_path)
    payload = {
        "results": [
            {
                "slug": "otvod",
                "work_status": "runtime_verified_with_confirmed_catalog_gaps",
                "runtime_work_complete": True,
                "reopen_reasons": [],
                "runtime_defect_codes": [],
                "evidence_debt_codes": ["acceptance_strict_signals_missing"],
                "verification_key": "sha256:abc",
            }
        ]
    }
    monkeypatch.setattr(
        "apatch.slug_cockpit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", live=True, api_func=_fake_api)

    assert out["operational"]["ok"] is True
    assert out["operational"]["advisory_exit"] is True
    assert out["operational"]["runtime_work_complete"] is True
    assert out["operational"]["runtime_reopen_required"] is False
    assert out["operational"]["evidence_only"] is True
    evidence = next(item for item in out["action_items"] if item["id"] == "evidence_debt_only")
    assert evidence["severity"] == "low"
    assert not [item for item in out["action_items"] if item["id"] == "runtime_reopen_required"]
    assert "do not rebuild" in out["agent_next"]



def test_slug_cockpit_runtime_complete_downgrades_missing_feedback_replay_to_yellow(
    tmp_path, monkeypatch
):
    _write_cockpit_fixture(tmp_path)
    _configure_operational_status(tmp_path)
    regressions = tmp_path / "tests" / "regressions"
    (regressions / "otvod_feedback_triage.tsv").unlink()
    (regressions / "feedback_approved_contract.tsv").unlink()
    (tmp_path / ".apatch" / "reality.jsonl").unlink()
    payload = {
        "slug": "otvod",
        "work_status": "runtime_verified",
        "runtime_work_complete": True,
        "reopen_reasons": [],
        "runtime_defect_codes": [],
        "evidence_debt_codes": [],
        "verification_key": "sha256:complete",
    }
    monkeypatch.setattr(
        "apatch.slug_cockpit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", live=True, api_func=_fake_api)

    assert out["feedback"]["ok"] is False
    assert out["feedback"]["error_type"] == "TRIAGE_NOT_FOUND"
    assert out["health"]["state"] == "yellow"
    assert out["health"]["runtime_state"] == "complete"
    assert out["health"]["high"] == 0
    assert "TRIAGE_NOT_FOUND" in out["health"]["reasons"]
    assert "do not rebuild" in out["agent_next"]

def test_slug_cockpit_reopens_only_reported_runtime_defect(tmp_path, monkeypatch):
    _write_cockpit_fixture(tmp_path)
    _configure_operational_status(tmp_path)
    payload = {
        "slug": "otvod",
        "work_status": "needs_runtime_work",
        "runtime_work_complete": False,
        "reopen_reasons": ["live_case_failed"],
        "runtime_defect_codes": ["wrong_top_result"],
        "evidence_debt_codes": ["acceptance_strict_signals_missing"],
        "verification_key": "sha256:def",
    }
    monkeypatch.setattr(
        "apatch.slug_cockpit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", live=True, api_func=_fake_api)

    assert out["operational"]["runtime_reopen_required"] is True
    assert out["health"]["state"] == "red"
    assert out["health"]["runtime_state"] == "reopen_required"
    reopen = next(item for item in out["action_items"] if item["id"] == "runtime_reopen_required")
    assert reopen["severity"] == "high"
    assert reopen["runtime_defect_codes"] == ["wrong_top_result"]


def test_slug_cockpit_treats_invalid_operational_json_as_hook_problem(tmp_path, monkeypatch):
    _write_cockpit_fixture(tmp_path)
    _configure_operational_status(tmp_path)
    monkeypatch.setattr(
        "apatch.slug_cockpit.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    )

    out = slug_cockpit_workspace(str(tmp_path), slug="otvod", live=True, api_func=_fake_api)

    assert out["operational"]["ok"] is False
    assert out["operational"]["error_type"] == "OPERATIONAL_STATUS_INVALID_JSON"
    unavailable = next(item for item in out["action_items"] if item["id"] == "operational_status_unavailable")
    assert unavailable["severity"] == "medium"
    assert "do not infer" in unavailable["recommended_action"]


def test_mcp_slug_cockpit_registered_and_callable(monkeypatch, tmp_path):
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp import server as mcp_server

    _write_cockpit_fixture(tmp_path)
    monkeypatch.setattr("apatch.slug_close._post_search_api", _fake_api)
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None and "apatch_slug_cockpit" in tm._tools
    result = tm._tools["apatch_slug_cockpit"].fn(slug="otvod", target_dir=str(tmp_path))
    assert result["ok"] is True
    assert result["summary"]["non_fixed_feedback"] == 1
