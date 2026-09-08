import pytest
from apatch.manifest_validator import ManifestValidationError, assert_valid_manifest, validate_manifest
from apatch.strip import StripSpec


SAMPLE = """line0
// --- Block A start ---
block a line 1
block a line 2
// --- Block A end ---
// --- Block B start ---
block b line 1
// --- Block B end ---
tail
"""


def test_no_overlap_valid():
    lines = SAMPLE.splitlines(keepends=True)
    specs = [
        StripSpec(start="// --- Block A start ---", until="// --- Block A end ---", label="a"),
        StripSpec(start="// --- Block B start ---", until="// --- Block B end ---", label="b"),
    ]
    assert validate_manifest(lines, specs) == []


def test_touching_boundary_rejected():
    """Until of A must not be the same line as start of B (bottom-up strip breaks)."""
    lines = """line0
// --- Block A start ---
block a
// --- Block B start ---
block b
// --- Block B end ---
""".splitlines(keepends=True)
    specs = [
        StripSpec(start="// --- Block A start ---", until="// --- Block B start ---", label="a"),
        StripSpec(start="// --- Block B start ---", until="// --- Block B end ---", label="b"),
    ]
    errors = validate_manifest(lines, specs)
    assert errors
    assert any("boundary conflict" in e.lower() for e in errors)


def test_overlap_detected():
    lines = SAMPLE.splitlines(keepends=True)
    specs = [
        StripSpec(start="// --- Block A start ---", until="// --- Block B end ---", label="a"),
        StripSpec(start="// --- Block B start ---", until="// --- Block B end ---", label="b"),
    ]
    errors = validate_manifest(lines, specs)
    assert len(errors) == 1
    assert "overlap" in errors[0].lower()


def test_assert_valid_raises():
    lines = SAMPLE.splitlines(keepends=True)
    specs = [
        StripSpec(start="// --- Block A start ---", until="// --- Block B end ---", label="a"),
        StripSpec(start="// --- Block B start ---", until="// --- Block B end ---", label="b"),
    ]
    with pytest.raises(ManifestValidationError):
        assert_valid_manifest(lines, specs)
