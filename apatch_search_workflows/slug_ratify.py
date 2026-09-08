"""Single-call slug ratification: one verify run, batch attestation, conformance gate.

Closing one slug/category by hand costs ~40 minutes on prod, most of it
re-running the same expensive verify (raw feedback replay) once per requirement,
creating marker mutations for primary attestation, and running it once more for
the conformance verdict. ``slug_ratify_workspace``
collapses that loop into one operation with fail-fast stages:

1. **resolve** — exact-ownership spec resolution (explicit ``spec`` param →
   contract yaml ``spec_generation.spec_id`` → conformance contract entry →
   ``docs/specs/SPEC-<SLUG>-1.md``). Never fuzzy-matched; on failure the
   error lists candidates instead of guessing.
2. **lint** — the contract yaml must parse; the slug's feedback triage TSV must
   be vocabulary-clean (reuses ``apatch.slug_feedback_lint``).
3. **verify** — each unique requirement verify command executes **exactly
   once**; the measured per-requirement results are kept for reuse.
4. **rebind** — every green pending/in-progress/stale requirement is attested
   in one governed session and one signed ledger commit from the precomputed
   verify results. No marker files and no per-Rk lifecycle loop.
5. **gate** — the conformance verdict (``apatch.conformance``, RFP-035) is
   produced from the SAME measured results via the ``verify_runner`` seam —
   no second run.

The DTO is deliberately compact (stage list, counters, verdict) — no needle
dumps, no full per-requirement detail.
"""

from __future__ import annotations

import glob
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from apatch.apatch_paths import normalize_rel
from apatch.spec_contract_resolver import (
    CONTRACT_DIR_REL,
    _load_contract_yaml,
    _resolve_spec_id,
)

STAGE_ORDER = ("resolve", "lint", "verify", "rebind", "gate")


class _OnceVerifyRunner:
    """Conformance-compatible verify runner that runs each unique command once.

    Result shape matches ``apatch.conformance.run_spec_verify`` with
    ``capture_details=True`` (``(ran, failures, broken, details)``): the first
    requirement naming a command executes it via conformance's
    ``_run_verify_command``; every later requirement — including the
    conformance gate's re-classification — replays the cached outcome.
    """

    def __init__(self, root: str, timeout: float) -> None:
        self.root = os.path.abspath(root)
        self.timeout = float(timeout)
        self.commands_run = 0
        self.elapsed_sec = 0.0
        self._cache: Dict[str, Dict[str, Any]] = {}

    def is_green(self, cmd: Optional[str]) -> bool:
        cached = self._cache.get(cmd or "")
        return bool(cached) and not cached["failed"] and not cached["broken_suffix"]

    def evidence(self) -> Dict[str, Any]:
        """Compact signed evidence for the batch attestation."""
        return {
            "kind": "slug_ratify_shared_verify",
            "commands_run": self.commands_run,
            "commands": [
                {
                    "command": cmd,
                    "green": not row["failed"] and not row["broken_suffix"],
                }
                for cmd, row in self._cache.items()
            ],
        }

    def _measure(self, rid: str, cmd: str) -> Dict[str, Any]:
        from apatch.conformance import _run_verify_command

        started = time.monotonic()
        res = _run_verify_command(self.root, rid, cmd, self.timeout)
        self.elapsed_sec += time.monotonic() - started
        self.commands_run += 1
        broken = res.get("broken")
        return {
            "failed": bool(res.get("failure")),
            "broken_suffix": str(broken).split(":", 1)[1] if broken else None,
            "detail": res.get("detail"),
        }

    def __call__(
        self, root: str, requirements: List[Dict[str, Any]]
    ) -> Tuple[int, List[str], List[str], List[Dict[str, Any]]]:
        ran = 0
        failures: List[str] = []
        broken: List[str] = []
        details: List[Dict[str, Any]] = []
        for req in requirements or []:
            cmd = req.get("verify")
            if not cmd:
                continue
            rid = str(req.get("id") or "?")
            cached = self._cache.get(cmd)
            if cached is None:
                cached = self._measure(rid, cmd)
                self._cache[cmd] = cached
            ran += 1
            if cached["failed"]:
                failures.append(rid)
            elif cached["broken_suffix"]:
                broken.append("{}:{}".format(rid, cached["broken_suffix"]))
            if cached["detail"]:
                detail = dict(cached["detail"])
                detail["id"] = rid
                details.append(detail)
        return ran, failures, broken, details


def slug_ratify_workspace(
    target_dir: str = ".",
    slug: str = "",
    *,
    spec: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Ratify a slug: resolve → lint → verify once → batch attest → gate.

    ``dry_run=True`` stops after resolve+lint and reports what WOULD run (the
    unique verify commands and the open requirement ids) without executing
    verifies or opening governed sessions. Errors are stage-tagged:
    ``{ok: false, stage, error, hint}``.
    """
    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    slug_n = _norm(slug)
    if not slug_n:
        return {"ok": False, "stage": "resolve", "error": "slug is required", "error_type": "SLUG_REQUIRED"}

    stages: List[Dict[str, Any]] = []

    # ── resolve ──────────────────────────────────────────────────────────────
    started = time.monotonic()
    contract_abs = os.path.join(root, CONTRACT_DIR_REL, f"{slug_n}.yaml")
    triage_abs = os.path.join(root, "tests", "regressions", f"{slug_n}_feedback_triage.tsv")
    files = {
        "contract": _file_record(root, contract_abs),
        "triage": _file_record(root, triage_abs, count_rows=True),
    }
    contract_data, contract_error = _load_contract_yaml(contract_abs)

    spec_id, resolved_via, resolve_error = _resolve_spec_id(root, slug_n, spec, contract_data)
    if resolve_error:
        resolve_error.update({"slug": slug_n, "files": files})
        return resolve_error
    _stage(stages, "resolve", started, True, f"spec={spec_id} (via {resolved_via})")

    # ── lint ─────────────────────────────────────────────────────────────────
    started = time.monotonic()
    lint_parts: List[str] = []
    if files["contract"]:
        if contract_error:
            return _error("lint", contract_error, slug_n, stages,
                          hint="Fix the contract yaml (or install the yaml extra: pip install 'apatch[yaml]').")
        lint_parts.append("contract yaml parsed")
    else:
        lint_parts.append("no contract yaml (optional)")
    if files["triage"]:
        from apatch_search_workflows.slug_feedback_lint import feedback_lint_workspace

        lint = feedback_lint_workspace(root, slug=slug_n)
        if not lint.get("ok"):
            return _error("lint", str(lint.get("error")), slug_n, stages,
                          hint="Fix the triage TSV, then re-run slug ratify.")
        if not lint.get("clean"):
            findings = [
                {k: f.get(k) for k in ("severity", "id", "message")}
                for f in (lint.get("findings") or [])[:5]
            ]
            return _error(
                "lint",
                "feedback triage lint is not clean ({} finding(s))".format(len(lint.get("findings") or [])),
                slug_n,
                stages,
                hint=f"Run `apatch slug feedback-lint {slug_n}` and apply the proposed needles via the governed path.",
                findings=findings,
            )
        lint_parts.append("triage lint clean ({} rows)".format(files["triage"].get("rows")))
    else:
        lint_parts.append("no triage TSV (optional)")
    _stage(stages, "lint", started, True, "; ".join(lint_parts))

    # ── verify (once) ────────────────────────────────────────────────────────
    from apatch.conformance import _VERIFY_TIMEOUT, _positive_int, load_conformance_config
    from apatch.spec_coverage import spec_status_with_coverage

    started = time.monotonic()
    status = spec_status_with_coverage(root, spec=spec_id)
    if not status.get("ok"):
        return _error("verify", str(status.get("error")), slug_n, stages,
                      hint="The resolved spec no longer loads; check docs/specs.")
    requirements = status.get("requirements") or []
    open_rows = [
        r for r in requirements
        if r.get("state") in ("pending", "in_progress", "stale")
    ]
    stale_rows = [r for r in open_rows if r.get("state") == "stale"]
    primary_rows = [r for r in open_rows if r.get("state") != "stale"]
    unverified_rows = [r for r in open_rows if not r.get("verify")]
    commands = _unique_commands(requirements)

    if dry_run:
        _stage(stages, "verify", started, True,
               "dry run: {} unique command(s) would run once".format(len(commands)))
        return {
            "ok": True,
            "slug": slug_n,
            "spec": spec_id,
            "workspace": root,
            "dry_run": True,
            "stages": stages,
            "files": files,
            "would_run": {
                "verify_commands": commands,
                "open_requirements": [r["id"] for r in open_rows],
                "primary_requirements": [r["id"] for r in primary_rows],
                "stale_requirements": [r["id"] for r in stale_rows],
            },
            "next_action": (
                "Dry run: {} verify command(s) would run once and {} open requirement(s) "
                "would be batch-attested. Re-run without dry_run to ratify.".format(
                    len(commands), len(open_rows)
                )
            ),
        }

    if not commands:
        return _error("verify", f"{spec_id} has no verify commands — nothing to ratify", slug_n, stages,
                      hint="Add '(verify: <cmd>)' to the spec requirements first.")

    cfg = load_conformance_config(root)
    timeout = _positive_int(cfg.get("verify_timeout_sec"), _VERIFY_TIMEOUT) or _VERIFY_TIMEOUT
    runner = _OnceVerifyRunner(root, timeout)
    _ran, failures, broken, _details = runner(root, requirements)
    green = not failures and not broken and not unverified_rows
    open_ids = {str(row.get("id") or "") for row in open_rows}
    broken_ids = {str(item).split(":", 1)[0] for item in broken}
    missing_ids = {str(row.get("id") or "") for row in unverified_rows}
    open_blockers = sorted(open_ids & (set(failures) | broken_ids | missing_ids))
    can_rebind_open = not failures and not open_blockers
    verify_out: Dict[str, Any] = {
        "commands_run": runner.commands_run,
        "elapsed_sec": round(runner.elapsed_sec, 3),
        "green": green,
        "can_rebind_open": can_rebind_open,
    }
    if not green:
        verify_out["red"] = {
            "failures": failures,
            "broken": broken,
            "missing_verify": [r["id"] for r in unverified_rows],
        }
    if green:
        verify_summary = "green"
    elif can_rebind_open and broken:
        verify_summary = (
            "open requirements green; {} broken verify reference(s) outside the open set are advisory"
        ).format(len(broken))
    else:
        verify_summary = "red ({})".format(
            ", ".join(failures + broken + [
                "{}:missing_verify".format(r["id"]) for r in unverified_rows
            ])
        )
    _stage(
        stages,
        "verify",
        started,
        can_rebind_open,
        "{} unique command(s) run once; {}".format(runner.commands_run, verify_summary),
    )

    # ── rebind/primary attest (one session, one signed commit) ──────────────
    started = time.monotonic()
    attested: List[str] = []
    primary_attested: List[str] = []
    reattested: List[str] = []
    rebind_errors: List[Dict[str, Any]] = []
    attestation_stats = {"sessions_opened": 0, "ledger_commits": 0}
    if not open_rows:
        _stage(stages, "rebind", started, True, "no open requirements")
    elif not can_rebind_open:
        _stage(
            stages,
            "rebind",
            started,
            False,
            "skipped: an actual failure or broken open verify blocks {} open requirement(s)".format(
                len(open_rows)
            ),
        )
    else:
        from apatch.spec_rebind import attest_verified_requirements

        verify_results = {
            str(r.get("id")): runner.is_green(r.get("verify"))
            for r in requirements
            if r.get("verify")
        }
        rebound = attest_verified_requirements(
            root,
            spec=spec_id,
            verify_results=verify_results,
            evidence=runner.evidence(),
        )
        if not rebound.get("ok"):
            return _error("rebind", str(rebound.get("error")), slug_n, stages,
                          hint="Rebind could not read the spec/ledger; fix that and re-run.")
        attested = list(rebound.get("attested") or [])
        primary_ids = {str(r["id"]) for r in primary_rows}
        stale_ids = {str(r["id"]) for r in stale_rows}
        primary_attested = [rid for rid in attested if rid in primary_ids]
        reattested = [rid for rid in attested if rid in stale_ids]
        rebind_errors = list(rebound.get("errors") or [])
        attestation_stats = {
            "sessions_opened": int(rebound.get("sessions_opened") or 0),
            "ledger_commits": int(rebound.get("ledger_commits") or 0),
        }
        _stage(stages, "rebind", started, not rebind_errors,
               "{} primary + {} stale requirement(s) attested in {} session / {} commit{}".format(
                   len(primary_attested), len(reattested),
                   attestation_stats["sessions_opened"],
                   attestation_stats["ledger_commits"],
                   f", {len(rebind_errors)} error(s)" if rebind_errors else ""))

    # ── gate (same measured results via the verify_runner seam) ─────────────
    started = time.monotonic()
    gate = _gate_verdict(root, spec_id, cfg, runner, failures, broken)
    _stage(stages, "gate", started, gate.get("verdict") != "failed",
           "{} (buckets: {})".format(gate.get("verdict"), gate.get("buckets")))

    ok = bool(can_rebind_open and gate.get("verdict") != "failed" and not rebind_errors)
    result: Dict[str, Any] = {
        "ok": ok,
        "complete": bool(green and gate.get("verdict") != "failed" and not rebind_errors),
        "ratified_with_advisories": bool(ok and broken),
        "slug": slug_n,
        "spec": spec_id,
        "workspace": root,
        "stages": stages,
        "files": files,
        "verify": verify_out,
        "attested": attested,
        "primary_attested": primary_attested,
        "reattested": reattested,
        "attestation": attestation_stats,
        "gate": gate,
        "next_action": _next_action(
            slug_n, spec_id, green, can_rebind_open, failures, broken, unverified_rows,
            primary_attested, reattested, rebind_errors, gate,
        ),
    }
    if rebind_errors:
        result["rebind_errors"] = rebind_errors
    return result


def slug_ratify_enriched(target_dir: str = ".", **kwargs: Any) -> Dict[str, Any]:
    from apatch.session_state import enrich_tool_response

    root = os.path.abspath(os.path.expanduser(target_dir or "."))
    return enrich_tool_response(
        "apatch_slug_ratify",
        slug_ratify_workspace(root, **kwargs),
        target_dir=root,
    )


# Exact ownership helpers are core-owned and re-exported under historical names.

# ── verify / gate helpers ────────────────────────────────────────────────────


def _unique_commands(requirements: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for req in requirements:
        cmd = req.get("verify")
        if cmd and cmd not in seen:
            seen.append(cmd)
    return seen


def _gate_verdict(
    root: str,
    spec_id: str,
    cfg: Dict[str, Any],
    runner: _OnceVerifyRunner,
    failures: List[str],
    broken: List[str],
) -> Dict[str, Any]:
    """Conformance verdict for this spec from the already-measured results."""
    if cfg.get("enabled"):
        from apatch.conformance import conformance_gate

        out = conformance_gate(
            root,
            specs=[spec_id],
            live=True,
            timeout=int(runner.timeout),
            verify_runner=runner,
        )
        gate: Dict[str, Any] = {
            "verdict": out.get("gate"),
            "buckets": out.get("buckets"),
            "enabled": True,
            "mode": out.get("mode"),
            "contract_holds": out.get("contract_holds"),
        }
        if out.get("reason"):
            gate["reason"] = out.get("reason")
        return gate

    # No standing conformance config — classify locally from the same results.
    from apatch.conformance import BROKEN, CONFORMANT, DRIFTED, STALE, classify_attestation
    from apatch.spec_coverage import spec_status_with_coverage

    final = spec_status_with_coverage(root, spec=spec_id)
    attest_bucket = classify_attestation(final.get("summary") or {}) if final.get("ok") else "unproven"
    if failures:
        bucket = DRIFTED
    elif broken:
        bucket = BROKEN
    elif attest_bucket == STALE:
        bucket = STALE
    else:
        bucket = CONFORMANT
    return {
        "verdict": "failed" if bucket == DRIFTED else "passed",
        "buckets": {bucket: 1},
        "enabled": False,
        "note": "conformance config absent — verdict computed locally from the measured verify results",
    }


# ── small shared helpers (sibling slug_* module conventions) ────────────────


def _stage(stages: List[Dict[str, Any]], name: str, started: float, ok: bool, summary: str) -> None:
    stages.append(
        {
            "name": name,
            "ok": bool(ok),
            "elapsed_sec": round(time.monotonic() - started, 3),
            "summary": summary,
        }
    )


def _error(stage: str, error: str, slug_n: str, stages: List[Dict[str, Any]], *, hint: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "slug": slug_n, "stage": stage, "error": error, "hint": hint, "stages": stages}
    out.update(extra)
    return out


def _next_action(
    slug_n: str,
    spec_id: str,
    green: bool,
    can_rebind_open: bool,
    failures: List[str],
    broken: List[str],
    unverified_rows: List[Dict[str, Any]],
    primary_attested: List[str],
    reattested: List[str],
    rebind_errors: List[Dict[str, Any]],
    gate: Dict[str, Any],
) -> str:
    if not can_rebind_open:
        labels = failures + broken + [
            "{}:missing_verify".format(r["id"]) for r in unverified_rows
        ]
        return (
            "Open verification is not rebindable for {} ({}); fix the actual failure or "
            "broken open verify, then re-run `apatch slug ratify {}`."
        ).format(spec_id, ", ".join(labels), slug_n)
    if rebind_errors:
        return "Rebind hit {} error(s) — inspect rebind_errors, then re-run slug ratify.".format(len(rebind_errors))
    if gate.get("verdict") == "failed":
        return "Gate failed: {} — check .apatch/conformance.json block_on policy.".format(
            gate.get("reason") or gate.get("buckets"))
    if broken:
        return (
            "{} advanced: {} primary + {} stale requirement(s) attested in one batch; "
            "{} already-closed broken verify reference(s) remain advisory ({}). Repair "
            "those SPEC links separately; they did not block green open work."
        ).format(
            spec_id,
            len(primary_attested),
            len(reattested),
            len(broken),
            ", ".join(broken),
        )
    return (
        "{} ratified: verify green (once), {} primary + {} stale requirement(s) "
        "attested in one batch, gate {}."
    ).format(spec_id, len(primary_attested), len(reattested), gate.get("verdict"))


def _file_record(root: str, path: str, *, count_rows: bool = False) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    from apatch.spec_coverage import _sha256_file

    record: Dict[str, Any] = {"path": _rel(root, path), "sha256": _sha256_file(path)}
    if count_rows:
        from apatch_search_workflows.slug_close import _read_tsv

        _fieldnames, rows = _read_tsv(path)
        record["rows"] = len(rows)
    return record


def _rel(root: str, path: str) -> str:
    try:
        return normalize_rel(os.path.relpath(path, root))
    except ValueError:
        return normalize_rel(path)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()
