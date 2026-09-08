import json

import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.slug_feedback_lint import feedback_lint_workspace

MODERN_HEADER = (
    "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde"
    "\texpected_top\tuser_comment\tdecision_note"
)
ALIAS_LINE = "fb_alias\tui:2\tКран под манометр Ду15\tdislike\tneighbor_slug\totvod\t\t\t\told"


def _write_fixture(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        "\n".join(
            [
                MODERN_HEADER,
                "fb_ok\tui:1\tОтвод SML DN100 45\tdislike\tfixed\totvod\t027-1868\t\t\told",
                ALIAS_LINE,
                "fb_unknown\tui:3\tОтвод непонятный\tdislike\ttotal_junk_status\totvod\t\t\t\told",
                "fb_empty\tui:4\tОтвод без статуса\tdislike\t\totvod\t\t\t\told",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (reg / "legacy_feedback_triage.tsv").write_text(
        "triage_id\tquery\tnotes\nlegacy_1\tстарый запрос\tзаметка\n",
        encoding="utf-8",
    )
    (reg / "feedback_approved_contract.tsv").write_text(
        "fb_ok\tОтвод SML DN100 45\tmust_find\totvod\tДу100х45гр\n"
        "fb_bad\tОтвод стальной Ду42\tmaybe_find\totvod\t\n",
        encoding="utf-8",
    )


def test_lint_reports_alias_unknown_legacy_and_approved(tmp_path):
    _write_fixture(tmp_path)

    out = feedback_lint_workspace(str(tmp_path))

    assert out["ok"] is True
    assert out["clean"] is False
    summary = out["summary"]
    assert summary["files_scanned"] == 2
    assert summary["alias_rows"] == 1
    assert summary["unknown_status_values"] == 1
    assert summary["files_missing_status_column"] == 1
    assert summary["proposed_needles"] == 1

    ids = [f["id"] for f in out["findings"]]
    # highs first (sorted by severity), then medium alias, then low empty.
    assert ids[0:3] == ["missing_status_column", "unknown_approved_status", "unknown_status"]
    assert "alias_status" in ids and "empty_status" in ids

    needle = out["proposed_needles"][0]
    assert needle["action"] == "replace"
    assert needle["target_file"].endswith("otvod_feedback_triage.tsv")
    assert needle["find_text"] == ALIAS_LINE
    assert needle["replace_text"] == ALIAS_LINE.replace("neighbor_slug", "other_slug")

    unknown = next(f for f in out["findings"] if f["id"] == "unknown_status")
    assert unknown["value"] == "total_junk_status"
    assert unknown["triage_ids"] == ["fb_unknown"]

    approved = next(f for f in out["findings"] if f["id"] == "unknown_approved_status")
    assert approved["value"] == "maybe_find"
    assert approved["record_id"] == "fb_bad"

    assert "other_slug" in out["vocabulary"]
    assert out["aliases"] == {"neighbor_slug": "other_slug"}


def test_lint_single_slug_scopes_to_one_file(tmp_path):
    _write_fixture(tmp_path)

    out = feedback_lint_workspace(str(tmp_path), slug="otvod")

    assert out["ok"] is True
    assert out["summary"]["files_scanned"] == 1
    assert out["summary"]["files_missing_status_column"] == 0
    assert out["files"][0]["path"].endswith("otvod_feedback_triage.tsv")
    assert out["files"][0]["status_counts"]["other_slug"] == 1  # alias counted canonically


def test_lint_missing_slug_file_errors(tmp_path):
    (tmp_path / "tests" / "regressions").mkdir(parents=True)

    out = feedback_lint_workspace(str(tmp_path), slug="net_takogo")

    assert out["ok"] is False
    assert out["error_type"] == "TRIAGE_NOT_FOUND"


def test_lint_clean_workspace(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        MODERN_HEADER
        + "\nfb_ok\tui:1\tОтвод\tdislike\tfixed\totvod\t\t\t\tok\n"
        + "fb_oblig\tui:2\tОтвод эталонный\tdislike\tmust_find\totvod\t\t\t\tok\n",
        encoding="utf-8",
    )

    out = feedback_lint_workspace(str(tmp_path))

    assert out["ok"] is True
    assert out["clean"] is True
    assert out["findings"] == []
    assert out["proposed_needles"] == []


def test_lint_dedupes_needles_for_identical_lines(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        "\n".join([MODERN_HEADER, ALIAS_LINE, ALIAS_LINE]) + "\n",
        encoding="utf-8",
    )

    out = feedback_lint_workspace(str(tmp_path))

    # Two alias rows, but one needle: duplicate find_text would abort the
    # whole apatch_generate_batch with an overlap error.
    assert out["summary"]["alias_rows"] == 2
    assert out["summary"]["proposed_needles"] == 1
    assert len(out["proposed_needles"]) == 1


def test_lint_flags_quoting_mismatch_instead_of_empty(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    # Unterminated leading quote: csv swallows the following tabs, the parsed
    # status becomes empty even though 'neighbor_slug' is on disk.
    bad = 'fb_q\tui:3\t"Кран Ду15 1/2\tdislike\tneighbor_slug\totvod\t\t\t\told'
    (reg / "kran_feedback_triage.tsv").write_text(
        MODERN_HEADER + "\n" + bad + "\n",
        encoding="utf-8",
    )

    out = feedback_lint_workspace(str(tmp_path))

    ids = [f["id"] for f in out["findings"]]
    assert "quoting_mismatch" in ids
    assert "empty_status" not in ids
    finding = next(f for f in out["findings"] if f["id"] == "quoting_mismatch")
    assert finding["raw_status"] == "neighbor_slug"
    assert out["summary"]["quoting_suspect_rows"] == 1
    assert out["files"][0]["quoting_suspect_rows"] == 1


def test_lint_cli_json_exits_nonzero_on_drift(tmp_path):
    _write_fixture(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["slug", "feedback-lint", "--target-dir", str(tmp_path), "--json"],
    )

    # Lint convention: drift fails the command (like spec lint / rfp lint).
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["clean"] is False
    assert payload["summary"]["proposed_needles"] == 1


def test_lint_cli_json_exits_zero_when_clean(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        MODERN_HEADER + "\nfb_ok\tui:1\tОтвод\tdislike\tfixed\totvod\t\t\t\tok\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        ["slug", "feedback-lint", "--target-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["clean"] is True


def test_mcp_feedback_lint_registered_and_callable(tmp_path):
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp import server as mcp_server

    _write_fixture(tmp_path)
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None and "apatch_slug_feedback_lint" in tm._tools
    result = tm._tools["apatch_slug_feedback_lint"].fn(target_dir=str(tmp_path))
    assert result["ok"] is True
    assert result["summary"]["proposed_needles"] == 1


def test_slug_close_normalizes_alias_status_without_oracle(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "kran_feedback_triage.tsv").write_text(
        "\n".join(
            [
                MODERN_HEADER,
                "fb_neighbor\tui:1\tОбратный клапан 2» VALTEC\tdislike\tneighbor_slug\tkran\t\t\t\told",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_api(_api_url, _query, _size, _timeout):
        return {
            "items": [{"article": "109-0001", "name": "Клапан обр лат Ду50"}],
            "total": {"value": 1},
            "category": "kran",
            "debug": {"decision_graph": {"summary": {"category_slug": "kran", "total": 1}}},
        }

    from apatch.slug_close import slug_close_workspace

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=fake_api)

    assert out["ok"] is True
    row = out["suggestions"][0]
    # Alias resolves to the canonical owner-set status and is kept, not reopened.
    assert row["suggested_status"] == "other_slug"
    # The proposed needle normalizes the TSV cell to the canonical spelling.
    needle = out["proposed_needles"][0]
    assert "\tneighbor_slug\t" in needle["find_text"]
    assert "\tother_slug\t" in needle["replace_text"]


def test_cockpit_treats_alias_current_status_as_owner_terminal():
    from apatch.slug_cockpit import _compact_close

    close = {
        "ok": True,
        "rows_scanned": 1,
        "summary": {},
        "suggestions": [
            {
                "triage_id": "fb_neighbor",
                "query": "Обратный клапан 2» VALTEC",
                "current_status": "neighbor_slug",
                "suggested_status": "open_runtime_bug",
                "root_cause": "expected_top_not_top1",
                "evidence": [],
            }
        ],
    }

    compact = _compact_close(close)

    assert compact["open_feedback"] == []
    assert compact["classified_non_fixed"][0]["owner_terminal_status"] is True
