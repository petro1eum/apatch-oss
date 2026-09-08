"""Docs must not silently drift from reality. Two invariants are gated against the live
MCP tool registry so the numbers and the CLI<->MCP parity table can't rot:

  * any live "MCP tools: N" claim in the human/agent entry docs equals the real count;
  * the mcp_setup parity table names every registered tool.

Meta-references to stale/illustrative numbers (e.g. "цифры вроде «38» в архиве") are skipped.
"""
import glob
import os
import re

import pytest

pytest.importorskip("mcp")

from apatch.mcp import server as mcp_server

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# a tool-count claim in any of the phrasings docs use (RU + EN)
_COUNT_RES = [
    re.compile(r"(\d{2,3})\s+(?:MCP\s+)?tools?\b"),
    re.compile(r"(\d{2,3})\s+инструмент"),
    re.compile(r"MCP[- :(]+\*{0,2}(\d{2,3})\b"),
    re.compile(r"tools?\s*\(\s*(\d{2,3})"),
    re.compile(r"сейчас\s*\*\*(\d{2,3})"),
    re.compile(r"count[^()\n]{0,30}?\(\s*\*{0,2}(\d{2,3})"),
    re.compile(r"\|\s*\*{0,2}full\*{0,2}\s*\|\s*(\d{2,3})\s*\|"),  # mcp_setup profile row
    # "17 compact / 124 full" — a profile size written as prose. docs/README.md carried
    # "15 compact" long after every gated phrasing had been corrected, because no
    # pattern looked for a number followed by a profile name.
    re.compile(r"(\d{2,3})\s+(?:compact|core|spec|full)\b"),
]
def _profile_bundle_counts():
    """Legit non-full counts: the compact/core/spec profile bundles themselves.

    Derived, never hardcoded — a profile may grow or shrink under its own
    requirement without silently turning this guard red.
    """
    from apatch.mcp.profiles import allowed_tools

    sizes = set()
    for name in ("compact", "core", "spec"):
        allowed = allowed_tools(name)
        if allowed is not None:
            sizes.add(len(allowed))
    return sizes
# lines that merely *reference* a number (historical delta / archive / version / date) —
# never a current full-count claim, so never gated
_META = re.compile(
    r"«|»|архив|вроде|archive|historical|0\.7\.0's|было|RFP-0\d|0\.7\.|2026-0"
    r"|\d+\s*→\s*\d+|\(\+\d|node|since|tool call|→\s*\*{0,2}\d")


def _registered():
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    if tm is None or not hasattr(tm, "_tools"):
        pytest.skip("FastMCP tool manager API unavailable")
    return set(tm._tools)


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _count_docs():
    # AGENTS.md is the contract an agent reads first; leaving it out of the guard let it
    # claim 15 compact tools while every gated doc had been corrected to 17.
    rels = ["README.md", "AGENTS.md"] + [
        os.path.relpath(p, _ROOT) for p in glob.glob(os.path.join(_ROOT, "docs", "*.md"))
    ]
    return sorted(set(rels))


def test_documented_tool_count_matches_reality():
    n = len(_registered())
    ok = _profile_bundle_counts() | {n}
    wrong = []
    for rel in _count_docs():
        for ln, line in enumerate(_read(rel).splitlines(), 1):
            if _META.search(line):
                continue
            for rx in _COUNT_RES:
                for m in rx.finditer(line):
                    val = int(m.group(1))
                    if val not in ok:
                        wrong.append(f"{rel}:{ln}: claims {val} tools, real {n} — {line.strip()[:75]!r}")
    assert not wrong, "stale tool counts (real=%d):\n%s" % (n, "\n".join(sorted(set(wrong))))


def test_mcp_setup_parity_lists_every_registered_tool():
    doc = _read("docs/mcp_setup.md")
    missing = sorted(t for t in _registered() if t not in doc)
    assert not missing, f"docs/mcp_setup.md parity table missing: {missing}"
