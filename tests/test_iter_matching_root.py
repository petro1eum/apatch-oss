"""Regression: iter_matching_files must match ROOT-level files for recursive globs.

Bug: fnmatch treats '**/' as requiring a literal '/' in the path, so a root-level
file (rel path == basename, no slash) never matches '**/*' or '**/*.md'. This made
`replace` needles (whose default glob is '**/*') silently skip root files — e.g. a
find/replace targeting README.md returned "find_text not found".
"""
from apatch.generate import iter_matching_files


def _seed(tmp_path):
    (tmp_path / "README.md").write_text("root md\n")
    (tmp_path / "omega_spec.md").write_text("root md 2\n")
    sub = tmp_path / "docs"
    sub.mkdir()
    (sub / "guide.md").write_text("subdir md\n")
    (tmp_path / "script.py").write_text("root py\n")


def test_recursive_glob_matches_root_and_subdir_md(tmp_path):
    _seed(tmp_path)
    got = set(iter_matching_files(str(tmp_path), "**/*.md"))
    assert "README.md" in got, "root-level README.md must match **/*.md"
    assert "omega_spec.md" in got
    assert "docs/guide.md" in got
    assert "script.py" not in got


def test_default_glob_matches_root_files(tmp_path):
    _seed(tmp_path)
    got = set(iter_matching_files(str(tmp_path), "**/*"))
    # '**/*' is the default glob for replace needles — must include root files.
    assert "README.md" in got
    assert "docs/guide.md" in got


def test_exact_target_file_under_symlinked_dir_matches(tmp_path):
    real_docs = tmp_path / "real_docs"
    real_docs.mkdir()
    (real_docs / "SPEC.md").write_text("spec\n", encoding="utf-8")
    (tmp_path / "docs").symlink_to(real_docs, target_is_directory=True)

    got = list(iter_matching_files(str(tmp_path), "docs/SPEC.md"))

    assert got == ["docs/SPEC.md"]
