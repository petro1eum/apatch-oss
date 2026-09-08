import os
import stat
import sys
import difflib
import tempfile
import subprocess
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from apatch.ingestor import PatchCandidate
from apatch.match_session import MatchSession
from apatch.path_index import PathIndex
from apatch.resolver import resolve_smart_path, is_safe_subpath

class InteractiveTUI:
    """
    InteractiveTUI renders a beautiful, premium terminal UI using Rich
    for reviewing and safely applying AI proposed patches.
    """

    def __init__(
        self,
        candidates: List[PatchCandidate],
        target_dir: str,
        non_interactive: bool = False,
        dry_run: bool = False,
        verify_cmd: Optional[str] = None,
        verify_deferred: bool = False,
        verify_baseline: Optional[Dict[str, Any]] = None,
        no_trustchain: bool = False,
        replace_all: bool = False,
        min_confidence: Optional[float] = None,
        only_drifted: bool = False,
        report_path: Optional[str] = None,
        quiet: bool = False,
    ):
        self.candidates = candidates
        self.target_dir = os.path.abspath(target_dir)
        self.path_index = PathIndex.build(self.target_dir)
        self.match_session = MatchSession()
        self.non_interactive = non_interactive
        self.quiet = quiet
        self.dry_run = dry_run
        self.verify_cmd = verify_cmd
        self.verify_deferred = verify_deferred
        self.verify_baseline = dict(verify_baseline or {})
        self.last_verify_baseline: Dict[str, Any] = {}
        # Global override: force every candidate to replace all occurrences.
        self.replace_all = replace_all
        # Auto-apply gating in --yes mode: skip below this confidence.
        self.min_confidence = min_confidence
        # Apply only candidates that need fuzzy help (skip clean exact matches).
        self.only_drifted = only_drifted
        # Optional machine-readable session report path.
        self.report_path = report_path
        self.report_entries: List[dict] = []
        self.verify_rollback: bool = False
        self.last_verify_output: str = ""
        self._quiet_file = None
        try:
            from apatch.mcp.stdio_guard import is_mcp_stdio_mode
        except ImportError:
            is_mcp_stdio_mode = lambda: False  # noqa: E731
        if quiet or is_mcp_stdio_mode():
            self._quiet_file = open(os.devnull, "w", encoding="utf-8")
            self.console = Console(
                file=self._quiet_file,
                stderr=False,
                width=120,
                force_terminal=False,
                highlight=False,
            )
        else:
            self.console = Console()
        from apatch.backup import BackupManager
        from apatch.trustchain_helper import TrustChainHelper
        self.tc_helper = TrustChainHelper(
            self.target_dir, auto_init=not no_trustchain
        )
        self.tc_checkpoint_name: Optional[str] = None
        self.trustchain_committed: bool = False

        if not no_trustchain and not self.dry_run and self.tc_helper.has_trustchain():
            self.console.print(
                f"[bold green]🛡️ TrustChain[/bold green] active at {self.tc_helper.trustchain_dir}"
            )
            self.tc_checkpoint_name = self.tc_helper.begin_mutating_session("apatch_sess")
            if self.tc_checkpoint_name:
                self.console.print(
                    f"  checkpoint [cyan]{self.tc_checkpoint_name}[/cyan] (before apply)"
                )
        # Physical backups and TrustChain must share one rollback identifier so
        # rollback_workspace(checkpoint) can restore both after this process exits.
        self.backup_mgr = BackupManager(
            self.target_dir,
            session_id=self.tc_checkpoint_name,
        )

    def _commit_write(self, target_path: str, content: str, action_type: str, matcher) -> None:
        """Apply a resolved change to disk: delete, or write with safe encoding.

        Preserves the file's original newline style and encoding, but transparently
        falls back to UTF-8 when the proposed content contains characters the
        original encoding cannot represent (avoids UnicodeEncodeError corruption).
        """
        if action_type == "DELETE":
            if os.path.exists(target_path):
                os.remove(target_path)
            self.match_session.invalidate(target_path)
            return

        if action_type == "CHMOD":
            from apatch.file_modes import mode_to_int

            os.chmod(target_path, mode_to_int(content))
            self.match_session.invalidate(target_path)
            return

        parent = os.path.dirname(target_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        formatted_content = matcher.restore_formatting(content)
        encoding = matcher.encoding
        try:
            formatted_content.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            self.console.print(
                f"[bold yellow]⚠ Content not representable in '{encoding}'; "
                f"writing as UTF-8 instead.[/bold yellow]"
            )
            encoding = "utf-8"
        # Atomic write: stage in a temp file in the same directory, fsync,
        # then os.replace - an interrupted write can never truncate the
        # user's existing file (partial-write corruption guard).
        original_mode = None
        if os.path.exists(target_path):
            original_mode = stat.S_IMODE(os.stat(target_path, follow_symlinks=False).st_mode)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=parent or ".", prefix=".apatch-write-")
        try:
            if original_mode is not None:
                os.fchmod(tmp_fd, original_mode)
            with os.fdopen(tmp_fd, "w", encoding=encoding, newline="") as f:
                f.write(formatted_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        self.match_session.invalidate(target_path)

    def _mark_step_rolled_back(self, step_index: int, reason: str) -> None:
        self.verify_rollback = True
        for entry in self.report_entries:
            if entry.get("step_index") == step_index and entry.get("outcome") == "applied":
                entry["outcome"] = "rolled_back"
                entry["reason"] = reason

    def _mark_all_applied_rolled_back(self, reason: str) -> None:
        self.verify_rollback = True
        for entry in self.report_entries:
            if entry.get("outcome") == "applied":
                entry["outcome"] = "rolled_back"
                entry["reason"] = reason

    def _record(self, cand, target_path, outcome, strategy, confidence, warnings, reason=""):
        """Accumulate a machine-readable session report entry."""
        self.report_entries.append({
            "step_index": cand.step_index,
            "tool_name": cand.tool_name,
            "target_file": cand.target_file,
            "resolved_path": target_path,
            "action_type": cand.action_type,
            "outcome": outcome,
            "strategy": strategy,
            "confidence": confidence,
            "warnings": list(warnings or []),
            "reason": reason,
        })

    def _incremental_notarization_enabled(self) -> bool:
        from apatch.enforcement import is_enforcement_enabled, load_enforcement_config

        if not is_enforcement_enabled(self.target_dir):
            return False
        return bool(load_enforcement_config(self.target_dir).get("incremental_notarization", True))

    def _build_files_metadata(self, file_paths) -> dict:
        import hashlib

        files_metadata = {}
        for fpath in file_paths:
            if not os.path.exists(fpath):
                continue
            hasher = hashlib.sha256()
            try:
                with open(fpath, "rb") as fh:
                    hasher.update(fh.read())
                fhash = hasher.hexdigest()
            except Exception:
                fhash = "unknown"
            rel_path = os.path.relpath(fpath, self.target_dir)
            diff_text = ""
            mode = None
            mode_before = None
            try:
                mode = f"{os.stat(fpath).st_mode & 0o777:03o}"
                backups = self.backup_mgr._load_existing_backups()
                backup_file = None
                for b in backups:
                    if os.path.abspath(b["original_file"]) == os.path.abspath(fpath):
                        backup_file = os.path.join(self.backup_mgr.session_path, b["backup_file"])
                        break
                if backup_file and os.path.exists(backup_file):
                    mode_before = f"{os.stat(backup_file).st_mode & 0o777:03o}"
                    with open(backup_file, "r", encoding="utf-8") as f_old:
                        old_lines = f_old.readlines()
                    with open(fpath, "r", encoding="utf-8") as f_new:
                        new_lines = f_new.readlines()
                    diff_lines = list(difflib.unified_diff(
                        old_lines, new_lines,
                        fromfile=f"a/{rel_path}",
                        tofile=f"b/{rel_path}",
                    ))
                    diff_text = "".join(diff_lines)
            except Exception:
                pass
            metadata = {"sha256": fhash, "diff": diff_text}
            if mode:
                metadata["mode"] = mode
            if mode_before and mode and mode_before != mode:
                metadata["mode_diff"] = f"{mode_before}->{mode}"
            files_metadata[rel_path] = metadata
        return files_metadata

    def _commit_trustchain_payload(self, files_metadata: dict, *, applied: int, skipped: int, label: str) -> bool:
        if not files_metadata or not self.tc_helper.has_trustchain():
            return False
        payload = {
            "session_id": self.backup_mgr.session_id,
            "applied_patches": applied,
            "skipped_patches": skipped,
            "checkpoint_ref": self.tc_checkpoint_name or "",
            "action": label,
            "files": files_metadata,
        }
        from apatch.enforcement import is_enforcement_enabled, verify_commit_produced_block

        ok = bool(self.tc_helper.commit_action(tool_id="apatch", payload=payload))
        if ok and is_enforcement_enabled(self.target_dir):
            ok = verify_commit_produced_block(self.tc_helper)
        if ok:
            self.trustchain_committed = True
        return ok

    def _final_applied_paths(self) -> List[str]:
        """Deduplicate files that survived verification/rollback in this chunk."""
        paths: List[str] = []
        seen = set()
        for entry in self.report_entries:
            if entry.get("outcome") != "applied":
                continue
            path = entry.get("resolved_path")
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    def _rollback_for_notarization_failure(self, reason: str) -> None:
        from apatch.enforcement import assert_notarization_committed

        self.verify_rollback = True
        try:
            self.backup_mgr.rollback_session(self.target_dir, self.backup_mgr.session_id)
        except Exception:
            pass
        if self.tc_helper.has_trustchain() and self.tc_checkpoint_name:
            self.tc_helper.rollback_checkpoint(self.tc_checkpoint_name)
        self._mark_all_applied_rolled_back(reason)
        err = assert_notarization_committed(
            self.target_dir,
            committed=False,
            files_meta={},
        )

    def _notarize_paths_or_rollback(self, paths, *, applied: int, skipped: int, label: str) -> bool:
        meta = self._build_files_metadata(paths)
        if not meta:
            return True
        if self._commit_trustchain_payload(meta, applied=applied, skipped=skipped, label=label):
            return True
        from apatch.enforcement import assert_notarization_committed, is_enforcement_enabled

        if is_enforcement_enabled(self.target_dir):
            self._rollback_for_notarization_failure("notarization_failed")
            return False
        return True

    def _write_report(self) -> None:
        if not self.report_path:
            return
        import json
        summary = {
            "target_dir": self.target_dir,
            "dry_run": self.dry_run,
            "checkpoint": self.tc_checkpoint_name,
            "verify_rollback": self.verify_rollback,
            "applied": sum(1 for e in self.report_entries if e["outcome"] == "applied"),
            "skipped": sum(1 for e in self.report_entries if e["outcome"] in ("skipped", "failed", "error", "rolled_back")),
            "entries": self.report_entries,
        }
        try:
            parent = os.path.dirname(os.path.abspath(self.report_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.report_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            self.console.print(f"[bold green]✓ Session report written to {self.report_path}[/bold green]")
        except Exception as e:
            self.console.print(f"[bold yellow]Could not write session report: {e}[/bold yellow]")

    def _run_verification(self) -> bool:
        """
        Runs the custom verification command and returns True if successful, False otherwise.
        """
        if not self.verify_cmd:
            return True
        from apatch.tool_paths import run_shell_verify

        self.console.print(f"[bold cyan]Running verification command: '{self.verify_cmd}'...[/bold cyan]")
        # run_shell_verify materializes the lead tool (python3/pytest/npm/...) to an
        # absolute path and augments PATH. A bare subprocess here inherited the MCP
        # server PATH where python3 can be the Xcode stub without pytest, so inner
        # verify failed while the same command passed everywhere else.
        ok, output = run_shell_verify(self.verify_cmd, self.target_dir)
        self.last_verify_output = output or ""
        if ok:
            self.console.print("[bold green]✔ Verification command passed![/bold green]")
            return True
        if self.verify_baseline:
            from apatch.verify_baseline import compare_failures, parse_failed_tests

            current = parse_failed_tests(self.last_verify_output)
            report = compare_failures(
                current,
                baseline=self.verify_baseline.get("failures") or [],
            )
            report["baseline_found"] = True
            report["baseline_path"] = self.verify_baseline.get("path")
            if not current:
                report["unparsed_output"] = True
            self.last_verify_baseline = report
            if current and not report["new_failures"]:
                self.console.print(
                    "[bold green]✔ Verification has only pre-existing failures.[/bold green]"
                )
                return True
        self.console.print("✗ Verification command failed", style="bold red")
        if self.last_verify_output:
            self.console.print(self.last_verify_output, markup=False)
        return False

    def run_apply_loop(self):
        total = len(self.candidates)
        if total == 0:
            self.console.print("[bold yellow]No patch candidates found in the logs.[/bold yellow]")
            return

        dry_str = " (DRY-RUN SIMULATION)" if self.dry_run else ""
        self.console.print(Panel(
            Text.assemble(
                ("Starting interactive patch session with ", "white"),
                (f"{total} candidates{dry_str}", "bold green"),
                ("\nTarget Directory: ", "white"),
                (self.target_dir, "bold blue")
            ),
            title="[bold green]apatch Analyzer[/bold green]",
            border_style="green"
        ))

        applied_count = 0
        skipped_count = 0

        # Group candidates by step_index
        from collections import defaultdict
        step_groups = defaultdict(list)
        for cand in self.candidates:
            step_groups[cand.step_index].append(cand)
        sorted_steps = sorted(step_groups.keys())

        # Track global candidate index for displaying [idx/total]
        global_idx = 0
        aborted = False

        for step_pos, step_idx in enumerate(sorted_steps, 1):
            if aborted:
                break
            step_candidates = step_groups[step_idx]
            self.console.print("\n" + "═" * 80)
            self.console.print(f"[bold cyan]⚡ Applying Step {step_idx} ({step_pos}/{len(sorted_steps)}) - containing {len(step_candidates)} file(s)[/bold cyan]")

            # Track candidates applied or skipped inside this step transaction
            applied_candidates_in_step = []
            skipped_candidates_in_step = []

            for cand in step_candidates:
                global_idx += 1
                target_path = self._resolve_smart_path(cand.target_file, cand.action_type)
                if not target_path:
                    self.console.print(f"[bold red]Error: Could not resolve target file path for '{cand.target_file}' under project root.[/bold red]")
                    skipped_candidates_in_step.append(cand)
                    continue

                self.console.print("\n" + "─" * 80)
                self.console.print(Text.assemble(
                    (f"[{global_idx}/{total}] ", "bold cyan"),
                    ("Step ", "white"),
                    (f"{cand.step_index} ", "bold yellow"),
                    (f"({cand.tool_name}) ", "magenta"),
                    ("-> File: ", "white"),
                    (os.path.basename(target_path), "bold green"),
                    (f" [Action: {cand.action_type}]", "bold blue"),
                    (f"\nAligned Path: {target_path}", "dim white")
                ))

                try:
                    matcher = self.match_session.matcher_for(target_path)
                except Exception as e:
                    self.console.print(f"[bold red]Error loading target file: {e}[/bold red]")
                    skipped_candidates_in_step.append(cand)
                    continue

                # Try to match patch
                effective_replace_all = self.replace_all or getattr(cand, "replace_all", False)
                match = matcher.evaluate(
                    cand.old_content, cand.new_content, cand.action_type,
                    replace_all=effective_replace_all,
                )
                success, replaced_content, strategy = match.success, match.content, match.strategy
                confidence, match_warnings = match.confidence, match.warnings
                if (
                    success
                    and cand.action_type == "REPLACE"
                    and replaced_content == matcher.content
                ):
                    success = False
                    strategy = "no-change"
                    confidence = 0.0
                    match_warnings = list(match_warnings) + [
                        "matched replacement produced no byte change"
                    ]

                # --only-drifted: skip clean exact matches (nothing to recover).
                if self.only_drifted and strategy in ("exact", "exact-all"):
                    self.console.print(f"[dim]↷ Skipped (exact, not drifted): {os.path.basename(target_path)}[/dim]")
                    self._record(cand, target_path, "skipped", strategy, confidence, match_warnings, reason="only-drifted")
                    skipped_candidates_in_step.append(cand)
                    continue

                # Build status banner based on strategy success
                if success:
                    border_style = "green" if strategy in ("exact", "whitespace-fuzzy", "create", "delete") else "yellow"
                    strategy_desc = f"{strategy.upper()} MATCH (RECOVERED! 🎉)" if strategy == "ast-fuzzy" else f"{strategy.upper()} MATCH"
                    status_text = Text.assemble(
                        ("✔ Path Alignment Strategy: ", "bold green"),
                        (strategy_desc, "bold " + border_style),
                        (f"  (confidence {confidence:.2f})", "dim white"),
                    )
                else:
                    border_style = "red"
                    status_text = Text.assemble(
                        ("✘ Path Alignment Failed: ", "bold red"),
                        ("Context drift too high / Node mismatch", "bold red")
                    )

                # Print code preview diff
                diff_text = self._generate_diff_preview(matcher.content, replaced_content, target_path)
                
                self.console.print(Panel(
                    Syntax(diff_text, "diff", theme="monokai", background_color="default"),
                    title=f"[bold {border_style}]Diff Preview[/bold {border_style}]",
                    subtitle=status_text,
                    border_style=border_style
                ))

                # Surface warnings (e.g. signature change silently dropped by AST-fuzzy).
                for w in match_warnings:
                    self.console.print(f"[bold yellow]⚠ {w}[/bold yellow]")

                if self.non_interactive:
                    # Confidence gate for unattended batch application.
                    if success and self.min_confidence is not None and confidence < self.min_confidence:
                        self.console.print(
                            f"[bold yellow]✘ Auto-skipped patch {global_idx}/{total}: confidence "
                            f"{confidence:.2f} < --min-confidence {self.min_confidence:.2f}.[/bold yellow]"
                        )
                        self._record(cand, target_path, "skipped", strategy, confidence, match_warnings, reason="min-confidence")
                        skipped_candidates_in_step.append(cand)
                        continue
                    if success:
                        try:
                            if self.dry_run:
                                self.console.print(f"[bold green]✔ [dry-run] Auto-aligned patch {global_idx}/{total} successfully![/bold green]")
                                applied_candidates_in_step.append((cand, target_path, replaced_content, matcher.encoding, matcher.newline_format))
                            else:
                                # Pre-backup file with step transaction metadata
                                self.backup_mgr.create_backup(target_path, step_index=step_idx, action_type=cand.action_type)

                                self._commit_write(target_path, replaced_content, cand.action_type, matcher)

                                self.console.print(f"[bold green]✔ Auto-applied patch {global_idx}/{total} and wrote to {target_path}![/bold green]")
                                applied_candidates_in_step.append((cand, target_path, replaced_content, matcher.encoding, matcher.newline_format))
                            self._record(cand, target_path, "applied", strategy, confidence, match_warnings)
                        except Exception as e:
                            self.console.print(f"[bold red]Failed to auto-write target file: {e}[/bold red]")
                            self._record(cand, target_path, "error", strategy, confidence, match_warnings, reason=str(e))
                            skipped_candidates_in_step.append(cand)
                    else:
                        self.console.print(f"[bold red]✘ Auto-skipped patch {global_idx}/{total} due to alignment failure.[/bold red]")
                        self._record(cand, target_path, "failed", strategy, confidence, match_warnings)
                        skipped_candidates_in_step.append(cand)
                    continue

                # Interactive prompt
                if not success:
                    self.console.print("[bold yellow]Previewing raw proposed code change:[/bold yellow]")
                    raw_syntax = Syntax(cand.new_content, matcher.get_language_name() or "text", theme="monokai")
                    self.console.print(Panel(raw_syntax, title="Raw Proposed Patch Block"))

                # Ask user for action
                action = self._prompt_user()
                if action == 'q':
                    self.console.print("[bold yellow]Aborted session. No further files written.[/bold yellow]")
                    aborted = True
                    break
                elif action == 's':
                    self.console.print("[bold white]Skipped patch.[/bold white]")
                    self._record(cand, target_path, "skipped", strategy, confidence, match_warnings, reason="user-skip")
                    skipped_candidates_in_step.append(cand)
                elif action == 'a':
                    if not success:
                        self.console.print("[bold red]Cannot apply a failed match safely.[/bold red]")
                        action_override = self.console.input("[bold yellow]Force write full replacement anyway? (y/N): [/bold yellow]").strip().lower()
                        if action_override != 'y':
                            self._record(cand, target_path, "skipped", strategy, confidence, match_warnings, reason="failed-not-forced")
                            skipped_candidates_in_step.append(cand)
                            continue

                    # Save changes
                    try:
                        if self.dry_run:
                            self.console.print(f"[bold green]✔ [dry-run] Match marked as applied (dry-run).[/bold green]")
                            applied_candidates_in_step.append((cand, target_path, replaced_content, matcher.encoding, matcher.newline_format))
                        else:
                            # Pre-backup file with step transaction metadata
                            self.backup_mgr.create_backup(target_path, step_index=step_idx, action_type=cand.action_type)

                            self._commit_write(target_path, replaced_content, cand.action_type, matcher)

                            self.console.print(f"[bold green]✔ Safely applied patch and wrote to {target_path}![/bold green]")
                            applied_candidates_in_step.append((cand, target_path, replaced_content, matcher.encoding, matcher.newline_format))
                        self._record(cand, target_path, "applied", strategy, confidence, match_warnings)
                    except Exception as e:
                        self.console.print(f"[bold red]Failed to write target file: {e}[/bold red]")
                        self._record(cand, target_path, "error", strategy, confidence, match_warnings, reason=str(e))
                        skipped_candidates_in_step.append(cand)
                elif action == 'e':
                    # Launch system editor (vim, nano, etc.) directly on the file contents
                    self.console.print("[bold cyan]Opening system editor...[/bold cyan]")
                    content_to_edit = replaced_content if success else matcher.content
                    
                    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(target_path)[1], mode="w+", delete=False, encoding="utf-8") as tf:
                        os.chmod(tf.name, 0o600)
                        tf.write(content_to_edit)
                        temp_name = tf.name
                        
                    try:
                        editor = os.environ.get('EDITOR', 'nano')
                        subprocess.call([editor, temp_name])
                        
                        with open(temp_name, "r", encoding="utf-8") as tf:
                            edited_content = tf.read()
                            
                        if edited_content != content_to_edit:
                            confirm = self.console.input("[bold yellow]Apply manual changes to target file? (Y/n): [/bold yellow]").strip().lower()
                            if confirm != 'n':
                                if self.dry_run:
                                    self.console.print("[bold green]✔ [dry-run] Manual edits confirmed (dry-run).[/bold green]")
                                    applied_candidates_in_step.append((cand, target_path, edited_content, matcher.encoding, matcher.newline_format))
                                else:
                                    # Pre-backup file with step transaction metadata
                                    self.backup_mgr.create_backup(target_path, step_index=step_idx, action_type=cand.action_type)

                                    self._commit_write(target_path, edited_content, cand.action_type, matcher)

                                    self.console.print(f"[bold green]✔ Safely wrote manual edits to {target_path}![/bold green]")
                                    applied_candidates_in_step.append((cand, target_path, edited_content, matcher.encoding, matcher.newline_format))
                            else:
                                self.console.print("[bold white]Manual edits discarded.[/bold white]")
                                skipped_candidates_in_step.append(cand)
                        else:
                            self.console.print("[bold white]No manual changes detected. Skipped.[/bold white]")
                            skipped_candidates_in_step.append(cand)
                    except Exception as e:
                        self.console.print(f"[bold red]Failed to execute system editor: {e}[/bold red]")
                        skipped_candidates_in_step.append(cand)
                    finally:
                        if os.path.exists(temp_name):
                            os.remove(temp_name)

            # --- STEP-LEVEL TRANSACTION VERIFICATION AND ATOMIC ROLLBACK ---
            if applied_candidates_in_step and self.verify_cmd and not self.verify_deferred and not self.dry_run:
                self.console.print(f"\n[bold cyan]⏳ Executing step transaction verification for Step {step_idx}...[/bold cyan]")
                if not self._run_verification():
                    self.console.print(f"\n[bold red]❌ Verification failed for Step {step_idx}! Reverting all changes in this step...[/bold red]")
                    
                    if self.non_interactive:
                        # Auto-rollback in non-interactive batch mode
                        self.backup_mgr.rollback_step(step_idx)
                        self._mark_step_rolled_back(step_idx, "verify_failed")
                        self.console.print(f"[bold yellow]✓ Step {step_idx} rolled back atomically.[/bold yellow]")
                        skipped_count += len(applied_candidates_in_step) + len(skipped_candidates_in_step)
                    else:
                        # Prompt user for manual override/force apply in interactive mode
                        choice = self.console.input("[bold yellow][S] Rollback & Skip step, [F] Force keep step anyway: [/bold yellow]").strip().lower()
                        if choice == 'f':
                            self.console.print(f"[bold green]✔ Forced step {step_idx} changes and kept them on disk.[/bold green]")
                            applied_count += len(applied_candidates_in_step)
                            skipped_count += len(skipped_candidates_in_step)
                        else:
                            self.backup_mgr.rollback_step(step_idx)
                            self.console.print(f"[bold yellow]✓ Step {step_idx} rolled back and skipped.[/bold yellow]")
                            skipped_count += len(applied_candidates_in_step) + len(skipped_candidates_in_step)
                else:
                    applied_count += len(applied_candidates_in_step)
                    skipped_count += len(skipped_candidates_in_step)
            else:
                applied_count += len(applied_candidates_in_step)
                skipped_count += len(skipped_candidates_in_step)

        # Deferred verification check once at the end of the entire session
        if self.verify_cmd and self.verify_deferred and applied_count > 0 and not self.dry_run:
            self.console.print("\n[bold cyan]⏳ Running deferred session verification...[/bold cyan]")
            if not self._run_verification():
                self.console.print("\n[bold red]❌ Deferred verification failed! Reverting entire session...[/bold red]")
                try:
                    self.backup_mgr.rollback_session(self.target_dir, self.backup_mgr.session_id)
                    self.console.print("[bold green]✓ Entire session successfully rolled back.[/bold green]")
                except Exception as e:
                    self.console.print(f"[bold red]Failed to roll back session files: {e}[/bold red]")
                
                if self.tc_helper.has_trustchain() and self.tc_checkpoint_name:
                    self.console.print(f"[bold yellow]🛡️ Resetting TrustChain state to checkpoint: {self.tc_checkpoint_name}[/bold yellow]")
                    self.tc_helper.rollback_checkpoint(self.tc_checkpoint_name)
                
                self._mark_all_applied_rolled_back("verify_deferred_failed")
                skipped_count += applied_count
                applied_count = 0
            else:
                self.console.print("[bold green]✔ Deferred session verification passed successfully![/bold green]")

        # One signed mutation record per apply invocation/chunk.
        if self.tc_helper.has_trustchain() and applied_count > 0 and not self.dry_run:
            if not self.quiet:
                self.console.print(
                    "[bold green]🛡️ Committing one signed chunk record to TrustChain...[/bold green]"
                )
            files_metadata = self._build_files_metadata(self._final_applied_paths())
            if not self._commit_trustchain_payload(
                files_metadata,
                applied=applied_count,
                skipped=skipped_count,
                label="chunk",
            ):
                from apatch.enforcement import is_enforcement_enabled

                if is_enforcement_enabled(self.target_dir):
                    self._rollback_for_notarization_failure("chunk_notarization_failed")
                    applied_count = 0

        # Summary statistics
        self.console.print("\n" + "═" * 80)
        action_word = "Simulated" if self.dry_run else "Safely Applied"
        self.console.print(Panel(
            Text.assemble(
                (f"Patches {action_word}: ", "white"),
                (f"{applied_count}\n", "bold green"),
                ("Patches Skipped/Failed: ", "white"),
                (f"{skipped_count}\n", "bold yellow"),
                ("Total Inspected: ", "white"),
                (f"{total}", "bold cyan")
            ),
            title="[bold green]Session Summary[/bold green]",
            border_style="green"
        ))

        self._write_report()

    def _generate_diff_preview(self, original: str, proposed: str, filename: str) -> str:
        orig_lines = original.splitlines(keepends=True)
        prop_lines = proposed.splitlines(keepends=True)
        diff = difflib.unified_diff(orig_lines, prop_lines, fromfile=f"a/{os.path.basename(filename)}", tofile=f"b/{os.path.basename(filename)}", n=3)
        return "".join(list(diff))

    def _prompt_user(self) -> str:
        prompt_text = Text.assemble(
            ("[A] Apply Patch ", "bold green"),
            (" | [S] Skip ", "bold yellow"),
            (" | [E] Edit manually ", "bold cyan"),
            (" | [Q] Quit ", "bold red"),
            ("\nYour Choice > ", "white")
        )
        while True:
            self.console.print(prompt_text, end="")
            sys.stdout.flush()
            # Read single char if possible or standard input
            try:
                choice = sys.stdin.readline().strip().lower()
                if choice in ('a', 's', 'e', 'q'):
                    return choice
            except KeyboardInterrupt:
                return 'q'
            self.console.print("[bold red]Invalid option. Enter A, S, E, or Q.[/bold red]")

    def _is_safe_subpath(self, resolved_path: str) -> bool:
        return is_safe_subpath(self.target_dir, resolved_path)

    def _resolve_smart_path(self, logged_path: str, action_type: str = "REPLACE") -> Optional[str]:
        return resolve_smart_path(
            self.target_dir, logged_path, action_type, index=self.path_index
        )
