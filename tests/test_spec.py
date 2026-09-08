"""Executable Specifications (RFP-007): parser + ledger-derived requirement coverage."""

from apatch.spec import (
    Requirement,
    parse_spec,
    parse_spec_file,
    next_open_requirement,
    resolve_requirement,
    spec_lint,
    spec_lint_workspace,
    spec_status_from_entries,
    spec_next_workspace,
    spec_status_workspace,
)

SPEC_TEXT = """# SPEC-42 Avatar Economics

Intro paragraph (ignored).

## R1 Add Sandbox (verify: pytest tests/test_sandbox.py)
Body about sandbox.

## R2: Add Lease
Lease body.
verify: pytest tests/test_lease.py

## R3 Add Doctor Status
Doctor body, no verify here.
"""


# --- parsing -----------------------------------------------------------------------

def test_parse_ids_titles_and_verify():
    spec = parse_spec(SPEC_TEXT, source_path="docs/specs/SPEC-42.md")
    assert spec.id == "SPEC-42"
    assert [r.id for r in spec.requirements] == ["R1", "R2", "R3"]
    by_id = {r.id: r for r in spec.requirements}
    assert by_id["R1"].title == "Add Sandbox"
    assert by_id["R2"].title == "Add Lease"
    assert by_id["R3"].title == "Add Doctor Status"
    # verify from heading parenthesis and from body line
    assert by_id["R1"].verify == "pytest tests/test_sandbox.py"
    assert by_id["R2"].verify == "pytest tests/test_lease.py"
    assert by_id["R3"].verify is None
    assert all(r.content_hash.startswith("sha256:") for r in spec.requirements)
    assert spec.warnings == []


def test_parse_verify_format_tolerance():
    """Acceptance check recognized across common markdown forms (real-world specs)."""
    text = (
        "# S\n\n"
        "## R1 a\nbody\n(verify: npm run build)\n\n"      # parenthesized own line
        "## R2 b\n**verify:** ruff check\n\n"               # bold
        "## R3 c\n- verify: pytest tests/x.py\n\n"          # bullet
        "## R4 d\n> verify: go test ./...\n\n"              # blockquote
        "## R5 e (verify: make t)\nbody\n"                  # heading inline
    )
    spec = parse_spec(text)
    got = {r.id: r.verify for r in spec.requirements}
    assert got == {
        "R1": "npm run build",
        "R2": "ruff check",
        "R3": "pytest tests/x.py",
        "R4": "go test ./...",
        "R5": "make t",
    }


def test_parse_requirement_id_variants():
    text = "# S\n\n## FR-2 Functional\nx\n\n## NFR3 Perf\ny\n\n## REQ-10 Tenth\nz\n"
    spec = parse_spec(text)
    assert [r.id for r in spec.requirements] == ["FR-2", "NFR3", "REQ-10"]


def test_parse_ignores_non_requirement_headings():
    text = "# S\n\n## Overview\nnot a requirement\n\n## R1 Real\nyes\n"
    spec = parse_spec(text)
    assert [r.id for r in spec.requirements] == ["R1"]


def test_parse_spec_id_fallback_to_filename():
    spec = parse_spec("## R1 thing\nbody\n", source_path="/x/y/SPEC-OPS.md")
    assert spec.id == "SPEC-OPS"


def test_parse_duplicate_requirement_warns():
    text = "# S\n\n## R1 first\na\n\n## R1 dup\nb\n"
    spec = parse_spec(text)
    assert [r.id for r in spec.requirements] == ["R1"]
    assert any("duplicate" in w for w in spec.warnings)


def test_parse_no_requirements_warns():
    spec = parse_spec("# S\n\njust prose\n")
    assert spec.requirements == []
    assert any("no requirements" in w for w in spec.warnings)


def test_per_requirement_hash_isolation():
    """Editing one requirement changes only its hash, not the others."""
    a = parse_spec(SPEC_TEXT)
    edited = SPEC_TEXT.replace("Body about sandbox.", "Body about sandbox, expanded.")
    b = parse_spec(edited)
    ha = {r.id: r.content_hash for r in a.requirements}
    hb = {r.id: r.content_hash for r in b.requirements}
    assert ha["R1"] != hb["R1"]
    assert ha["R2"] == hb["R2"]
    assert ha["R3"] == hb["R3"]


def test_parse_spec_file(tmp_path):
    p = tmp_path / "SPEC-1.md"
    p.write_text(SPEC_TEXT, encoding="utf-8")
    spec = parse_spec_file(str(p))
    assert spec.id == "SPEC-42"  # H1 token wins over filename
    assert len(spec.requirements) == 3


# --- ledger-derived status ---------------------------------------------------------

def _intent_row(op, req_key, ts, content_hash=None):
    art = {"kind": "spec", "id": req_key}
    if content_hash:
        art["content_hash"] = content_hash
    return {
        "id": op,
        "tool_id": "apatch",
        "timestamp": ts,
        "payload": {"action": "engineering_pipeline", "intent": req_key, "artifacts": [art]},
    }


def _mutation_row(op, ts):
    return {"id": op, "tool_id": "apatch", "timestamp": ts, "payload": {"action": "apply", "applied_patches": 1}}


def _attest_row(op, ts):
    return {"id": op, "tool_id": "apatch_attest", "timestamp": ts, "payload": {"action": "attest"}}


def test_status_all_pending_without_ledger():
    spec = parse_spec(SPEC_TEXT)
    out = spec_status_from_entries(spec, [])
    assert out["summary"]["total"] == 3
    assert out["pending"] == ["R1", "R2", "R3"]
    assert out["complete"] == []
    assert out["done"] is False


def test_status_pending_in_progress_attested():
    spec = parse_spec(SPEC_TEXT)
    h1 = spec.requirements[0].content_hash
    entries = [
        _intent_row("i1", "SPEC-42#R1", "2026-01-01T00:01:00", h1),
        _mutation_row("m1", "2026-01-01T00:01:30"),
        _attest_row("a1", "2026-01-01T00:01:45"),
        _intent_row("i2", "SPEC-42#R2", "2026-01-01T00:02:00", spec.requirements[1].content_hash),
        _mutation_row("m2", "2026-01-01T00:02:30"),
    ]
    out = spec_status_from_entries(spec, entries)
    states = {r["id"]: r["state"] for r in out["requirements"]}
    assert states == {"R1": "attested", "R2": "in_progress", "R3": "pending"}
    assert out["complete"] == ["R1"]
    assert out["in_progress"] == ["R2"]
    assert out["pending"] == ["R3"]
    assert out["summary"]["percent_complete"] == 33.3


def test_status_stale_on_spec_drift():
    spec = parse_spec(SPEC_TEXT)
    entries = [
        _intent_row("i1", "SPEC-42#R1", "t1", "sha256:stalehash00000000"),
        _mutation_row("m1", "t2"),
        _attest_row("a1", "t3"),
    ]
    out = spec_status_from_entries(spec, entries)
    r1 = next(r for r in out["requirements"] if r["id"] == "R1")
    assert r1["state"] == "stale"
    assert r1["stale"] is True
    assert out["stale"] == ["R1"]


def test_status_attested_when_hash_matches():
    spec = parse_spec(SPEC_TEXT)
    h1 = spec.requirements[0].content_hash
    entries = [
        _intent_row("i1", "SPEC-42#R1", "t1", h1),
        _mutation_row("m1", "t2"),
        _attest_row("a1", "t3"),
    ]
    out = spec_status_from_entries(spec, entries)
    r1 = next(r for r in out["requirements"] if r["id"] == "R1")
    assert r1["state"] == "attested"
    assert r1["stale"] is False


def test_status_complete_when_all_attested():
    spec = parse_spec("# S\n\n## R1 a\nx\n")
    h = spec.requirements[0].content_hash
    entries = [
        _intent_row("i1", "S#R1", "t1", h),
        _mutation_row("m1", "t2"),
        _attest_row("a1", "t3"),
    ]
    out = spec_status_from_entries(spec, entries)
    assert out["done"] is True
    assert out["summary"]["percent_complete"] == 100.0


def test_next_open_requirement_order():
    spec = parse_spec(SPEC_TEXT)
    h1 = spec.requirements[0].content_hash
    entries = [
        _intent_row("i1", "SPEC-42#R1", "t1", h1),
        _mutation_row("m1", "t2"),
        _attest_row("a1", "t3"),
    ]
    out = spec_status_from_entries(spec, entries)
    nxt = next_open_requirement(out)
    assert nxt["id"] == "R2"  # R1 attested, R2 is next open


# --- resolve_requirement -----------------------------------------------------------

def _write_spec(tmp_path):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True)
    p = d / "SPEC-42.md"
    p.write_text(SPEC_TEXT, encoding="utf-8")
    return p


def test_resolve_requirement_full_ref(tmp_path):
    _write_spec(tmp_path)
    res = resolve_requirement(str(tmp_path), "SPEC-42#R2")
    assert res["ok"] is True
    assert res["spec_title"] == "SPEC-42 Avatar Economics"
    assert res["requirement"] == "R2"
    assert res["requirement_title"] == "Add Lease"
    assert res["intent"] == "SPEC-42#R2: Add Lease"
    assert res["artifact"].startswith("spec:SPEC-42#R2@sha256:")
    assert res["verify"] == "pytest tests/test_lease.py"


def test_resolve_requirement_bare_with_spec_path(tmp_path):
    p = _write_spec(tmp_path)
    res = resolve_requirement(str(tmp_path), "R3", spec="SPEC-42", spec_path=str(p))
    assert res["ok"] is True
    assert res["requirement"] == "R3"
    assert res["artifact"].startswith("spec:SPEC-42#R3@")


def test_resolve_requirement_unknown(tmp_path):
    _write_spec(tmp_path)
    res = resolve_requirement(str(tmp_path), "SPEC-42#R9")
    assert res["ok"] is False
    assert "R9" in res["error"]
    assert "R1" in res["available"]


def test_resolve_requirement_missing_spec(tmp_path):
    res = resolve_requirement(str(tmp_path), "NOPE#R1")
    assert res["ok"] is False


# --- workspace facades -------------------------------------------------------------

def test_spec_status_workspace_discovery(tmp_path):
    _write_spec(tmp_path)
    out = spec_status_workspace(str(tmp_path), spec="SPEC-42")
    assert out["ok"] is True
    assert out["ledger_active"] is False
    assert out["pending"] == ["R1", "R2", "R3"]


def test_spec_status_workspace_remembers_path(tmp_path):
    p = _write_spec(tmp_path)
    # first call with explicit path writes the registry pointer
    spec_status_workspace(str(tmp_path), spec_path=str(p))
    reg = tmp_path / ".apatch" / "specs" / "SPEC-42.json"
    assert reg.is_file()
    # subsequent call by id only resolves via the remembered pointer
    out = spec_status_workspace(str(tmp_path), spec="SPEC-42")
    assert out["ok"] is True


def test_spec_next_workspace(tmp_path):
    _write_spec(tmp_path)
    out = spec_next_workspace(str(tmp_path), spec="SPEC-42")
    assert out["ok"] is True
    assert out["next"]["id"] == "R1"
    assert "apatch_session_start(requirement='SPEC-42#R1')" == out["session_start"]


def test_spec_status_missing_returns_error(tmp_path):
    out = spec_status_workspace(str(tmp_path), spec="GHOST")
    assert out["ok"] is False
    assert "not found" in out["error"]


# --- lint --------------------------------------------------------------------------

def test_lint_clean_spec_passes():
    text = (
        "# SPEC-OK Title\n\n"
        "## R1 a (verify: pytest tests/a.py)\nbody\n\n"
        "## R2 b (verify: pytest tests/b.py)\nbody\n"
    )
    out = spec_lint(parse_spec(text), raw_text=text)
    assert out["passed"] is True
    assert out["counts"]["errors"] == 0


def test_lint_verify_cwd_mismatch(tmp_path):
    # A verify that cd's into a dir not present at the workspace root is caught at
    # LINT time, not silently at attest time (spec execute runs verify from the root).
    text = "# SPEC-CWD Title\n\n## R1 a (verify: cd subproj && pytest tests/a.py)\nbody\n"
    out = spec_lint(parse_spec(text), raw_text=text, root=str(tmp_path))
    assert "verify_cwd_mismatch" in [w["code"] for w in out["warnings"]]
    (tmp_path / "subproj").mkdir()  # cd target now exists from root -> no warning
    out2 = spec_lint(parse_spec(text), raw_text=text, root=str(tmp_path))
    assert "verify_cwd_mismatch" not in [w["code"] for w in out2["warnings"]]


def test_lint_verify_cwd_reenters_workspace(tmp_path):
    root = tmp_path / "opensearch" / "search"
    root.mkdir(parents=True)
    text = (
        "# SPEC-CWD Title\n\n"
        "## R1 a (verify: cd opensearch/search && pytest tests/a.py)\nbody\n"
    )

    out = spec_lint(parse_spec(text), raw_text=text, root=str(root))

    codes = [w["code"] for w in out["warnings"]]
    assert "verify_cwd_reenters_workspace" in codes


def test_lint_id_mismatch_is_error():
    text = (
        "# SPEC-HERMES-1 Title\n\n"
        "> apatch artifact: spec:HERMES-1\n\n"
        "## R1 a (verify: pytest a.py)\nbody\n"
    )
    out = spec_lint(parse_spec(text), raw_text=text)
    assert out["passed"] is False
    codes = [e["code"] for e in out["errors"]]
    assert "id_mismatch" in codes


def test_lint_missing_verify_warns():
    text = "# S\n\n## R1 a\nno verify here\n"
    out = spec_lint(parse_spec(text), raw_text=text)
    assert any(w["code"] == "missing_verify" for w in out["warnings"])


def test_lint_generic_and_weak_verify():
    text = (
        "# S\n\n"
        "## R1 a (verify: npm run build)\nx\n\n"
        "## R2 b (verify: npm run build)\ny\n"
    )
    out = spec_lint(parse_spec(text), raw_text=text)
    assert any(w["code"] == "generic_verify" for w in out["warnings"])
    assert any(i["code"] == "weak_verify" for i in out["info"])


def test_spec_lint_rejects_unattested_completion_claims(tmp_path):
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    path = docs / "SPEC-CLAIM.md"
    path.write_text(
        "# SPEC-CLAIM Completion claims\n\n"
        "> **Status:** IMPLEMENTED LOCALLY\n"
        "> **apatch artifact:** `spec:SPEC-CLAIM`\n\n"
        "| Requirement | Description |\n"
        "|---|---|\n"
        "| R1 | Complete-release gate |\n\n"
        "| Requirement | Status |\n"
        "|---|---|\n"
        "| R1 | COMPLETE |\n\n"
        "## R1 guarded change (verify: pytest tests/guarded.py)\nbody\n",
        encoding="utf-8",
    )

    out = spec_lint_workspace(
        str(tmp_path),
        spec="SPEC-CLAIM",
        include_needles_scaffold=False,
        include_rfp_gates=False,
    )

    assert out["passed"] is False
    codes = [error["code"] for error in out["errors"]]
    assert codes == [
        "unattested_spec_completion",
        "unattested_requirement_completion",
    ]
    assert out["derived_status"]["requirements"] == {"R1": "pending"}


def test_lint_workspace_on_file(tmp_path):
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True)
    (d / "SPEC-9.md").write_text(
        "# SPEC-9 Title\n\n## R1 a (verify: pytest a.py)\nbody\n", encoding="utf-8"
    )
    out = spec_lint_workspace(str(tmp_path), spec="SPEC-9")
    assert out["ok"] is True
    assert out["passed"] is True
