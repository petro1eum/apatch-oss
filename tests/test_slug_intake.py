import json

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.reality import add_reality_record
from apatch.slug_intake import slug_intake_workspace


def _write_slug_fixture(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    rec_id = add_reality_record(
        str(tmp_path),
        id="REC-truba-feedback",
        summary="Client query for truba does not return the expected item",
        source="feedback",
        kind="bug",
    )
    (specs / "SPEC-TRUBA-1.md").write_text(
        f"""# SPEC-TRUBA-1

> **apatch artifact:** `spec:SPEC-TRUBA-1`

## R0 Data map (verify: python3 -c "print('truba data audit')")

Map pipe semantic type to real catalog params.

## R1 Query-first live search gate (verify: python3 -c "print('query items truba')")

discharges: {rec_id}
""",
        encoding="utf-8",
    )
    atomic = tmp_path / "config" / "atomic"
    atomic.mkdir(parents=True)
    (atomic / "pipe_type.json").write_text('{"truba": ["pipe"]}\n', encoding="utf-8")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "slug-intake.json").write_text(
        json.dumps(
            {
                "aliases": {"truba": ["pipe"]},
                "dictionary_roots": ["config"],
                "hooks": {
                    "data_audit": "python tools/audit_catalog.py {slug}",
                    "query_verify": "pytest tests/live/test_{slug}_queries.py",
                },
            }
        ),
        encoding="utf-8",
    )
    apatch_dir = tmp_path / ".apatch"
    apatch_dir.mkdir(exist_ok=True)
    (apatch_dir / "conformance.json").write_text(
        json.dumps({"enabled": True, "contract": {"specs": ["SPEC-TRUBA-1"]}}),
        encoding="utf-8",
    )


def test_slug_intake_collects_specs_reality_and_gates(tmp_path):
    _write_slug_fixture(tmp_path)

    out = slug_intake_workspace(str(tmp_path), slug="truba")

    assert out["ok"] is True
    assert out["slug"] == "truba"
    assert out["specs"][0]["id"] == "SPEC-TRUBA-1"
    assert out["reality"]["records"][0]["id"] == "REC-truba-feedback"
    assert out["reality"]["pending"] == ["REC-truba-feedback"]
    assert out["conformance"]["enabled"] is True
    assert out["required_gates"]["query_first"]["present"] is True
    assert out["required_gates"]["data_model"]["present"] is True
    gap_ids = {g["id"] for g in out["gaps"]}
    assert "missing_query_first_verify" not in gap_ids
    assert "missing_data_model_audit" not in gap_ids
    assert "reality_not_closed" in gap_ids
    assert "SPEC-TRUBA-1" in out["spec_template"]["content"]


def test_slug_intake_reports_missing_slug_context(tmp_path):
    out = slug_intake_workspace(str(tmp_path), slug="mufta")

    assert out["ok"] is True
    gap_ids = {g["id"] for g in out["gaps"]}
    assert "missing_spec" in gap_ids
    assert "missing_query_first_verify" in gap_ids
    assert out["agent_next"].startswith("Create a slug spec")


def test_slug_intake_treats_owner_triage_as_reality_source(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-FIKSATOR-1.md").write_text(
        """# SPEC-FIKSATOR-1

> **apatch artifact:** `spec:SPEC-FIKSATOR-1`

## R1 Query-first live search gate (verify: python3 -c "print('query items fiksator')")
""",
        encoding="utf-8",
    )
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "fiksator_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note",
                "fb_gap\tui:1\tФиксатор Rehau Stabil 20х22\tdislike\tcatalog_gap\tremaining\t\t\tнет в каталоге\towner accepted gap",
                "fb_positive\tui:2\tФиксатор ROS\tdislike\tpositive_feedback\tremaining\t\t\t\tpositive feedback row",
                "fb_neighbor\tui:3\tВтулка под фланец\tdislike\tneighbor_slug\tvtulka\t\t\t\tbelongs to neighbor slug",
                "fb_noise\tui:4\tмусорная строка\tdislike\tnoise\t\t\t\t\tnoise row",
                "fb_ambiguous\tui:5\tТрубка красная б=9 для трубы\tdislike\tambiguous_query\ttrubka\t\t\t\tmissing pipe diameter",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out = slug_intake_workspace(str(tmp_path), slug="fiksator")

    assert out["reality"]["records"][0]["id"] == "fb_gap"
    assert out["reality"]["records"][0]["kind"] == "feedback_triage"
    assert out["reality"]["covered"] == [
        "fb_gap",
        "fb_positive",
        "fb_neighbor",
        "fb_noise",
        "fb_ambiguous",
    ]
    assert out["required_gates"]["reality_coverage"]["passed"] is True
    gap_ids = {g["id"] for g in out["gaps"]}
    assert "missing_reality" not in gap_ids
    assert "reality_not_closed" not in gap_ids


def test_slug_intake_keeps_open_owner_triage_red(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-FIKSATOR-1.md").write_text(
        """# SPEC-FIKSATOR-1

> **apatch artifact:** `spec:SPEC-FIKSATOR-1`

## R1 Query-first live search gate (verify: python3 -c "print('query items fiksator')")
""",
        encoding="utf-8",
    )
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "fiksator_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note",
                "fb_open\tui:1\tФиксатор Rehau Stabil 20х22\tdislike\tneeds_human_review\tremaining\t\t\tне закрыто\t",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out = slug_intake_workspace(str(tmp_path), slug="fiksator")

    assert out["reality"]["uncovered"] == ["fb_open"]
    gap_ids = {g["id"] for g in out["gaps"]}
    assert "missing_reality" not in gap_ids
    assert "reality_not_closed" in gap_ids



def test_slug_intake_does_not_match_a_longer_slug_prefix(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-UGOLNIK-1.md").write_text(
        """# SPEC-UGOLNIK-1

> **apatch artifact:** `spec:SPEC-UGOLNIK-1`

## R0 Query-first live gate (verify: python3 -c "print('ugolnik items')")
""",
        encoding="utf-8",
    )
    atomic = tmp_path / "atomic"
    atomic.mkdir()
    (atomic / "ugolnik_specs.json").write_text("{}", encoding="utf-8")
    (atomic / "ugol_specs.json").write_text("{}", encoding="utf-8")

    out = slug_intake_workspace(str(tmp_path), slug="ugol")

    assert out["specs"] == []
    assert {row["path"] for row in out["evidence"]["dictionary_files"]} == {
        "atomic/ugol_specs.json"
    }
    assert "missing_spec" in {gap["id"] for gap in out["gaps"]}


def test_slug_intake_cli_json(tmp_path):
    _write_slug_fixture(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["slug", "intake", "truba", "--target-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["specs"][0]["id"] == "SPEC-TRUBA-1"


def test_mcp_slug_intake_registered_and_callable(tmp_path):
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp import server as mcp_server

    _write_slug_fixture(tmp_path)
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None and "apatch_slug_intake" in tm._tools
    result = tm._tools["apatch_slug_intake"].fn(slug="truba", target_dir=str(tmp_path))
    assert result["ok"] is True
    assert result["specs"][0]["id"] == "SPEC-TRUBA-1"
