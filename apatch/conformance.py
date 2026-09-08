"""Continuous conformance — opt-in standing contract-gate (RFP-035, Phase 1+2).

apatch gives *governance* (every change attested) but not *enforcement of state*:
nothing continuously proves the whole contract still holds after a change, so
specs are attested once and silently drift. This module adds the missing link —
a per-spec conformance state and a standing gate that classifies *all* gated
specs at once over the current tree.

Opt-in (A35-A): without ``.apatch/conformance.json`` this is inert. Contract
scope, blocking-vs-advisory, and schedule are each project's decision.

Two signals, deliberately separated (the dogfood lesson — RFP-035 A35-C):

* **attestation freshness** (``spec_status``: ``stale``) — cheap, from the ledger;
* **live conformance** (running each requirement's ``verify`` now) — the truth.

A spec whose attestation went stale but whose ``verify`` still passes is **stale**
(advisory — just re-attest), NOT **drifted**. ``drifted`` is reserved for a *live
red verify* — a real, blocking regression. Conflating the two makes the gate cry
wolf, which is the RFP's own anti-pattern. Blocking is therefore on ``drifted``
only; ``stale`` / ``unproven`` are surfaced as advisories.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import signal
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import Any, Callable, Dict, List, Optional, Tuple

CONFIG_REL = os.path.join(".apatch", "conformance.json")

# Per-spec conformance buckets (A35-C).
CONFORMANT = "conformant"      # attested, none stale, verify green (when live)
DRIFTED = "drifted"            # live verify exit 1 — a test actually FAILED → real, blocking regression
BROKEN = "broken"             # live verify exit >=2 — the verify can't RUN (renamed/missing test, collection/usage error) → advisory: fix the verify, not the code
STALE = "stale"               # attested but attestation stale; verify still green — advisory
UNPROVEN = "unproven"          # in contract but nothing proven (no verify / no spec)
IN_PROGRESS = "in_progress"    # partially proven, actively in flight
_BUCKETS = (CONFORMANT, DRIFTED, BROKEN, STALE, UNPROVEN, IN_PROGRESS)

_VERIFY_TIMEOUT = int(os.environ.get("APATCH_CONFORMANCE_TIMEOUT_SEC", "60") or "60")
_DETAIL_TAIL_CHARS = int(os.environ.get("APATCH_CONFORMANCE_DETAIL_TAIL", "4000") or "4000")
_DEFAULT_LIVE_JOBS = int(os.environ.get("APATCH_CONFORMANCE_JOBS", "4") or "4")
_DEFAULT_REQUIREMENT_JOBS = int(os.environ.get("APATCH_CONFORMANCE_REQUIREMENT_JOBS", "1") or "1")


def conformance_identity(root: str = ".") -> Dict[str, str]:
    """Workspace provenance for conformance output.

    Agents regularly run conformance from nested checkouts or remote workspaces;
    surfacing the resolved workspace and config path makes "looked at the wrong
    repo" visible before interpreting green/red buckets.
    """
    workspace = os.path.abspath(root)
    return {
        "workspace": workspace,
        "cwd": os.getcwd(),
        "config_path": os.path.join(workspace, CONFIG_REL),
    }


def load_conformance_config(root: str) -> Dict[str, Any]:
    """Read ``.apatch/conformance.json``; absent → disabled (no behaviour change)."""
    path = os.path.join(os.path.abspath(root), CONFIG_REL)
    if not os.path.isfile(path):
        return {"enabled": False, "present": False, "config_path": path}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return {"enabled": False, "present": True, "error": "conformance.json is not an object"}
    except (OSError, json.JSONDecodeError) as exc:
        return {"enabled": False, "present": True, "error": f"unreadable conformance.json: {exc}"}
    cfg.setdefault("enabled", False)
    cfg.setdefault("mode", "advisory")
    cfg.setdefault("live_verify", False)
    cfg.setdefault("block_on", [DRIFTED])  # which buckets fail the gate — the project's policy
    cfg.setdefault("present", True)
    cfg["config_path"] = path
    return cfg


def resolve_block_on(cfg: Dict[str, Any]) -> List[str]:
    """The buckets a project chose to block on (default: a real red verify only).
    apatch gives the lever; the project decides its own blocking policy (R1)."""
    raw = cfg.get("block_on") or [DRIFTED]
    chosen = [b for b in raw if b in _BUCKETS]
    return chosen or [DRIFTED]


# A contract id is `SPEC-` plus ordinary id characters; a heading still holding a
# scaffold placeholder (`SPEC-<ID>`) is an authoring aid, never a contract.
_WELL_FORMED_SPEC_ID = re.compile(r"^SPEC-[A-Za-z0-9][A-Za-z0-9._-]*$")


def gated_specs(root: str, cfg: Dict[str, Any]) -> List[str]:
    """The contract for this project: explicit ``contract.specs``, else the spec
    registry filtered by an optional ``contract.spec_glob`` (default: all)."""
    contract = cfg.get("contract") or {}
    explicit = contract.get("specs")
    if explicit:
        return [str(s) for s in explicit if s]
    root = os.path.abspath(root)
    specs_dir = os.path.join(root, ".apatch", "specs")
    ids: List[str] = []
    if os.path.isdir(specs_dir):
        for fn in sorted(os.listdir(specs_dir)):
            if fn.endswith(".json"):
                ids.append(fn[:-5])
    # The registry cache (.apatch/specs/*.json) is gitignored, so a fresh checkout
    # (CI) has none — gating it would gate an EMPTY contract. The committed source of
    # truth is docs/specs/SPEC-*.md, so fall back to those ids: the gate covers the
    # real contract in CI, not nothing.
    if not ids:
        docs_specs = os.path.join(root, "docs", "specs")
        if os.path.isdir(docs_specs):
            ids = [
                fn[:-3]
                for fn in sorted(os.listdir(docs_specs))
                if fn.startswith("SPEC-") and fn.endswith(".md")
            ]
    # Templates/scaffolds (e.g. SPEC-TEMPLATE.md) carry placeholder verify commands
    # like `tests/test_<id>_r1.py` and are authoring aids, not real contracts — a
    # gate must never treat them as a spec under contract (they always "fail").
    # Excluding them by name is not enough: the registry stores the PARSED id, so the
    # template lands there under its placeholder heading `SPEC-<ID>`, which carries no
    # "TEMPLATE" and turned the standing gate red on a file nobody wrote as a contract.
    ids = [
        i
        for i in ids
        if "TEMPLATE" not in i.upper() and _WELL_FORMED_SPEC_ID.match(i)
    ]
    pattern = contract.get("spec_glob")
    if pattern:
        ids = [i for i in ids if fnmatch.fnmatch(i, pattern)]
    return ids


def classify_attestation(summary: Dict[str, Any]) -> str:
    """Bucket from attestation state alone — never ``drifted`` (that needs a live
    red verify). Attestation staleness maps to ``stale`` (advisory)."""
    total = int(summary.get("total", 0) or 0)
    attested = int(summary.get("attested", 0) or 0)
    stale = int(summary.get("stale", 0) or 0)
    blocked = int(summary.get("blocked", 0) or 0)
    pending = int(summary.get("pending", 0) or 0)
    if total == 0:
        return UNPROVEN
    if attested == total and stale == 0 and blocked == 0:
        return CONFORMANT
    if stale > 0:
        return STALE
    if attested == 0 and pending == total:
        return UNPROVEN
    return IN_PROGRESS


def is_ci_safe(cmd: str) -> bool:
    """A verify is CI-safe only if it is a **single, pure pytest invocation** —
    no shell chaining (``&&``/``;``/``|``), no extra tools (``rg``, ``test -d``),
    no sibling repo (``../``), no apatch-self/ledger calls. Such a verify runs
    deterministically in a fresh CI checkout, so a gate can block on it without
    crying wolf. Everything else is advisory-skipped in ci_safe mode."""
    c = cmd.strip()
    if any(tok in c for tok in ("&&", ";", "|", "../", "`", "$(")):
        return False
    return c.startswith(("python3 -m pytest", "python -m pytest", "pytest "))


def _tail_text(value: bytes | str | None, limit: int = _DETAIL_TAIL_CHARS) -> str:
    """Decode and bound subprocess output for agent-facing conformance diagnostics."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-limit:]


def _positive_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _run_verify_command(root: str, rid: str, cmd: str, timeout: float) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        duration = round(time.monotonic() - started, 3)
        rc = proc.returncode
        detail = None
        failure = None
        broken = None
        if rc == 1:
            failure = rid
        elif rc != 0:
            broken = f"{rid}:exit{rc}"
        if rc != 0:
            detail = {
                "id": rid,
                "kind": "failure" if rc == 1 else "broken",
                "cmd": cmd,
                "exit_code": rc,
                "duration_sec": duration,
                "stdout_tail": _tail_text(stdout),
                "stderr_tail": _tail_text(stderr),
            }
        return {"id": rid, "failure": failure, "broken": broken, "detail": detail}
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=2)
        except (OSError, ProcessLookupError):
            pass
        return {
            "id": rid,
            "failure": None,
            "broken": f"{rid}:timeout",
            "detail": {
                "id": rid,
                "kind": "broken",
                "cmd": cmd,
                "exit_code": "timeout",
                "duration_sec": round(timeout, 3),
                "stdout_tail": _tail_text(getattr(exc, "stdout", None)),
                "stderr_tail": (_tail_text(getattr(exc, "stderr", None)) + f"\nverify timed out after {round(timeout, 3)}s").strip(),
            },
        }
    except OSError as exc:
        return {
            "id": rid,
            "failure": None,
            "broken": f"{rid}:{type(exc).__name__}",
            "detail": {
                "id": rid,
                "kind": "broken",
                "cmd": cmd,
                "exit_code": type(exc).__name__,
                "duration_sec": 0,
                "stdout_tail": "",
                "stderr_tail": str(exc),
            },
        }


def run_spec_verify(
    root: str,
    requirements: List[Dict[str, Any]],
    *,
    timeout: float = _VERIFY_TIMEOUT,
    budget_sec: Optional[float] = None,
    ci_safe: bool = False,
    capture_details: bool = False,
    fail_fast: bool = False,
    requirement_jobs: int = 1,
) -> Tuple[int, List[str], List[str]] | Tuple[int, List[str], List[str], List[Dict[str, Any]]]:
    """Run each requirement's ``verify`` live. Returns ``(ran, failures, broken)``:

    * ``failures`` — exit 1: a test ran and **FAILED** → a real regression.
    * ``broken`` — exit >=2 (incl. pytest 4=usage/not-found, 5=no tests, timeout,
      OSError): the verify could not run the intended test (e.g. a renamed/deleted
      test, a collection error, an env-skip). The *verify command* is broken, not
      necessarily the code — so the gate must not call this a regression.

    Distinguishing the two is what stops the gate crying wolf on rotted verify
    references and env-dependent specs. With ``ci_safe=True``, verifies that depend
    on a sibling repo or the local ledger are skipped (not run, not counted)."""
    ran = 0
    failures: List[str] = []
    broken: List[str] = []
    details: List[Dict[str, Any]] = []
    candidates: List[Tuple[int, str, str]] = []
    for index, r in enumerate(requirements):
        cmd = r.get("verify")
        if not cmd:
            continue
        if ci_safe and not is_ci_safe(cmd):
            continue
        candidates.append((index, r.get("id") or "?", cmd))

    def _collect(result: Dict[str, Any]) -> None:
        failure = result.get("failure")
        broken_label = result.get("broken")
        if failure:
            failures.append(str(failure))
        if broken_label:
            broken.append(str(broken_label))
        if capture_details and result.get("detail"):
            details.append(dict(result["detail"]))

    workers = max(1, min(int(requirement_jobs or 1), len(candidates) or 1))
    deadline = time.monotonic() + float(budget_sec) if budget_sec else None
    if workers > 1 and len(candidates) > 1:
        next_pos = 0
        stopped = False
        active: Dict[Any, Tuple[int, str]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            def submit_next() -> bool:
                nonlocal next_pos, stopped
                if stopped or next_pos >= len(candidates):
                    return False
                index, rid, cmd = candidates[next_pos]
                next_pos += 1
                cmd_timeout = float(timeout)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        broken.append(f"{rid}:budget_exhausted")
                        if capture_details:
                            details.append(
                                {
                                    "id": rid,
                                    "kind": "broken",
                                    "cmd": cmd,
                                    "exit_code": "budget_exhausted",
                                    "duration_sec": budget_sec,
                                    "stdout_tail": "",
                                    "stderr_tail": f"spec verify budget exhausted after {budget_sec}s",
                                }
                            )
                        stopped = True
                        return False
                    cmd_timeout = min(cmd_timeout, remaining)
                future = executor.submit(_run_verify_command, root, rid, cmd, cmd_timeout)
                active[future] = (index, rid)
                return True

            while len(active) < workers and submit_next():
                pass
            while active:
                wait_timeout = None
                if deadline is not None:
                    wait_timeout = max(0.0, deadline - time.monotonic())
                done, _pending = wait(active, timeout=wait_timeout, return_when=FIRST_COMPLETED)
                if not done:
                    stopped = True
                    for future, (_index, rid) in active.items():
                        future.cancel()
                        broken.append(f"{rid}:budget_exhausted")
                        if capture_details:
                            details.append(
                                {
                                    "id": rid,
                                    "kind": "broken",
                                    "cmd": "<parallel verify budget>",
                                    "exit_code": "budget_exhausted",
                                    "duration_sec": budget_sec,
                                    "stdout_tail": "",
                                    "stderr_tail": f"spec verify budget exhausted after {budget_sec}s",
                                }
                            )
                        break
                    break
                for future in sorted(done, key=lambda f: active[f][0]):
                    _index, _rid = active.pop(future)
                    ran += 1
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001 — classify worker failure as broken
                        result = {
                            "id": _rid,
                            "failure": None,
                            "broken": f"{_rid}:{type(exc).__name__}",
                            "detail": {
                                "id": _rid,
                                "kind": "broken",
                                "cmd": "<verify worker>",
                                "exit_code": type(exc).__name__,
                                "duration_sec": 0,
                                "stdout_tail": "",
                                "stderr_tail": str(exc),
                            },
                        }
                    _collect(result)
                    if fail_fast and (result.get("failure") or result.get("broken")):
                        stopped = True
                while not stopped and len(active) < workers and submit_next():
                    pass
        if capture_details:
            return ran, failures, broken, details
        return ran, failures, broken

    for _index, rid, cmd in candidates:
        cmd_timeout = float(timeout)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                broken.append(f"{rid}:budget_exhausted")
                if capture_details:
                    details.append(
                        {
                            "id": rid,
                            "kind": "broken",
                            "cmd": cmd,
                            "exit_code": "budget_exhausted",
                            "duration_sec": budget_sec,
                            "stdout_tail": "",
                            "stderr_tail": f"spec verify budget exhausted after {budget_sec}s",
                        }
                    )
                break
            cmd_timeout = min(cmd_timeout, remaining)
        ran += 1
        result = _run_verify_command(root, rid, cmd, cmd_timeout)
        _collect(result)
        if deadline is not None and time.monotonic() >= deadline:
            break
        if fail_fast and (result.get("failure") or result.get("broken")):
            break
    if capture_details:
        return ran, failures, broken, details
    return ran, failures, broken


def changed_files(root: str, base: str) -> Optional[set]:
    """Files changed since ``base`` (git). ``None`` = unknown → verify everything."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "diff", "--name-only", base],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return None


def _spec_touched(spec_status: Dict[str, Any], changed: Optional[set]) -> bool:
    """True if any requirement's attested files intersect ``changed`` (or unknown)."""
    if changed is None:
        return True
    for r in spec_status.get("requirements") or []:
        for f in r.get("files") or []:
            if f in changed:
                return True
    return False


def conformance_status(
    root: str = ".",
    *,
    specs: Optional[List[str]] = None,
    live: bool = False,
    no_live: bool = False,
    base: Optional[str] = None,
    ci_safe: bool = False,
    timeout: Optional[int] = None,
    jobs: Optional[int] = None,
    spec_budget_sec: Optional[int] = None,
    fail_fast: Optional[bool] = None,
    requirement_jobs: Optional[int] = None,
    verify_runner: Optional[Callable[[str, List[Dict[str, Any]]], Tuple[int, List[str]]]] = None,
) -> Dict[str, Any]:
    """Classify every gated spec over the current tree, all buckets at once
    (A35-B/C/D). With ``live=True`` each spec's ``verify`` runs now, so ``drifted``
    means a *real red verify* (not just a stale attestation). With ``base`` set,
    live verify is **baseline-aware** (A35-H): only specs whose attested files
    changed since ``base`` are re-run; untouched specs keep their attestation
    state — so the gate scales without re-running the whole contract. ``specs``
    overrides the configured contract (tests)."""
    root = os.path.abspath(root)
    cfg = load_conformance_config(root)
    identity = conformance_identity(root)
    if not cfg.get("enabled"):
        return {
            "ok": True,
            "enabled": False,
            "skipped": cfg.get("error") or "conformance disabled (no .apatch/conformance.json)",
            **identity,
        }

    live = False if no_live else bool(live or cfg.get("live_verify"))
    base = base or (cfg.get("baseline") if isinstance(cfg.get("baseline"), str) else None)
    ci_safe = bool(ci_safe or cfg.get("ci_safe"))
    verify_timeout = _positive_int(timeout, _positive_int(cfg.get("verify_timeout_sec"), _VERIFY_TIMEOUT)) or _VERIFY_TIMEOUT
    per_spec_budget = _positive_int(spec_budget_sec, _positive_int(cfg.get("spec_budget_sec"), None))
    live_jobs = _positive_int(jobs, _positive_int(cfg.get("jobs"), _DEFAULT_LIVE_JOBS)) or 1
    live_requirement_jobs = _positive_int(
        requirement_jobs,
        _positive_int(cfg.get("requirement_jobs"), _DEFAULT_REQUIREMENT_JOBS),
    ) or 1
    live_fail_fast = bool(cfg.get("fail_fast", True) if fail_fast is None else fail_fast)
    runner = verify_runner or (
        lambda r, reqs: run_spec_verify(
            r,
            reqs,
            timeout=verify_timeout,
            budget_sec=per_spec_budget,
            ci_safe=ci_safe,
            capture_details=True,
            fail_fast=live_fail_fast,
            requirement_jobs=live_requirement_jobs,
        )
    )
    from apatch.spec import spec_status_workspace

    scope = changed_files(root, base) if (live and base) else None
    contract = list(specs) if specs is not None else gated_specs(root, cfg)
    buckets: Dict[str, List[str]] = {b: [] for b in _BUCKETS}
    per_spec: List[Dict[str, Any]] = []

    def _evaluate_spec(index: int, sid: str) -> Tuple[int, Dict[str, Any], str, int]:
        try:
            st = spec_status_workspace(root, spec=sid)
        except Exception as exc:  # noqa: BLE001 — a broken spec is a hole, not a crash
            return index, {"spec": sid, "conformance": UNPROVEN, "error": f"{type(exc).__name__}: {exc}"}, UNPROVEN, 0
        if not st.get("ok"):
            return index, {"spec": sid, "conformance": UNPROVEN, "error": st.get("error")}, UNPROVEN, 0

        reqs = st.get("requirements") or []
        attest_bucket = classify_attestation(st.get("summary") or {})
        bucket = attest_bucket
        entry: Dict[str, Any] = {"spec": sid, "summary": st.get("summary")}
        # In live mode the live verify is the truth — run it whenever the spec HAS a
        # verify command, regardless of attestation state. (Crucial in a fresh CI
        # checkout: the ledger is absent so every spec is attestation-`unproven`, but
        # the verify commands come from the committed spec md and must still run —
        # otherwise the gate sees the contract but never checks it.) Baseline-aware
        # (A35-H): with a base, only re-verify specs whose files moved.
        has_verify = any(r.get("verify") for r in reqs)
        do_live = live and has_verify and (base is None or _spec_touched(st, scope))
        verified_inc = 0
        if do_live:
            verified_inc = 1
            try:
                res = runner(root, reqs)
            except Exception as exc:  # noqa: BLE001 — conformance must report a broken verify, not crash
                res = (
                    0,
                    [],
                    [f"runner:{type(exc).__name__}"],
                    [
                        {
                            "id": "?",
                            "kind": "broken",
                            "cmd": "<conformance runner>",
                            "exit_code": type(exc).__name__,
                            "duration_sec": 0,
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    ],
                )
            details: List[Dict[str, Any]] = []
            if len(res) >= 4:
                ran, failures, broken, details = res[0], res[1], res[2], list(res[3] or [])
            elif len(res) == 3:
                ran, failures, broken = res
            else:
                ran, failures, broken = res[0], res[1], []  # 2-tuple back-compat
            entry["verify_ran"] = ran
            if failures:
                bucket = DRIFTED  # exit 1 — a test failed → real, blocking regression
                entry["verify_failures"] = failures
                if details:
                    entry["verify_details"] = details
                if broken:
                    entry["verify_broken"] = broken
            elif broken:
                bucket = BROKEN   # exit >=2 — the verify can't run → advisory, fix the verify
                entry["verify_broken"] = broken
                if details:
                    entry["verify_details"] = details
            elif ran > 0:
                # all green live → the contract holds for this spec. Keep STALE only when
                # the ledger says the attestation drifted (advisory, re-attest); otherwise
                # conformant — a green live verify is the truth even without ledger attestation.
                bucket = STALE if attest_bucket == STALE else CONFORMANT
        if bucket == STALE:
            entry["recommended_mcp"] = f"apatch_rebind_stale(target_dir='.', spec='{sid}')"
            entry["recommended_cli"] = f"apatch spec rebind-stale --spec {sid} --target-dir ."
            entry["recommended_action"] = f"MCP: {entry['recommended_mcp']} | CLI: {entry['recommended_cli']}"
        entry["conformance"] = bucket
        return index, entry, bucket, verified_inc

    results: List[Optional[Tuple[int, Dict[str, Any], str, int]]] = [None] * len(contract)
    if live and live_jobs > 1 and len(contract) > 1:
        with ThreadPoolExecutor(max_workers=min(live_jobs, len(contract))) as executor:
            futures = [executor.submit(_evaluate_spec, idx, sid) for idx, sid in enumerate(contract)]
            for future in as_completed(futures):
                idx, entry, bucket, verified_inc = future.result()
                results[idx] = (idx, entry, bucket, verified_inc)
    else:
        for idx, sid in enumerate(contract):
            results[idx] = _evaluate_spec(idx, sid)

    verified = 0
    for idx, result in enumerate(results):
        if result is None:
            sid = contract[idx]
            result = (idx, {"spec": sid, "conformance": BROKEN, "error": "internal missing conformance result"}, BROKEN, 0)
        _, entry, bucket, verified_inc = result
        buckets[bucket].append(entry["spec"])
        per_spec.append(entry)
        verified += verified_inc

    # The project chooses which buckets block (default: a real red verify only).
    # stale/unproven are advisories unless the project opts them into block_on.
    block_on = resolve_block_on(cfg)
    holds = not any(buckets[b] for b in block_on)
    return {
        "ok": True,
        "enabled": True,
        "mode": cfg.get("mode", "advisory"),
        "block_on": block_on,
        "live": live,
        "base": base,
        "ci_safe": ci_safe,
        "jobs": live_jobs if live else 1,
        "requirement_jobs": live_requirement_jobs if live else 1,
        "verify_timeout_sec": verify_timeout,
        "spec_budget_sec": per_spec_budget,
        "fail_fast": live_fail_fast if live else False,
        "workspace": identity["workspace"],
        "cwd": identity["cwd"],
        "config_path": identity["config_path"],
        "verified_live": verified,
        "contract_holds": holds,
        "gated": len(contract),
        "buckets": {b: len(v) for b, v in buckets.items()},
        "drifted": buckets[DRIFTED],
        "broken": buckets[BROKEN],
        "stale": buckets[STALE],
        "unproven": buckets[UNPROVEN],
        "in_progress": buckets[IN_PROGRESS],
        "per_spec": per_spec,
    }


def conformance_gate(
    root: str = ".",
    *,
    base: Optional[str] = None,
    live: bool = False,
    no_live: bool = False,
    ci_safe: bool = False,
    specs: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    jobs: Optional[int] = None,
    spec_budget_sec: Optional[int] = None,
    fail_fast: Optional[bool] = None,
    requirement_jobs: Optional[int] = None,
    verify_runner: Optional[Callable[[str, List[Dict[str, Any]]], Tuple[int, List[str]]]] = None,
) -> Dict[str, Any]:
    """Standing gate: red only on a real ``drifted`` (live red verify). In
    ``blocking`` mode that fails (``ok=False``); ``advisory`` reports only
    (A35-F). ``stale`` / ``unproven`` are surfaced but never block. With ``base``
    set, live verify is baseline-aware (A35-H) — only changed specs are re-run.
    With ``ci_safe`` set, env-dependent verifies (sibling repo / ledger) are
    skipped, so a fresh-checkout CI gate blocks only on real, self-contained
    regressions — no cry wolf (A35-E, pragmatic: blocks without the local ledger).
    ``verify_runner`` (slug ratify seam): a precomputed/caching runner so the
    verdict reuses already-measured verify results instead of re-running them."""
    status = conformance_status(
        root,
        specs=specs,
        live=live,
        no_live=no_live,
        base=base,
        ci_safe=ci_safe,
        timeout=timeout,
        jobs=jobs,
        spec_budget_sec=spec_budget_sec,
        fail_fast=fail_fast,
        requirement_jobs=requirement_jobs,
        verify_runner=verify_runner,
    )
    if not status.get("enabled"):
        return {
            "ok": True,
            "gate": "skipped",
            "skipped": status.get("skipped"),
            "workspace": status.get("workspace"),
            "cwd": status.get("cwd"),
            "config_path": status.get("config_path"),
        }

    blocking = status.get("mode") == "blocking"
    holds = bool(status.get("contract_holds"))
    out = dict(status)
    if holds:
        out["gate"] = "passed"
        out["ok"] = True
        adv = {k: len(status.get(k) or []) for k in ("broken", "stale", "unproven") if status.get(k)}
        if adv:
            out["advisories"] = adv
    else:
        out["gate"] = "failed" if blocking else "advisory"
        out["ok"] = not blocking
        parts = [f"{len(status[b])} {b}" for b in status.get("block_on", ["drifted"]) if status.get(b)]
        out["reason"] = "contract not conformant: " + ", ".join(parts)
    return out


# ── Gate falsification → one driver of the unified differential probe (RFP-005) ────
# falsify/regress/ratify share one shape — perturb, re-measure, judge the delta against
# a polarity. The engine now lives in apatch/probe.py; ``falsify_requirement`` is kept
# here as a back-compat name: it corrupts the files a requirement guards and asserts its
# (verify:) DIVERGES (goes RED); a gate that stays green is a FALSE gate.
from apatch.probe import (  # noqa: E402,F401  (re-exported for back-compat)
    _corrupt,
    _run_verify_rc,
    falsify,
    falsify_requirement,
    verify_health,
)
