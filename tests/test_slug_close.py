import json

# The fake API models the production items/debug envelope used by slug close.
import pytest
from click.testing import CliRunner

from apatch.cli import cli
from apatch.slug_close import slug_close_workspace


def _write_fixture(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "otvod_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note",
                "fb_fixed\tui:1\tОтвод SML DN100 45\tdislike\tneeds_human_review\totvod\t\t\t\told",
                "fb_other\tui:2\tКоллектор с 2 отводами\tdislike\tneeds_human_review\totvod\t\t\t\told",
                "fb_gap\tui:3\tОтвод PP DN40 45\tdislike\tneeds_human_review\totvod\t\t\t\told",
                "fb_mismatch\tui:4\tОтвод стальной Ду20\tdislike\tneeds_human_review\totvod\t\t\tнадо 027-9999\told",
                'fb_quoted\tui:5\t"""Тройник переходной чугунный 32х15""",\tdislike\tneeds_human_review\totvod\t\tТройник чуг\tquoted\told',
                "fb_review\tui:6\tОтвод без эталона\tdislike\tneeds_review\totvod\t\t\t\told note mentions 027-0000 but is not oracle",
                "fb_kombi\tui:7\tХомут Kombi-Kralle DN 100\tdislike\tneeds_review\totvod\t\t\tне верно\told",
                "fb_invalid\tui:8\tЗаглушка чугунная SML Ду150\tdislike\tinvalid_feedback\totvod\t\t\tне актуально\told",
                "fb_dup_noise\tui:9\tОтвод дубль с шумным комментарием\tdislike\tduplicate\totvod\t\t\tожидали 027-9999 в старой строке\tduplicate of fb_fixed",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (reg / "feedback_approved_contract.tsv").write_text(
        "fb_fixed\tОтвод SML DN100 45\tmust_find\totvod\tДу100х45гр\n"
        "fb_gap\tОтвод PP DN40 45\tmust_find\totvod\tотвод pp-h дн40х45\n"
        "fb_kombi\tХомут Kombi-Kralle DN 100\tmust_find\totvod\t108-116\n"
        "fb_invalid\tЗаглушка чугунная SML Ду150\tmust_find\totvod\tsgacx\n"
        "fb_dup_noise\tОтвод дубль с шумным комментарием\tmust_find\totvod\tДу20\n"
        "fb_empty_gap\tОтвод стальной Ду42\tcatalog_gap\totvod\t\n",
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
    if query == "Коллектор с 2 отводами":
        return {
            "items": [{"article": "127-1096", "name": "Коллектор лат 2в"}],
            "total": {"value": 1},
            "category": "kollektor",
            "debug": {"decision_graph": graph},
        }
    if query == "Отвод PP DN40 45":
        gap_graph = {
            "summary": {
                "category_slug": "otvod",
                "diagnosis_status": "red",
                "primary_issue": "zero_results_after_applied_filters",
                "applied_filter_count": 5,
                "total": 0,
            },
            "diagnostic_probes": [{"kind": "catalog_probe", "hits": 0}],
        }
        return {
            "items": [],
            "total": {"value": 0},
            "category": "otvod",
            "ai_escalation_reason": "l1_required_filter_catalog_gap",
            "debug": {"decision_graph": gap_graph},
        }
    if query == "Отвод стальной Ду20":
        return {
            "items": [{"article": "027-1157", "name": "Отвод ст Ду20"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if query == "Отвод стальной Ду42":
        return {
            "items": [{"article": "027-1161", "name": "Отвод ст Дн42,3 (Ду32)"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if "Тройник переходной чугунный 32х15" in query:
        return {
            "items": [{"article": "127-3656", "name": "Тройник чуг Ду32х15"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if query == "Отвод без эталона":
        return {
            "items": [{"article": "027-1000", "name": "Отвод ст Ду50"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if query == "Хомут Kombi-Kralle DN 100":
        return {
            "items": [{"article": "013-2785", "name": "Хомут ст оц рез/пр М12 (108-116)мм"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if query == "Заглушка чугунная SML Ду150":
        return {
            "items": [{"article": "027-3592", "name": "Заглушка чуг SML Ду150 торцевая б/рас SGACX"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    if query == "Отвод дубль с шумным комментарием":
        return {
            "items": [{"article": "027-1157", "name": "Отвод ст Ду20"}],
            "total": {"value": 1},
            "category": "otvod",
            "debug": {"decision_graph": graph},
        }
    raise AssertionError(query)


def test_slug_close_classifies_rows_and_emits_needles(tmp_path):
    _write_fixture(tmp_path)

    out = slug_close_workspace(str(tmp_path), slug="otvod", api_func=_fake_api)

    assert out["ok"] is True
    assert out["summary"]["suggested_status_counts"] == {
        "catalog_gap": 1,
        "duplicate": 1,
        "fixed": 2,
        "invalid_feedback": 1,
        "needs_review": 2,
        "open_runtime_bug": 1,
        "other_slug": 1,
    }
    by_id = {row["triage_id"]: row for row in out["suggestions"]}
    assert by_id["fb_fixed"]["suggested_jde"] == "027-1868"
    assert by_id["fb_other"]["suggested_slug"] == "kollektor"
    assert by_id["fb_gap"]["root_cause"] == "catalog_gap_zero_results_after_applied_filters"
    assert by_id["fb_mismatch"]["root_cause"] == "expected_code_not_top1"
    assert by_id["fb_review"]["suggested_status"] == "needs_review"
    assert by_id["fb_review"]["root_cause"] == "owner_status_preserved_without_positive_oracle"
    assert by_id["fb_kombi"]["suggested_status"] == "needs_review"
    assert by_id["fb_kombi"]["root_cause"] == "approved_query_signal_not_top1_needs_review_preserved"
    assert by_id["fb_invalid"]["suggested_status"] == "invalid_feedback"
    assert by_id["fb_invalid"]["root_cause"] == "approved_query_signal_not_top1_invalid_feedback_preserved"
    assert by_id["fb_dup_noise"]["suggested_status"] == "duplicate"
    assert by_id["fb_dup_noise"]["root_cause"] == "owner_terminal_status_preserved"
    assert len(out["proposed_needles"]) == 9
    quoted = [
        needle for needle in out["proposed_needles"]
        if needle["find_text"].startswith("fb_quoted\t")
    ]
    assert quoted and '"""Тройник переходной чугунный 32х15""",' in quoted[0]["find_text"]
    assert [row["id"] for row in out["approved_conflicts"]] == ["fb_gap", "fb_kombi", "fb_invalid"]
    assert out["approved_needles"][0]["replace_text"].endswith("\tcatalog_gap\totvod\t")




def test_slug_close_attributes_zero_result_gap_to_routed_neighbor_slug(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "flanec_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\tstatus\texpected_slug\texpected_jde\texpected_top\tdecision_note",
                "fb_burt\tui:1\tБурт ПП под металлический фланец 75/65\tneighbor_slug\tburt\t\t\told",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_api(_api_url, _query, _size, _timeout):
        return {
            "items": [],
            "total": {"value": 0},
            "category": "burt",
            "debug": {
                "decision_graph": {
                    "summary": {
                        "category_slug": "burt",
                        "diagnosis_status": "red",
                        "primary_issue": "zero_results_after_applied_filters",
                        "applied_filter_count": 4,
                        "total": 0,
                    },
                    "diagnostic_probes": [{"kind": "ablation", "hits": 0}],
                }
            },
        }

    out = slug_close_workspace(str(tmp_path), slug="flanec", api_func=fake_api)

    suggestion = out["suggestions"][0]
    assert suggestion["suggested_status"] == "catalog_gap"
    assert suggestion["suggested_slug"] == "burt"
    assert suggestion["root_cause"] == "other_slug_catalog_gap_zero_results_after_applied_filters"

def test_slug_close_treats_legacy_slug_alias_as_same_owner(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "flanec_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tsource\tquery\tstatus\texpected_slug\texpected_jde\texpected_top\tdecision_note",
                "fb_flanec\tui:1\tФланец 400-10-04-1-ст.12Х18Н10Т\tfixed\tflanec\t129-6607\tФланец плоский нерж 400-10\told",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_api(_api_url, _query, _size, _timeout):
        return {
            "items": [{"article": "129-6607", "name": "Фланец плоский нерж 400-10 12Х18Н10Т"}],
            "total": {"value": 1},
            "category": "flanets",
            "debug": {"decision_graph": {"summary": {"category_slug": "flanets"}}},
        }

    out = slug_close_workspace(str(tmp_path), slug="flanec", api_func=fake_api)

    suggestion = out["suggestions"][0]
    assert suggestion["suggested_status"] == "fixed"
    assert suggestion["suggested_slug"] == "flanec"
    assert suggestion["root_cause"] == "live_top1_matches_expected_or_slug"

def test_slug_close_reads_legacy_owner_notes_as_positive_oracles(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "kran_feedback_triage.tsv").write_text(
        "\n".join(
            [
                "triage_id\tquery\tactual_scope\tstatus\tsource\tnotes",
                "kran_fb_001\tКран шаровой латунный, резьбовое присоединение, номинальный диаметр 25 мм\tkran\tfixed\tapproved_contract\t109-0766",
                "kran_fb_011\t44. Шаровой кран латунный 1/2\" ВР-НР, ручка-рычаг, LD Pride 47.15.В-Н.Р\tkran\tfixed\tapproved_contract\t47.15.в-н.р",
                'kran_fb_019\t"10. Шаровой кран латунный 3/4"" ВР, LD Pride 47.20.В-В.Р"\tkran\tfixed\tapproved_contract\t47.20.в-в.р',
                "kran_fb_gap\tКран шаровой межфланцевый из нержавеющей стали Ду50 Ру16\tkran\tcatalog_gap\tapproved_contract\tcatalog_gap: no stainless межфланец BV17 Ду50 Ру16 in kran catalog",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (reg / "feedback_approved_contract.tsv").write_text(
        "fb_001\tКран шаровой латунный, резьбовое присоединение, номинальный диаметр 25 мм\tmust_find\tkran\tКран шар\n"
        "fb_011\t44. Шаровой кран латунный 1/2\" ВР-НР, ручка-рычаг, LD Pride 47.15.В-Н.Р\tmust_find\tkran\t47.15.В-Н.Р\n"
        "fb_019\t10. Шаровой кран латунный 3/4\" ВР, LD Pride 47.20.В-В.Р\tmust_find\tkran\t47.20.В-В.Р\n"
        "fb_gap\tКран шаровой межфланцевый из нержавеющей стали Ду50 Ру16\tcatalog_gap\tkran\t\n",
        encoding="utf-8",
    )

    def fake_api(_api_url, query, _size, _timeout):
        graph = {"summary": {"category_slug": "kran", "diagnosis_status": "ok", "total": 1}}
        if query.startswith("Кран шаровой латунный"):
            return {
                "items": [{"article": "109-0766", "name": "Кран шар лат BVR-R Ду25 Ру40 м/м полн рыч Ридан"}],
                "category": "kran",
                "debug": {"decision_graph": graph},
            }
        if query.startswith("44. Шаровой кран"):
            return {
                "items": [{"article": "109-0635", "name": "Кран шар лат Pride нк Ду15 Ру40 м/р рыч LD 47.15.В-Н.Р"}],
                "category": "kran",
                "debug": {"decision_graph": graph},
            }
        if query.startswith("10. Шаровой кран"):
            return {
                "items": [{"article": "109-0718", "name": "Кран шар лат Pride нк Ду20 Ру40 м/м рыч LD 47.20.В-В.Р"}],
                "category": "kran",
                "debug": {"decision_graph": graph},
            }
        gap_graph = {
            "summary": {
                "category_slug": "kran",
                "diagnosis_status": "red",
                "primary_issue": "zero_results_after_applied_filters",
                "total": 0,
            }
        }
        return {
            "items": [],
            "total": {"value": 0},
            "category": "kran",
            "debug": {"decision_graph": gap_graph},
        }

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=fake_api)

    assert out["ok"] is True
    by_id = {row["triage_id"]: row for row in out["suggestions"]}
    assert by_id["kran_fb_001"]["suggested_status"] == "fixed"
    assert by_id["kran_fb_011"]["suggested_status"] == "fixed"
    assert by_id["kran_fb_019"]["suggested_status"] == "fixed"
    assert by_id["kran_fb_gap"]["suggested_status"] == "catalog_gap"
    assert out["approved_conflicts"] == []
    assert out["proposed_needles"] == []


def test_slug_close_prefers_top_level_runtime_slug_over_legacy_debug_slug(tmp_path):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "smesitel_feedback_triage.tsv").write_text(
        "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde\texpected_top\tuser_comment\tdecision_note\n"
        "fb_smesitel\tui:1\tСмеситель для душа прораб\tdislike\tfixed\tsmesitel\t026-1772\tСмес/душ П-серия Прораб\told\told\n",
        encoding="utf-8",
    )
    (reg / "feedback_approved_contract.tsv").write_text(
        "fb_smesitel\tСмеситель для душа прораб\tmust_find\tsmesitel\tСмес/душ П-серия Прораб\n",
        encoding="utf-8",
    )

    def fake_api(_api_url, _query, _size, _timeout):
        return {
            "category_slug": "smesitel",
            "items": [{"article": "026-1772", "name": "Смес/душ П-серия однор в/к Прораб Славен"}],
            "debug": {
                "index_category_slug": "smes",
                "decision_graph": {"summary": {"category_slug": "smesitel", "diagnosis_status": "ok", "total": 1}},
            },
        }

    out = slug_close_workspace(str(tmp_path), slug="smesitel", api_func=fake_api)

    assert out["summary"]["suggested_status_counts"] == {"fixed": 1}
    assert out["approved_conflicts"] == []
    assert out["suggestions"][0]["suggested_slug"] == "smesitel"


def test_slug_close_cli_json(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    monkeypatch.setattr("apatch.slug_close._post_search_api", _fake_api)

    result = CliRunner().invoke(
        cli,
        ["slug", "close", "otvod", "--target-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["summary"]["updates"] == 9


def test_slug_close_cli_compact_json(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    monkeypatch.setattr("apatch.slug_close._post_search_api", _fake_api)

    result = CliRunner().invoke(
        cli,
        ["slug", "close", "otvod", "--target-dir", str(tmp_path), "--json", "--compact"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "suggestions" not in payload
    assert [row["triage_id"] for row in payload["non_fixed"]] == [
        "fb_other",
        "fb_gap",
        "fb_mismatch",
        "fb_review",
        "fb_kombi",
        "fb_invalid",
        "fb_dup_noise",
    ]
    assert [row["id"] for row in payload["approved_conflicts"]] == ["fb_gap", "fb_kombi", "fb_invalid"]


def test_mcp_slug_close_registered_and_callable(monkeypatch, tmp_path):
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from apatch.mcp import server as mcp_server

    _write_fixture(tmp_path)
    monkeypatch.setattr("apatch.slug_close._post_search_api", _fake_api)
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    assert tm is not None and "apatch_slug_close" in tm._tools
    result = tm._tools["apatch_slug_close"].fn(slug="otvod", target_dir=str(tmp_path))
    assert result["ok"] is True
    assert result["summary"]["updates"] == 9
