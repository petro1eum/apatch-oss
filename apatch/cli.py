from __future__ import annotations

from apatch.warn_filters import configure_apatch_warnings

configure_apatch_warnings()

import os
import sys
import re
import json
import hashlib
from typing import Optional
import click
from apatch import __version__
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from apatch.ingestor import LogIngestor
from apatch.tui import InteractiveTUI
from apatch.strip import StripSpec, suggest_until_candidates
from apatch.trustchain_helper import TrustChainHelper
from apatch.strip_pipeline import load_specs_from_manifest_or_markers
from apatch.consumer_profiles import list_profiles
from apatch.doctor import init_consumer, run_doctor
from apatch.generate import generate_patches, write_jsonl
from apatch.workflows import (
    WorkflowError,
    apply_from_logs,
    arch_check_workspace,
    compile_markdown_file,
    db_check_workspace,
    db_revision_workspace,
    db_run_manifest,
    db_safety_workspace,
    index_build_workspace,
    index_query_workspace,
    trustchain_coverage_workspace,
    trustchain_intent_history_workspace,
    pipeline_run_manifest,
    refactor_run_manifest,
    semantic_verify_workspace,
    filter_patch_candidates,
    generate_patch_jsonl,
    generate_patch_jsonl_batch,
    impact_analysis,
    natives_check,
    phase_run,
    plan_from_logs,
    rollback_workspace,
    run_strip,
    scan_transcripts,
    view_log_candidates,
)

console = Console()


def _strip_rollback_transaction(
    tc_helper: Optional[TrustChainHelper],
    tc_checkpoint: Optional[str],
    file_path: str,
    exported_meta: list,
    report_path: Optional[str] = None,
) -> None:
    """Revert strip: restore source file from backup and reset TrustChain HEAD."""
    from apatch.workflows import strip_rollback_transaction

    outcome = strip_rollback_transaction(
        tc_helper, tc_checkpoint, file_path, exported_meta, report_path
    )
    if outcome.get("trustchain_restored"):
        console.print(
            "[bold green]🛡️ Rolled back TrustChain checkpoint and restored source file(s).[/bold green]"
        )
    elif tc_helper and tc_checkpoint:
        console.print(
            "[bold yellow]TrustChain rollback did not fully restore files; "
            "check .apatch/backups/ manually.[/bold yellow]"
        )
    elif outcome.get("backup_restored"):
        console.print("[bold green]✓ Restored files from strip backup session[/bold green]")


def _filter_candidates(candidates, tool, keyword, steps, range_str):
    """Filter candidates using tool, keyword, steps, and range arguments."""
    try:
        return filter_patch_candidates(
            candidates,
            tool=tool,
            keyword=keyword,
            steps=steps,
            range_str=range_str,
        )
    except WorkflowError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)

@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx):
    """apatch: Governed Mutation System — Agent Patch & Trace Analyzer 🚀"""
    if ctx.invoked_subcommand is not None:
        return
    if sys.stdin.isatty() and sys.stdout.isatty():
        from apatch.console.mvp import run_console

        sys.exit(run_console(".", refresh=True))
    click.echo(ctx.get_help())

@cli.command()
@click.option("--path", "extra_paths", multiple=True, type=click.Path(), help="Extra file or directory to search for .jsonl transcripts (repeatable).")
@click.option("--limit", default=20, show_default=True, help="Maximum number of transcripts to list.")
@click.option("--no-cwd", is_flag=True, help="Do not include *.jsonl in the current directory.")
@click.option("--no-count", is_flag=True, help="Skip parsing each transcript to count patch candidates (faster).")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of a table.")
def scan(extra_paths, limit, no_cwd, no_count, as_json):
    """Auto-discover agent transcript logs (Cursor / Claude / Gemini / cwd)."""
    rows = scan_transcripts(
        extra_paths=list(extra_paths),
        limit=limit,
        include_cwd=not no_cwd,
        count_candidates=not no_count,
    )

    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        console.print("[bold yellow]No agent transcripts (.jsonl) found.[/bold yellow]")
        console.print("[dim]Tip: pass --path <dir-or-file> to point apatch at custom locations.[/dim]")
        return

    import datetime
    table = Table(title="Discovered agent transcripts", border_style="cyan")
    table.add_column("#", style="dim")
    table.add_column("Modified", style="yellow")
    table.add_column("Source", style="magenta")
    table.add_column("Candidates", style="green")
    table.add_column("Path", style="blue")

    for idx, t in enumerate(rows, 1):
        modified = datetime.datetime.fromtimestamp(t["mtime"]).strftime("%Y-%m-%d %H:%M")
        count = "-" if t.get("candidate_count") is None else str(t["candidate_count"])
        table.add_row(str(idx), modified, t["source"], count, t["path"])

    console.print(table)
    console.print(
        f"[bold green]Found {len(rows)} transcript(s).[/bold green] "
        "Inspect one with: [cyan]apatch plan --logs <path> --target-dir .[/cyan]"
    )

@cli.command()
@click.option("--logs", required=True, type=click.Path(exists=True), help="Path to the agent JSONL transcript log.")
@click.option("--tool", help="Filter patches by specific tool name (e.g. StrReplace, replace_file_content).")
@click.option("--filter", "keyword", help="Filter patches by keyword matches inside target content.")
@click.option("--steps", help="Comma-separated step numbers to view (e.g. 5,10,12).")
@click.option("--range", "range_str", help="Inclusive step index range to view (e.g. 10-20).")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of a table.")
def view(logs, tool, keyword, steps, range_str, as_json):
    """View and list all patch candidates found in the log file."""
    try:
        rows = view_log_candidates(
            logs, tool=tool, keyword=keyword, steps=steps, range_str=range_str
        )
    except WorkflowError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Error parsing transcript logs: {e}[/bold red]")
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        console.print("[bold yellow]No matching patch candidates found in logs.[/bold yellow]")
        return

    table = Table(title=f"Patch Candidates in {os.path.basename(logs)}", border_style="cyan")
    table.add_column("Step", style="yellow")
    table.add_column("Tool", style="magenta")
    table.add_column("Target File", style="green")
    table.add_column("Source", style="blue")
    table.add_column("Old Code Length", style="dim")
    table.add_column("New Code Length", style="dim")

    for cand in rows:
        table.add_row(
            str(cand["step_index"]),
            cand["tool_name"],
            cand["target_file"] or "<unknown>",
            cand["source_format"],
            f"{cand['old_len']} chars",
            f"{cand['new_len']} chars",
        )

    console.print(table)
    console.print(f"[bold green]Total: {len(rows)} patch candidates listed.[/bold green]")

@cli.command()
@click.option("--logs", required=True, type=click.Path(exists=True), help="Path to the agent JSONL transcript log.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False), help="Path to the target codebase root.")
@click.option("--tool", help="Filter patches by specific tool name.")
@click.option("--filter", "keyword", help="Filter patches by keyword matches.")
@click.option("-y", "--yes", is_flag=True, help="Run in non-interactive batch mode, auto-applying successful alignments.")
@click.option("-n", "--dry-run", is_flag=True, help="Simulate matching and previews without modifying files.")
@click.option("-a", "--all", "replace_all", is_flag=True, help="Replace EVERY occurrence of each match (token-rename refactors), not just the first.")
@click.option("--steps", help="Comma-separated step numbers to apply (e.g. 5,10,12).")
@click.option("--range", "range_str", help="Inclusive step index range to apply (e.g. 10-20).")
@click.option(
    "--verify",
    "verify_cmd",
    help="Shell command to run after each step (shell=True). Prefer a script e.g. 'pytest' or './scripts/check.sh'; "
    "avoid complex inline Python with quotes/globs.",
)
@click.option("--verify-deferred", is_flag=True, help="Defer verification command execution to run once at the end of the entire session.")
@click.option("--no-trustchain", is_flag=True, help="Do not auto-init or write to .trustchain/.")
@click.option("--only-drifted", is_flag=True, help="Apply only candidates that need fuzzy help; skip clean exact matches.")
@click.option("--min-confidence", type=float, default=None, help="In --yes mode, auto-apply only when match confidence >= this value (0..1).")
@click.option("--report", "report_path", type=click.Path(dir_okay=False), help="Write a machine-readable JSON session report to this path.")
@click.option("--budget", type=click.Choice(["small", "medium", "large"]), default=None, help="Reject apply if estimated diff exceeds preset (R49).")
@click.option("--max-files", type=int, default=None, help="Change budget: max files touched.")
@click.option("--max-insertions", type=int, default=None, help="Change budget: max added lines.")
@click.option("--max-deletions", type=int, default=None, help="Change budget: max removed lines.")
def apply(logs, target_dir, tool, keyword, yes, dry_run, replace_all, steps, range_str, verify_cmd, verify_deferred, no_trustchain, only_drifted, min_confidence, report_path, budget, max_files, max_insertions, max_deletions):
    """Interactively review and apply patches to the target codebase."""
    if yes:
        from apatch.change_budget import resolve_budget

        change_budget = resolve_budget(
            budget,
            max_files=max_files,
            max_insertions=max_insertions,
            max_deletions=max_deletions,
        )
        from apatch.runtime.runtime import MutationRuntime

        result = MutationRuntime(target_dir).apply(
            logs,
            tool=tool,
            keyword=keyword,
            steps=steps,
            range_str=range_str,
            replace_all=replace_all,
            min_confidence=min_confidence,
            only_drifted=only_drifted,
            verify=verify_cmd,
            verify_deferred=verify_deferred,
            no_trustchain=no_trustchain,
            dry_run=dry_run,
            report_path=report_path,
            change_budget=change_budget,
        )
        if not result.get("ok") and result.get("error_type") == "RUNTIME_TRANSITION":
            console.print(f"[bold red]{result.get('error')}[/bold red]")
            if result.get("hint"):
                console.print(f"[dim]{result['hint']}[/dim]")
            sys.exit(1)
        if result.get("total", 0) == 0:
            console.print("[bold yellow]No matching patch candidates found in logs.[/bold yellow]")
            return
        if not result["ok"]:
            if result.get("reason") == "change_budget_exceeded":
                cb = result.get("change_budget") or {}
                console.print(f"[bold red]Change budget exceeded[/bold red]: {cb.get('violations')}")
                console.print(json.dumps(cb.get("estimate"), indent=2))
            sys.exit(1)
        ckpt = result.get("checkpoint")
        if ckpt:
            console.print(f"[dim]Checkpoint: {ckpt}[/dim]")
        console.print(
            f"[bold green]Applied {result['applied']}, skipped {result['skipped']}, "
            f"failed {result['failed']}[/bold green]"
        )
        return

    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).apply_interactive(
        logs,
        tool=tool,
        keyword=keyword,
        steps=steps,
        range_str=range_str,
        replace_all=replace_all,
        min_confidence=min_confidence,
        only_drifted=only_drifted,
        verify=verify_cmd,
        verify_deferred=verify_deferred,
        no_trustchain=no_trustchain,
        dry_run=dry_run,
        report_path=report_path,
    )
    if result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
        sys.exit(1)
    if result.get("total", 0) == 0 and result.get("ok", True):
        console.print("[bold yellow]No matching patch candidates found in logs.[/bold yellow]")
        return
    if not result.get("ok", True):
        sys.exit(1)


@cli.command("apply-session")
@click.option("--logs", required=True, type=click.Path(exists=True), help="Path to patches JSONL.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--verify", "verify_cmd", help="Verify command (deferred until last chunk by default).")
@click.option("--session", "session_path", default=None, help="Session state file (default .apatch/apply_session.json).")
@click.option("--chunk-files", default=5, show_default=True, type=int, help="Max unique files per chunk.")
@click.option("-a", "--all", "replace_all", is_flag=True)
@click.option("--only-drifted", is_flag=True)
@click.option("--min-confidence", type=float, default=None)
@click.option("--no-trustchain", is_flag=True)
@click.option("--reset", is_flag=True, help="Discard prior session and replan.")
@click.option("--abort", is_flag=True, help="Rollback last checkpoint and clear session.")
@click.option("--loop", is_flag=True, help="Run chunks until complete or failure.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def apply_session_cmd(
    logs,
    target_dir,
    verify_cmd,
    session_path,
    chunk_files,
    replace_all,
    only_drifted,
    min_confidence,
    no_trustchain,
    reset,
    abort,
    loop,
    as_json,
):
    """Chunked apply with TrustChain checkpoint after each chunk."""
    from apatch.runtime.runtime import MutationRuntime

    rt = MutationRuntime(target_dir)
    if loop and not abort:
        while True:
            result = rt.apply_session(
                logs,
                session_path=session_path,
                verify=verify_cmd,
                chunk_max_files=chunk_files,
                replace_all=replace_all,
                only_drifted=only_drifted,
                min_confidence=min_confidence,
                no_trustchain=no_trustchain,
                reset=reset,
                abort=False,
            )
            reset = False
            if result.get("error_type") == "RUNTIME_TRANSITION":
                console.print(f"[bold red]{result.get('error')}[/bold red]")
                sys.exit(1)
            prog = result.get("progress") or {}
            console.print(
                f"[cyan]Chunk {prog.get('chunks_done', '?')}/{prog.get('chunks_total', '?')}[/cyan] "
                f"checkpoint={result.get('checkpoint')}"
            )
            if not result.get("continue"):
                break
            if not result.get("ok"):
                console.print(f"[bold red]Session stopped: {result}[/bold red]")
                sys.exit(1)
        console.print("[bold green]apply-session complete[/bold green]")
        return

    result = rt.apply_session(
        logs,
        session_path=session_path,
        verify=verify_cmd,
        chunk_max_files=chunk_files,
        replace_all=replace_all,
        only_drifted=only_drifted,
        min_confidence=min_confidence,
        no_trustchain=no_trustchain,
        reset=reset,
        abort=abort,
    )
    if result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)

    if as_json:
        console.print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        from apatch.cli_status import print_apply_session_human

        print_apply_session_human(console, result)
    if not result.get("ok"):
        sys.exit(1)


@cli.command("plan")
@click.option("--logs", required=True, type=click.Path(exists=True), help="Path to the agent JSONL transcript log.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False), help="Path to the target codebase root.")
@click.option("--tool", help="Filter patches by specific tool name.")
@click.option("--filter", "keyword", help="Filter patches by keyword matches.")
@click.option("--steps", help="Comma-separated step numbers to plan (e.g. 5,10,12).")
@click.option("--range", "range_str", help="Inclusive step index range to plan (e.g. 10-20).")
@click.option("--diff", "show_diff", is_flag=True, help="Print a unified diff preview for each candidate.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of a table.")
@click.option(
    "--workers",
    default=0,
    show_default=True,
    type=int,
    help="Parallel workers for plan --json (0=sequential; -1=all CPUs). Env: APATCH_PLAN_WORKERS.",
)
def plan_cmd(logs, target_dir, tool, keyword, steps, range_str, show_diff, as_json, workers):
    """Dry-run: show how each patch would align (path, strategy, confidence) without writing."""
    from apatch.runtime.runtime import MutationRuntime

    plan = MutationRuntime(target_dir).plan(
        logs,
        tool=tool,
        keyword=keyword,
        steps=steps,
        range_str=range_str,
        show_diff=show_diff,
        workers=workers,
    )
    if not plan.get("ok", True) and plan.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{plan.get('error')}[/bold red]")
        if plan.get("hint"):
            console.print(f"[dim]{plan['hint']}[/dim]")
        sys.exit(1)
    if plan.get("error") and not plan.get("entries"):
        console.print(f"[bold red]Error: {plan['error']}[/bold red]")
        sys.exit(1)

    entries = plan["entries"]
    would = plan["would_apply"]

    if as_json:
        click.echo(json.dumps(entries, indent=2, ensure_ascii=False))
        return

    if not entries:
        console.print("[bold yellow]No matching patch candidates found in logs.[/bold yellow]")
        return

    from rich.syntax import Syntax
    table = Table(title=f"Apply plan for {os.path.basename(logs)}", border_style="cyan")
    table.add_column("Step", style="yellow")
    table.add_column("File", style="green")
    table.add_column("Action", style="blue")
    table.add_column("Strategy", style="magenta")
    table.add_column("Conf", style="cyan")
    table.add_column("Apply?", style="bold")

    for e in entries:
        file_label = os.path.basename(e["resolved_path"]) if e["resolved_path"] else f"[red]{e['target_file'] or '<unknown>'}[/red]"
        apply_label = "[green]yes[/green]" if e["would_apply"] else "[red]no[/red]"
        table.add_row(
            str(e["step_index"]),
            file_label,
            e["action_type"],
            e["strategy"] or "-",
            f"{e['confidence']:.2f}",
            apply_label,
        )
    console.print(table)

    for e in entries:
        for w in e["warnings"]:
            console.print(f"[bold yellow]⚠ step {e['step_index']} {os.path.basename(e['resolved_path'] or e['target_file'] or '')}: {w}[/bold yellow]")
        if show_diff and e["diff"]:
            console.print(Panel(
                Syntax(e["diff"], "diff", theme="monokai", background_color="default"),
                title=f"[cyan]step {e['step_index']} diff[/cyan]",
                border_style="cyan",
            ))

    console.print(f"[bold green]{would}/{len(entries)} candidates would apply.[/bold green]")

@cli.command("strip")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False), help="Target source file.")
@click.option("--start", "start_marker", help="First line containing this marker (inclusive) is removed.")
@click.option("--until", "until_marker", help="First line containing this marker (exclusive) ends the removed span.")
@click.option("--replace", "replace_text", default="", help="Replacement text (stub comment).")
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), help="JSON/YAML manifest with multiple strip specs.")
@click.option("-n", "--dry-run", is_flag=True, help="Preview only; do not write the file.")
@click.option("--out-dir", type=click.Path(file_okay=False), help="Directory to export the stripped blocks into.")
@click.option("--export-ext", default=None, help="Extension for exported blocks (default: language-aware, .fragment.txt for TS).")
@click.option("--export-filename", help="Explicit filename to export the stripped block into (ignores dynamic extraction).")
@click.option("--no-trustchain", is_flag=True, help="Do not auto-init or write to .trustchain/.")
@click.option("--to-native", help="C++ registration function name. If provided, automatically transforms all extracted blocks into C++ Native modules.")
@click.option("--to-module", type=click.Choice(["hook", "component", "util"]), help="Generate TS/React module scaffold from extracted block.")
@click.option("--module-out-dir", type=click.Path(file_okay=False), help="Directory for generated TS modules (--to-module).")
@click.option("--post-hook", help="Command template to execute after a successful strip. `{extracted_path}`, `{out_path}`, `{label}`, `{report_path}` will be interpolated.")
@click.option("--strict", is_flag=True, help="Aborts with exit 1 if C++ native converter detects blocker incompatibilities.")
@click.option("--strict-overlap", is_flag=True, help="Abort if manifest blocks overlap (recommended for batch strips).")
@click.option("--strict-dangling", is_flag=True, help="Abort if parent still references symbols from removed blocks.")
@click.option("--suggest-until", is_flag=True, help="Prints fuzzy boundary suggestions forward from start marker on exact miss.")
@click.option("--native-out-dir", type=click.Path(file_okay=False), help="Directory to export the transformed C++ native modules into (defaults to --out-dir).")
@click.option("--emit-wiring", type=click.Path(dir_okay=False), help="Optional path to emit human-readable Markdown patch/wiring instructions.")
@click.option("--verify", "verify_cmd", help="Shell command after strip; rollback on failure.")
@click.option("--auto-wire", is_flag=True, help="Insert parent_import and replace stub with parent_wire (--to-module).")
@click.option("--emit-barrel", type=click.Path(dir_okay=False), help="Append barrel re-exports to this index.ts file.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON result.")
def strip_cmd(
    file_path,
    start_marker,
    until_marker,
    replace_text,
    manifest,
    dry_run,
    out_dir,
    export_ext,
    export_filename,
    no_trustchain,
    to_native,
    to_module,
    module_out_dir,
    post_hook,
    strict,
    strict_overlap,
    strict_dangling,
    suggest_until,
    native_out_dir,
    emit_wiring,
    verify_cmd,
    auto_wire,
    emit_barrel,
    as_json,
):
    """Remove marker-bounded blocks from a source file (refactor stub insertion).

    Use instead of ad-hoc python/sed for bulk native-module extractions.
    """
    try:
        if start_marker and suggest_until and not until_marker:
            specs = [StripSpec(start=start_marker, until="", replace="", export="")]
        else:
            specs = load_specs_from_manifest_or_markers(
                manifest, start_marker, until_marker, replace_text, export_filename
            )
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)

    if suggest_until:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for spec in specs:
                start_idx = -1
                for idx, line in enumerate(lines):
                    if spec.start in line:
                        start_idx = idx
                        break
                if start_idx == -1:
                    console.print(f"[bold red]Start marker not found for suggestion:[/bold red] {spec.start!r}")
                    continue
                console.print(f"[bold blue]🔍 Boundary 'until' suggestions for start marker[/bold blue] {spec.start!r}:")
                candidates = suggest_until_candidates(lines, start_idx, file_path=file_path)
                for idx, desc in candidates:
                    console.print(f"  - Line {idx}: {desc.strip()!r}")
            sys.exit(0)
        except Exception as e:
            console.print(f"[bold red]Error running --suggest-until:[/bold red] {e}")
            sys.exit(1)

    from apatch.runtime.runtime import MutationRuntime
    from apatch.import_paths import resolve_consumer_root

    workspace = resolve_consumer_root(file_path)
    payload = MutationRuntime(workspace).strip(
        file_path,
        manifest_path=manifest,
        start_marker=start_marker,
        until_marker=until_marker,
        replace_text=replace_text,
        export_filename=export_filename,
        dry_run=dry_run,
        out_dir=out_dir,
        export_ext=export_ext,
        no_trustchain=no_trustchain,
        to_native=to_native,
        to_module=to_module,
        module_out_dir=module_out_dir,
        native_out_dir=native_out_dir,
        post_hook=post_hook,
        strict=strict,
        strict_overlap=strict_overlap,
        strict_dangling=strict_dangling,
        emit_wiring=emit_wiring,
        verify_cmd=verify_cmd,
        auto_wire=auto_wire,
        emit_barrel=emit_barrel,
    )

    if payload.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{payload.get('error')}[/bold red]")
        if payload.get("hint"):
            console.print(f"[dim]{payload['hint']}[/dim]")
        sys.exit(1)

    if not payload["ok"]:
        if not as_json:
            for err in payload["errors"]:
                if "Static Decoupling Blockers" in err:
                    console.print(f"\n[bold red]❌ {err}[/bold red]")
                else:
                    console.print(f"[bold red]{err}[/bold red]")
        if as_json:
            click.echo(json.dumps({"ok": False, "errors": payload["errors"]}, indent=2))
        sys.exit(payload.get("exit_code") or 1)

    tc = payload.get("trustchain") or {}
    if not dry_run and tc.get("active"):
        console.print("[bold green]🛡️ TrustChain[/bold green] active for strip session")
    if tc.get("checkpoint"):
        console.print(
            f"[bold green]🛡️ TrustChain checkpoint[/bold green] {tc['checkpoint']} "
            f"(before strip; source backed up under .apatch/backups/)"
        )

    if not as_json:
        for r in payload.get("strip_results", []):
            mode = "dry-run" if dry_run else "applied"
            console.print(
                f"[green]✓[/green] [{mode}] {r['label']}: lines {r['start_line']}-{r['end_line']} "
                f"({r['removed_lines']} removed)"
            )

        for meta in payload.get("exported_meta", []):
            console.print(
                f"  [blue]→[/blue] exported {meta.get('filename')} -> {meta.get('export_path')}"
            )
            dangling = meta.get("dangling_references") or []
            if dangling:
                names = ", ".join(d["name"] for d in dangling)
                console.print(f"  [yellow]⚠ dangling references:[/yellow] {names}")

        if payload.get("report_path"):
            console.print(
                f"[bold green]✓[/bold green] extraction report: {payload['report_path']}"
            )

        dups = payload.get("native_duplicates") or {}
        if dups:
            console.print("[bold yellow]⚠ duplicate native registrations detected[/bold yellow]")
            for name, files in dups.items():
                console.print(f"  - {name}: {', '.join(files)}")

        if tc.get("active") and payload.get("exported_meta") and not dry_run:
            console.print("[bold green]🛡️ TrustChain[/bold green] strip audit committed")

        if verify_cmd and payload["ok"] and not dry_run:
            console.print("[bold green]✓ Build Verification Passed Successfully![/bold green]")

    if as_json:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))

@cli.command("compile")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--out-dir", type=click.Path(file_okay=False), help="Directory to save knowledge_map.json.")
@click.option("-n", "--dry-run", is_flag=True, help="Preview SID injection without modifying the file.")
@click.option("--no-trustchain", is_flag=True, help="Do not auto-init or write checkpoint to .trustchain/.")
def compile_cmd(file_path, out_dir, dry_run, no_trustchain):
    """Compile a Markdown document: inject unique stable Semantic IDs (sid)
    and export a knowledge map of the document's structure.
    """
    try:
        result = compile_markdown_file(
            file_path,
            out_dir=out_dir,
            dry_run=dry_run,
            no_trustchain=no_trustchain,
        )
    except Exception as e:
        console.print(f"[bold red]Compiler failed: {e}[/bold red]")
        sys.exit(1)

    if dry_run:
        console.print("[bold yellow]Dry-run: Previewing compiled document structure[/bold yellow]")
        for sid, meta in result.get("knowledge_map", {}).items():
            console.print(f"  [green]{sid}[/green] ({meta['type']}): {meta['line_count']} lines")
        return

    console.print(
        f"[bold green]✓[/bold green] Compiled [blue]{os.path.basename(file_path)}[/blue] and injected stable IDs."
    )
    console.print(
        f"[bold green]✓[/bold green] Exported knowledge map to [blue]{result['knowledge_map_path']}[/blue]."
    )
    if result.get("trustchain_committed"):
        console.print("[bold green]🛡️ TrustChain[/bold green] compile session committed successfully")

@cli.command("commit-attested")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--session", "session_ids", multiple=True, required=True, help="Exact attested governed session id; repeat for multiple sessions.")
@click.option("-m", "--message", required=True, help="Git commit message.")
@click.option("--push", is_flag=True, help="Push after the exact commit succeeds.")
@click.option("--remote", default="origin", show_default=True, help="Remote for the first push when no upstream exists.")
@click.option("--dry-run", is_flag=True, help="Validate exact scope and proof without staging or committing.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def commit_attested_cmd(target_dir, session_ids, message, push, remote, dry_run, as_json):
    """Commit and optionally push only exact files from attested sessions."""
    from apatch.workflows import commit_attested_workspace

    result = commit_attested_workspace(
        target_dir,
        governed_session_id=None,
        session_ids=list(session_ids),
        message=message,
        push=push,
        remote=remote,
        dry_run=dry_run,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            raise click.exceptions.Exit(1)
        return
    if not result.get("ok"):
        raise click.ClickException(
            f"{result.get('error_type', 'COMMIT_ATTESTED_FAILED')}: "
            f"{result.get('message', 'commit-attested failed')}"
        )
    if result.get("status") == "nothing_to_commit":
        console.print("[bold yellow]No attested changed files to commit.[/bold yellow]")
        return
    if dry_run:
        console.print(
            f"[bold green]Validated[/bold green] {len(result.get('files') or [])} exact attested files."
        )
        return
    console.print(
        f"[bold green]Committed[/bold green] {len(result.get('files') or [])} files "
        f"as {result.get('commit', '')[:12]}."
    )
    if result.get("pushed"):
        console.print(f"[bold green]Pushed[/bold green] branch {result.get('branch')}.")


@cli.command("rollback")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False), help="Path to the target codebase root.")
@click.option("--session", "session_id", help="Specific session ID to rollback (defaults to the last session).")
@click.option("--preview", is_flag=True, help="List files the checkpoint would restore without writing to disk.")
def rollback_cmd(target_dir, session_id, preview):
    """Roll back applied patches for a given session."""
    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).rollback(session_id, preview=preview)
    if not result.get("ok") and result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    if preview:
        if result.get("error"):
            console.print(f"[bold red]Preview failed: {result['error']}[/bold red]")
            sys.exit(1)
        console.print(
            f"[bold cyan]Rollback preview[/bold cyan] session={result.get('session_id')} "
            f"files={result.get('count', 0)}"
        )
        tc = result.get("trustchain") or {}
        if tc.get("checkpoint"):
            console.print(f"  TrustChain checkpoint: {tc['checkpoint']}")
        for entry in result.get("files") or []:
            action = entry.get("action", "modified")
            console.print(f"  • [{action}] {entry.get('path')}")
        return
    tc = result.get("trustchain") or {}
    if tc.get("checkpoint"):
        if tc.get("reset"):
            console.print(
                f"[bold green]🛡️ TrustChain HEAD reset to checkpoint: {tc['checkpoint']}[/bold green]"
            )
        elif tc.get("warning"):
            console.print(f"[bold yellow]TrustChain rollback warning: {tc['warning']}[/bold yellow]")
    if result.get("error"):
        console.print(f"[bold red]Rollback failed: {result['error']}[/bold red]")
        sys.exit(1)
    restored = result.get("restored") or []
    if restored:
        console.print(f"[bold green]Successfully rolled back {len(restored)} files:[/bold green]")
        for f in restored:
            console.print(f"  ✓ {os.path.basename(f)}")
    else:
        console.print("[bold yellow]No files were restored.[/bold yellow]")


@cli.command("status")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def status_cmd(target_dir, as_json):
    """Unified project dashboard: specs, hygiene, conflicts, next action."""
    from apatch.cli_status import build_status_view, render_status_human

    dto = build_status_view(target_dir)
    if as_json:
        click.echo(json.dumps(dto, indent=2, ensure_ascii=False))
        return
    render_status_human(console, dto)




@cli.command("report")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--html", is_flag=True, help="Write self-contained HTML report.")
@click.option("--format", "fmt", type=click.Choice(["md"]), default=None, help="Output format.")
@click.option("--locale", default="en", type=click.Choice(["en", "ru"]))
@click.option("--out", "out_path", default=None, help="Output path (default .apatch/report.html or stdout for md -).")
def report_cmd(target_dir, html, fmt, locale, out_path):
    """Architect HTML or manager markdown report from project status DTO."""
    from pathlib import Path

    from apatch.report_render import build_report_bundle, render_html, render_md

    bundle = build_report_bundle(target_dir)
    if not bundle.get("ok"):
        console.print(f"[bold red]{bundle.get('error', 'report failed')}[/bold red]")
        sys.exit(1)
    if html:
        content = render_html(bundle)
        dest = out_path or ".apatch/report.html"
    elif fmt == "md":
        content = render_md(bundle, locale=locale)
        dest = out_path or "-"
    else:
        console.print("[yellow]Specify --html or --format md[/yellow]")
        sys.exit(1)
    if dest == "-":
        click.echo(content, nl=False if content.endswith("\n") else True)
        return
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    console.print(f"[bold green]Wrote[/bold green] {path}")


@cli.command("doctor")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def doctor_cmd(target_dir, as_json):
    """Environment diagnostics: TrustChain mode (audit_pending/audit/enforce), toolchain, sandbox."""
    info = run_doctor(target_dir)
    if as_json:
        click.echo(json.dumps(info, indent=2))
        return
    console.print(f"[bold]apatch[/bold] {info['version']} (Python {info['python']})")
    console.print(f"Python: {info.get('python_executable') or info['python']}")
    console.print(f"Executable: {info.get('apatch_executable') or 'not on PATH'}")
    console.print(f"Workspace: {info['workspace']}")
    mcp = info.get("mcp_health") or {}
    mcp_mark = "[green]✓[/green]" if mcp.get("ok") else "[yellow]![/yellow]"
    console.print(f"\n[bold]MCP health:[/bold] {mcp_mark} ok={mcp.get('ok')}")
    if mcp.get("recommended_install"):
        console.print(f"  install: {mcp['recommended_install']}")
    if mcp.get("sync_command"):
        console.print(f"  fix config: {mcp['sync_command']}")
    for w in mcp.get("warnings") or []:
        console.print(f"  [yellow]• {w}[/yellow]")
    tc = info["trustchain"]
    mode = tc.get("mode", "audit_pending")
    mode_colors = {
        "enforce": "bold red",
        "audit": "bold yellow",
        "audit_pending": "dim",
    }
    console.print(
        f"[{mode_colors.get(mode, 'white')}]TrustChain mode: {mode}[/]"
    )
    if tc.get("summary"):
        console.print(f"  {tc['summary']}")
    if tc.get("active"):
        console.print(f"  [green]ledger[/green] {tc['path']}")
    elif tc.get("behaviors", {}).get("auto_init_on_first_mutate"):
        console.print("  [dim]ledger will auto-init on first apply/strip[/dim]")
    if tc.get("upgrade_to_enforce"):
        console.print(f"  [cyan]upgrade:[/cyan] {tc['upgrade_to_enforce']}")
    console.print("\n[bold]tree-sitter grammars:[/bold]")
    for g in info["tree_sitter_grammars"]:
        mark = "[green]✓[/green]" if g["available"] else "[red]✗[/red]"
        console.print(f"  {mark} {g['name']}")
    if info.get("recommended_verify"):
        console.print(f"\n[bold]Recommended verify:[/bold] {info['recommended_verify']}")
    toolchain = info.get("toolchain", {}).get("tools", {})
    if toolchain:
        console.print("\n[bold]Toolchain:[/bold]")
        for name, meta in toolchain.items():
            mark = "[green]✓[/green]" if meta.get("found") else "[dim]–[/dim]"
            ver = f" ({meta['version']})" if meta.get("version") else ""
            console.print(f"  {mark} {name}{ver}")
    if info.get("detected_profiles"):
        console.print(f"\n[bold]Detected profiles:[/bold] {', '.join(info['detected_profiles'])}")
    hygiene = info.get("hygiene") or {}
    hstat = hygiene.get("status") or "unknown"
    hcolor = {"ok": "green", "clean": "green", "warn": "yellow", "degraded": "yellow", "critical": "red"}.get(
        hstat, "white"
    )
    console.print(f"\n[bold]Hygiene[/bold]: [{hcolor}]{hstat}[/{hcolor}]")
    for issue in (hygiene.get("issues") or [])[:3]:
        itype = issue.get("type") or "issue"
        sev = issue.get("severity") or "?"
        console.print(f"  [yellow]• {itype}[/yellow] (severity={sev})")
    if hygiene.get("gc_recommendation"):
        console.print(f"  {hygiene['gc_recommendation']}")
    sandbox = info.get("sandbox") or {}
    console.print(f"\n[bold]Sandbox[/bold]: mode={sandbox.get('mode')} enabled={sandbox.get('enabled')}")
    lane = info.get("lane") or {}
    console.print(f"[bold]Lane[/bold]: {lane.get('lane_id')} ({lane.get('source')})")
    for w in info.get("warnings") or []:
        console.print(f"  [yellow]• {w}[/yellow]")

    console.print("\n[bold]Example commands:[/bold]")
    for cmd in info["example_commands"]:
        console.print(f"  {cmd}")


@cli.group("mcp")
def mcp_group():
    """MCP install diagnostics and IDE config sync (Cursor, Antigravity, …)."""


@mcp_group.command("hygiene")
@click.option("--target-dir", default=".", type=click.Path(file_okay=True))
@click.option("--sweep-leases", is_flag=True, help="Remove stale write_lease.json in workspace.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON report.")
def mcp_hygiene_cmd(target_dir, sweep_leases, as_json):
    """Ghost MCP processes and lease health (RFP-019 L1-8)."""
    from apatch.mcp.hygiene import run_mcp_hygiene

    out = run_mcp_hygiene(target_dir, sweep_leases=sweep_leases)
    if as_json:
        console.print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    ghosts = out.get("ghost_count", 0)
    procs = out.get("process_count", 0)
    if out.get("ok"):
        console.print(f"[green]OK[/green] apatch MCP processes: {procs} (ghosts: {ghosts})")
    else:
        console.print(f"[yellow]WARN[/yellow] ghost MCP processes: {ghosts} of {procs}")
    console.print(out.get("hint", ""))
    lease = out.get("lease")
    if lease:
        console.print(f"Lease: {lease}")


@mcp_group.command("sync")
@click.option("--target-dir", default=".", type=click.Path(file_okay=True))
@click.option("--force", is_flag=True, help="Overwrite existing apatch MCP blocks.")
@click.option(
    "--ide-path",
    "ide_paths",
    multiple=True,
    type=click.Path(),
    help="Extra IDE config path for workspace_launcher stub (repeatable).",
)
@click.option(
    "--no-auto-ide",
    "no_auto_ide",
    is_flag=True,
    help="Skip auto-detect of existing IDE MCP config files.",
)
@click.option("--profile", type=click.Choice(["compact", "core", "spec", "full"]), default=None,
              help="Explicit profile selection; omitted preserves the existing profile.")
def mcp_sync_cmd(target_dir, force, ide_paths, no_auto_ide, profile):
    """Write ``.apatch/mcp.json`` + IDE stubs (auto-detect existing configs by default)."""
    from apatch.mcp_health import build_mcp_health, sync_mcp_configs

    try:
        paths = sync_mcp_configs(
            target_dir,
            overwrite=force,
            ide_paths=ide_paths if ide_paths else None,
            auto_ide=not no_auto_ide,
            profile=profile,
        )
    except (FileExistsError, ValueError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        console.print("Use --force to replace the apatch MCP server block.")
        sys.exit(1)
    health = build_mcp_health(target_dir)
    for path in paths:
        console.print(f"[green]✓[/green] {path}")
    if health.get("ok"):
        console.print("[green]MCP health OK[/green]")
    else:
        for w in health.get("warnings") or []:
            console.print(f"[yellow]• {w}[/yellow]")
        raise click.exceptions.Exit(1)


@mcp_group.command("check")
@click.option("--target-dir", default=".", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
def mcp_check_cmd(target_dir, as_json):
    """Check configured bootstrap/stdio without repairing user configuration."""
    from apatch.mcp_health import build_mcp_health

    health = build_mcp_health(target_dir)
    if as_json:
        click.echo(json.dumps(health, ensure_ascii=False))
    else:
        console.print("MCP health OK" if health.get("ok") else "MCP health FAILED")
        for warning in health.get("warnings") or []:
            console.print(warning)
    if not health.get("ok"):
        raise click.exceptions.Exit(1)


@mcp_group.command("codex-approve")
@click.option("--target-dir", default=".", type=click.Path(file_okay=False), help="Workspace path for APATCH_WORKSPACE when server block is missing.")
@click.option("--config", "config_path", default=None, type=click.Path(dir_okay=False), help="Codex config path; default ~/.codex/config.toml.")
@click.option("--server", default="apatch", show_default=True, help="Codex MCP server name.")
@click.option("--python", "python_path", default=None, help="Python executable for a missing server block.")
@click.option("--preset", default="autopilot", show_default=True, type=click.Choice(["remote", "autopilot", "all"]), help="Approval preset to write.")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing config.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON result.")
def mcp_codex_approve_cmd(target_dir, config_path, server, python_path, preset, dry_run, as_json):
    """Make Codex stop prompting for approved apatch MCP autopilot tools."""
    from apatch.codex_approval import write_codex_approvals

    result = write_codex_approvals(
        config_path=config_path,
        server=server,
        target_dir=target_dir,
        python=python_path,
        preset=preset,
        dry_run=dry_run,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    status = "would update" if dry_run and result.get("changed") else ("updated" if result.get("changed") else "already ok")
    console.print("[bold green]Codex approvals[/bold green]: {} {}".format(status, result["path"]))
    console.print("[dim]server={} preset={} tools={}[/dim]".format(server, preset, result["tool_count"]))
    if result.get("changed_tools"):
        console.print("[dim]changed: {}[/dim]".format(", ".join(result["changed_tools"])))
    if result.get("restart_required"):
        console.print("[yellow]Restart/reload the Codex MCP server for changes to take effect.[/yellow]")


@mcp_group.command("codex-doctor")
@click.option("--target-dir", default=".", type=click.Path(file_okay=False), help="Workspace path you want the agent to work on.")
@click.option("--config", "config_path", default=None, type=click.Path(dir_okay=False), help="Codex config path; default ~/.codex/config.toml.")
@click.option("--server", default="apatch", show_default=True, help="Codex MCP server name.")
@click.option("--preset", default="autopilot", show_default=True, type=click.Choice(["remote", "autopilot", "all"]), help="Approval preset to inspect.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON result.")
def mcp_codex_doctor_cmd(target_dir, config_path, server, preset, as_json):
    """Diagnose why Codex keeps asking for apatch-related approvals."""
    from apatch.codex_approval import diagnose_codex_prompt_friction

    result = diagnose_codex_prompt_friction(
        config_path=config_path,
        server=server,
        target_dir=target_dir,
        preset=preset,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    console.print("[bold]Codex prompt diagnosis[/bold]")
    for finding in result.get("findings") or []:
        console.print("[yellow]{}[/yellow]: {}".format(finding["kind"], finding["message"]))
        console.print("[dim]{}[/dim]".format(finding["recommended_action"]))
    autopilot = result.get("autopilot") or {}
    if autopilot:
        console.print("[bold]autopilot[/bold]: {}".format(autopilot.get("mode")))
        console.print("[dim]{}[/dim]".format(autopilot.get("agent_next")))
    if not result.get("findings"):
        console.print("[green]No apatch MCP approval or workspace-scope issue detected.[/green]")


@cli.command("init-consumer")
@click.option("--target-dir", default=".", type=click.Path(file_okay=True))
@click.option(
    "--profile",
    type=click.Choice(list_profiles()),
    default="default",
    show_default=True,
)
@click.option("--with-ci", is_flag=True, help="Copy GitHub Actions apatch workflow template.")
@click.option(
    "--with-arch-rules",
    is_flag=True,
    help="Copy arch-rules, semantic-verify, and engineering-pipeline templates.",
)
@click.option(
    "--with-enforcement",
    is_flag=True,
    help="Enable TrustChain notarization enforcement + pre-commit hook template.",
)
@click.option(
    "--governed-mode",
    type=click.Choice(["off", "auto_session", "strict"]),
    default="auto_session",
    show_default=True,
    help="Session gate for mutations when --with-enforcement (written to enforcement.json).",
)
@click.option(
    "--with-sandbox",
    is_flag=True,
    help="Write sandbox config + Cursor hooks (protected: src/app/services/packages).",
)
@click.option(
    "--with-devcontainer",
    is_flag=True,
    help="Copy .devcontainer/devcontainer.json for isolated agent environments.",
)
@click.option(
    "--with-mcp/--no-with-mcp",
    default=True,
    help="Write .apatch/mcp.json with canonical python -m apatch.mcp.launcher config.",
)
@click.option(
    "--refresh-agents",
    is_flag=True,
    help="Rewrite AGENTS.md from apatch template; preserve <!-- apatch:project:* --> block.",
)
def init_consumer_cmd(
    target_dir,
    profile,
    with_ci,
    with_arch_rules,
    with_enforcement,
    governed_mode,
    with_sandbox,
    with_devcontainer,
    with_mcp,
    refresh_agents,
):
    """Scaffold manifests/README and .gitignore entry for a consumer project."""
    try:
        created = init_consumer(
            target_dir,
            profile=profile,
            with_ci=with_ci,
            with_arch_rules=with_arch_rules,
            with_enforcement=with_enforcement,
            governed_mode=governed_mode if with_enforcement else "off",
            with_sandbox=with_sandbox,
            with_devcontainer=with_devcontainer,
            with_mcp=with_mcp,
            refresh_agents=refresh_agents,
        )
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    if created:
        for p in created:
            console.print(f"[green]✓[/green] {p}")
    else:
        console.print("[dim]Templates already exist; nothing written.[/dim]")


@cli.command("simulate")
@click.option("--logs", "logs_path", type=click.Path(exists=True), help="Patches JSONL to simulate.")
@click.option("--manifest", "manifest_path", type=click.Path(exists=True), help="Pipeline manifest with patches_jsonl.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--chunk-files", default=5, show_default=True, type=int)
@click.option("-a", "--all", "replace_all", is_flag=True)
@click.option("--only-drifted", is_flag=True)
@click.option("--min-confidence", type=float, default=None)
@click.option("--budget", type=click.Choice(["small", "medium", "large"]), default=None)
@click.option("--json", "as_json", is_flag=True)
def simulate_cmd(
    logs_path,
    manifest_path,
    target_dir,
    chunk_files,
    replace_all,
    only_drifted,
    min_confidence,
    budget,
    as_json,
):
    """Preflight risk map (R58): no writes, estimates rollback probability."""
    from apatch.workflows import WorkflowError, simulate_workspace

    try:
        result = simulate_workspace(
            target_dir,
            logs_path=logs_path,
            manifest_path=manifest_path,
            chunk_max_files=chunk_files,
            replace_all=replace_all,
            only_drifted=only_drifted,
            min_confidence=min_confidence,
            budget=budget,
        )
    except WorkflowError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        rm = result.get("risk_map") or {}
        console.print(f"[bold]risk_level[/bold]: {result.get('risk_level')}  "
                      f"[bold]rollback_probability[/bold]: {result.get('rollback_probability')}")
        console.print(f"[bold]files_touched[/bold]: {rm.get('files_touched')}  "
                      f"[bold]chunks[/bold]: {rm.get('chunks_required')}")
        console.print(f"[bold]recommended[/bold]: {result.get('recommended_path')}")
        console.print(f"[dim]{result.get('next_action')}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@cli.command("generate")
@click.option("--find", "find_text", required=True, help="Substring to search for in each file.")
@click.option("--replace", "replace_text", required=True, help="Replacement text.")
@click.option("--glob", "glob_pattern", default="**/*", show_default=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write JSONL to file (default: stdout).")
@click.option("--all", "replace_all", is_flag=True, default=True, help="Set AllowMultiple on each patch.")
@click.option(
    "--match",
    "match_mode",
    type=click.Choice(["literal", "whitespace", "regex", "json", "yaml"]),
    default="literal",
    show_default=True,
    help="How to find matches in each file.",
)
@click.option("--find-pattern", help="Regex pattern when --match regex (defaults to --find).")
@click.option("--append", is_flag=True, help="Append steps to existing JSONL (continue step_index).")
def generate_cmd(find_text, replace_text, glob_pattern, target_dir, out_path, replace_all, match_mode, find_pattern, append):
    """Generate patch JSONL from find/replace patterns (feed to plan/apply)."""
    if out_path:
        result = generate_patch_jsonl(
            find_text=find_text,
            replace_text=replace_text,
            target_dir=target_dir,
            glob_pattern=glob_pattern,
            out_path=out_path,
            replace_all=replace_all,
            match_mode=match_mode,
            find_pattern=find_pattern,
            append=append,
        )
        console.print(
            f"[green]✓[/green] Wrote {result['count']} patches to {out_path}"
            + (f" (total {result.get('total', result['count'])})" if append else "")
        )
    else:
        import io

        patches = generate_patches(
            find=find_text,
            replace=replace_text,
            target_dir=target_dir,
            glob_pattern=glob_pattern,
            replace_all=replace_all,
            match_mode=match_mode,
            find_pattern=find_pattern,
        )
        buf = io.StringIO()
        count = write_jsonl(patches, buf)
        click.echo(buf.getvalue(), nl=False)
        console.print(f"[dim](# {count} patches)[/dim]", stderr=True)


@cli.command("generate-batch")
@click.option(
    "--needles",
    "needles_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON file: [{action, …}, …] — replace|create|delete|rename|chmod|shift_outline|insert_before|insert_section",
)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--out", "out_path", default="patches.jsonl", show_default=True)
@click.option("--append", is_flag=True, help="Append to existing JSONL.")
@click.option("--glob", "glob_pattern", default="**/*", show_default=True, help="Default glob per needle.")
@click.option(
    "--match",
    "match_mode",
    type=click.Choice(["literal", "whitespace", "regex", "json", "yaml"]),
    default="literal",
    show_default=True,
)
@click.option("--all", "replace_all", is_flag=True, default=False, help="AllowMultiple per needle.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of text.")
def generate_batch_cmd(needles_path, target_dir, out_path, append, glob_pattern, match_mode, replace_all, as_json):
    """Generate patch JSONL from mutation needles (replace/create/delete/rename/chmod/outline)."""
    with open(needles_path, encoding="utf-8") as handle:
        needles = json.load(handle)
    if not isinstance(needles, list):
        raise click.ClickException("--needles file must be a JSON array")
    result = generate_patch_jsonl_batch(
        needles=needles,
        target_dir=target_dir,
        out_path=out_path,
        append=append,
        default_glob_pattern=glob_pattern,
        default_match_mode=match_mode,
        default_replace_all=replace_all,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        raise click.ClickException(result.get("error") or "generate-batch failed")
    console.print(
        f"[green]✓[/green] Wrote {result['count']} patches ({result.get('needles')} needles) "
        f"to {out_path} — total {result.get('total')}"
    )


@cli.command("natives-check")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
def natives_check_cmd(directory):
    """Scan all C++ source files in a directory to identify duplicate native registrations."""
    console.print(f"[bold blue]🔍 Scanning directory for duplicate register_native() calls:[/bold blue] {directory}")
    result = natives_check(directory)
    dups = result.get("duplicates") or {}
    if dups:
        console.print("\n[bold red]❌ Duplicate Native Registrations Detected:[/bold red]")
        for name, files in dups.items():
            console.print(f"  - Native [bold]'{name}'[/bold] in: {', '.join(files)}")
        sys.exit(1)
    else:
        console.print("[bold green]✓ No duplicate native registrations found.[/bold green]")

@click.group("arch")
def arch_group():
    """Architecture invariant checks."""
    pass


@arch_group.command("check")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--rules", "rules_path", type=click.Path(dir_okay=False), help="arch-rules.yaml path")
@click.option("--since", default=None, help="Only report violations touching git diff since ref")
@click.option("--json", "as_json", is_flag=True)
def arch_check_cmd(target_dir, rules_path, since, as_json):
    """Check forbidden imports, layer rules, and service invariants."""
    try:
        result = arch_check_workspace(target_dir, rules_path=rules_path, since=since)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Architecture check passed[/bold green]")
    else:
        for v in result.get("violations") or []:
            console.print(f"[bold red]{v.get('type')}[/bold red] {v.get('file') or v.get('from')}: {v.get('message') or v.get('rule')}")
    if not result.get("ok"):
        sys.exit(1)


@click.command("impact")
@click.argument("target")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--kind", type=click.Choice(["file", "symbol"]), default=None)
@click.option("--depth", default=1, show_default=True)
@click.option("--no-tests", is_flag=True, help="Skip test file classification")
@click.option("--json", "as_json", is_flag=True)
def impact_cmd(target, target_dir, kind, depth, no_tests, as_json):
    """Show files/tests affected by a file or symbol change."""
    result = impact_analysis(
        target,
        target_dir,
        kind=kind,
        depth=depth,
        include_tests=not no_tests,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]Target:[/bold] {result['target']} ({result['kind']})")
    if result.get("defined_in"):
        console.print(f"[dim]Defined in:[/dim] {result['defined_in']}")
    for key in ("affected_files", "affected_tests", "affected_services"):
        items = result.get(key) or []
        if items:
            console.print(f"\n[bold]{key}[/bold]")
            for item in items:
                console.print(f"  {item}")


@click.group("db")
def db_group():
    """Database refactor helpers (models vs migrations)."""
    pass


@db_group.command("check")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--profile", required=True, type=click.Choice(["sqlalchemy", "django", "prisma"]))
@click.option("--since", default="HEAD", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def db_check_cmd(target_dir, profile, since, as_json):
    """Detect model changes without a matching migration file in git diff."""
    try:
        result = db_check_workspace(target_dir, profile=profile, since=since)
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ DB check passed[/bold green]")
    else:
        console.print(f"[bold red]{result.get('reason')}[/bold red]: {result.get('hint')}")
    if not result.get("ok"):
        sys.exit(1)


@db_group.command("revision")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--profile", required=True, type=click.Choice(["sqlalchemy", "django", "prisma"]))
@click.option("--message", "-m", default="apatch revision", show_default=True)
@click.option("--dry-run", is_flag=True, help="Print stack command without running")
@click.option("--json", "as_json", is_flag=True)
def db_revision_cmd(target_dir, profile, message, dry_run, as_json):
    """Run stack CLI to draft a migration after model changes."""
    result = db_revision_workspace(
        target_dir, profile=profile, message=message, dry_run=dry_run
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(f"[green]✓[/green] {result.get('command_run')}")
        for p in result.get("created_files") or []:
            console.print(f"  created: {p}")
    else:
        console.print(f"[bold red]revision failed[/bold red]\n{result.get('stderr')}")
        sys.exit(1)


@db_group.command("safety")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--profile", required=True, type=click.Choice(["sqlalchemy", "django", "prisma"]))
@click.option("--since", default="HEAD", show_default=True, help="Scan only migrations changed since ref")
@click.option("--all", "scan_all", is_flag=True, help="Scan all migration files, ignore --since")
@click.option("--json", "as_json", is_flag=True)
def db_safety_cmd(target_dir, profile, since, scan_all, as_json):
    """Flag risky migration operations (DROP, TRUNCATE, ALTER TYPE, ...)."""
    result = db_safety_workspace(
        target_dir,
        profile=profile,
        since=None if scan_all else since,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("safe"):
        console.print("[bold green]✓ No risky migration patterns detected[/bold green]")
    else:
        for f in result.get("findings") or []:
            console.print(f"[bold red]{f['risk']}[/bold red] {f['file']}:{f['line']} — {f['reason']}")
    if not result.get("safe"):
        sys.exit(1)


@db_group.command("run")
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, help="Preview phases without writes or shell commands")
@click.option("--json", "as_json", is_flag=True)
def db_run_cmd(manifest, target_dir, dry_run, as_json):
    """Run db-refactor manifest: apply → db check → revision → verify."""
    try:
        result = db_run_manifest(manifest, target_dir, dry_run=dry_run)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ DB refactor manifest completed[/bold green]")
        for ph in result.get("phases") or []:
            console.print(f"  [green]✓[/green] {ph.get('name')} ({ph.get('action')})")
    else:
        console.print(f"[bold red]Failed at phase:[/bold red] {result.get('failed_phase')}")
        console.print(f"  {result.get('reason')}")
        sys.exit(1)
    if not result.get("ok"):
        sys.exit(1)


@click.group("index")
def index_group():
    """Project memory index (symbols, imports, routes)."""
    pass


@index_group.command("build")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def index_build_cmd(target_dir, as_json):
    """Build .apatch/project_index.json."""
    result = index_build_workspace(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        stats = result.get("stats") or {}
        console.print(f"[green]✓[/green] index built: {stats}")


@index_group.command("query")
@click.argument("query")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def index_query_cmd(query, target_dir, as_json):
    """Query project index for symbols, routes, usages."""
    try:
        result = index_query_workspace(target_dir, query)
    except FileNotFoundError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for sym in result.get("symbols") or []:
            console.print(f"symbol: {sym['name']} @ {sym['file']}")
        for u in result.get("usages") or []:
            console.print(f"  usage: {u}")
        for m in result.get("migration_files") or []:
            console.print(f"migration: {m}")
        for d in result.get("adr_docs") or []:
            label = d.get("adr_id") or d.get("title") or d.get("file")
            console.print(f"adr: {label} ({d.get('file')})")


@click.group("trustchain")
def trustchain_group():
    """TrustChain ledger utilities."""
    pass


@trustchain_group.command("history")
@click.option("--query", "-q", default=None, help="Filter by intent, adr, or manifest substring.")
@click.option(
    "--artifact",
    "-a",
    default=None,
    help="Filter by artifact kind:id (e.g. spec:SPEC-42, adr:ADR-001).",
)
@click.option("--limit", default=50, show_default=True, type=int)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def trustchain_history_cmd(query, artifact, limit, target_dir, as_json):
    """List engineering intent/artifact checkpoints from TrustChain history."""
    result = trustchain_intent_history_workspace(
        target_dir, query=query, artifact=artifact, limit=limit
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error', 'trustchain error')}[/bold red]")
        sys.exit(1)
    entries = result.get("entries") or []
    if not entries:
        console.print("[dim]No intent/adr entries in TrustChain ledger.[/dim]")
        return
    for e in entries:
        parts = [p for p in (e.get("adr"), e.get("intent")) if p]
        for art in e.get("artifacts") or []:
            if isinstance(art, dict) and art.get("kind") and art.get("id"):
                parts.append(f"{art['kind']}:{art['id']}")
        console.print(f"• {' — '.join(parts) or e.get('action')}")
        if e.get("manifest"):
            console.print(f"  manifest: {e['manifest']}")
        if e.get("signature"):
            console.print(f"  sig: {e['signature'][:16]}…")


@trustchain_group.command("coverage")
@click.option(
    "--artifact",
    "-a",
    default=None,
    help="Artifact kind:id to report (e.g. spec:SPEC-42). Omit for all artifacts.",
)
@click.option("--op-id", default=None, help="Reverse lookup: map op_id to linked artifact(s).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def trustchain_coverage_cmd(artifact, op_id, target_dir, as_json):
    """Artifact traceability: mutations, attestations, op_id reverse mapping."""
    result = trustchain_coverage_workspace(target_dir, artifact=artifact, op_id=op_id)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error', 'trustchain error')}[/bold red]")
        sys.exit(1)
    if op_id:
        arts = result.get("artifacts") or []
        labels = [f"{a.get('kind')}:{a.get('id')}" for a in arts if isinstance(a, dict)]
        console.print(f"[bold]op_id[/bold] {result.get('op_id')} → {', '.join(labels) or '—'}")
        console.print(f"[dim]role: {result.get('role')} · action: {result.get('action')}[/dim]")
        return
    if artifact:
        cov = result.get("coverage") or {}
        status = "complete" if cov.get("complete") else "partial"
        console.print(f"[bold]{artifact}[/bold] — coverage: {status}")
        console.print(
            f"  intents: {len(result.get('intents') or [])} · "
            f"mutations: {cov.get('mutation_count', 0)} · "
            f"attestations: {cov.get('attestation_count', 0)}"
        )
        for m in result.get("mutations") or []:
            console.print(f"  mutation {str(m.get('op_id', ''))[:16]}… ({m.get('action')})")
        for a in result.get("attestations") or []:
            console.print(f"  attestation {str(a.get('op_id', ''))[:16]}…")
        return
    summary = result.get("coverage_summary") or {}
    console.print(
        f"[bold]artifacts[/bold]: {summary.get('total_artifacts', 0)} · "
        f"complete: {summary.get('complete', 0)}"
    )
    for row in result.get("artifacts") or []:
        art = row.get("artifact") or {}
        key = row.get("artifact_key") or f"{art.get('kind')}:{art.get('id')}"
        cov = row.get("coverage") or {}
        tag = "complete" if cov.get("complete") else "partial"
        console.print(
            f"• {key} — {tag} "
            f"(mut={row.get('mutations', 0)}, attest={row.get('attestations', 0)})"
        )


@trustchain_group.command("verify-anchor")
@click.option("--root-ca", default=None, help="Root CA PEM path or literal (or env APATCH_ROOT_CA).")
@click.option("--intermediate", default=None, help="Intermediate CA PEM (or env APATCH_INTERMEDIATE_CA).")
@click.option("--leaf-cert", default=None, help="Agent leaf cert PEM (or env APATCH_AGENT_CERT).")
@click.option("--signer-key", default=None, help="Enrolled signing key PEM (or env APATCH_AGENT_KEY).")
@click.option("--registry-base", default=None, help="TrustChain pub registry URL (or env APATCH_PLATFORM_URL).")
@click.option("--agent-id", default=None, help="Agent CN for registry fetch (or env APATCH_AGENT_ID).")
@click.option("--crl", default=None, help="CRL PEM path/literal (optional, PEM mode).")
@click.option("--json", "as_json", is_flag=True)
def trustchain_verify_anchor_cmd(
    root_ca, intermediate, leaf_cert, signer_key, registry_base, agent_id, crl, as_json
):
    """Verify apatch's signing identity chains to a pinned TrustChain root (CI gate)."""
    from apatch.trust_identity import verify_anchor

    result = verify_anchor(
        root_ca=root_ca,
        intermediate=intermediate,
        leaf_cert=leaf_cert,
        signer_key=signer_key,
        registry_base=registry_base,
        agent_id=agent_id,
        crl=crl,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ anchor verified[/bold green] — signing key → leaf → root")
        if result.get("agent_id"):
            console.print(f"  agent: {result['agent_id']}")
    else:
        console.print("[bold red]✗ anchor verification failed[/bold red]")
        for err in result.get("errors") or []:
            console.print(f"  • {err}")
    if not result.get("ok"):
        sys.exit(1)


@trustchain_group.command("verify-inclusion")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--platform-url", default=None, help="Platform base URL (default APATCH_PLATFORM_URL).")
@click.option("--json", "as_json", is_flag=True)
def trustchain_verify_inclusion_cmd(target_dir, platform_url, as_json):
    """Prove recorded ledger ops are included in the external append-only log."""
    from apatch.inclusion import verify_inclusion

    result = verify_inclusion(target_dir, base_url=platform_url)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("skipped"):
        console.print(f"[dim]inclusion check skipped: {result['skipped']}[/dim]")
    elif result.get("ok"):
        console.print(
            f"[bold green]✓ inclusion verified[/bold green] "
            f"({result.get('verified')}/{result.get('checked')} in external log)"
        )
    else:
        console.print("[bold red]✗ inclusion verification failed[/bold red]")
        for op in result.get("missing") or []:
            console.print(f"  missing: {op}")
        for op in result.get("inconsistent") or []:
            console.print(f"  inconsistent proof: {op}")
        for e in result.get("errors") or []:
            console.print(f"  error: {e}")
    if not result.get("ok"):
        sys.exit(1)


@trustchain_group.command("enroll")
@click.option("--invitation", "-i", required=True, help="Enrollment invitation token (mint via /api/enroll/invite).")
@click.option("--platform-url", "-p", required=True, help="TrustChain enroll base URL (e.g. https://trust-chain.ai).")
@click.option("--out-dir", "-o", default=".apatch/identity", show_default=True, help="Where to save agent.key/agent.crt/root-ca.pem.")
@click.option("--agent-id", default=None, help="Optional agent CN.")
@click.option("--json", "as_json", is_flag=True)
def trustchain_enroll_cmd(invitation, platform_url, out_dir, agent_id, as_json):
    """Enroll apatch as a TrustChain agent (bridges to `tc cert request`)."""
    from apatch.trust_identity import enroll_agent

    result = enroll_agent(
        invitation=invitation,
        platform_url=platform_url,
        out_dir=out_dir,
        agent_id=agent_id,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(f"[bold green]✓ enrolled[/bold green] → {result.get('out_dir')}")
        for step in result.get("next_steps") or []:
            console.print(f"  {step}")
    else:
        console.print(f"[bold red]✗ enroll failed[/bold red]: {result.get('error')}")
        if result.get("stderr"):
            console.print(f"[dim]{result['stderr']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@click.group("policy")
def policy_group():
    """Signed monitor policy — make the config tamper-evident (RFP-005 §0.3)."""
    pass


@policy_group.command("sign")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def policy_sign_cmd(target_dir, as_json):
    """Sign the monitor config (sandbox/enforcement/hooks) with the enrolled identity."""
    from apatch.policy_lock import sign_policy

    result = sign_policy(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(f"[bold green]✓ policy signed[/bold green] → {result.get('lock_path')}")
        for f in result.get("files") or []:
            console.print(f"  {f}")
        if not result.get("anchor_secure"):
            console.print("[yellow]⚠ signer is not root-anchored (dev identity)[/yellow]")
    else:
        console.print(f"[bold red]✗ policy sign failed[/bold red]: {result.get('error')}")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@policy_group.command("verify")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def policy_verify_cmd(target_dir, as_json):
    """Verify the policy lock: signature valid and no config drift."""
    from apatch.policy_lock import verify_policy

    result = verify_policy(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif not result.get("signed"):
        console.print("[dim]policy not signed (.apatch/policy.lock.json absent)[/dim]")
    elif result.get("ok"):
        secure = "root-anchored" if result.get("secure") else "dev identity"
        console.print(f"[bold green]✓ policy verified[/bold green] ({secure})")
    else:
        console.print(f"[bold red]✗ policy verification failed[/bold red]: {result.get('errors')}")
        drift = result.get("drift") or {}
        for kind in ("changed", "added", "removed"):
            for f in drift.get(kind) or []:
                console.print(f"  {kind}: {f}")
    if result.get("signed") and not result.get("ok"):
        sys.exit(1)


@click.group("graph")
def graph_group():
    """Dependency-driven execution graph (R55)."""
    pass


@graph_group.command("plan")
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--graph", "graph_path", default=None, help="Output path (default .apatch/execution_graph.json).")
@click.option("--json", "as_json", is_flag=True)
def graph_plan_cmd(manifest, target_dir, graph_path, as_json):
    """Build execution graph from engineering-pipeline manifest."""
    from apatch.workflows import WorkflowError, plan_graph_workspace

    try:
        result = plan_graph_workspace(manifest, target_dir, graph_path=graph_path)
    except (FileNotFoundError, ValueError, WorkflowError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold green]✓ Graph[/bold green] {result.get('node_count')} nodes")
        console.print(f"  order: {' → '.join(result.get('order') or [])}")
        console.print(f"  path: {result.get('graph_path')}")


@graph_group.command("execute")
@click.option("--manifest", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option("--graph", "graph_path", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True)
@click.option("--chunk-files", default=5, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True)
def graph_execute_cmd(manifest, graph_path, target_dir, dry_run, chunk_files, as_json):
    """Execute graph nodes in topological order."""
    from apatch.workflows import WorkflowError, execute_graph_workspace

    try:
        result = execute_graph_workspace(
            target_dir,
            manifest_path=manifest,
            graph_path=graph_path,
            dry_run=dry_run,
            chunk_max_files=chunk_files,
        )
    except (FileNotFoundError, ValueError, WorkflowError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Graph execution completed[/bold green]")
    else:
        console.print(f"[bold red]Failed at node:[/bold red] {result.get('failed_node')}")
    if not result.get("ok"):
        sys.exit(1)


@cli.command("orchestrate")
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True)
@click.option("--skip-simulate", is_flag=True)
@click.option("--chunk-files", default=5, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True)
def orchestrate_cmd(manifest, target_dir, dry_run, skip_simulate, chunk_files, as_json):
    """Single entry: simulate → plan graph → execute graph (R54)."""
    from apatch.workflows import WorkflowError, orchestrate_workspace

    try:
        result = orchestrate_workspace(
            manifest,
            target_dir,
            dry_run=dry_run,
            skip_simulate=skip_simulate,
            chunk_max_files=chunk_files,
        )
    except (FileNotFoundError, ValueError, WorkflowError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Orchestration completed[/bold green]")
    elif result.get("aborted"):
        console.print(f"[bold yellow]Aborted:[/bold yellow] {result.get('reason')}")
    else:
        failed = (result.get("execution") or {}).get("failed_node")
        console.print(f"[bold red]Failed[/bold red] at node {failed!r}")
    if not result.get("ok"):
        sys.exit(1)


@cli.command("replay")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--session-id", default=None, help="Checkpoint id from apply_session.")
@click.option("--mode", default="deterministic", type=click.Choice(["deterministic", "summary"]))
@click.option("--json", "as_json", is_flag=True)
def replay_cmd(target_dir, session_id, mode, as_json):
    """Replay apply_session chunk timeline (R57)."""
    from apatch.workflows import replay_session_workspace

    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).replay(session_id=session_id, mode=mode)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    else:
        console.print(f"[bold green]✓ Replay[/bold green] {result.get('chunks_recorded')} chunks")
        console.print(f"  log: {result.get('replay_log_path')}")
    if not result.get("ok"):
        sys.exit(1)


@click.group("pipeline")
def pipeline_group():
    """Unified engineering change pipeline (R52)."""
    pass


@pipeline_group.command("run")
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def pipeline_run_cmd(manifest, target_dir, dry_run, as_json):
    """Run engineering-pipeline manifest (plan → apply → verify → db → arch → …)."""
    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).pipeline_run(manifest, dry_run=dry_run)
    if result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Engineering pipeline completed[/bold green]")
    else:
        err = result.get("error") or result.get("failed_phase")
        console.print(f"[bold red]Failed at:[/bold red] {err}")
    if not result.get("ok"):
        sys.exit(1)


@click.group("refactor")
def refactor_group():
    """Semantic refactor bundles (rename_symbol, …)."""
    pass


@refactor_group.command("run")
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def refactor_run_cmd(manifest, target_dir, dry_run, as_json):
    """Run refactor-bundle manifest: impact → generate → plan → apply → verify."""
    try:
        result = refactor_run_manifest(manifest, target_dir, dry_run=dry_run)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Refactor bundle completed[/bold green]")
        impact = result.get("impact") or {}
        if impact.get("affected_files"):
            console.print(f"  affected: {len(impact['affected_files'])} files")
    else:
        console.print(f"[bold red]Failed at phase:[/bold red] {result.get('failed_phase')}")
        sys.exit(1)
    if not result.get("ok"):
        sys.exit(1)


@click.group("sandbox")
def sandbox_group():
    """Write sandbox — capability lease + path policy (v1)."""
    pass


@sandbox_group.command("status")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def sandbox_status_cmd(target_dir, as_json):
    """Show sandbox mode, lease, and Cursor hook installation."""
    from apatch.sandbox import sandbox_status_workspace

    result = sandbox_status_workspace(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold]mode[/bold]: {result.get('mode')}  [bold]enabled[/bold]: {result.get('enabled')}")
        lease = result.get("lease") or {}
        console.print(f"[bold]lease[/bold]: active={lease.get('active')} paths={lease.get('paths_count')}")
        console.print(f"[bold]cursor_hooks[/bold]: {result.get('cursor_hooks_installed')}")


@sandbox_group.command("check-path")
@click.option("--path", "rel_path", required=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def sandbox_check_path_cmd(rel_path, target_dir, as_json):
    """Evaluate write policy for a single path."""
    from apatch.sandbox import evaluate_write_policy

    result = evaluate_write_policy(target_dir, rel_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        ok = result.get("allowed")
        console.print(f"[bold]{'allow' if ok else 'deny'}[/bold]: {result.get('path')} ({result.get('reason')})")


@sandbox_group.command("hook-pre-tool")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def sandbox_hook_pre_tool_cmd(target_dir):
    """Cursor preToolUse hook (stdin JSON → stdout permission JSON)."""
    from apatch.sandbox import hook_cli_main

    raise SystemExit(hook_cli_main(["hook-pre-tool", "--target-dir", target_dir]))


@sandbox_group.command("hook-shell")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def sandbox_hook_shell_cmd(target_dir):
    """Cursor beforeShellExecution hook."""
    from apatch.sandbox import hook_cli_main

    raise SystemExit(hook_cli_main(["hook-shell", "--target-dir", target_dir]))


@sandbox_group.command("hook-pre-mcp")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def sandbox_hook_pre_mcp_cmd(target_dir):
    """Cursor beforeMCPExecution hook (apatch MCP whitelist)."""
    from apatch.sandbox import hook_cli_main

    raise SystemExit(hook_cli_main(["hook-pre-mcp", "--target-dir", target_dir]))


@sandbox_group.command("audit")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--auto-revert", is_flag=True, help="Restore tracked + remove untracked violations.")
@click.option("--json", "as_json", is_flag=True)
def sandbox_audit_cmd(target_dir, auto_revert, as_json):
    """One-shot audit: log unleased protected mutations (optional revert)."""
    from apatch.sandbox_watch import run_sandbox_watch_once

    result = run_sandbox_watch_once(
        target_dir,
        force=True,
        auto_revert=auto_revert,
        respect_watcher_revert=False,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        n = result.get("scanned_violations", 0)
        console.print(
            f"[bold]violations[/bold]: {n}  [bold]logged[/bold]: {result.get('new_log_entries', 0)}  "
            f"[bold]reverted[/bold]: {result.get('reverted_count', 0)}"
        )
        if n:
            for v in (result.get("violations") or [])[:5]:
                console.print(f"  • {v.get('path')}")
        for p in (result.get("reverted_tracked") or [])[:3]:
            console.print(f"[green]↩[/green] restored {p}")
        for p in (result.get("removed_untracked") or [])[:3]:
            console.print(f"[yellow]✕[/yellow] removed untracked {p}")
    if not result.get("ok") and not result.get("skipped"):
        sys.exit(1)


@sandbox_group.command("ci-gate")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--base",
    default=None,
    help="Merge-base ref (e.g. origin/main) — gate the PR diff vs this ref (Ring-2 authority).",
)
@click.option("--json", "as_json", is_flag=True)
def sandbox_ci_gate_cmd(target_dir, base, as_json):
    """CI gate: audit + notarization when sandbox/enforcement configured."""
    from apatch.sandbox_watch import run_sandbox_ci_gate

    result = run_sandbox_ci_gate(target_dir, base=base)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("skipped"):
        console.print("[dim]ci-gate skipped (no sandbox/enforcement config)[/dim]")
    elif result.get("ok"):
        console.print("[bold green]✓ ci-gate passed[/bold green]")
    else:
        console.print(f"[bold red]ci-gate failed:[/bold red] {result.get('reason')}")
        sys.exit(1)
    if not result.get("ok") and not result.get("skipped"):
        sys.exit(1)


@sandbox_group.command("watch")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--interval", default=2.0, show_default=True, type=float)
@click.option("--once", "run_once", is_flag=True, help="Single pass (same as sandbox audit).")
@click.option(
    "--auto-revert",
    is_flag=True,
    help="Restore tracked + remove untracked violations (also when watcher=revert).",
)
@click.option("--json", "as_json", is_flag=True)
def sandbox_watch_cmd(target_dir, interval, run_once, auto_revert, as_json):
    """Audit loop for unleased writes in protected zones (P1)."""
    from apatch.sandbox_watch import run_sandbox_watch_loop, run_sandbox_watch_once

    if run_once:
        result = run_sandbox_watch_once(target_dir, auto_revert=auto_revert, force=True)
    else:
        result = run_sandbox_watch_loop(
            target_dir,
            interval=interval,
            max_iterations=1 if as_json else None,
            auto_revert=auto_revert,
        )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif run_once:
        console.print(
            f"[bold]audit[/bold]: violations={result.get('scanned_violations', 0)} "
            f"logged={result.get('new_log_entries', 0)}"
        )
    if not result.get("ok") and not result.get("skipped"):
        sys.exit(1)


@click.group("session")
def session_group():
    """Governed session aggregate (Intent → Mutation → Verification)."""
    pass


@session_group.command("start")
@click.option(
    "--lane",
    default=None,
    help="Explicit isolated lane for this session (safe with other active sessions).",
)
@click.option("--intent", default=None, help="Why this session exists (ADR, task, ticket).")
@click.option(
    "--artifact",
    multiple=True,
    help="Typed artifact anchor (repeatable): kind:id or kind:id@content_hash.",
)
@click.option(
    "--requirement",
    default=None,
    help="RFP-007: anchor to a spec requirement, e.g. SPEC-42#R3 (resolves intent+artifact).",
)
@click.option("--spec-path", default=None, help="Spec markdown path (with --requirement).")
@click.option("--request-id", default=None, help="Stable retry id; reuse after a client timeout.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def session_start_cmd(lane, intent, artifact, requirement, spec_path, request_id, target_dir, as_json):
    """Open a session with explicit Intent (Core Invariant)."""
    from apatch.lane_context import bind_lane_from_kwargs
    from apatch.runtime.runtime import MutationRuntime

    bind_lane_from_kwargs({"target_dir": target_dir, "lane": lane or ""})
    artifacts = list(artifact) if artifact else []
    resolved_intent = intent
    if requirement:
        from apatch.spec import resolve_requirement

        res = resolve_requirement(target_dir, requirement, spec_path=spec_path)
        if not res.get("ok"):
            if as_json:
                click.echo(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                console.print(f"[bold red]{res.get('error')}[/bold red]")
            sys.exit(1)
        resolved_intent = intent or res["intent"]
        artifacts.append(res["artifact"])
    if not resolved_intent:
        console.print("[bold red]--intent or --requirement is required[/bold red]")
        sys.exit(1)

    result = MutationRuntime(target_dir).open_session(
        resolved_intent, artifacts=artifacts or None, request_id=request_id
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        s = result.get("session") or {}
        console.print(f"[bold green]Session[/bold green] {s.get('session_id')}")
        console.print(f"[bold]intent[/bold]: {s.get('intent')}")
        arts = s.get("artifacts") or []
        if arts:
            labels = [f"{a.get('kind')}:{a.get('id')}" for a in arts if isinstance(a, dict)]
            if labels:
                console.print(f"[bold]artifacts[/bold]: {', '.join(labels)}")
    else:
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@session_group.command("end")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def session_end_cmd(target_dir, as_json):
    """Close the active session."""
    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).close_session()
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]Session ended[/bold green]")
    else:
        console.print(f"[bold red]{result.get('error')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@session_group.command("recover")
@click.argument("governed_session_id")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def session_recover_cmd(governed_session_id, target_dir, as_json):
    """Recover one exact governed session without a manual repair chain."""
    from apatch.runtime.recovery import recover_session

    result = recover_session(target_dir, governed_session_id)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(
            "[bold green]Session recovery[/bold green] "
            f"{result.get('recovery')}"
        )
    else:
        console.print(f"[bold red]{result.get('error')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@click.group("spec")
def spec_group():
    """Executable Specifications (RFP-007): SPEC.md → requirements → coverage."""
    pass


@click.group("slug")
def slug_group():
    """Slug/category intake cockpit for spec-driven work."""
    pass


@slug_group.command("intake")
@click.argument("slug")
@click.option("--alias", "aliases", multiple=True, help="Additional slug alias/synonym.")
@click.option("--manifest-path", default=None, help="Optional manifests/slug-intake.json path.")
@click.option("--live", is_flag=True, help="Run live conformance verify for matching specs.")
@click.option("--limit", default=25, show_default=True, type=int, help="Max evidence rows per section.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def slug_intake_cmd(slug, aliases, manifest_path, live, limit, target_dir, as_json):
    """Collect specs, reality, conformance, and evidence for a slug."""
    from apatch.slug_intake import slug_intake_workspace

    result = slug_intake_workspace(
        target_dir,
        slug=slug,
        aliases=list(aliases or []),
        manifest_path=manifest_path,
        live=live,
        limit=limit,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    console.print(f"[bold]Slug intake[/bold]: {result.get('slug')}  aliases={', '.join(result.get('aliases') or [])}")
    specs = result.get("specs") or []
    console.print(f"[bold]Specs[/bold]: {len(specs)}")
    for spec in specs:
        sm = spec.get("summary") or {}
        console.print(
            f"  {spec.get('id')} {sm.get('attested', 0)}/{sm.get('total', 0)} attested"
            + (" [green]done[/green]" if spec.get("done") else "")
        )
    reality = result.get("reality") or {}
    console.print(
        f"[bold]Reality[/bold]: {reality.get('total_matching', 0)} matching "
        f"({len(reality.get('uncovered') or [])} uncovered, {len(reality.get('pending') or [])} pending)"
    )
    qf = ((result.get("required_gates") or {}).get("query_first") or {})
    dm = ((result.get("required_gates") or {}).get("data_model") or {})
    console.print(f"[bold]Query-first[/bold]: {'present' if qf.get('present') else 'missing'}")
    console.print(f"[bold]Data model[/bold]: {'present' if dm.get('present') else 'missing'}")
    gaps = result.get("gaps") or []
    if gaps:
        console.print("[bold yellow]Gaps[/bold yellow]:")
        for gap in gaps:
            console.print(f"  {gap.get('severity', 'medium')}: {gap.get('id')} — {gap.get('message')}")
    console.print(f"[bold]Next[/bold]: {result.get('agent_next')}")


@slug_group.command("close")
@click.argument("slug")
@click.option("--api-url", default=None, help="Live smart-search API URL.")
@click.option("--triage-path", default=None, help="Path to tests/regressions/<slug>_feedback_triage.tsv.")
@click.option("--approved-path", default=None, help="Path to feedback_approved_contract.tsv.")
@click.option("--size", default=5, show_default=True, type=int, help="Live API result size.")
@click.option("--timeout", default=30.0, show_default=True, type=float, help="Live API timeout seconds.")
@click.option("--limit", default=0, show_default=True, type=int, help="Limit rows for a fast smoke run.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
@click.option("--compact", is_flag=True, help="With --json, emit summary/non-fixed/conflicts/needles only.")
def slug_close_cmd(slug, api_url, triage_path, approved_path, size, timeout, limit, target_dir, as_json, compact):
    """Replay feedback rows and propose closure statuses/needles for a slug."""
    from apatch.slug_close import DEFAULT_API_URL, slug_close_workspace

    result = slug_close_workspace(
        target_dir,
        slug=slug,
        api_url=api_url or DEFAULT_API_URL,
        triage_path=triage_path,
        approved_path=approved_path,
        size=size,
        timeout=timeout,
        limit=limit,
    )
    if as_json:
        payload = _compact_slug_close_result(result) if compact else result
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    summary = result.get("summary") or {}
    console.print(f"[bold]Slug close[/bold]: {result.get('slug')} rows={result.get('rows_scanned')}")
    console.print(f"  statuses: {summary.get('suggested_status_counts')}")
    console.print(f"  proposed needles: {summary.get('updates', 0)}")
    console.print(f"  approved conflicts: {summary.get('approved_conflicts', 0)}")
    for row in (result.get("suggestions") or [])[:10]:
        console.print(
            f"  {row.get('triage_id')}: {row.get('current_status')} -> "
            f"{row.get('suggested_status')} ({row.get('root_cause')})"
        )
    console.print(f"[bold]Next[/bold]: {result.get('agent_next')}")


@slug_group.command("cockpit")
@click.argument("slug")
@click.option("--alias", "aliases", multiple=True, help="Additional slug alias/synonym.")
@click.option("--manifest-path", default=None, help="Optional manifests/slug-intake.json path.")
@click.option("--live", is_flag=True, help="Run scoped conformance plus the configured operational_status hook.")
@click.option("--evidence-limit", default=25, show_default=True, type=int, help="Max intake evidence rows per section.")
@click.option("--feedback-limit", default=0, show_default=True, type=int, help="Limit feedback rows; 0 means all rows.")
@click.option("--api-url", default=None, help="Live smart-search API URL.")
@click.option("--triage-path", default=None, help="Path to tests/regressions/<slug>_feedback_triage.tsv.")
@click.option("--approved-path", default=None, help="Path to feedback_approved_contract.tsv.")
@click.option("--size", default=5, show_default=True, type=int, help="Live API result size for each feedback replay.")
@click.option("--timeout", default=30.0, show_default=True, type=float, help="Live API timeout seconds.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def slug_cockpit_cmd(
    slug,
    aliases,
    manifest_path,
    live,
    evidence_limit,
    feedback_limit,
    api_url,
    triage_path,
    approved_path,
    size,
    timeout,
    target_dir,
    as_json,
):
    """One-screen slug status: specs, gates, live feedback, graph root causes."""
    from apatch.slug_close import DEFAULT_API_URL
    from apatch.slug_cockpit import slug_cockpit_workspace

    result = slug_cockpit_workspace(
        target_dir,
        slug=slug,
        aliases=list(aliases or []),
        manifest_path=manifest_path,
        live=live,
        evidence_limit=evidence_limit,
        feedback_limit=feedback_limit,
        api_url=api_url or DEFAULT_API_URL,
        triage_path=triage_path,
        approved_path=approved_path,
        size=size,
        timeout=timeout,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    health = result.get("health") or {}
    summary = result.get("summary") or {}
    console.print(
        f"[bold]Slug cockpit[/bold]: {result.get('slug')} "
        f"health={health.get('state')} high={health.get('high')} medium={health.get('medium')}"
    )
    console.print(
        "  specs={}/{} feedback_rows={} non_fixed={} approved_conflicts={}".format(
            summary.get("specs_done"),
            summary.get("specs"),
            summary.get("feedback_rows"),
            summary.get("non_fixed_feedback"),
            summary.get("approved_conflicts"),
        )
    )
    diagnosis = result.get("diagnosis") or {}
    operational = result.get("operational") or {}
    if operational.get("configured"):
        console.print(
            "  operational: status={} complete={} reopen={} evidence_debt={}".format(
                operational.get("work_status"),
                operational.get("runtime_work_complete"),
                operational.get("runtime_reopen_required"),
                len(operational.get("evidence_debt_codes") or []),
            )
        )
    console.print(f"  feedback_statuses: {diagnosis.get('feedback_status_counts')}")
    if diagnosis.get("feedback_root_cause_counts"):
        console.print(f"  root_causes: {diagnosis.get('feedback_root_cause_counts')}")
    for item in (result.get("action_items") or [])[:12]:
        console.print(
            f"  [{item.get('severity', 'medium')}] {item.get('id')}: "
            f"{item.get('message') or item.get('recommended_action')}"
        )
    console.print(f"[bold]Next[/bold]: {result.get('agent_next')}")


@slug_group.command("feedback-lint")
@click.argument("slug", required=False)
@click.option("--triage-path", default=None, help="Lint one specific triage TSV instead of slug/all discovery.")
@click.option("--approved-path", default=None, help="Path to feedback_approved_contract.tsv.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def slug_feedback_lint_cmd(slug, triage_path, approved_path, target_dir, as_json):
    """Validate feedback TSV statuses against the canonical vocabulary."""
    from apatch.slug_feedback_lint import feedback_lint_workspace

    result = feedback_lint_workspace(
        target_dir,
        slug=slug,
        triage_path=triage_path,
        approved_path=approved_path,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok") or not result.get("clean"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    summary = result.get("summary") or {}
    state = "[green]clean[/green]" if result.get("clean") else "[yellow]drift[/yellow]"
    console.print(
        f"[bold]Feedback lint[/bold]: {state} files={summary.get('files_scanned')} rows={summary.get('rows_scanned')}"
    )
    for finding in (result.get("findings") or [])[:20]:
        console.print(f"  [{finding.get('severity', 'medium')}] {finding.get('id')}: {finding.get('message')}")
    if summary.get("proposed_needles"):
        console.print(f"  proposed needles: {summary.get('proposed_needles')}")
    console.print(f"[bold]Next[/bold]: {result.get('agent_next')}")
    if not result.get("clean"):
        sys.exit(1)


@slug_group.command("ratify")
@click.argument("slug")
@click.option("--spec", "spec_id", default=None, help="Explicit SPEC id; overrides contract/conformance resolution.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, help="Resolve + lint only; report the verify commands and stale Rk that would run.")
@click.option("--json", "as_json", is_flag=True)
def slug_ratify_cmd(slug, spec_id, target_dir, dry_run, as_json):
    """Ratify a slug: verify once, batch-attest open Rk, gate conformance."""
    from apatch.slug_ratify import slug_ratify_workspace

    result = slug_ratify_workspace(
        target_dir,
        slug=slug,
        spec=spec_id,
        dry_run=dry_run,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok") and result.get("error"):
        console.print(f"[bold red]{result.get('stage')}: {result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"  hint: {result.get('hint')}")
        if result.get("candidates"):
            console.print(f"  candidates: {', '.join(result.get('candidates'))}")
        sys.exit(1)
    header = f"[bold]Slug ratify[/bold]: {result.get('slug')} spec={result.get('spec')}"
    console.print(header + (" (dry run)" if result.get("dry_run") else ""))
    for stage in result.get("stages") or []:
        mark = "[green]ok[/green]" if stage.get("ok") else "[red]failed[/red]"
        console.print(f"  {stage.get('name')}: {mark} ({stage.get('elapsed_sec')}s) {stage.get('summary')}")
    would = result.get("would_run") or {}
    if would:
        for cmd in would.get("verify_commands") or []:
            console.print(f"  would run: {cmd}")
        console.print(f"  stale: {', '.join(would.get('stale_requirements') or []) or '—'}")
    verify = result.get("verify") or {}
    if verify:
        console.print(
            f"  verify: commands_run={verify.get('commands_run')} "
            f"elapsed={verify.get('elapsed_sec')}s green={verify.get('green')}"
        )
    if result.get("primary_attested"):
        console.print(f"  primary attested: {', '.join(result.get('primary_attested'))}")
    if result.get("reattested"):
        console.print(f"  reattested: {', '.join(result.get('reattested'))}")
    attestation = result.get("attestation") or {}
    if attestation:
        console.print(
            f"  attestation: sessions={attestation.get('sessions_opened')} "
            f"ledger_commits={attestation.get('ledger_commits')}"
        )
    gate = result.get("gate") or {}
    if gate:
        console.print(f"  gate: {gate.get('verdict')} buckets={gate.get('buckets')}")
    console.print(f"[bold]Next[/bold]: {result.get('next_action')}")
    if not result.get("ok"):
        sys.exit(1)


@slug_group.command("repair-map")
@click.option("--root-cause", default=None, help="Test routing: a slug_close root_cause value.")
@click.option("--graph-issue", default=None, help="Test routing: a decision-graph primary_issue value.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def slug_repair_map_cmd(root_cause, graph_issue, target_dir, as_json):
    """Validate the repair map (manifests/repair-map.json) and optionally route a diagnosis."""
    from apatch.repair_map import load_repair_map, route

    result = load_repair_map(target_dir)
    payload = {
        "ok": bool(result.get("present")) and not result.get("errors"),
        "present": result.get("present"),
        "path": result.get("path"),
        "rules": [rule["id"] for rule in result.get("rules") or []],
        "errors": result.get("errors") or [],
    }
    if root_cause or graph_issue:
        payload["routed"] = route(result, {"root_cause": root_cause, "graph_primary_issue": graph_issue})
    if as_json:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        if not payload["ok"]:
            sys.exit(1)
        return
    if not payload["ok"]:
        console.print(f"[bold red]Repair map missing or invalid[/bold red]: {payload['errors'] or 'file not found'}")
        sys.exit(1)
    console.print(f"[bold]Repair map[/bold]: {payload['path']} rules={len(payload['rules'])}")
    for rule_id in payload["rules"]:
        console.print(f"  {rule_id}")
    routed = payload.get("routed")
    if routed:
        console.print(f"[bold]Route[/bold]: [{routed[0]['rule_id']}] {routed[0]['action']}")
    elif root_cause or graph_issue:
        console.print("[bold yellow]No route matched[/bold yellow] — extend the map or file a platform RFC.")


def _compact_slug_close_result(result):
    non_fixed = []
    for row in result.get("suggestions") or []:
        if row.get("suggested_status") == "fixed":
            continue
        live = row.get("live") or {}
        non_fixed.append(
            {
                "triage_id": row.get("triage_id"),
                "query": row.get("query"),
                "current_status": row.get("current_status"),
                "suggested_status": row.get("suggested_status"),
                "suggested_slug": row.get("suggested_slug"),
                "suggested_jde": row.get("suggested_jde"),
                "root_cause": row.get("root_cause"),
                "top_jde": live.get("top_jde"),
                "top_name": live.get("top_name"),
                "evidence": (row.get("evidence") or [])[:6],
            }
        )
    return {
        "ok": result.get("ok"),
        "workspace": result.get("workspace"),
        "slug": result.get("slug"),
        "api_url": result.get("api_url"),
        "triage_path": result.get("triage_path"),
        "approved_path": result.get("approved_path"),
        "rows_scanned": result.get("rows_scanned"),
        "summary": result.get("summary"),
        "non_fixed": non_fixed,
        "approved_conflicts": result.get("approved_conflicts") or [],
        "proposed_needles": result.get("proposed_needles") or [],
        "approved_needles": result.get("approved_needles") or [],
        "agent_next": result.get("agent_next"),
    }


@spec_group.command("list")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_list_cmd(target_dir, as_json):
    """Discover executable spec ids from docs/specs/SPEC-*.md."""
    from apatch.cli_status import spec_list_workspace

    result = spec_list_workspace(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    specs = result.get("specs") or []
    console.print(f"[bold]Specs[/bold] ({result.get('count', len(specs))}):")
    for sid in specs:
        console.print(f"  {sid}")



_SPEC_STATE_COLOR = {
    "attested": "green",
    "in_progress": "yellow",
    "pending": "dim",
    "stale": "bold red",
    "blocked": "red",
}


@spec_group.command("status")
@click.option("--spec", default=None, help="Spec id (auto-discovered at docs/specs/<id>.md).")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_status_cmd(spec, spec_path, target_dir, as_json):
    """Per-requirement coverage of a spec, derived from the TrustChain ledger."""
    from apatch.spec import spec_status_workspace

    result = spec_status_workspace(target_dir, spec=spec, spec_path=spec_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    s = result.get("summary") or {}
    console.print(
        f"[bold]{result.get('spec')}[/bold] — {s.get('attested', 0)}/{s.get('total', 0)} "
        f"attested ({s.get('percent_complete', 0)}%)"
    )
    for r in result.get("requirements") or []:
        color = _SPEC_STATE_COLOR.get(r["state"], "white")
        verify = f" [dim](verify: {r['verify']})[/dim]" if r.get("verify") else ""
        console.print(f"  [{color}]{r['state']:<12}[/{color}] {r['id']} — {r['title']}{verify}")
    for w in result.get("warnings") or []:
        console.print(f"[yellow]warning:[/yellow] {w}")




@spec_group.command("coverage")
@click.option("--spec", default=None, help="Spec id (auto-discovered at docs/specs/<id>.md).")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_coverage_cmd(spec, spec_path, target_dir, as_json):
    """Per-Rk coverage matrix with file-drift staleness (RFP-010)."""
    from apatch.spec_coverage import spec_coverage_workspace

    result = spec_coverage_workspace(target_dir, spec=spec, spec_path=spec_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    s = result.get("summary") or {}
    console.print(
        f"[bold]{result.get('spec')}[/bold] coverage — "
        f"attested={s.get('attested', 0)} stale={s.get('stale', 0)} pending={s.get('pending', 0)}"
    )
    for r in result.get("requirements") or []:
        color = _SPEC_STATE_COLOR.get(r["state"], "white")
        drift = f" drifted={r.get('drifted')}" if r.get("drifted") else ""
        console.print(f"  [{color}]{r['state']:<12}[/{color}] {r['id']}{drift}")

@spec_group.command("interference")
@click.option("--spec", "specs", multiple=True, help="Spec id (repeat for 2+ specs).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--level", default=2, show_default=True, type=int, help="Analysis depth 1|2.")
@click.option("--json", "as_json", is_flag=True)
def spec_interference_cmd(specs, target_dir, level, as_json):
    """Cross-spec interference matrix (RFP-014 Phase 1)."""
    from apatch.spec_interference import spec_interference_workspace

    if len(specs) < 2:
        console.print("[bold red]pass --spec at least twice[/bold red]")
        sys.exit(1)
    result = spec_interference_workspace(
        target_dir,
        specs=list(specs),
        level=level,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    s = result.get("summary") or {}
    console.print(
        f"[bold]interference[/bold] specs={result.get('specs')} "
        f"conflicts={s.get('total_conflicts', 0)} risk={result.get('risk_score')}"
    )
    for c in result.get("conflicts") or []:
        console.print(
            f"  [{c.get('severity')}] {c.get('type')} {c.get('spec_a')} x {c.get('spec_b')} "
            f"file={c.get('file', '-')}"
        )
    if result.get("safe_order"):
        console.print(f"  safe_order={result.get('safe_order')}")




@spec_group.command("schedule")
@click.option("--spec", "specs", multiple=True, help="Spec id (repeat for 2+ specs).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--level", default=2, show_default=True, type=int)
@click.option(
    "--strategy",
    default="safe",
    show_default=True,
    type=click.Choice(["safe", "risk_first"]),
)
@click.option("--json", "as_json", is_flag=True)
def spec_schedule_cmd(specs, target_dir, level, strategy, as_json):
    """Cross-spec safe_order schedule (RFP-014 Phase 1.5)."""
    from apatch.spec_interference import spec_schedule_workspace

    if len(specs) < 2:
        console.print("[bold red]pass --spec at least twice[/bold red]")
        sys.exit(1)
    result = spec_schedule_workspace(
        target_dir, specs=list(specs), level=level, strategy=strategy
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    sched = result.get("schedule") or {}
    console.print(
        f"[bold]schedule[/bold] safe_order={sched.get('safe_order')} "
        f"risk={sched.get('risk_score')} irreconcilable={sched.get('irreconcilable')}"
    )


@spec_group.command("run-multi")
@click.option("--spec", "specs", multiple=True, help="Spec id (repeat for 2+ specs).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--requirements", default=None, help="JSON file: {SPEC: {Rk: {needles}}} per spec.")
@click.option("--cross-verify", is_flag=True, help="Level-3 cross-verify gate before each next spec.")
@click.option("--no-re-interference", is_flag=True, help="Skip L1/L2 re-interference after each spec.")
@click.option(
    "--execution-mode",
    type=click.Choice(["serial", "shared_maintenance"]),
    default="serial",
    show_default=True,
    help="Use one partitioned apply for independent maintenance targets.",
)
@click.option("--maintenance-verify", default=None, help="Aggregate verify after exact Rk verifies.")
@click.option("--verify-jobs", default=8, show_default=True, type=click.IntRange(min=1))
@click.option("--verify-timeout", default=120.0, show_default=True, type=click.FloatRange(min=0.1))
@click.option(
    "--maintenance-chunk-max-files",
    default=100,
    show_default=True,
    type=click.IntRange(min=1),
)
@click.option("--json", "as_json", is_flag=True)
def spec_run_multi_cmd(
    specs,
    target_dir,
    requirements,
    cross_verify,
    no_re_interference,
    execution_mode,
    maintenance_verify,
    verify_jobs,
    verify_timeout,
    maintenance_chunk_max_files,
    as_json,
):
    """Run multiple specs in schedule order (RFP-014 Phase 3)."""
    from apatch.spec_run_multi import spec_run_multi_enriched

    if len(specs) < 2:
        console.print("[bold red]pass --spec at least twice[/bold red]")
        sys.exit(1)
    req_map = None
    if requirements:
        with open(requirements, encoding="utf-8") as fh:
            req_map = json.load(fh)
    result = spec_run_multi_enriched(
        target_dir,
        specs=list(specs),
        requirements=req_map,
        cross_verify=cross_verify,
        re_interference=not no_re_interference,
        execution_mode=execution_mode,
        maintenance_verify=maintenance_verify,
        verify_jobs=verify_jobs,
        verify_timeout=verify_timeout,
        maintenance_chunk_max_files=maintenance_chunk_max_files,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(
            f"[bold green]run-multi[/bold green] completed={result.get('completed_specs')} "
            f"order={result.get('order')}"
        )
    else:
        console.print(f"[bold red]{result.get('error')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@spec_group.command("cross-verify")
@click.option("--spec", "specs", multiple=True, help="Spec id (repeat for 2+ specs).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
@click.option("--include-attested", is_flag=True, help="Use attested ledger needles as apply source.")
def spec_cross_verify_cmd(specs, target_dir, as_json, include_attested):
    """Cross-spec semantic cross-verify (RFP-014 Phase 2)."""
    from apatch.spec_cross_verify import spec_cross_verify_workspace

    if len(specs) < 2:
        console.print("[bold red]pass --spec at least twice[/bold red]")
        sys.exit(1)
    result = spec_cross_verify_workspace(
        target_dir,
        specs=list(specs),
        include_attested=include_attested,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    console.print(
        f"[bold]cross-verify[/bold] specs={result.get('specs')} "
        f"conflicts={len(result.get('semantic_conflicts') or [])} "
        f"all_passed={result.get('all_passed')}"
    )
    for c in result.get("semantic_conflicts") or []:
        console.print(
            f"  [{c.get('severity')}] {c.get('source_spec')} → {c.get('victim_spec')} "
            f"{c.get('requirement')}: {c.get('detail', '')[:120]}"
        )



@spec_group.command("needles-scaffold")
@click.option("--spec", required=True, help="Spec id.")
@click.option("--spec-path", default=None)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_needles_scaffold_cmd(spec, spec_path, target_dir, as_json):
    """Build deterministic needles/plan scaffold (RFP-024) after spec lint."""
    from apatch.spec_needles_scaffold import spec_needles_scaffold_workspace

    result = spec_needles_scaffold_workspace(
        target_dir, spec=spec, spec_path=spec_path
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    console.print(f"[bold green]scaffold[/bold green] {result.get('spec')} — pending: {', '.join(result.get('pending') or [])}")
    console.print(result.get("agent_next") or "")


@spec_group.command("rebind-stale")
@click.option("--spec", required=True, help="Spec id whose file_drift-stale requirements should be re-anchored.")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--no-verify", is_flag=True, help="Do not run each stale requirement's verify before noop-attest.")
@click.option("--requirement-id", "requirement_ids", multiple=True, help="Only rebind this Rk (repeatable).")
@click.option("--exclude-requirement-id", "exclude_requirement_ids", multiple=True, help="Keep this Rk stale (repeatable).")
@click.option("--json", "as_json", is_flag=True)
def spec_rebind_stale_cmd(spec, spec_path, target_dir, no_verify, requirement_ids, exclude_requirement_ids, as_json):
    """Re-verify and noop-attest file_drift-stale requirements for one spec."""
    from apatch.spec_rebind import rebind_stale_requirements

    result = rebind_stale_requirements(
        target_dir,
        spec=spec,
        spec_path=spec_path,
        run_verify=not no_verify,
        requirement_ids=requirement_ids or None,
        exclude_requirement_ids=exclude_requirement_ids or None,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok") or result.get("errors"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    console.print(
        f"[bold green]rebind-stale[/bold green] {result.get('spec')} "
        f"rebound={len(result.get('rebound') or [])} "
        f"skipped_red={len(result.get('skipped_red') or [])} "
        f"errors={len(result.get('errors') or [])}"
    )
    if result.get("rebound"):
        console.print("  rebound: " + ", ".join(result.get("rebound") or []))
    for row in result.get("skipped_red") or []:
        console.print(f"  [yellow]red[/yellow] {row.get('requirement')}: {row.get('verify')}")
    for row in result.get("errors") or []:
        console.print(f"  [bold red]error[/bold red] {row.get('requirement')}: {row.get('error')}")
    if result.get("errors"):
        sys.exit(1)


@spec_group.command("lint")
@click.option("--spec", default=None, help="Spec id (auto-discovered at docs/specs/<id>.md).")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_lint_cmd(spec, spec_path, target_dir, as_json):
    """Validate a spec against the authoring standard (id, verify, coverage readiness)."""
    from apatch.spec import spec_lint_workspace

    result = spec_lint_workspace(target_dir, spec=spec, spec_path=spec_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok") or not result.get("passed"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    c = result.get("counts") or {}
    verdict = "[bold green]PASS[/bold green]" if result.get("passed") else "[bold red]FAIL[/bold red]"
    console.print(
        f"{verdict} {result.get('spec')} — errors={c.get('errors', 0)} "
        f"warnings={c.get('warnings', 0)} info={c.get('info', 0)}"
    )
    for e in result.get("errors") or []:
        console.print(f"  [bold red]error[/bold red] [{e['code']}] {e['message']}")
    for w in result.get("warnings") or []:
        loc = f"{w['requirement']}: " if w.get("requirement") else ""
        console.print(f"  [yellow]warn[/yellow]  [{w['code']}] {loc}{w['message']}")
    for i in result.get("info") or []:
        loc = f"{i['requirement']}: " if i.get("requirement") else ""
        console.print(f"  [dim]info  [{i['code']}] {loc}{i['message']}[/dim]")
    if not result.get("passed"):
        sys.exit(1)


@spec_group.command("scaffold")
@click.option("--from-contract", "rfp", required=True, help="RFP id (RFP-X) or path whose Acceptance table seeds the requirements.")
@click.option("--spec", "spec_id", required=True, help="New SPEC id to scaffold (e.g. SPEC-FOO-1).")
@click.option("--out", "out_path", default="", help="Write the skeleton here (default: print to stdout).")
@click.option("--mandatory-only", is_flag=True, help="Only MUST-level acceptance rows.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_scaffold_cmd(rfp, spec_id, out_path, mandatory_only, target_dir, as_json):
    """Scaffold a SPEC.md skeleton from an RFP Acceptance table (RFP-023): one Rk per
    acceptance row + a pre-filled traceability gate, contract-complete by construction.
    Fill the (verify:) placeholders \u2014 you no longer hand-author the checklist."""
    from apatch.rfp_coverage import scaffold_spec_from_rfp_workspace

    res = scaffold_spec_from_rfp_workspace(
        target_dir, rfp=rfp, spec_id=spec_id, out_path=out_path or None,
        mandatory_only=mandatory_only)
    if as_json:
        click.echo(json.dumps({k: v for k, v in res.items() if k != "spec_markdown"},
                              indent=2, ensure_ascii=False))
        sys.exit(0 if res.get("ok") else 1)
    if not res.get("ok"):
        console.print(f"[bold red]{res.get('error')}[/bold red]")
        sys.exit(1)
    if out_path:
        c = res["counts"]
        console.print(f"[bold green]\u2713 scaffolded[/bold green] {res['spec_id']} \u2192 {res.get('written')} \u2014 {c['scaffolded']} Rk from {c['acceptance']} acceptance rows ({res['rfp']}). Fill the (verify:) placeholders.")
    else:
        click.echo(res["spec_markdown"])


@spec_group.command("falsify")
@click.option("--spec", default=None, help="SPEC id.")
@click.option("--spec-path", default=None)
@click.option("--requirement", "-r", required=True, help="Requirement id (e.g. R5).")
@click.option("--files", required=True, help="Comma-separated files this requirement guards.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_falsify_cmd(spec, spec_path, requirement, files, target_dir, as_json):
    """Prove a requirement's gate can be RED: corrupt the files it guards and assert its
    (verify:) fails. A gate that stays green is FALSE — the test does not test it."""
    from apatch.spec import _load_spec
    from apatch.conformance import falsify_requirement

    parsed = _load_spec(target_dir, spec=spec, spec_path=spec_path)
    req = next((r for r in parsed.requirements if r.id == requirement), None)
    if not req:
        console.print(f"[bold red]requirement {requirement!r} not found in spec[/bold red]")
        sys.exit(1)
    if not req.verify:
        console.print(f"[bold red]{requirement} has no (verify:) — nothing to falsify[/bold red]")
        sys.exit(1)
    flist = [f.strip() for f in files.split(",") if f.strip()]
    res = falsify_requirement(target_dir, req.verify, flist, record=True)
    if as_json:
        click.echo(json.dumps({"requirement": requirement, **res}, indent=2, ensure_ascii=False))
        sys.exit(1 if res.get("verdict") in ("false", "baseline_red") else 0)
    v = res.get("verdict")
    if v == "real":
        console.print(f"[bold green]{requirement} \u2713 kills[/bold green] \u2014 corrupting {flist} made verify RED, restored \u2192 GREEN. Real gate.")
        if not res.get("restored_ok", True):
            console.print(f"[bold red]WARNING:[/bold red] verify did not return to GREEN after restore (rc {res.get('restored_rc')}) \u2014 check {flist}")
    elif v == "false":
        console.print(f"[bold red]{requirement} \u2717 survives[/bold red] \u2014 corrupting {flist} left verify GREEN. FALSE gate: the test does not test this requirement. Fix it.")
        sys.exit(1)
    elif v == "baseline_red":
        console.print(f"[yellow]{requirement}[/yellow] \u2014 verify is not green to begin with (rc {res.get('baseline_rc')}); cannot falsify")
        sys.exit(1)
    else:
        console.print(f"[yellow]{requirement}[/yellow] \u2014 {v}: {res.get('note') or res.get('missing') or res}")
        sys.exit(1)


@spec_group.command("execute")
@click.option("--spec", default=None, help="Spec id (auto-discovered at docs/specs/<id>.md).")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--requirement", default=None, help="SPEC-ID#Rk override instead of auto-next.")
@click.option("--dry-run", is_flag=True, help="Lint + deps + plan only; no session/mutations.")
@click.option("--finalize", is_flag=True, help="verify → attest → session_end after mutations.")
@click.option("--needles", default=None, help="JSON file: [{find_text, replace_text, target_file}, …].")
@click.option("--logs", "logs_path", default="patches.jsonl", show_default=True)
@click.option("--skip-lint", is_flag=True)
@click.option("--no-deps", "no_deps", is_flag=True, help="Skip upstream spec dependency gate.")
@click.option("--governed-session-id", default=None, help="Exact active session to continue/finalize.")
@click.option("--session-token", default=None, help="Capability paired with --governed-session-id.")
@click.option("--request-id", default=None, help="Stable retry id; reuse after a client timeout.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_execute_cmd(
    spec,
    spec_path,
    requirement,
    dry_run,
    finalize,
    needles,
    logs_path,
    skip_lint,
    no_deps,
    governed_session_id,
    session_token,
    request_id,
    target_dir,
    as_json,
):
    """RFP-008: requirement execution — next Rk through governed cycle."""
    from apatch.spec_executor import execute_next_enriched

    needle_list = None
    if needles:
        with open(needles, encoding="utf-8") as fh:
            needle_list = json.load(fh)
    result = execute_next_enriched(
        target_dir,
        spec=spec,
        spec_path=spec_path,
        requirement=requirement,
        dry_run=dry_run,
        finalize=finalize,
        needles=needle_list,
        logs_path=logs_path,
        skip_lint=skip_lint,
        check_dependencies=not no_deps,
        governed_session_id=governed_session_id,
        session_token=session_token,
        request_id=request_id,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        phase = result.get("execution_phase", "?")
        console.print(f"[bold green]execute[/bold green] phase={phase}")
        if result.get("requirement_token"):
            console.print(f"  requirement: {result['requirement_token']}")
        if result.get("agent_next"):
            console.print(f"[dim]{result['agent_next']}[/dim]")
    else:
        console.print(f"[bold red]{result.get('error')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@spec_group.command("run")
@click.option("--spec", default=None, help="Spec id (auto-discovered at docs/specs/<id>.md).")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--requirements", default=None, help="JSON file: {Rk: {needles: [...]}} inline manifest.")
@click.option("--manifest", "manifest_path", default=None, help="Run manifest JSON (CI/review; optional).")
@click.option("--dry-run", is_flag=True, help="Plan all pending Rk; returns manifest_template.")
@click.option("--reset", is_flag=True, help="Clear .apatch/spec_run.json state.")
@click.option("--abort", is_flag=True, help="Rollback last checkpoint and clear spec_run state.")
@click.option("--no-resume", is_flag=True, help="Do not resume active spec_run state.")
@click.option("--chunk-rk", "chunk_rk_per_call", default=0, show_default=True, help="Rk per tick; 0 = all pending.")
@click.option("--logs", "logs_path", default="patches-spec-run.jsonl", show_default=True)
@click.option("--skip-lint", is_flag=True)
@click.option("--no-deps", "no_deps", is_flag=True)
@click.option("--request-id", default=None, help="Stable retry id; reuse after a client timeout.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_run_cmd(
    spec,
    spec_path,
    requirements,
    manifest_path,
    dry_run,
    reset,
    abort,
    no_resume,
    chunk_rk_per_call,
    logs_path,
    skip_lint,
    no_deps,
    request_id,
    target_dir,
    as_json,
):
    """RFP-009: batch-run entire spec — inline requirements or optional manifest file."""
    from apatch.spec_run import spec_run_enriched

    req_map = None
    if requirements:
        with open(requirements, encoding="utf-8") as fh:
            req_map = json.load(fh)
    result = spec_run_enriched(
        target_dir,
        spec=spec,
        spec_path=spec_path,
        requirements=req_map,
        manifest_path=manifest_path,
        dry_run=dry_run,
        reset=reset,
        abort=abort,
        resume=not no_resume,
        chunk_rk_per_call=chunk_rk_per_call,
        logs_path=logs_path,
        skip_lint=skip_lint,
        check_dependencies=not no_deps,
        request_id=request_id,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        phase = result.get("execution_phase", "?")
        prog = result.get("progress") or {}
        console.print(
            f"[bold green]spec-run[/bold green] phase={phase} "
            f"done={result.get('done')} continue={result.get('continue')} "
            f"rk={prog.get('rk_done', 0)}/{prog.get('rk_total', 0)}"
        )
        if result.get("agent_next"):
            console.print(f"[dim]{result['agent_next']}[/dim]")
    else:
        console.print(f"[bold red]{result.get('error')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@spec_group.command("next")
@click.option("--spec", default=None, help="Spec id (auto-discovered at docs/specs/<id>.md).")
@click.option("--spec-path", default=None, help="Path to the spec markdown file.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_next_cmd(spec, spec_path, target_dir, as_json):
    """Next open requirement + its acceptance check + ready-to-run session start."""
    from apatch.spec import spec_next_workspace

    result = spec_next_workspace(target_dir, spec=spec, spec_path=spec_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    nxt = result.get("next")
    if not nxt:
        console.print("[bold green]All requirements attested.[/bold green]")
        return
    console.print(f"[bold]next[/bold]: {nxt['id']} — {nxt['title']} [dim]({nxt['state']})[/dim]")
    if nxt.get("verify"):
        console.print(f"  verify: {nxt['verify']}")
    console.print(f"[dim]{result.get('session_start')}[/dim]")


@session_group.command("continue")
@click.option("--logs", required=True, type=click.Path(exists=True), help="Patches JSONL (same as apply-session).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--verify", "verify_cmd", help="Verify command (deferred until last chunk by default).")
@click.option("--session", "session_path", default=None, help="Chunk session file (default .apatch/apply_session.json).")
@click.option("--chunk-files", default=5, show_default=True, type=int)
@click.option("-a", "--all", "replace_all", is_flag=True)
@click.option("--only-drifted", is_flag=True)
@click.option("--min-confidence", type=float, default=None)
@click.option("--no-trustchain", is_flag=True)
@click.option("--loop", is_flag=True, help="Run chunks until complete or failure.")
@click.option("--json", "as_json", is_flag=True)
def session_continue_cmd(
    logs,
    target_dir,
    verify_cmd,
    session_path,
    chunk_files,
    replace_all,
    only_drifted,
    min_confidence,
    no_trustchain,
    loop,
    as_json,
):
    """Resume chunked apply from the last checkpoint."""
    from apatch.runtime.session import continue_apply_session

    if loop:
        from apatch.runtime.runtime import MutationRuntime

        rt = MutationRuntime(target_dir)
        while True:
            result = rt.apply_session(
                logs,
                session_path=session_path,
                verify=verify_cmd,
                chunk_max_files=chunk_files,
                replace_all=replace_all,
                only_drifted=only_drifted,
                min_confidence=min_confidence,
                no_trustchain=no_trustchain,
                reset=False,
            )
            if result.get("error_type") == "RUNTIME_TRANSITION":
                if as_json:
                    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
                else:
                    console.print(f"[bold red]{result.get('error')}[/bold red]")
                sys.exit(1)
            if as_json:
                click.echo(json.dumps(result, indent=2, ensure_ascii=False))
            if not result.get("continue"):
                break
            if not result.get("ok"):
                sys.exit(1)
        return

    result = continue_apply_session(
        target_dir,
        logs,
        session_path=session_path,
        verify=verify_cmd,
        chunk_max_files=chunk_files,
        replace_all=replace_all,
        only_drifted=only_drifted,
        min_confidence=min_confidence,
        no_trustchain=no_trustchain,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
    elif result.get("ok"):
        prog = result.get("progress") or {}
        console.print(
            f"[bold green]continued[/bold green] chunk "
            f"{prog.get('chunks_done')}/{prog.get('chunks_total')} "
            f"checkpoint={result.get('checkpoint')}"
        )
    else:
        console.print(f"[bold red]{result.get('error', 'continue failed')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@session_group.command("status")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def session_status_cmd(target_dir, as_json):
    """Domain view: lifecycle, intent, invariant, policy."""
    from apatch.runtime.session import build_session_view

    result = build_session_view(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    s = result.get("session") or {}
    console.print(f"[bold]lifecycle[/bold]: {s.get('lifecycle')}  [dim](phase={s.get('phase')})[/dim]")
    console.print(f"[bold]session_id[/bold]: {s.get('session_id') or '—'}")
    console.print(f"[bold]intent[/bold]: {s.get('intent') or '—'}")
    console.print(f"[bold]checkpoint[/bold]: {s.get('checkpoint') or '—'}")
    console.print(f"[bold]risk[/bold]: {s.get('risk_level')}")
    console.print(f"[bold]next[/bold]: {s.get('next_action')}")
    pol = result.get("policy") or {}
    console.print(
        f"[bold]policy[/bold]: trustchain={pol.get('trustchain_mode')} "
        f"enforce={pol.get('enforcement')} sandbox={pol.get('sandbox_mode')}"
    )
    if s.get("failure"):
        console.print(f"[bold red]failure[/bold red]: {s['failure'].get('error_type')}")
    if not (result.get("invariant") or {}).get("satisfied"):
        console.print("[yellow]Tip: apatch session start --intent \"…\"[/yellow]")


@click.group("attestation")
def attestation_group():
    """TrustChain cryptographic evidence (separate from Verification)."""
    pass


@attestation_group.command("export")
@click.option(
    "--out",
    "out_path",
    required=True,
    type=click.Path(dir_okay=False),
    help="Write audit bundle JSON (session + attestation + events).",
)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def attestation_export_cmd(out_path, target_dir, as_json):
    """Export session, attestation, and event stream for audit."""
    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).export_attestation(out_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold green]Exported[/bold green] → {result.get('path')}")
        console.print(f"[dim]{result.get('event_count')} events[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@attestation_group.command("commit")
@click.option("--message", default=None, help="Attestation message (defaults to session intent).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def attestation_commit_cmd(message, target_dir, as_json):
    """Commit TrustChain attestation for the current session (runtime attest)."""
    from apatch.runtime.runtime import MutationRuntime

    result = MutationRuntime(target_dir).attest(message=message)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
    elif result.get("ok"):
        console.print("[bold green]✓ Attestation committed[/bold green]")
    else:
        console.print(f"[bold red]{result.get('error', 'attest failed')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@attestation_group.command("noop")
@click.option("--covered-by", "covered_by", required=True, help="Comma-separated Rk ids that satisfy this requirement (e.g. 'R1,R4').")
@click.option("--message", default=None, help="Attestation message (defaults to session intent).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def attestation_noop_cmd(covered_by, message, target_dir, as_json):
    """Attest the current requirement as covered by another Rk's mutation (RFP-027) \u2014 no
    marker file, no new change. Mirrors the apatch_noop_attest MCP tool for shell agents."""
    from apatch.runtime.runtime import MutationRuntime

    cov = [c.strip() for c in str(covered_by).split(",") if c.strip()]
    result = MutationRuntime(target_dir).noop_attest(cov, message=message)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
    elif result.get("ok"):
        console.print(f"[bold green]\u2713 Noop-attested[/bold green] (covered by {', '.join(cov)})")
    else:
        console.print(f"[bold red]{result.get('error', 'noop-attest failed')}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@attestation_group.command("show")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def attestation_show_cmd(target_dir, as_json):
    """Show attestation mode, HEAD, and recent domain events."""
    from apatch.runtime.attestation import build_attestation_view

    result = build_attestation_view(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    a = result.get("attestation") or {}
    console.print(f"[bold]mode[/bold]: {a.get('mode')}")
    console.print(f"[dim]{a.get('summary')}[/dim]")
    if a.get("head"):
        console.print(f"[bold]HEAD[/bold]: {a.get('head')}")
    console.print(f"[bold]events[/bold]: {result.get('events_log')}")


# Porcelain alias (RFP-004 transition)
@click.group("proof")
def proof_group():
    """Alias for attestation (legacy porcelain)."""
    pass


@proof_group.command("show")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def proof_show_cmd(target_dir, as_json):
    """Same as apatch attestation show."""
    from apatch.runtime.attestation import build_attestation_view

    result = build_attestation_view(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    a = result.get("attestation") or {}
    console.print(f"[bold]mode[/bold]: {a.get('mode')}")
    console.print(f"[dim]{a.get('summary')}[/dim]")
    if a.get("head"):
        console.print(f"[bold]HEAD[/bold]: {a.get('head')}")


@cli.command("console")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--once", is_flag=True, help="Render once and exit (no interactive refresh).")
def console_cmd(target_dir, once):
    """Mutation Console — Session / Attestation / Events."""
    from apatch.console.mvp import run_console

    sys.exit(run_console(target_dir, refresh=not once))


@click.group("verify")
def verify_group():
    """Verification beyond shell commands."""
    pass


@verify_group.command("status")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--job-id", "job_id", default=None, help="Poll async verify job (AR-2).")
@click.option("--json", "as_json", is_flag=True)
def verify_status_cmd(target_dir, job_id, as_json):
    """Session phase + recommended verify + policy, or poll async verify job."""
    from apatch.runtime.runtime import MutationRuntime

    rt = MutationRuntime(target_dir)
    result = rt.verify_job_status(job_id) if job_id else rt.verification_status()
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    s = result.get("session") or {}
    v = result.get("verification") or {}
    console.print(f"[bold]lifecycle[/bold]: {s.get('lifecycle')}  phase={s.get('phase')}")
    console.print(f"[bold]next[/bold]: {s.get('next_action')}")
    console.print(f"[bold]recommended[/bold]: {v.get('recommended')}")
    console.print(
        f"[bold]policy[/bold]: trustchain={v.get('trustchain_mode')} "
        f"sandbox={v.get('sandbox_mode')} enforce={v.get('enforcement_active')}"
    )
    for w in v.get("warnings") or []:
        console.print(f"[yellow]{w}[/yellow]")


@verify_group.command("run")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--verify", "verify_cmd", default=None, help="Shell verify command (default: doctor recommended_verify_resolved).")
@click.option("--semantic", is_flag=True, help="Run semantic verify.")
@click.option("--notarization", is_flag=True, help="Run notarization check.")
@click.option("--pipeline", "pipeline_manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="With --pipeline: plan only.")
@click.option("--rules", "rules_path", type=click.Path(dir_okay=False))
@click.option("--since", default="HEAD", show_default=True)
@click.option("--staged", is_flag=True, help="Notarization: check staged files.")
@click.option("--working-tree", is_flag=True, help="Notarization: check working tree.")
@click.option(
    "--baseline",
    type=click.Choice(["off", "capture", "compare"]),
    default="off",
    show_default=True,
    help="Baseline-aware verify: capture pre-apply failures / compare after apply.",
)
@click.option(
    "--allow-failure",
    "allowed_failures",
    multiple=True,
    help="Failing test node id (or substring) that must not block verify. Repeatable.",
)
@click.option("--async", "async_mode", is_flag=True, help="Run shell verify as background job (AR-2).")
@click.option("--json", "as_json", is_flag=True)
def verify_run_cmd(
    target_dir,
    verify_cmd,
    semantic,
    notarization,
    pipeline_manifest,
    dry_run,
    rules_path,
    since,
    staged,
    working_tree,
    baseline,
    allowed_failures,
    async_mode,
    as_json,
):
    """Facade: shell, semantic, notarization, or engineering pipeline verify."""
    from apatch.runtime.runtime import MutationRuntime

    rt = MutationRuntime(target_dir)
    result = rt.verify_run(
        verify=verify_cmd,
        semantic=semantic,
        notarization=notarization,
        pipeline_manifest=pipeline_manifest,
        dry_run=dry_run,
        rules_path=rules_path,
        since=since,
        staged=staged,
        working_tree=working_tree,
        baseline=baseline,
        allowed_failures=list(allowed_failures) or None,
        async_mode=async_mode,
    )

    if result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Verify run passed[/bold green]")
    else:
        prompt = result.get("rejection_prompt") or result.get("agent_prompt")
        if prompt:
            console.print(prompt)
        else:
            for v in result.get("violations") or []:
                console.print(
                    f"[bold red]{v.get('type') or v.get('reason')}[/bold red] "
                    f"{v.get('file') or v.get('path')}: {v.get('message') or v.get('hint', '')}"
                )
            if result.get("error"):
                console.print(f"[bold red]{result['error']}[/bold red]")
    if not result.get("ok"):
        sys.exit(1)


@verify_group.command("notarization")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--staged", is_flag=True, help="Check git staged files (pre-commit).")
@click.option("--working-tree", is_flag=True, help="Check unstaged modifications.")
@click.option("--rebuild-index", is_flag=True, help="Rebuild notarized index from TrustChain ledger.")
@click.option("--json", "as_json", is_flag=True)
def verify_notarization_cmd(target_dir, staged, working_tree, rebuild_index, as_json):
    """Reject files changed without TrustChain notarization (enforcement mode)."""
    from apatch.workflows import verify_notarization_workspace

    if not staged and not working_tree:
        staged = True
    result = verify_notarization_workspace(
        target_dir,
        staged=staged,
        working_tree=working_tree,
        rebuild_index=rebuild_index,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ All checked files are notarized[/bold green]")
    else:
        prompt = result.get("rejection_prompt") or result.get("agent_prompt")
        if prompt:
            console.print(prompt)
        else:
            for v in result.get("violations") or []:
                console.print(
                    f"[bold red]{v.get('reason')}[/bold red] {v.get('path')}: {v.get('hint', '')}"
                )
    if not result.get("ok"):
        sys.exit(1)


@verify_group.command("semantic")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--rules", "rules_path", type=click.Path(dir_okay=False))
@click.option("--since", default="HEAD", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def verify_semantic_cmd(target_dir, rules_path, since, as_json):
    """Check routes, exports, events, OpenAPI paths not removed in git diff."""
    from apatch.runtime.runtime import MutationRuntime

    try:
        result = MutationRuntime(target_dir).verify_semantic(
            rules_path=rules_path, since=since
        )
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)
    if result.get("error_type") == "RUNTIME_TRANSITION":
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        if result.get("hint"):
            console.print(f"[dim]{result['hint']}[/dim]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print("[bold green]✓ Semantic verify passed[/bold green]")
    else:
        for v in result.get("violations") or []:
            console.print(f"[bold red]{v.get('type')}[/bold red] {v.get('file')}: {v.get('message')}")
    if not result.get("ok"):
        sys.exit(1)


@click.group("phase")
def phase_group():
    """Manage and run high-level codebase refactoring phases ✂️🚀"""
    pass

@phase_group.command("run")
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False), help="Path to JSON/YAML phase manifest.")
@click.option("--file", "file_path", required=False, type=click.Path(exists=True, dir_okay=False), help="Target source file (optional if manifest.files[] is set).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False), help="Workspace root for multi-file manifests.")
@click.option(
    "--profile",
    type=click.Choice(["cpp", "frontend", "generic"]),
    default="cpp",
    help="cpp: --to-native auto; frontend: TS strip + optional --to-module; generic: strip+report only.",
)
@click.option("--native-out-dir", type=click.Path(file_okay=False), help="Directory to export C++ native modules into.")
@click.option("--module-out-dir", type=click.Path(file_okay=False), help="Directory for generated TS modules (--to-module).")
@click.option("--out-dir", default="extracted", type=click.Path(file_okay=False), help="Directory to export raw stubs and report.")
@click.option("--to-module", type=click.Choice(["hook", "component", "util"]), help="Generate TS/React module (frontend profile).")
@click.option("--verify", "verify_cmd", help="Build/test command; rollback entire phase on failure.")
@click.option("--strict", is_flag=True, help="Aborts with exit 1 if C++ native converter detects blockers.")
@click.option("--emit-wiring", type=click.Path(dir_okay=False), help="Emit Markdown wiring instructions.")
@click.option("--auto-wire", is_flag=True, help="Auto-insert parent import and wire hook after --to-module.")
@click.option("--emit-barrel", type=click.Path(dir_okay=False), help="Append barrel re-exports after module generation.")
@click.pass_context
def phase_run_cmd(
    ctx,
    manifest,
    file_path,
    target_dir,
    profile,
    native_out_dir,
    module_out_dir,
    out_dir,
    to_module,
    verify_cmd,
    strict,
    emit_wiring,
    auto_wire,
    emit_barrel,
):
    """Orchestrate strip + optional native/module conversion + verify + rollback."""
    label = os.path.basename(file_path) if file_path else os.path.basename(manifest)
    console.print(f"[bold blue]🏁 Phase run[/bold blue] [{profile}] {label}")
    result = phase_run(
        file_path,
        manifest,
        profile=profile,
        out_dir=out_dir,
        native_out_dir=native_out_dir,
        module_out_dir=module_out_dir,
        to_module=to_module,
        verify_cmd=verify_cmd,
        strict=strict,
        emit_wiring=emit_wiring,
        auto_wire=auto_wire,
        emit_barrel=emit_barrel,
        target_dir=target_dir,
    )
    if not result.get("ok"):
        for err in result.get("errors") or []:
            console.print(f"[bold red]{err}[/bold red]")
        sys.exit(result.get("exit_code") or 1)
    if verify_cmd:
        console.print("[bold green]✓ Build Verification Passed Successfully![/bold green]")
    console.print("\n[bold green]🎉 PHASE EXECUTION COMPLETED SUCCESSFULLY![/bold green]")

_mutation_group = click.Group(
    "mutation",
    help="Domain mutation verbs (aliases: plan, apply, strip).",
)
for _mutation_cmd in ("plan", "apply", "strip", "replay"):
    _src = cli.commands[_mutation_cmd]
    _mutation_group.add_command(
        click.Command(
            name=_mutation_cmd,
            callback=_src.callback,
            params=_src.params,
            help=_src.help,
            context_settings=_src.context_settings,
        ),
        name=_mutation_cmd,
    )


@click.command("gc")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", "dry_run", is_flag=True, default=True, help="Report only (default; no deletions).")
@click.option("--json", "as_json", is_flag=True, help="Emit RFP-016 §4.2 JSON.")
@click.option("--safe", is_flag=True, help="Delete gc_allowed EPHEMERAL artifacts.")
@click.option("--rotate", is_flag=True, help="Rotate HISTORY overflow artifacts.")
@click.option("--reconcile", is_flag=True, help="Register inferred artifacts into registry.")
def gc_cmd(target_dir, dry_run, as_json, safe, rotate, reconcile):
    """Artifact lifecycle report — default dry-run, no filesystem changes."""
    from apatch.gc import run_gc

    mode = (
        "safe"
        if safe
        else ("rotate" if rotate else ("reconcile" if reconcile else "report"))
    )
    effective_dry = mode == "report" or (dry_run and mode not in ("safe", "rotate", "reconcile"))
    result = run_gc(target_dir, mode=mode, dry_run=effective_dry)
    if not result.get("ok"):
        if as_json:
            click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            console.print(f"[red]{result.get('error')}[/red]")
        sys.exit(1)
    report = result.get("report") or result
    if as_json:
        if result.get("mode") in ("safe", "rotate"):
            payload = {
                k: result[k]
                for k in (
                    "ok",
                    "mode",
                    "deleted_count",
                    "reclaimed_bytes",
                    "deleted",
                    "skipped",
                )
                if k in result
            }
        else:
            payload = {k: report[k] for k in report if k in (
                "status", "artifact_count", "classified", "issues", "size_reclaimable",
                "recommendation", "orphan_count", "unclassified_count", "inferred_count", "layout",
            )}
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]status[/bold]: {report.get('status')}")
    console.print(f"[bold]artifacts[/bold]: {report.get('artifact_count')}")
    console.print(f"[bold]reclaimable[/bold]: {report.get('size_reclaimable')}")
    console.print(f"[bold]recommendation[/bold]: {report.get('recommendation')}")
    if report.get("issues"):
        console.print(f"[bold]issues[/bold]: {len(report['issues'])}")


@click.command("build-diagnose")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--verify", default=None, help="Build command to run when --log-file omitted.")
@click.option("--log-file", default=None, type=click.Path(exists=True, dir_okay=False), help="Parse existing compiler log.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON (RFP-018).")
@click.option("--no-artifacts", is_flag=True, help="Skip writing .apatch/build_log.json and diagnostics.json.")
def build_diagnose_cmd(target_dir, verify, log_file, as_json, no_artifacts):
    """Compiler feedback loop — structured diagnostics (RFP-018)."""
    from apatch.workflows import build_diagnose_workspace

    log_text = None
    if log_file:
        with open(log_file, encoding="utf-8", errors="replace") as fh:
            log_text = fh.read()
    result = build_diagnose_workspace(
        target_dir,
        verify=verify,
        log_text=log_text,
        write_artifacts=not no_artifacts,
    )
    if not result.get("ok"):
        if as_json:
            click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            console.print(f"[red]{result.get('error')}[/red]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold]build_ok[/bold]: {result.get('build_ok')}")
        console.print(f"[bold]diagnostics[/bold]: {result.get('diagnostic_count')}")
        console.print(f"[dim]{result.get('agent_next')}[/dim]")


@click.group("remote")
def remote_group():
    """Remote workspace onboarding and policy helpers."""
    pass


@remote_group.command("init")
@click.option("--alias", required=True, help="Opaque target alias, e.g. search-example.")
@click.option("--host", required=True, help="SSH host alias from local ssh config.")
@click.option("--path", "remote_path", required=True, help="Absolute remote git workspace path.")
@click.option("--display", default=None, help="Human label shown in redacted responses.")
@click.option("--health-url", default=None, help="Candidate service health URL, e.g. http://127.0.0.1:8080/health.")
@click.option("--service", default=None, help="Service alias to attach to --health-url, default api.")
@click.option("--service-kind", default="http", show_default=True, help="Service kind metadata.")
@click.option("--service-unit", default=None, help="systemd unit name for restart/status/logs.")
@click.option("--compose-file", default=None, help="docker compose file path for lifecycle actions.")
@click.option("--compose-service", default=None, help="docker compose service name for lifecycle actions.")
@click.option("--source-handoff", is_flag=True, help="Allow opaque local source handoff to this remote alias.")
@click.option("--source-root", "source_roots", multiple=True, help="Allowed local source root for source handoff; default --target-dir.")
@click.option("--python", "python_path", default=None, help="Remote Python executable.")
@click.option("--runtime-path", default=None, help="Absolute remote path containing apatch runtime source for PYTHONPATH.")
@click.option("--ssh-arg", "ssh_args", multiple=True, help="SSH argv entry; repeat for each token.")
@click.option("--timeout-sec", default=600, show_default=True, type=int)
@click.option("--health-timeout-sec", default=60, show_default=True, type=int)
@click.option("--allowed-host", "allowed_hosts", multiple=True, help="Allowed host pattern; default exact --host.")
@click.option("--allowed-root", "allowed_roots", multiple=True, help="Allowed remote root pattern; default exact --path.")
@click.option("--target-dir", default=".", type=click.Path(file_okay=False))
@click.option("--out", "out_path", default=None, type=click.Path(dir_okay=False), help="Policy output path; default .apatch/remote.json.")
@click.option("--force", is_flag=True, help="Replace an existing alias.")
@click.option("--json", "as_json", is_flag=True)
def remote_init_cmd(
    alias,
    host,
    remote_path,
    display,
    health_url,
    service,
    service_kind,
    service_unit,
    compose_file,
    compose_service,
    source_handoff,
    source_roots,
    python_path,
    runtime_path,
    ssh_args,
    timeout_sec,
    health_timeout_sec,
    allowed_hosts,
    allowed_roots,
    target_dir,
    out_path,
    force,
    as_json,
):
    """Create a remote alias policy from a few human inputs."""
    from apatch.remote.errors import RemoteTaskError
    from apatch.remote.onboarding import build_remote_config, write_remote_config

    try:
        config = build_remote_config(
            alias=alias,
            host=host,
            path=remote_path,
            display=display,
            health_url=health_url,
            service=service,
            service_kind=service_kind,
            service_unit=service_unit,
            compose_file=compose_file,
            compose_service=compose_service,
            source_handoff=source_handoff,
            source_roots=list(source_roots) or ([target_dir] if source_handoff else None),
            python=python_path,
            runtime_path=runtime_path,
            ssh_args=list(ssh_args) or None,
            timeout_sec=timeout_sec,
            health_timeout_sec=health_timeout_sec,
            allowed_hosts=list(allowed_hosts) or None,
            allowed_roots=list(allowed_roots) or None,
        )
        result = write_remote_config(
            target_dir=target_dir,
            config=config,
            out_path=out_path,
            force=force,
        )
        result["config"] = config
    except RemoteTaskError as exc:
        result = exc.to_result()
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(f"[bold green]Remote alias[/bold green] {alias} → {result.get('path')}")
        console.print("[dim]Next: apatch remote validate --alias {} --target-dir {}[/dim]".format(alias, target_dir))
        if health_url:
            console.print(f"[dim]Healthcheck candidate: {health_url}[/dim]")
    else:
        console.print(f"[bold red]{result.get('message') or result.get('error_type')}[/bold red]")
        if result.get("recommended_action"):
            console.print(f"[dim]{result['recommended_action']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@remote_group.command("handoff")
@click.option("--alias", required=True, help="Remote alias from .apatch/remote.json.")
@click.option("--source", "source_dir", default=".", type=click.Path(file_okay=False), help="Local source directory to hand off.")
@click.option("--mode", default="workspace_overlay", show_default=True, help="Policy-approved handoff mode.")
@click.option("--target-dir", default=".", type=click.Path(file_okay=False))
@click.option("--execute", is_flag=True, help="Execute internal archive handoff. Default is plan-only.")
@click.option("--json", "as_json", is_flag=True)
def remote_handoff_cmd(alias, source_dir, mode, target_dir, execute, as_json):
    """Plan or execute an opaque local-source handoff to a remote alias."""
    from apatch.remote.errors import RemoteTaskError
    from apatch.remote.handoff import execute_source_handoff, plan_source_handoff

    try:
        if execute:
            result = execute_source_handoff(
                alias=alias,
                source_dir=source_dir,
                policy_root=target_dir,
                mode=mode,
            )
        else:
            result = plan_source_handoff(
                alias=alias,
                source_dir=source_dir,
                policy_root=target_dir,
                mode=mode,
            )
    except RemoteTaskError as exc:
        result = exc.to_result()
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        mode_text = "executed" if execute else "planned"
        console.print("[bold green]Remote source handoff {}[/bold green] {}".format(mode_text, alias))
        console.print("[dim]operation: {}[/dim]".format(result.get("operation")))
    else:
        console.print(f"[bold red]{result.get('message') or result.get('error_type')}[/bold red]")
        if result.get("recommended_action"):
            console.print(f"[dim]{result['recommended_action']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@remote_group.command("service")
@click.option("--alias", required=True, help="Remote alias from .apatch/remote.json.")
@click.option("--service", "service_name", required=True, help="Configured service alias.")
@click.option(
    "--action",
    required=True,
    type=click.Choice(["status", "restart", "logs", "healthcheck"]),
    help="Lifecycle action to plan or execute.",
)
@click.option("--lines", default=80, show_default=True, type=int, help="Log lines for logs action.")
@click.option("--target-dir", default=".", type=click.Path(file_okay=False))
@click.option("--execute", is_flag=True, help="Execute over SSH. Default is plan-only.")
@click.option("--json", "as_json", is_flag=True)
def remote_service_cmd(alias, service_name, action, lines, target_dir, execute, as_json):
    """Plan or execute an approved remote service lifecycle action."""
    from apatch.remote.errors import RemoteTaskError
    from apatch.remote.services import execute_service_action, plan_service_action

    try:
        if execute:
            result = execute_service_action(
                alias=alias,
                service=service_name,
                action=action,
                policy_root=target_dir,
                lines=lines,
            )
        else:
            result = plan_service_action(
                alias=alias,
                service=service_name,
                action=action,
                policy_root=target_dir,
                lines=lines,
            )
    except RemoteTaskError as exc:
        result = exc.to_result()

    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        mode = "executed" if execute else "planned"
        console.print(
            "[bold green]Remote service {}[/bold green] {}.{} {}".format(
                mode,
                alias,
                service_name,
                action,
            )
        )
        if result.get("operation") == "command":
            console.print("[dim]argv: {}[/dim]".format(result.get("argv")))
        elif result.get("operation") == "http_healthcheck":
            console.print("[dim]healthcheck: {}[/dim]".format(result.get("url")))
        for stream in ("stdout", "stderr", "body"):
            if result.get(stream):
                console.print("[bold]{}[/bold]\n{}".format(stream, result[stream]))
    else:
        console.print(f"[bold red]{result.get('message') or result.get('error_type')}[/bold red]")
        if result.get("recommended_action"):
            console.print(f"[dim]{result['recommended_action']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@remote_group.command("validate")
@click.option("--alias", required=True, help="Alias to validate.")
@click.option("--target-dir", default=".", type=click.Path(file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def remote_validate_cmd(alias, target_dir, as_json):
    """Validate that a remote alias resolves under policy."""
    from apatch.remote.errors import RemoteTaskError
    from apatch.remote.onboarding import validate_remote_config

    try:
        result = validate_remote_config(target_dir=target_dir, alias=alias)
    except RemoteTaskError as exc:
        result = exc.to_result()
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        console.print(f"[bold green]Remote alias valid[/bold green] {alias}")
        console.print(f"[dim]{result.get('target')}[/dim]")
    else:
        console.print(f"[bold red]{result.get('message') or result.get('error_type')}[/bold red]")
        if result.get("recommended_action"):
            console.print(f"[dim]{result['recommended_action']}[/dim]")
    if not result.get("ok"):
        sys.exit(1)


@cli.group("probe")
def probe_group():
    """Perturb and watch the delta \u2014 unified falsify / regress / ratify (RFP-005).

    One primitive: apply a perturbation, re-measure a signal, judge the delta against a
    polarity. ``falsify`` is must_diverge (the gate MUST react to a corrupted artifact);
    ``regress`` and ``ratify`` are must_hold (the gate MUST stay green vs a corpus / over
    time)."""
    pass


@probe_group.command("falsify")
@click.option("--verify", required=True, help="Gate command (shell) that must go RED when the guarded files break.")
@click.option("--files", required=True, help="Comma-separated files this gate guards.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def probe_falsify_cmd(verify, files, target_dir, as_json):
    """must_diverge: corrupt the guarded files; the gate MUST go RED. Green = FALSE gate."""
    from apatch.probe import falsify
    flist = [f.strip() for f in files.split(",") if f.strip()]
    res = falsify(target_dir, verify, flist, record=True)
    if as_json:
        click.echo(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0 if res.get("verdict") == "real" else 1)
    v = res.get("verdict")
    if v == "real":
        console.print(f"[bold green]\u2713 real gate[/bold green] \u2014 corrupting {flist} made verify RED, restored \u2192 GREEN.")
        if not res.get("restored_ok", True):
            console.print(f"[bold red]WARNING:[/bold red] verify not GREEN after restore (rc {res.get('restored_rc')}) \u2014 check {flist}")
    elif v == "false":
        console.print(f"[bold red]\u2717 FALSE gate[/bold red] \u2014 corrupting {flist} left verify GREEN. The gate does not test what it guards. Fix it.")
        sys.exit(1)
    else:
        console.print(f"[yellow]{v}[/yellow]: {res.get('note') or res.get('missing') or res}")
        sys.exit(1)


@probe_group.command("regress")
@click.option("--verify", required=True, help="Gate command (shell).")
@click.option("--baseline-failures", default="", help="Comma-separated known-failing ids (default: .apatch/verify_baseline.json).")
@click.option("--allow", "allowed", default="", help="Comma-separated failing ids/substrings to tolerate.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def probe_regress_cmd(verify, baseline_failures, allowed, target_dir, as_json):
    """must_hold: the current tree must introduce NO new failures vs a recorded corpus."""
    from apatch.probe import regress
    base = [f.strip() for f in baseline_failures.split(",") if f.strip()]
    allow = [f.strip() for f in allowed.split(",") if f.strip()]
    res = regress(target_dir, verify, baseline_failures=base, allowed_failures=allow)
    if as_json:
        click.echo(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0 if res.get("verdict") == "stable" else 1)
    if res.get("verdict") == "stable":
        console.print(f"[bold green]\u2713 stable[/bold green] \u2014 no new failures vs baseline ({res.get('baseline_count')} known).")
    else:
        nf = res.get("new_failures") or []
        console.print(f"[bold red]\u2717 regressed[/bold red] \u2014 {len(nf)} NEW failure(s) vs baseline: {nf}")
        sys.exit(1)


@probe_group.command("ratify")
@click.option("--verify", required=True, help="Attested gate command (shell) that must STILL pass now.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def probe_ratify_cmd(verify, target_dir, as_json):
    """must_hold: an attested-green gate must still pass now. Red now = STALE attestation."""
    from apatch.probe import ratify
    res = ratify(target_dir, verify)
    if as_json:
        click.echo(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0 if res.get("verdict") == "ratified" else 1)
    if res.get("verdict") == "ratified":
        console.print("[bold green]\u2713 ratified[/bold green] \u2014 the attested gate is still GREEN.")
    else:
        console.print(f"[bold red]\u2717 stale[/bold red] \u2014 the attested gate is now RED (rc {res.get('current_rc')}). Re-verify or re-attest.")
        sys.exit(1)


@cli.group("scip")
def scip_group():
    """SCIP cross-file reference impact (RFP-033 Phase 2) \u2014 advisory, never blocks."""
    pass


@scip_group.command("index")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def scip_index_cmd(target_dir, as_json):
    """Produce .apatch/scip/index.scip via scip-python (out of band; graceful if absent)."""
    from apatch.scip_producer import produce_scip_index

    res = produce_scip_index(target_dir)
    if as_json:
        click.echo(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0 if res.get("ok") else 1)
    if res.get("ok"):
        console.print(f"[bold green]\u2713 .scip produced[/bold green] \u2192 {res.get('path')}")
    else:
        console.print(f"[yellow]{res.get('note')}[/yellow]")
        sys.exit(1)


@scip_group.command("impact")
@click.option("--since", default="HEAD", help="Git ref to diff from for changed symbols.")
@click.option("--spec", default="", help="Limit attested-requirement anchors to one SPEC id.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def scip_impact_cmd(since, spec, target_dir, as_json):
    """Advisory: which ATTESTED requirements reference a symbol changed since <since>, via
    the ingested .scip graph. Never marks stale or blocks."""
    from apatch.scip_producer import scip_impact_workspace

    res = scip_impact_workspace(target_dir, since=since, spec=spec or None)
    if as_json:
        click.echo(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0)
    if not res.get("model_present"):
        console.print(f"[dim]{res.get('note')}[/dim]")
        return
    imp = res.get("impact") or []
    if not imp:
        console.print(f"[green]no cross-file impact[/green] on attested requirements ({len(res.get('changed_symbols') or [])} changed symbols)")
        return
    console.print(f"[bold yellow]advisory:[/bold yellow] {len(imp)} attested requirement(s) reference a changed symbol")
    for w in imp:
        console.print(f"  [yellow]{w['requirement']}[/yellow] \u2014 {w['via_symbol']} references {w['impacted_by']}")


@cli.group("reality")
def reality_group():
    """Observed-reality ledger \u2014 records the spec must discharge (RFP-005)."""
    pass


@reality_group.command("add")
@click.option("--summary", required=True, help="What was observed (bug/incident/feedback/finding).")
@click.option("--id", "rec_id", default=None, help="Stable id (default: content hash).")
@click.option("--source", default="", help="Where it came from (tracker/telemetry/support/...).")
@click.option("--kind", default="observation", help="bug|incident|feedback|finding|...")
@click.option("--status", default="open", help="open|closed_wontfix (wontfix drops the obligation).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def reality_add_cmd(summary, rec_id, source, kind, status, target_dir):
    """Append an observed-reality record to the ledger (.apatch/reality.jsonl)."""
    from apatch.reality import add_reality_record

    rid = add_reality_record(target_dir, summary=summary, id=rec_id, source=source, kind=kind, status=status)
    console.print(f"[bold green]\u2713 reality[/bold green] {rid}: {summary}")


@reality_group.command("status")
@click.option("--spec", default=None, help="Limit to one SPEC id (default: all docs/specs/SPEC-*.md).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def reality_status_cmd(spec, target_dir, as_json):
    """Coverage of OBSERVED REALITY: which records are discharged by a green gate and
    which are undischarged debt. Reality is the source of truth; the spec is accountable."""
    from apatch.reality import reality_status_workspace

    cov = reality_status_workspace(target_dir, spec)
    records = cov.get("records", [])
    if as_json:
        click.echo(json.dumps(cov, indent=2, ensure_ascii=False))
        sys.exit(0 if cov["ok"] else 1)
    c = cov["counts"]
    console.print(f"[bold]reality coverage[/bold]: {c['covered']} covered \u00b7 {c['pending']} pending \u00b7 [bold red]{c['uncovered']} uncovered[/bold red] (of {cov['total']})")
    recmap = {r['id']: r for r in records}
    for rid in cov["uncovered"]:
        s = recmap.get(rid, {})
        console.print(f"  [red]uncovered[/red] {rid}: {s.get('summary','')[:80]}  [dim]({s.get('source','') or s.get('kind','')})[/dim]")
    for rid in cov["pending"]:
        s = recmap.get(rid, {})
        console.print(f"  [yellow]pending[/yellow]   {rid}: {s.get('summary','')[:80]}  [dim](claimed, gate not yet attested)[/dim]")
    if not cov["ok"]:
        sys.exit(1)


@click.group("lane")
def lane_group():
    """Isolated worktree lanes for parallel agents (RFP-005)."""
    pass


@lane_group.command("new")
@click.argument("lane_id")
@click.option("--branch", default=None, help="Branch for the lane (default work/<lane_id>).")
@click.option("--parent", "lanes_parent", default=None, help="Where worktrees go (default ../<repo>-lanes).")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def lane_new_cmd(lane_id, branch, lanes_parent, target_dir, as_json):
    """Create an isolated git-worktree lane for parallel agent work.

    Each lane is its own working tree + branch + .apatch state, so a second agent
    never shares the first agent's working tree or write-lease."""
    from apatch.worktree_lane import create_worktree_lane

    res = create_worktree_lane(target_dir, lane_id, branch=branch, lanes_parent=lanes_parent)
    if as_json:
        click.echo(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold green]\u2713 lane[/bold green] '{res['lane_id']}' \u2192 {res['path']}  (branch {res['branch']})")
        console.print("[dim]Open an agent there \u2014 isolated lane (own working tree, branch, lease).[/dim]")


@lane_group.command("rm")
@click.argument("lane_id")
@click.option("--parent", "lanes_parent", default=None)
@click.option("--force", is_flag=True, default=False)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def lane_rm_cmd(lane_id, lanes_parent, force, target_dir):
    """Remove a worktree lane (git worktree remove; the branch is kept)."""
    from apatch.worktree_lane import remove_worktree_lane

    if remove_worktree_lane(target_dir, lane_id, lanes_parent=lanes_parent, force=force):
        console.print(f"[bold green]\u2713 removed lane[/bold green] '{lane_id}'")
    else:
        console.print(f"[bold red]\u2717 could not remove lane[/bold red] '{lane_id}' (uncommitted changes? try --force)")
        sys.exit(1)


@click.group("layout")
def layout_group():
    """Structured .apatch layout (RFP-016 Phase 5)."""
    pass


@layout_group.command("migrate")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, default=False, help="Plan only; no filesystem changes.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON plan/result.")
def layout_migrate_cmd(target_dir, dry_run, as_json):
    """Migrate flat `.apatch/` to structured layout (not GC)."""
    from apatch.layout_migrate import run_layout_migrate

    result = run_layout_migrate(target_dir, dry_run=dry_run)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold]layout[/bold]: {result.get('layout_before')} → {result.get('layout_after')}")
        console.print(f"[bold]moves[/bold]: {len(result.get('moves') or [])}")
        if not dry_run:
            console.print(f"[bold]moved[/bold]: {result.get('moved_count', 0)}")


cli.add_command(lane_group)
cli.add_command(layout_group)
cli.add_command(remote_group)
cli.add_command(gc_cmd)
cli.add_command(build_diagnose_cmd)
cli.add_command(session_group)

@click.group("rfp")
def rfp_group():
    """RFP→SPEC traceability (RFP-023): lint Acceptance tables and coverage gaps."""
    pass


@rfp_group.command("lint")
@click.option("--rfp", default=None, help="RFP id (docs/RFP-NNN-*.md).")
@click.option("--rfp-path", default=None, help="Path to RFP markdown.")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def rfp_lint_cmd(rfp, rfp_path, target_dir, as_json):
    """Validate RFP Acceptance table (RFP-023)."""
    from apatch.rfp_coverage import rfp_lint_workspace

    result = rfp_lint_workspace(target_dir, rfp=rfp, rfp_path=rfp_path)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok") or not result.get("passed"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    c = result.get("counts") or {}
    verdict = "[bold green]PASS[/bold green]" if result.get("passed") else "[bold red]FAIL[/bold red]"
    console.print(
        f"{verdict} {result.get('rfp')} — acceptance={result.get('acceptance_count', 0)} "
        f"errors={c.get('errors', 0)} warnings={c.get('warnings', 0)}"
    )
    for e in result.get("errors") or []:
        console.print(f"  [bold red]error[/bold red] [{e['code']}] {e['message']}")
    if not result.get("passed"):
        sys.exit(1)


@rfp_group.command("coverage")
@click.option("--rfp", default=None, help="RFP id.")
@click.option("--spec", default=None, help="Single SPEC id.")
@click.option(
    "--specs",
    default=None,
    help="Comma-separated SPEC ids — multi-spec aggregate (RFP-023).",
)
@click.option("--rfp-path", default=None)
@click.option("--spec-path", default=None)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def rfp_coverage_cmd(rfp, spec, specs, rfp_path, spec_path, target_dir, as_json):
    """Check RFP Acceptance rows vs SPEC ## RFP traceability."""
    from apatch.rfp_coverage import rfp_spec_coverage_workspace

    result = rfp_spec_coverage_workspace(
        target_dir,
        rfp=rfp,
        spec=spec,
        specs=specs,
        rfp_path=rfp_path,
        spec_path=spec_path,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("ok") or not result.get("passed"):
            sys.exit(1)
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    verdict = "[bold green]PASS[/bold green]" if result.get("passed") else "[bold red]FAIL[/bold red]"
    if result.get("mode") == "aggregate":
        spec_label = ", ".join(result.get("specs") or [])
        console.print(f"{verdict} {result.get('rfp')} → [{spec_label}] (aggregate)")
    else:
        console.print(f"{verdict} {result.get('rfp')} → {result.get('spec')}")
    for g in result.get("gaps") or []:
        console.print(f"  [bold red]gap[/bold red] {g.get('id')}: {g.get('reason')}")
    for e in result.get("errors") or []:
        console.print(f"  [bold red]error[/bold red] [{e.get('code')}] {e.get('message')}")
    if not result.get("passed"):
        sys.exit(1)



@cli.command("timesheet")
@click.option("--by", default="identity",
              help="Group by identity,project,spec,day (comma-combinable).")
@click.option("--since", default=None, help="Lower time bound (ISO or epoch).")
@click.option("--until", default=None, help="Upper time bound (ISO or epoch).")
@click.option("--agent", default=None, help="Filter to one identity key_id.")
@click.option("--project", "project_id", default=None, help="Filter to one project id.")
@click.option("--idle-gap", "idle_gap", default=None, type=int,
              help="Work-block split: gaps longer than N min are breaks (default 30).")
@click.option("--ramp-up", "ramp_up", default=None, type=int,
              help="Warm-up min before the first op in each work block (default 15).")
@click.option("--format", "fmt", type=click.Choice(["md", "json"]), default="md")
@click.option("--verify", "do_verify", is_flag=True,
              help="Re-derive each event from the ledger; flag tamper/drift.")
@click.option("--include-audit", "include_audit", is_flag=True,
              help="Include non-attested (legacy/audit) events.")
@click.option("--target-dir", default=".",
              type=click.Path(exists=True, file_okay=False))
def timesheet_cmd(by, since, until, agent, project_id, idle_gap, ramp_up, fmt,
                  do_verify, include_audit, target_dir):
    """Per-identity, cross-project contribution timesheet (RFP-026)."""
    import json as _json
    from apatch.timesheet import run_timesheet

    out = run_timesheet(
        target_dir=target_dir, by=by, since=since, until=until, agent=agent,
        project=project_id, idle_gap=idle_gap, ramp_up=ramp_up, fmt=fmt,
        do_verify=do_verify, include_audit=include_audit,
    )
    if isinstance(out, str):
        click.echo(out)
    else:
        click.echo(_json.dumps(out, indent=2, ensure_ascii=False))


@click.group("contributions")
def contributions_group():
    """ContributionEvent delivery helpers for Avatar and HC Tracker."""
    pass


@contributions_group.command("sync")
@click.option("--platform-url", default=None, help="TrustChain URL, default APATCH_PLATFORM_URL.")
@click.option("--token", default=None, help="Avatar sync token, default APATCH_AVATAR_TOKEN.")
@click.option("--store-dir", default=None, type=click.Path(file_okay=False), help="Contribution store override.")
@click.option("--receipt-dir", default=None, type=click.Path(file_okay=False), help="Local sync receipt directory.")
@click.option("--limit", default=100, show_default=True, type=int, help="Max events per upload batch.")
@click.option("--timeout", default=15.0, show_default=True, type=float, help="HTTP timeout seconds.")
@click.option("--dry-run", is_flag=True, help="Show pending count without uploading.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def contributions_sync_cmd(platform_url, token, store_dir, receipt_dir, limit, timeout, dry_run, as_json):
    """Upload local signed ContributionEvents to trust-chain.ai Avatar."""
    from apatch.contribution_export import sync_to_trustchain_avatar

    result = sync_to_trustchain_avatar(
        platform_url=platform_url,
        token=token,
        store_dir=store_dir,
        receipt_dir=receipt_dir,
        limit=limit,
        timeout=timeout,
        dry_run=dry_run,
    )
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "ok" if result.get("ok") else "failed"
        click.echo(
            "Avatar contribution sync {status}: pending={pending} attempted={attempted} "
            "accepted={accepted} duplicates={duplicates}".format(
                status=status,
                pending=result.get("pending", 0),
                attempted=result.get("attempted", 0),
                accepted=result.get("accepted", 0),
                duplicates=result.get("duplicates", 0),
            )
        )
        for error in result.get("errors") or []:
            click.echo(f"  error: {error}")
    if not result.get("ok"):
        sys.exit(1)


@click.group("asset")
def asset_group():
    """Avatar Compiler — portable asset_summary over governed work (RFP-025)."""


@asset_group.command("summary")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".",
              type=click.Path(exists=True, file_okay=False))
def asset_summary_cmd(as_json, target_dir):
    """Deterministic asset_summary: attested artifacts, specs, methodology (RFP-025)."""
    import json as _json
    from apatch.avatar_compiler import build_asset_summary

    s = build_asset_summary(target_dir)
    if as_json:
        click.echo(_json.dumps(s, indent=2, ensure_ascii=False))
    else:
        tags = ", ".join(s["methodology_tags"]) or "-"
        click.echo(
            f"Avatar asset_summary: {s['artifact_count']} attested artifacts, "
            f"{s['spec_count']} specs; methodology: {tags}"
        )


@click.group("avatar")
def avatar_group():
    """Semantic engine — episodes, capabilities, evidence export (RFP-037)."""


@avatar_group.command("episodes")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".",
              type=click.Path(exists=True, file_okay=False))
def avatar_episodes_cmd(as_json, target_dir):
    """Signed WorkEpisodes with fail-closed capability eligibility."""
    import json as _json
    from apatch.episode import build_episodes

    eps = build_episodes(target_dir)
    if as_json:
        click.echo(_json.dumps(eps, indent=2, ensure_ascii=False))
        return
    qualified = sum(1 for e in eps if e["eligible_for_capability"])
    click.echo(f"Episodes: {len(eps)} total, {qualified} capability-eligible")
    for ep in eps:
        mark = "+" if ep["eligible_for_capability"] else "-"
        reasons = "" if ep["eligible_for_capability"] else f"  [{', '.join(ep['exclusion_reasons'])}]"
        click.echo(f" {mark} {ep['session_id']}  {ep['task']['label'][:60]}{reasons}")


@avatar_group.command("capabilities")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".",
              type=click.Path(exists=True, file_okay=False))
def avatar_capabilities_cmd(as_json, target_dir):
    """Capability estimates with evidence, intervals and uncertainty."""
    import json as _json
    from apatch.capability import build_capabilities

    caps = build_capabilities(target_dir)
    if as_json:
        click.echo(_json.dumps(caps, indent=2, ensure_ascii=False))
        return
    if not caps:
        click.echo("No capability estimates — inspect avatar episodes for evidence gaps.")
        return
    for cap in caps:
        success = cap["success_estimate"]
        observations = cap["observations"]
        refs = ",".join(cap["taxonomy_refs"][:2])
        click.echo(
            f" {refs}: {cap['status']}, observed={success['mean']} "
            f"[{success['lower']}, {success['upper']}] (n={observations['attempted']}), "
            f"{cap['recency']['state']}, "
            f"uncertainty {cap['uncertainty']['level']}"
            + (f" [{', '.join(cap['uncertainty']['reasons'])}]" if cap['uncertainty']['reasons'] else "")
        )


@avatar_group.command("evidence-export")
@click.option("--out", "out_path", default="-",
              help="Output file for the bundle JSON; '-' prints to stdout.")
@click.option("--verify", "do_verify", is_flag=True,
              help="Re-derive the freshly built bundle from the raw ledger and fail on drift.")
@click.option("--target-dir", default=".",
              type=click.Path(exists=True, file_okay=False))
def avatar_evidence_export_cmd(out_path, do_verify, target_dir):
    """Signed, money-free capability_evidence bundle for HC Capital (A37-H)."""
    import json as _json
    from apatch.avatar_evidence import build_evidence_bundle, verify_evidence_bundle

    bundle = build_evidence_bundle(target_dir)
    if do_verify:
        check = verify_evidence_bundle(bundle, target_dir)
        if not check["ok"]:
            click.echo(_json.dumps(check, indent=2, ensure_ascii=False))
            raise SystemExit(1)
    payload = _json.dumps(bundle, indent=2, ensure_ascii=False)
    if out_path == "-":
        click.echo(payload)
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        click.echo(
            f"capability_evidence: {len(bundle['episodes'])} episodes, "
            f"{len(bundle['capability_estimates'])} estimates -> {out_path}"
        )


@avatar_group.command("sync")
@click.option(
    "--url",
    "base_url",
    default=None,
    help="HC Tracker base URL; defaults to APATCH_HC_TRACKER_URL.",
)
@click.option(
    "--service-token",
    default=None,
    envvar="APATCH_HC_SERVICE_TOKEN",
    hide_input=True,
    help=(
        "Break-glass service-token override; normal internal sync resolves "
        "a purpose-bound TrustChain Secrets binding."
    ),
)
@click.option("--timeout", default=10.0, show_default=True, type=float)
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".",
              type=click.Path(exists=True, file_okay=False))
def avatar_sync_cmd(base_url, service_token, timeout, as_json, target_dir):
    """Durably queue and deliver CapabilityEvidence to HC Tracker."""
    import json as _json
    from apatch.avatar_delivery import sync_avatar_state

    result = sync_avatar_state(
        target_dir,
        base_url=base_url,
        service_token=service_token,
        timeout=timeout,
    )
    if result.get("status") == "credential_unavailable":
        if as_json:
            click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
        else:
            click.echo(
                "Avatar sync credential unavailable "
                f"({result['error_code']}); durable outboxes were preserved.",
                err=True,
            )
        raise SystemExit(1)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        delivery = result["evidence"]["delivery"]
        contribution_delivery = result["contributions"]["delivery"]
        outcome_delivery = result["outcomes"]
        click.echo(
            f"Avatar outcomes {outcome_delivery['status']}: "
            f"received={outcome_delivery['received']}, stored={outcome_delivery['stored']}; "
            f"evidence {delivery['status']}: "
            f"delivered={delivery['delivered']}, pending={delivery['pending']}; "
            f"timeline {contribution_delivery['status']}: "
            f"delivered={contribution_delivery['delivered']}, "
            f"pending={contribution_delivery['pending']}"
        )
    if not result["ok"]:
        raise SystemExit(1)




@click.group("work-assets")
def work_assets_group():
    """WorkAsset / Narabotka read-only surfaces (RFP-031)."""
    pass


@work_assets_group.command("list")
@click.option("--query", default="", help="Optional search query over WorkAsset metadata.")
@click.option("--limit", default=-1, show_default=True, type=int, help="Maximum rows; -1 means all.")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_list_cmd(query, limit, as_json, target_dir):
    """List deterministic WorkAsset candidates."""
    import json as _json
    from apatch.work_assets import list_work_assets

    result = list_work_assets(target_dir, query=query, limit=None if limit < 0 else limit)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
        return
    click.echo(f"WorkAssets: {result['asset_count']}")
    for asset in result.get("assets") or []:
        click.echo("- {asset_id} [{lifecycle}] {title}".format(**asset))


@work_assets_group.command("show")
@click.argument("asset_id")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_show_cmd(asset_id, as_json, target_dir):
    """Show one WorkAsset by id."""
    import json as _json
    from apatch.work_assets import show_work_asset

    result = show_work_asset(target_dir, asset_id=asset_id)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        asset = result["asset"]
        click.echo("{asset_id}: {title}".format(**asset))
        click.echo("specs: " + ", ".join(asset.get("spec_refs") or []))
    else:
        click.echo(result.get("error_type") or "WORK_ASSET_NOT_FOUND")
    if not result.get("ok"):
        sys.exit(1)


@work_assets_group.command("search")
@click.argument("query")
@click.option("--limit", default=10, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_search_cmd(query, limit, as_json, target_dir):
    """Search WorkAsset metadata."""
    import json as _json
    from apatch.work_assets import search_work_assets

    result = search_work_assets(target_dir, query=query, limit=limit)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
        return
    for row in result.get("results") or []:
        asset = row["asset"]
        click.echo("{score} {asset_id}: {title}".format(score=row["score"], **asset))



@work_assets_group.command("schema")
@click.option("--json", "as_json", is_flag=True)
def work_assets_schema_cmd(as_json):
    """Emit WorkAsset export JSON Schema."""
    import json as _json
    from apatch.work_assets import work_asset_export_schema

    result = work_asset_export_schema()
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        click.echo(f"{result['$id']} schema_version={result['properties']['schema_version']['const']}")

@work_assets_group.command("export")
@click.option("--query", default="", help="Optional search query to filter exported metadata.")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_export_cmd(query, as_json, target_dir):
    """Export metadata-only WorkAsset bundle."""
    import json as _json
    from apatch.work_assets import export_work_assets

    result = export_work_assets(target_dir, query=query)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        click.echo(f"WorkAsset export: {result['asset_count']} assets, content_safe={result['content_safe']}")


@work_assets_group.command("suggest")
@click.option("--intent", required=True, help="New task intent to match proven methods against.")
@click.option("--limit", default=3, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_suggest_cmd(intent, limit, as_json, target_dir):
    """Avatar Recall (AUC-1/A31-E): suggest proven, recallable WorkAssets."""
    import json as _json
    from apatch.work_asset_suggest import suggest_work_assets

    result = suggest_work_assets(target_dir, intent=intent, limit=limit)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not result["count"]:
        click.echo("No recallable match — this task will grow the avatar with new experience.")
        return
    for row in result["results"]:
        click.echo("{score:.2f} {asset_id}: {title}".format(**row))
        click.echo("     reasons: " + ", ".join(row.get("reasons") or []))
        click.echo("     next: {tool} {artifacts}".format(**row["next"]))


@work_assets_group.command("recall")
@click.argument("asset_id")
@click.option("--intent", default="", help="Optional task intent for why_fit reasons.")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_recall_cmd(asset_id, intent, as_json, target_dir):
    """RecallBundle (AUC-1 §4): compact money-free distillate of one proven method."""
    import json as _json
    from apatch.work_asset_suggest import build_recall_bundle

    result = build_recall_bundle(target_dir, asset_id=asset_id, intent=intent)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        click.echo("{asset_id}: {title}".format(**result))
        click.echo("method: {mode} {path}".format(**result["method"]))
        click.echo("next: {tool} {artifacts}".format(**result["next_action"]))
    else:
        click.echo("{error_type}: {error}".format(**result))
    if not result.get("ok"):
        sys.exit(1)


@work_assets_group.command("promote")
@click.argument("spec_id")
@click.argument("to_state", type=click.Choice(["accepted", "rejected", "deprecated", "superseded"]))
@click.option("--method-path", default=None,
              help="Method file (default docs/work_assets/<spec_id>.method.md); required for accepted.")
@click.option("--reason", default="", help="Why this transition (recorded in the signed event).")
@click.option("--superseded-by", default=None, help="Successor asset id (required for superseded).")
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_promote_cmd(spec_id, to_state, method_path, reason, superseded_by, as_json, target_dir):
    """Governed promotion — a signed ledger event (SPEC-WORK-ASSET-LIFECYCLE-1)."""
    import json as _json
    from apatch.work_asset_lifecycle import promote_work_asset

    result = promote_work_asset(target_dir, spec_id, to_state, method_path=method_path,
                                reason=reason, superseded_by=superseded_by)
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        click.echo("{spec_id}: {from_state} -> {to_state} (signed)".format(**result))
        method = (result.get("event") or {}).get("method")
        if method:
            click.echo("method pinned: {path} sha256={sha256}".format(**method))
    else:
        click.echo("PROMOTION REFUSED: " + "; ".join(result.get("errors") or []))
    if not result.get("ok"):
        sys.exit(1)


@work_assets_group.command("use")
@click.argument("spec_id")
@click.option("--verify-command", default="", help="The verification command that was run.")
@click.option("--verify-ok/--verify-failed", default=True, show_default=True,
              help="Verification outcome to record.")
@click.option("--adaptation", default="unchanged", show_default=True,
              type=click.Choice(["unchanged", "adapted"]))
@click.option("--proof-ref", default=None, help="Attestation op id backing this use.")
@click.option("--session-id", default=None)
@click.option("--json", "as_json", is_flag=True)
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
def work_assets_use_cmd(spec_id, verify_command, verify_ok, adaptation, proof_ref,
                        session_id, as_json, target_dir):
    """Record a signed use event — reuse learns only from the ledger (AUC-1 R-AUC-2)."""
    import json as _json
    from apatch.work_asset_lifecycle import record_work_asset_use

    result = record_work_asset_use(
        target_dir, spec_id,
        verification={"command": verify_command, "ok": verify_ok},
        adaptation=adaptation, proof_ref=proof_ref, session_id=session_id,
    )
    if as_json:
        click.echo(_json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        click.echo(f"{spec_id}: use recorded (signed), adaptation={adaptation}, verify_ok={verify_ok}")
    else:
        click.echo("USE REFUSED: " + "; ".join(result.get("errors") or []))
    if not result.get("ok"):
        sys.exit(1)


@click.group("workspace")
def workspace_group():
    """Human-managed local workspace aliases for safe MCP roaming."""
    pass


def _emit_workspace_cli_result(result, *, as_json):
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        alias = result.get("alias")
        if alias:
            console.print("[bold green]Workspace[/bold green] @{} → {}".format(alias, result.get("path")))
        else:
            for row in result.get("workspaces") or []:
                state = "ready" if row.get("ready") else "blocked"
                console.print("@{} [{}] {}".format(row.get("alias"), state, row.get("path")))
    else:
        console.print("[bold red]{}[/bold red]".format(result.get("error") or result.get("error_type")))
        if result.get("recommended_action"):
            console.print("[dim]{}[/dim]".format(result["recommended_action"]))
    if not result.get("ok"):
        raise click.ClickException(result.get("error") or result.get("error_type") or "workspace error")


@workspace_group.command("add")
@click.argument("alias")
@click.argument("root", type=click.Path(exists=True, file_okay=False))
@click.option("--force", is_flag=True, help="Replace an existing pin after reviewing root and AGENTS.md.")
@click.option("--json", "as_json", is_flag=True)
def workspace_add_cmd(alias, root, force, as_json):
    """Register or re-pin one local workspace alias (human-only)."""
    from apatch.workflows import workspace_register_workspace

    _emit_workspace_cli_result(
        workspace_register_workspace(alias, root, force=force),
        as_json=as_json,
    )


@workspace_group.command("list")
@click.option("--json", "as_json", is_flag=True)
def workspace_list_cmd(as_json):
    """List registered aliases and current pin/drift status."""
    from apatch.workflows import workspace_list_workspace

    _emit_workspace_cli_result(workspace_list_workspace(), as_json=as_json)


@workspace_group.command("inspect")
@click.argument("alias")
@click.option("--include-contract", is_flag=True, help="Print the full pinned AGENTS.md contract in JSON.")
@click.option("--json", "as_json", is_flag=True)
def workspace_inspect_cmd(alias, include_contract, as_json):
    """Inspect one alias without authorizing mutations."""
    from apatch.workflows import workspace_inspect_workspace

    _emit_workspace_cli_result(
        workspace_inspect_workspace(
            alias=alias,
            include_contract=include_contract,
        ),
        as_json=as_json,
    )


@workspace_group.command("remove")
@click.argument("alias")
@click.option("--json", "as_json", is_flag=True)
def workspace_remove_cmd(alias, as_json):
    """Remove a local workspace authorization."""
    from apatch.workflows import workspace_remove_workspace

    _emit_workspace_cli_result(
        workspace_remove_workspace(alias),
        as_json=as_json,
    )


cli.add_command(workspace_group)
from apatch.cli_extension import extension_group
cli.add_command(extension_group)
cli.add_command(contributions_group)
cli.add_command(asset_group)
cli.add_command(avatar_group)
cli.add_command(work_assets_group)
cli.add_command(spec_group)
cli.add_command(slug_group)
from apatch.cli_concept import concept_group  # RFP-034 Level 2 concept-graph CLI
cli.add_command(concept_group)
from apatch.cli_conformance import conformance_group  # RFP-035 continuous-conformance CLI
cli.add_command(conformance_group)
cli.add_command(rfp_group)
cli.add_command(attestation_group)
cli.add_command(_mutation_group)
cli.add_command(proof_group)
cli.add_command(arch_group)
cli.add_command(impact_cmd)
cli.add_command(db_group)
cli.add_command(index_group)
cli.add_command(trustchain_group)
cli.add_command(policy_group)
cli.add_command(sandbox_group)
cli.add_command(graph_group)
cli.add_command(pipeline_group)
cli.add_command(refactor_group)
cli.add_command(verify_group)
cli.add_command(phase_group)

if __name__ == "__main__":
    cli()
