"""strip parent-import parser ignores docstring/prose `from` lines (RFP-027 dogfood fix)."""
from apatch.imports_resolver import _regex_parent_imports


def test_parent_imports_ignores_docstring_from_line():
    content = (
        '"""Module doc.\n'
        'Reads signed receipts and aggregates time + volume\n'
        'from the raw signed ledger to detect tampering. Read-only.\n'
        '"""\n'
        "from __future__ import annotations\n"
        "import json\n"
        "from datetime import datetime, timezone\n"
        "from apatch.contribution import (\n"
        "from . import sibling\n"
    )
    imps = _regex_parent_imports("x.py", content)
    # real imports kept
    assert "import json" in imps
    assert "from __future__ import annotations" in imps
    assert "from datetime import datetime, timezone" in imps
    assert any(i.startswith("from apatch.contribution import") for i in imps)
    assert any(i.startswith("from . import sibling") for i in imps)
    # docstring/prose line dropped
    assert not any("raw signed ledger" in i for i in imps)