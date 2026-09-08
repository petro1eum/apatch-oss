"""Baseline-aware verify: pre-existing red tests must not block innocent mutations.

User feedback: a consumer repo often has old failing tests; a verify_run that fails
on them used to push the session to ``blocked`` and recommend rolling back unrelated
edits. The fix:

- ``apatch_verify_run(baseline='capture')`` BEFORE apply snapshots current failing
  pytest node ids to ``.apatch/verify_baseline.json``;
- ``apatch_verify_run(baseline='compare')`` AFTER apply passes when no NEW failures
  appeared vs the snapshot;
- ``allowed_failures=[node-id-or-substring]`` is a manual allow-list that works with
  or without a stored baseline.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence

BASELINE_REL = os.path.join(".apatch", "verify_baseline.json")

# pytest summary lines: `FAILED tests/test_x.py::test_y[case] - AssertionError`,
# `ERROR tests/test_z.py::test_w`. Works for -q / -rA summary formats.
_FAILED_LINE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def parse_failed_tests(output: str) -> List[str]:
    """Extract failing pytest node ids from verify output (deduped, ordered)."""
    seen = set()
    out: List[str] = []
    for m in _FAILED_LINE_RE.finditer(output or ""):
        node = m.group(1).rstrip(":")
        if node not in seen:
            seen.add(node)
            out.append(node)
    return out


def baseline_path(
    target_dir: str,
    *,
    session_id: Optional[str] = None,
    verify_command: Optional[Any] = None,
) -> str:
    if not session_id:
        return os.path.join(target_dir, BASELINE_REL)
    from apatch.runtime.namespace import runtime_namespace

    namespace = runtime_namespace(target_dir, session_id=session_id)
    command_text = json.dumps(verify_command, sort_keys=True, ensure_ascii=False)
    command_hash = hashlib.sha256(command_text.encode("utf-8")).hexdigest()[:16]
    return os.path.join(
        target_dir,
        str(namespace["relative_dir"]),
        f"verify-baseline-{command_hash}.json",
    )


def save_baseline(
    target_dir: str,
    *,
    verify_command: Any,
    failures: Sequence[str],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    data = {
        "verify_command": verify_command,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "failures": list(failures),
        "session_id": session_id,
    }
    path = baseline_path(
        target_dir,
        session_id=session_id,
        verify_command=verify_command,
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        from apatch.artifact_governance import register_on_write
        from apatch.runtime.namespace import runtime_namespace

        namespace = runtime_namespace(target_dir, session_id=session_id)
        register_on_write(
            target_dir,
            os.path.relpath(path, target_dir).replace("\\", "/"),
            class_name="EPHEMERAL" if session_id else "STATE",
            created_by_tool="apatch_verify_run",
            reason="pre-mutation verify baseline",
            governed_session_id=session_id,
            run_lease_id=f"lease_{session_id}" if session_id else None,
            replay_critical=bool(session_id),
            gc_allowed=False if session_id else False,
            runtime_namespace=namespace,
        )
    except Exception:
        pass
    return data


def load_baseline(
    target_dir: str,
    *,
    session_id: Optional[str] = None,
    verify_command: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    path = baseline_path(
        target_dir,
        session_id=session_id,
        verify_command=verify_command,
    )
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def capture_baseline(
    target_dir: str,
    *,
    verify_command: Any,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute verification before mutation and persist its failure set."""
    from apatch.tool_paths import run_shell_verify

    ok, output = run_shell_verify(verify_command, target_dir)
    data = save_baseline(
        target_dir,
        verify_command=verify_command,
        failures=[] if ok else parse_failed_tests(output or ""),
        session_id=session_id,
    )
    data["command_ok"] = bool(ok)
    data["path"] = baseline_path(
        target_dir,
        session_id=session_id,
        verify_command=verify_command,
    )
    return data


def _allowed(node: str, allowed_failures: Sequence[str]) -> bool:
    return any(pat and (pat == node or pat in node) for pat in allowed_failures)


def compare_failures(
    current: Sequence[str],
    *,
    baseline: Sequence[str] = (),
    allowed_failures: Sequence[str] = (),
) -> Dict[str, Any]:
    """Split current failures into pre-existing / allow-listed / new buckets."""
    base = set(baseline)
    pre_existing = [n for n in current if n in base]
    allowed = [n for n in current if n not in base and _allowed(n, allowed_failures)]
    new = [n for n in current if n not in base and not _allowed(n, allowed_failures)]
    return {
        "new_failures": new,
        "pre_existing_failures": pre_existing,
        "allowed_failures_matched": allowed,
    }
