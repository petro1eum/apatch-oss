"""Differential probe (RFP-005) — the unified "perturb and watch the delta" primitive.

apatch had three one-off checks that share one shape: apply a *perturbation*, re-measure
a *signal*, and judge the *delta* against an expected *polarity*. This module unifies them
into a single core (``differential_probe``) with three thin, domain-agnostic drivers:

* **falsify** — perturbation = corrupt the files a requirement guards (endogenous);
  baseline = verify green; polarity = ``must_diverge`` (the gate MUST react). A gate that
  stays green is FALSE — the test does not test the requirement.
* **regress** — perturbation = the new code since a recorded baseline (exogenous);
  baseline = recorded failing tests; polarity = ``must_hold`` (NO new failures). A new
  failure vs the baseline corpus is a regression.
* **ratify** — perturbation = elapsed time / a moved world (exogenous); baseline = the
  attested-green state; polarity = ``must_hold`` (the gate MUST still pass). An attested
  gate that is no longer green is a STALE attestation.

The primitive is universal: a *signal* is any ``measure() -> health`` callable, a
*perturbation* is any reversible mutation (or the identity, for an exogenous one already
present in the tree), and the verdict is a pure function of (baseline, observed, polarity).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence

from apatch.verify_baseline import parse_failed_tests

_VERIFY_TIMEOUT = 300

POLARITY_DIVERGE = "must_diverge"  # the signal MUST change under perturbation (sensitivity)
POLARITY_HOLD = "must_hold"        # the signal MUST stay healthy (stability)

_FALSIFY_PY_MUTANT = 'raise RuntimeError("apatch-falsify mutant - proves the gate is real")\n'
_FALSIFY_GENERIC_MUTANT = "APATCH_FALSIFY_MUTANT_INVALID_$$$\n"


# ── primitives: a signal and a perturbation ──────────────────────────────────────
def _run_verify_rc(root: str, cmd: str, timeout: int) -> int:
    """Run a verify command from the workspace root; return its exit code (0 = green)."""
    try:
        proc = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, timeout=timeout)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127


def verify_health(root: str, cmd: str, timeout: int) -> Dict[str, Any]:
    """Measure a gate's health: exit code + the set of failing test ids (best effort)."""
    try:
        proc = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, timeout=timeout)
        rc = proc.returncode
        out = (proc.stdout or b"").decode("utf-8", "replace") + \
            (proc.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": 124, "failures": frozenset()}
    except OSError:
        return {"ok": False, "rc": 127, "failures": frozenset()}
    return {"ok": rc == 0, "rc": rc, "failures": frozenset(parse_failed_tests(out))}


def _corrupt(path: str, mode: str = "prepend") -> str:
    """Perturb a file; return its original content (for restore).

    ``prepend`` keeps the content and adds a mutant: that falsifies a gate asserting
    something is absent, or that the file still parses. ``replace`` drops the content
    instead, because a gate asserting a line is PRESENT -- every traceability table is
    one -- cannot be broken by adding text. Prepending alone reported all of them as
    false, so the tool built to catch a false gate was crying wolf on real ones.
    """
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    mutant = _FALSIFY_PY_MUTANT if path.endswith(".py") else _FALSIFY_GENERIC_MUTANT
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(mutant if mode == "replace" else mutant + original)
    return original


# ── the unified core ─────────────────────────────────────────────────────────────
def _degraded(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Did health get worse from ``before`` to ``after``? Pure.

    Worse = a green baseline turned red, OR new failing tests appeared that were not
    failing in the baseline. Pre-existing baseline failures do NOT count — so this is
    baseline-aware by construction (the regression semantics)."""
    became_red = bool(before.get("ok", True)) and not bool(after.get("ok", True))
    base_fail = set(before.get("failures") or [])
    new_fail = sorted(set(after.get("failures") or []) - base_fail)
    return {"degraded": became_red or bool(new_fail), "new_failures": new_fail,
            "became_red": became_red}


def differential_probe(
    *,
    measure: Callable[[], Dict[str, Any]],
    polarity: str,
    perturb: Optional[Callable[[], Callable[[], None]]] = None,
    baseline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Perturb (optionally) and watch the delta; judge it against ``polarity``.

    * ``measure()`` returns a health snapshot ``{"ok": bool, "failures": set, ...}``.
    * ``perturb()`` applies a reversible perturbation and returns a ``restore()``
      callable; pass ``None`` for an exogenous perturbation (new code / a moved world)
      already present in the tree.
    * ``baseline`` is the reference health; if ``None`` it is measured before perturb.
    * ``polarity`` — ``must_diverge`` (the perturbation MUST degrade the signal) or
      ``must_hold`` (the signal MUST stay healthy).

    ``perturb``'s ``restore()`` ALWAYS runs (finally), so the tree is never left dirty.
    """
    before = baseline if baseline is not None else measure()
    if polarity == POLARITY_DIVERGE and not before.get("ok", True):
        # cannot demonstrate sensitivity on an already-red gate
        return {"verdict": "baseline_red", "passed": False, "polarity": polarity,
                "before": before, "after": None, "degraded": False, "new_failures": []}
    restore = perturb() if perturb is not None else None
    try:
        after = measure()
    finally:
        if restore is not None:
            restore()
    delta = _degraded(before, after)
    deg = delta["degraded"]
    passed = deg if polarity == POLARITY_DIVERGE else (not deg)
    return {"verdict": "diverged" if deg else "held", "passed": passed, "polarity": polarity,
            "before": before, "after": after, "degraded": deg,
            "new_failures": delta["new_failures"], "became_red": delta["became_red"]}


def _record_falsify_result(
    root: str,
    verify: str,
    files: Sequence[str],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Bind a differential gate result to the active governed session."""
    enriched = dict(result)
    verdict = str(result.get("verdict") or "")
    if verdict == "real" and result.get("restored_ok") is True:
        gate_quality = "falsified"
    elif verdict == "false":
        gate_quality = "false_gate"
    else:
        enriched["recorded"] = False
        enriched["recording_reason"] = "result_not_eligible"
        return enriched
    enriched["gate_quality"] = gate_quality

    from apatch.session_state import load_session_state

    state = load_session_state(root)
    if not state.get("session_id") or state.get("ended_at"):
        enriched["recorded"] = False
        enriched["recording_reason"] = "no_active_governed_session"
        return enriched

    payload = {
        "action": "probe_falsify",
        "mode": "falsify",
        "verdict": verdict,
        "gate_quality": gate_quality,
        "verify_sha256": hashlib.sha256(verify.encode("utf-8")).hexdigest(),
        "files": list(files),
        "restored_ok": bool(result.get("restored_ok")),
    }
    from apatch.trustchain_helper import TrustChainHelper

    committed = bool(TrustChainHelper(root).commit_action("apatch_probe", payload))
    enriched["recorded"] = committed
    if not committed:
        enriched["recording_reason"] = "trustchain_commit_failed"
    return enriched


# ── driver 1: falsify (must_diverge — corrupt the files the gate guards) ──────────
def falsify(
    root: str,
    verify: Optional[str],
    files: Sequence[str],
    *,
    timeout: int = _VERIFY_TIMEOUT,
    record: bool = False,
) -> Dict[str, Any]:
    """Prove a requirement's verify gate can go RED: corrupt the files it guards and
    assert verify fails, then restore. ``verdict``: ``real`` (killed), ``false``
    (survived = the gate does not test this), ``baseline_red`` / ``no_verify`` /
    ``no_files`` / ``missing_files``. Guarded files are ALWAYS restored (finally)."""
    root = os.path.abspath(root)
    files = [f for f in (files or []) if f]
    if not verify:
        return {"mode": "falsify", "verdict": "no_verify", "killed": False}
    if not files:
        return {"mode": "falsify", "verdict": "no_files", "killed": False,
                "note": "no guarded files given — pass the files this requirement protects"}
    abs_files = [os.path.join(root, f) for f in files]
    missing = [f for f, a in zip(files, abs_files) if not os.path.isfile(a)]
    if missing:
        return {"mode": "falsify", "verdict": "missing_files", "killed": False, "missing": missing}

    def _perturbation(mode: str) -> Callable[[], Callable[[], None]]:
        def perturb() -> Callable[[], None]:
            originals = {a: _corrupt(a, mode) for a in abs_files}

            def restore() -> None:
                for a, content in originals.items():
                    try:
                        with open(a, "w", encoding="utf-8") as fh:
                            fh.write(content)
                    except OSError:
                        pass
            return restore
        return perturb

    def measure() -> Dict[str, Any]:
        return verify_health(root, verify, timeout)

    res = differential_probe(
        measure=measure, perturb=_perturbation("prepend"), polarity=POLARITY_DIVERGE,
    )
    if res["verdict"] == "baseline_red":
        return {"mode": "falsify", "verdict": "baseline_red", "killed": False,
                "baseline_rc": (res.get("before") or {}).get("rc"),
                "note": "verify is not green to begin with — cannot falsify"}
    mutation = "prepend"
    if not res["passed"] and any(not f.endswith(".py") for f in files):
        # Adding text cannot break a presence check. Try removal before calling the
        # gate false, or every traceability table is reported as a false gate.
        removal = differential_probe(
            measure=measure, perturb=_perturbation("replace"), polarity=POLARITY_DIVERGE,
        )
        if removal["passed"]:
            res, mutation = removal, "replace"
    killed = bool(res["passed"])
    restored = verify_health(root, verify, timeout)
    result = {
        "mode": "falsify",
        "verdict": "real" if killed else "false",
        "killed": killed,
        "mutation": mutation,
        "files": list(files),
        "baseline_rc": (res.get("before") or {}).get("rc"),
        "mutated_rc": (res.get("after") or {}).get("rc"),
        "restored_rc": restored.get("rc"),
        "restored_ok": bool(restored.get("ok")),
    }
    return (
        _record_falsify_result(root, verify, files, result)
        if record
        else result
    )


# back-compat alias (older callers import ``falsify_requirement``)
def falsify_requirement(
    root,
    verify,
    files,
    *,
    timeout=_VERIFY_TIMEOUT,
    record=False,
):
    return falsify(root, verify, files, timeout=timeout, record=record)


# ── driver 2: regress (must_hold — no NEW failures vs a recorded corpus) ──────────
def regress(
    root: str,
    verify: Optional[str],
    *,
    baseline_failures: Sequence[str] = (),
    allowed_failures: Sequence[str] = (),
    timeout: int = _VERIFY_TIMEOUT,
) -> Dict[str, Any]:
    """Stability vs a recorded baseline/corpus: the current tree must introduce NO new
    failures vs ``baseline_failures`` (loaded from ``.apatch/verify_baseline.json`` if
    not given). ``allowed_failures`` (id or substring) are tolerated. ``verdict``:
    ``stable`` / ``regressed`` / ``no_verify``."""
    root = os.path.abspath(root)
    if not verify:
        return {"mode": "regress", "verdict": "no_verify"}
    base: List[str] = list(baseline_failures)
    loaded_from = None
    if not base:
        from apatch.verify_baseline import load_baseline
        bl = load_baseline(root)
        if bl:
            base = list(bl.get("failures") or [])
            loaded_from = ".apatch/verify_baseline.json"
    res = differential_probe(
        measure=lambda: verify_health(root, verify, timeout),
        perturb=None, polarity=POLARITY_HOLD,
        baseline={"ok": not base, "failures": frozenset(base)},
    )

    def _allowed(node: str) -> bool:
        return any(p and (p == node or p in node) for p in allowed_failures)

    new = [n for n in res["new_failures"] if not _allowed(n)]
    return {
        "mode": "regress",
        "verdict": "stable" if not new else "regressed",
        "new_failures": new,
        "allowed_skipped": [n for n in res["new_failures"] if _allowed(n)],
        "baseline_count": len(base),
        "baseline_source": loaded_from,
        "current_rc": (res.get("after") or {}).get("rc"),
    }


# ── driver 3: ratify (must_hold — an attested gate must STILL be green now) ────────
def ratify(
    root: str,
    verify: Optional[str],
    *,
    timeout: int = _VERIFY_TIMEOUT,
) -> Dict[str, Any]:
    """Re-earn an attestation: an attested-green gate must STILL pass now (the world
    moved; we apply no perturbation of our own). ``verdict``: ``ratified`` (still green)
    / ``stale`` (was green, now red) / ``no_verify``."""
    root = os.path.abspath(root)
    if not verify:
        return {"mode": "ratify", "verdict": "no_verify"}
    res = differential_probe(
        measure=lambda: verify_health(root, verify, timeout),
        perturb=None, polarity=POLARITY_HOLD,
        baseline={"ok": True, "failures": frozenset()},
    )
    return {
        "mode": "ratify",
        "verdict": "ratified" if res["passed"] else "stale",
        "current_rc": (res.get("after") or {}).get("rc"),
        "new_failures": res["new_failures"],
    }
