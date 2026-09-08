"""SPEC-CONCEPT-CLI-1 — `apatch concept ...` CLI over the RFP-034 Level 2 concept graph.

Wraps the concept-graph library (compile / coverage / verify / dedup / render / clarify /
merge) as terminal commands, each with ``--json`` for agents/CI. Recorded merges (§B.2)
collapse the graph before coverage/verify/dedup/graph/clarify see it, so the loop closes:
dedup raises a candidate, a human merges it, dedup stops raising it.
"""
from __future__ import annotations
from pathlib import Path

import glob
import json as _json
import os

import click

from apatch.concept_clarify import (
    clarifications,
    needs_clarification,
    resolve_clarification,
    set_clarification,
)
from apatch.concept_compile import (
    C4_LEVELS,
    compile_concept_graph,
    concept_coverage,
    dedup_candidates,
    render_concept_graph_md,
)
from apatch.concept_crud import add_concept, has_concept, remove_concept, update_concept
from apatch.concept_merge import (
    collapse_graph,
    merge_map,
    merges,
    record_merge,
    unmerge,
)
from apatch.concept_verify import verify_concept_invariants


def _load_concepts(target_dir: str, concepts_path) -> str:
    """Read one concept doc, or concatenate all docs/concepts/*.md under target_dir."""
    if concepts_path:
        with open(concepts_path, encoding="utf-8") as fh:
            return fh.read()
    files = sorted(glob.glob(os.path.join(target_dir, "docs", "concepts", "*.md")))
    return "\n\n".join(Path(f).read_text(encoding="utf-8") for f in files)


def _graph(target_dir: str, concepts_path):
    """Compiled graph with recorded merges collapsed in — the canonical live view."""
    g = compile_concept_graph(_load_concepts(target_dir, concepts_path))
    return collapse_graph(g, merge_map(target_dir))


@click.group("concept")
def concept_group():
    """Concept graph (RFP-034 Level 2): compile / coverage / verify / dedup / graph / clarify / merge / new / edit / rm."""
    pass


@concept_group.command("compile")
@click.argument("doc", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
def concept_compile_cmd(doc, as_json):
    """Compile a markdown doc's concept/claim fences into the concept graph."""
    g = compile_concept_graph(Path(doc).read_text(encoding="utf-8"))
    if as_json:
        click.echo(_json.dumps(g, indent=2, ensure_ascii=False))
    else:
        click.echo("concepts: %d  claims: %d  errors: %d"
                   % (len(g["concepts"]), len(g["claims"]), len(g["errors"])))
        for e in g["errors"]:
            click.echo("  ! " + e)
    if g["errors"]:
        raise SystemExit(1)


@concept_group.command("coverage")
@click.option("--concepts", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def concept_coverage_cmd(concepts, target_dir, as_json):
    """Guarded vs grey coverage of the concept graph (§B.5)."""
    cov = concept_coverage(_graph(target_dir, concepts))
    if as_json:
        click.echo(_json.dumps(cov, indent=2, ensure_ascii=False))
    else:
        click.echo("guarded %d/%d (%.0f%%); grey: %s" % (
            len(cov["guarded"]), cov["total"], cov["percent"], ", ".join(cov["grey"]) or "—"))


@concept_group.command("verify")
@click.option("--concepts", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def concept_verify_cmd(concepts, target_dir, as_json):
    """Run node/edge invariants (§B.3): green / red / broken / grey."""
    res = verify_concept_invariants(_graph(target_dir, concepts), target_dir)
    if as_json:
        click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    else:
        for cid, r in res.items():
            click.echo("%-30s %s" % (cid, r["status"]))
    if any(r["status"] == "red" for r in res.values()):
        raise SystemExit(1)


@concept_group.command("dedup")
@click.option("--concepts", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def concept_dedup_cmd(concepts, target_dir, as_json):
    """Raise merge candidates (§B.2) — never auto-merge. Recorded merges are excluded."""
    cands = dedup_candidates(_graph(target_dir, concepts))
    if as_json:
        click.echo(_json.dumps(cands, indent=2, ensure_ascii=False))
    else:
        if not cands:
            click.echo("no merge candidates")
        for c in cands:
            click.echo("? %s <-> %s (%s: %s)"
                       % (c["a"], c["b"], c["reason"], ", ".join(c["evidence"])))


@concept_group.command("graph")
@click.option("--concepts", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--title", default="Концепт-граф")
@click.option("--verify", "do_verify", is_flag=True,
              help="Colour nodes by live verify status (green/red/broken) instead of coverage.")
def concept_graph_cmd(concepts, target_dir, title, do_verify):
    """Render the concept graph as a MkDocs/Mermaid page to stdout (§B.7).

    Recorded merges (§B.2) are collapsed in; open clarifications (§4a) always overlay as
    amber `needs_clarification`, on top of the coverage or `--verify` colouring."""
    g = _graph(target_dir, concepts)
    statuses = None
    if do_verify:
        res = verify_concept_invariants(g, target_dir)
        statuses = {cid: r["status"] for cid, r in res.items()}
    click.echo(render_concept_graph_md(g, title=title, statuses=statuses,
                                       clarify=needs_clarification(target_dir)))


@concept_group.command("clarify")
@click.argument("cid", required=False)
@click.option("--note", default=None, help="Human note: why this is not yet settled.")
@click.option("--resolve", "do_resolve", is_flag=True, help="Clear the clarification (human decision).")
@click.option("--by", default=None, help="Who raised/resolved it.")
@click.option("--concepts", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def concept_clarify_cmd(cid, note, do_resolve, by, concepts, target_dir, as_json):
    """Mark/resolve a concept needing clarification (§4a). No CID -> list open ones.

    A human says "this is not yet settled"; the machine holds it `needs_clarification`
    (not-green) until a human resolves it — loose input is never silently taken as fact."""
    if not cid:
        data = clarifications(target_dir)
        if as_json:
            click.echo(_json.dumps(data, indent=2, ensure_ascii=False))
        elif not data:
            click.echo("no open clarifications")
        else:
            for k, v in sorted(data.items()):
                click.echo("? %-30s %s" % (k, v.get("note") or ""))
        return
    g = _graph(target_dir, concepts)
    known = set(g["concepts"]) | set(g["claims"])
    if cid not in known:
        raise click.ClickException("unknown concept/claim id: %s" % cid)
    if do_resolve:
        existed = resolve_clarification(cid, target_dir)
        click.echo(("resolved " if existed else "nothing open for ") + cid)
    else:
        if not note:
            raise click.ClickException("--note is required to raise a clarification")
        set_clarification(cid, note, target_dir, by=by)
        click.echo("flagged " + cid + " needs_clarification")


@concept_group.command("merge")
@click.argument("dropped", required=False)
@click.argument("into", required=False)
@click.option("--note", default=None, help="Why these are the same concept.")
@click.option("--unmerge", "do_unmerge", is_flag=True, help="Undo a recorded merge.")
@click.option("--by", default=None, help="Who decided the merge.")
@click.option("--concepts", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def concept_merge_cmd(dropped, into, note, do_unmerge, by, concepts, target_dir, as_json):
    """Confirm a merge (§B.2): `merge <dropped> <into>` records `dropped same_as into` and
    collapses the graph. No args -> list recorded merges. Dedup raises candidates; this is
    the human cut — never automatic."""
    if not dropped:
        data = merges(target_dir)
        if as_json:
            click.echo(_json.dumps(data, indent=2, ensure_ascii=False))
        elif not data:
            click.echo("no recorded merges")
        else:
            for k, v in sorted(data.items()):
                click.echo("%s -> %s  %s" % (k, v.get("into"), v.get("note") or ""))
        return
    if do_unmerge:
        existed = unmerge(dropped, target_dir)
        click.echo(("unmerged " if existed else "no merge recorded for ") + dropped)
        return
    if not into:
        raise click.ClickException("merge needs two ids: <dropped> <into>")
    raw = compile_concept_graph(_load_concepts(target_dir, concepts))
    known = set(raw["concepts"]) | set(raw["claims"])
    for cid in (dropped, into):
        if cid not in known:
            raise click.ClickException("unknown concept/claim id: %s" % cid)
    if dropped == into:
        raise click.ClickException("cannot merge a concept into itself")
    record_merge(dropped, into, target_dir, by=by, note=note)
    click.echo("merged %s -> %s" % (dropped, into))


# --- CRUD (SPEC-CONCEPT-CRUD-1): each C/U/D emits a governed needle of the canon ---

def _emit_crud_needle(target_dir, doc_path, old_text, new_text, op, cid):
    """Write a full-file-replace needle for the canon edit and return its path. The change
    only lands (notarised) when run through apatch_generate_batch -> apatch_apply_session."""
    rel = os.path.relpath(doc_path, target_dir)
    needle = [{"action": "replace", "target_file": rel, "find_text": old_text, "replace_text": new_text}]
    out = os.path.join(target_dir, ".apatch", "tmp", "concept_%s_%s.json" % (op, cid))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        _json.dump(needle, fh, ensure_ascii=False, indent=2)
    return out


def _find_concept_doc(target_dir, cid, explicit):
    if explicit:
        return explicit
    for f in sorted(glob.glob(os.path.join(target_dir, "docs", "concepts", "*.md"))):
        if has_concept(Path(f).read_text(encoding="utf-8"), cid):
            return f
    return None


def _parse_rels(rel_strs):
    out = []
    for s in rel_strs:
        if ":" not in s:
            raise click.ClickException("relation must be rel:to, got: " + s)
        rel, to = s.split(":", 1)
        out.append({"rel": rel.strip(), "to": to.strip()})
    return out


def _report_needle(cid, doc_rel, op, out):
    click.echo("%s %s in %s" % (op, cid, doc_rel))
    click.echo("governed apply (notarises): apatch_generate_batch(needles_path='%s') "
               "-> apatch_apply_session(logs_path=<out_path>)" % out)


@concept_group.command("new")
@click.argument("cid")
@click.option("--name", default=None)
@click.option("--c4", default=None)
@click.option("--alias", "aliases", multiple=True)
@click.option("--realized-by", "realized_by", multiple=True)
@click.option("--rel", "rels", multiple=True, help="relation as rel:to (repeatable).")
@click.option("--def", "definition", default=None, help="Human definition prose.")
@click.option("--doc", default=None, type=click.Path(), help="Target doc (default avatar-layer.md).")
@click.option("--target-dir", default=".", type=click.Path())
def concept_new_cmd(cid, name, c4, aliases, realized_by, rels, definition, doc, target_dir):
    """Create a concept (§6). Emits a governed needle; nothing lands without apply_session."""
    if c4 and c4 not in C4_LEVELS:
        raise click.ClickException("unknown c4 level: %s (one of %s)" % (c4, ", ".join(C4_LEVELS)))
    doc = doc or os.path.join(target_dir, "docs", "concepts", "avatar-layer.md")
    old = Path(doc).read_text(encoding="utf-8")
    try:
        new = add_concept(old, cid, name=name, c4=c4, aliases=list(aliases) or None,
                          realized_by=list(realized_by) or None,
                          relations=_parse_rels(rels) or None, definition=definition)
    except ValueError as e:
        raise click.ClickException(str(e))
    out = _emit_crud_needle(target_dir, doc, old, new, "new", cid)
    _report_needle(cid, os.path.relpath(doc, target_dir), "created", out)


@concept_group.command("edit")
@click.argument("cid")
@click.option("--name", default=None)
@click.option("--c4", default=None)
@click.option("--def", "definition", default=None)
@click.option("--add-rel", "add_rels", multiple=True, help="add relation rel:to (repeatable).")
@click.option("--rm-rel", "rm_rels", multiple=True, help="remove relations to this id (repeatable).")
@click.option("--add-alias", "add_aliases", multiple=True)
@click.option("--add-realized-by", "add_rb", multiple=True)
@click.option("--doc", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
def concept_edit_cmd(cid, name, c4, definition, add_rels, rm_rels, add_aliases, add_rb, doc, target_dir):
    """Edit a concept's fields (§6). Emits a governed needle."""
    if c4 and c4 not in C4_LEVELS:
        raise click.ClickException("unknown c4 level: %s (one of %s)" % (c4, ", ".join(C4_LEVELS)))
    doc = _find_concept_doc(target_dir, cid, doc)
    if not doc:
        raise click.ClickException("concept not found in docs/concepts: " + cid)
    old = Path(doc).read_text(encoding="utf-8")
    try:
        new = update_concept(old, cid, name=name, c4=c4, definition=definition,
                             add_relations=_parse_rels(add_rels) or None,
                             remove_relations_to=list(rm_rels) or None,
                             add_aliases=list(add_aliases) or None,
                             add_realized_by=list(add_rb) or None)
    except ValueError as e:
        raise click.ClickException(str(e))
    out = _emit_crud_needle(target_dir, doc, old, new, "edit", cid)
    _report_needle(cid, os.path.relpath(doc, target_dir), "edited", out)


@concept_group.command("rm")
@click.argument("cid")
@click.option("--doc", default=None, type=click.Path())
@click.option("--target-dir", default=".", type=click.Path())
def concept_rm_cmd(cid, doc, target_dir):
    """Delete a concept (§6). Emits a governed needle."""
    doc = _find_concept_doc(target_dir, cid, doc)
    if not doc:
        raise click.ClickException("concept not found in docs/concepts: " + cid)
    old = Path(doc).read_text(encoding="utf-8")
    try:
        new = remove_concept(old, cid)
    except ValueError as e:
        raise click.ClickException(str(e))
    out = _emit_crud_needle(target_dir, doc, old, new, "rm", cid)
    _report_needle(cid, os.path.relpath(doc, target_dir), "removed", out)
