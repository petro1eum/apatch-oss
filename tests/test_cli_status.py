"""Tests for SPEC-CLI-STATUS-1 R1 (apatch status)."""

from __future__ import annotations

from click.testing import CliRunner

from apatch.cli_status import build_status_view, format_status_plain_lines


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


def test_status_json_shape(tmp_path):
    _write_spec(tmp_path, "SPEC-CLI-A")
    dto = build_status_view(str(tmp_path))
    assert dto["ok"] is True
    assert dto["schema_version"] == 1
    for key in (
        "specs",
        "summary",
        "conflicts",
        "active_session",
        "hygiene",
        "policy",
        "activity",
        "session_phase",
        "next_action",
    ):
        assert key in dto


def test_status_human_smoke(tmp_path):
    _write_spec(tmp_path, "SPEC-CLI-B", 2)
    dto = build_status_view(str(tmp_path))
    lines = format_status_plain_lines(dto)
    assert any("Session:" in ln for ln in lines)
    assert any("Hygiene:" in ln for ln in lines)
    assert any("Specs:" in ln for ln in lines)
    assert any("SPEC-CLI-B" in ln for ln in lines)


def test_status_cli_json_flag(tmp_path):
    _write_spec(tmp_path, "SPEC-CLI-C")
    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--target-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["schema_version"] == 1

def test_spec_list(tmp_path):
    _write_spec(tmp_path, "SPEC-LIST-A")
    _write_spec(tmp_path, "SPEC-LIST-B")
    from apatch.cli_status import spec_list_workspace

    out = spec_list_workspace(str(tmp_path))
    assert out["ok"] is True
    assert out["count"] == 2
    assert "SPEC-LIST-A" in out["specs"]
    assert "SPEC-LIST-B" in out["specs"]

    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["spec", "list", "--target-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "SPEC-LIST-A" in result.output
    assert "SPEC-LIST-B" in result.output
