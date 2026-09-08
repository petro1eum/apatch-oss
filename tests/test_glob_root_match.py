"""Regression: '**/'-prefixed globs must match ROOT-level files in the governance
and sandbox matchers too (same fnmatch root-skip root cause as iter_matching_files).

A recursive allow-glob like '**/*.md' is meant to cover every .md file, including a
root-level README.md. fnmatch treats '**/' as requiring a literal '/', so without
de-prefix handling a root file is silently excluded — currently masked in the default
sandbox config only because it also lists '*.md'.
"""
from apatch.artifact_governance import _glob_match
from apatch.sandbox import _glob_matches


def test_governance_glob_match_root_recursive():
    assert _glob_match("README.md", "**/*.md") is True
    assert _glob_match("docs/x.md", "**/*.md") is True
    # protected-style suffix globs still work
    assert _glob_match("apatch/generate.py", "apatch/**") is True
    assert _glob_match("README.md", "apatch/**") is False


def test_sandbox_glob_match_root_recursive():
    assert _glob_matches("README.md", "**/*.md") is True
    assert _glob_matches("docs/x.md", "**/*.md") is True
    # suffix protect globs unchanged
    assert _glob_matches("apatch/generate.py", "apatch/**") is True
    assert _glob_matches("README.md", "apatch/**") is False
