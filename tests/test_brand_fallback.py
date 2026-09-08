import json

from apatch.slug_close import _approved_status_compatible, slug_close_workspace

QUERY = 'Кран шаровой 2" VALTEC BASE VALTEC 2 шт.'
HEADER = (
    "triage_id\tsource\tquery\treaction\tstatus\texpected_slug\texpected_jde"
    "\texpected_top\tuser_comment\tdecision_note"
)


def _write_fixture(tmp_path, approved_status="must_find_brand_soft"):
    reg = tmp_path / "tests" / "regressions"
    reg.mkdir(parents=True)
    (reg / "kran_feedback_triage.tsv").write_text(
        HEADER + f'\nfb_v\tui:1\t"Кран шаровой 2"" VALTEC BASE VALTEC 2 шт."\tdislike\tfixed\tkran\t\t\t\told\n',
        encoding="utf-8",
    )
    (reg / "feedback_approved_contract.tsv").write_text(
        f"fb_v\t{QUERY}\t{approved_status}\tkran\tДу50\n",
        encoding="utf-8",
    )


def _api(markers=True, brand_item=False):
    def fake(_api_url, _query, _size, _timeout):
        items = [{"article": "109-0769", "name": "Кран шар лат BVR-R Ду50 Ру40 м/м полн рыч Ридан"}]
        if brand_item:
            items.append({"article": "109-1111", "name": "Кран шар лат VALTEC BASE Ду50"})
        debug = {"decision_graph": {"summary": {"category_slug": "kran", "total": len(items)}}}
        if markers:
            debug["fallback_dropped_filters"] = [
                {
                    "stage": "brand",
                    "source": "kran_query_suppress",
                    "rule_ids": ["valtec_base_two_inch_drop_brand_catalog_gap"],
                }
            ]
        return {"items": items, "category": "kran", "debug": debug}

    return fake


def test_observable_brand_fallback_closes_brand_soft_obligation(tmp_path):
    _write_fixture(tmp_path)

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=_api(markers=True))

    row = out["suggestions"][0]
    assert row["suggested_status"] == "accepted_brand_fallback"
    assert row["root_cause"] == "observable_brand_fallback"
    assert any("fallback_dropped_filters[brand]" in e for e in row["evidence"])
    # Compatible with the brand-soft obligation: no approved conflict.
    assert out["approved_conflicts"] == []
    # The closure is a proposed needle (status transition), governed as usual.
    assert len(out["proposed_needles"]) == 1
    assert "\taccepted_brand_fallback\t" in out["proposed_needles"][0]["replace_text"]


def test_unobservable_drop_stays_open_with_precise_diagnosis(tmp_path):
    _write_fixture(tmp_path)

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=_api(markers=False))

    row = out["suggestions"][0]
    assert row["suggested_status"] == "open_runtime_bug"
    assert row["root_cause"] == "brand_drop_not_observable"
    assert [c["id"] for c in out["approved_conflicts"]] == ["fb_v"]


def test_outranked_brand_candidate_stays_open(tmp_path):
    _write_fixture(tmp_path)

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=_api(markers=True, brand_item=True))

    row = out["suggestions"][0]
    assert row["suggested_status"] == "open_runtime_bug"
    assert row["root_cause"] == "brand_candidate_lost"
    assert [c["id"] for c in out["approved_conflicts"]] == ["fb_v"]


def test_plain_must_find_does_not_accept_brand_fallback(tmp_path):
    _write_fixture(tmp_path, approved_status="must_find")

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=_api(markers=True))

    row = out["suggestions"][0]
    assert row["suggested_status"] == "open_runtime_bug"
    assert row["root_cause"] == "approved_query_signal_not_top1"
    assert [c["id"] for c in out["approved_conflicts"]] == ["fb_v"]


def test_full_brand_match_is_fixed_under_brand_soft(tmp_path):
    _write_fixture(tmp_path)

    def fake(_api_url, _query, _size, _timeout):
        return {
            "items": [{"article": "109-1111", "name": "Кран шаровой лат VALTEC BASE Ду50 Ру40"}],
            "category": "kran",
            "debug": {"decision_graph": {"summary": {"category_slug": "kran", "total": 1}}},
        }

    out = slug_close_workspace(str(tmp_path), slug="kran", api_func=fake)

    assert out["suggestions"][0]["suggested_status"] == "fixed"
    assert out["approved_conflicts"] == []


def test_compatibility_matrix():
    assert _approved_status_compatible("must_find_brand_soft", "accepted_brand_fallback")
    assert _approved_status_compatible("must_find_brand_soft", "fixed")
    assert not _approved_status_compatible("must_find", "accepted_brand_fallback")
    assert not _approved_status_compatible("must_find_brand_soft", "open_runtime_bug")


def test_lint_accepts_brand_soft_contract_status(tmp_path):
    _write_fixture(tmp_path)
    from apatch.slug_feedback_lint import feedback_lint_workspace

    out = feedback_lint_workspace(str(tmp_path))

    assert out["clean"] is True, json.dumps(out["findings"], ensure_ascii=False)
