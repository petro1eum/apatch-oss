"""Gate for REC-6a1d: re-applying an anchor-preserving replace must be an idempotent
SKIP, not a silent duplication.

When ReplacementContent keeps the TargetContent anchor (new contains old), the anchor
survives inside the file after the first apply, so a naive re-match re-inserts and
duplicates. The matcher must detect the already-applied block and skip it."""
from apatch.matcher import ASTMatcher


def _apply(path, old, new):
    m = ASTMatcher(str(path))
    return m.apply_patch(old, new)  # (success, content, strategy)


def test_anchor_preserving_reapply_skips_not_duplicates(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("head\n# ANCHOR\ntail\n", encoding="utf-8")
    old = "# ANCHOR\n"
    new = "# ANCHOR\nINSERTED = 1\n"  # append-after: new contains old (anchor-preserving)

    # first apply works and inserts exactly once
    ok, content, _ = _apply(f, old, new)
    assert ok and content.count("INSERTED = 1") == 1
    f.write_text(content, encoding="utf-8")

    # second apply (already applied) must SKIP — no duplication
    ok2, content2, strat2 = _apply(f, old, new)
    assert ok2 is False and strat2 == "already_applied"
    assert content2.count("INSERTED = 1") == 1


def test_prepend_anchor_preserving_reapply_skips(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    old = "x = 1\n"
    new = "y = 0\nx = 1\n"  # prepend-before: new contains old
    ok, content, _ = _apply(f, old, new)
    assert ok and content.count("y = 0") == 1
    f.write_text(content, encoding="utf-8")
    ok2, content2, strat2 = _apply(f, old, new)
    assert ok2 is False and strat2 == "already_applied" and content2.count("y = 0") == 1


def test_non_anchor_preserving_first_apply_unaffected(tmp_path):
    # new does NOT contain old -> normal replacement, guard must not interfere
    f = tmp_path / "m.py"
    f.write_text("value = 1\n", encoding="utf-8")
    ok, content, strat = _apply(f, "value = 1", "value = 2")
    assert ok and content == "value = 2\n" and strat == "exact"
