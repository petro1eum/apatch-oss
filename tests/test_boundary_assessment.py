"""R35 boundary confidence and end_before / return-dedup strip behavior."""

from apatch.boundary_ranker import assess_until_marker
from apatch.strip import StripSpec, apply_strip, load_strip_manifest


def _clients_like_lines():
    return [
        "const Clients = () => {\n",
        "  const dispatch: AppDispatch = useDispatch();\n",
        "  const navigate = useNavigate();\n",
        "  const handleAdd = () => {\n",
        "    setIsModalOpen(true);\n",
        "  };\n",
        "  const handleSave = async () => {\n",
        "    await save();\n",
        "  };\n",
        "  return (\n",
        "    <div>clients</div>\n",
        "  );\n",
        "};\n",
    ]


def test_assess_until_root_return_high_confidence():
    assessment = assess_until_marker(
        "Page.tsx",
        _clients_like_lines(),
        "  const dispatch: AppDispatch = useDispatch();",
        "  return (",
    )
    assert assessment["ok"] is True
    assert assessment["confidence"] >= 0.45
    assert assessment["unstable"] is False
    assert assessment["kind"] == "root_return"


def test_assess_generic_until_unstable():
    assessment = assess_until_marker(
        "Page.tsx",
        _clients_like_lines(),
        "  const dispatch: AppDispatch = useDispatch();",
        "    };",
    )
    assert assessment["unstable"] is True
    assert assessment.get("error_type") == "STRIP_BOUNDARY_UNSTABLE"
    assert assessment["confidence"] <= 0.35


def test_end_before_manifest_alias():
    manifest = """[
      {
        "label": "hook",
        "start": "start here",
        "end_before": "until here",
        "replace": "// stub\\n"
      }
    ]"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.json"
        path.write_text(manifest, encoding="utf-8")
        specs = load_strip_manifest(path)
        assert len(specs) == 1
        assert specs[0].until == "until here"


def test_assess_until_root_return_within_function_scope():
    """Regression: no arbitrary line window — see test_boundary_scope.py for large bodies."""
    lines = ["const Page = () => {\n", "  const dispatch = useDispatch();\n", "  return (\n", "    <div />\n", "  );\n", "};\n"]
    assessment = assess_until_marker(
        "Page.tsx",
        lines,
        "  const dispatch = useDispatch();",
        "  return (",
    )
    assert assessment["unstable"] is False
    assert assessment["kind"] == "root_return"


def test_apply_strip_dedupes_return_in_replace_when_until_is_return():
    lines = [
        "const Page = () => {\n",
        "  const x = 1;\n",
        "  return (\n",
        "    <div />\n",
        "  );\n",
        "};\n",
    ]
    spec = StripSpec(
        start="const x = 1;",
        until="  return (",
        replace="  const { a } = usePage();\n\n  return (\n",
        module_kind="hook",
    )
    result = apply_strip(lines, spec)
    assert result.ok
    joined = "".join(lines)
    assert joined.count("return (") == 1
    assert "usePage" in joined
