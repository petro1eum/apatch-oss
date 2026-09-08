"""Tests for SPEC-REPORT-1."""

from __future__ import annotations


def _write_spec(tmp_path, spec_id: str, n_req: int = 1):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {spec_id} — Title",
        f"> **apatch artifact:** `spec:{spec_id}`",
        "",
    ]
    for i in range(1, n_req + 1):
        lines += [f"## R{i} Req {i}", "(verify: true)", ""]
    (d / f"{spec_id}.md").write_text("\n".join(lines), encoding="utf-8")


def test_report_html_self_contained(tmp_path):
    _write_spec(tmp_path, "SPEC-RPT-A", 2)
    _write_spec(tmp_path, "SPEC-RPT-B", 2)
    from apatch.report_render import build_report_bundle, render_html

    bundle = build_report_bundle(str(tmp_path))
    assert bundle.get("ok") is True
    html_out = render_html(bundle)
    lower = html_out.lower()
    assert "<html" in lower
    assert "http://" not in html_out and "https://" not in html_out
    assert "SPEC-RPT-A" in html_out
    assert "conflict graph" in lower
    assert "adherence drift" in lower


def test_report_md_contains_progress(tmp_path):
    _write_spec(tmp_path, "SPEC-RPT-C", 3)
    from apatch.report_render import build_report_bundle, render_md

    bundle = build_report_bundle(str(tmp_path))
    md_en = render_md(bundle, locale="en")
    assert "requirement" in md_en.lower()
    assert "3" in md_en
    md_ru = render_md(bundle, locale="ru")
    assert "требован" in md_ru.lower()


def test_report_renderers_consume_bundle_only(monkeypatch):
    import apatch.report_render as rr

    calls: list[str] = []

    def _track(*_a, **_k):
        calls.append("project_status")
        return {"ok": True}

    monkeypatch.setattr("apatch.project_status.project_status_workspace", _track)
    bundle = {
        "ok": True,
        "dto": {
            "ok": True,
            "workspace": "/tmp",
            "specs": [],
            "summary": {"attested": 0, "total_requirements": 0, "percent_complete": 0},
            "activity": [],
            "hygiene": {},
            "conflicts": None,
        },
        "schedule": {},
        "adherence": [],
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    rr.render_html(bundle)
    rr.render_md(bundle)
    assert calls == []


def test_report_cli_html(tmp_path):
    _write_spec(tmp_path, "SPEC-RPT-D")
    from click.testing import CliRunner

    from apatch.cli import cli

    out_file = tmp_path / "report.html"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["report", "--html", "--target-dir", str(tmp_path), "--out", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "SPEC-RPT-D" in text
