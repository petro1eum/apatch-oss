"""Durable budget for one frozen task, shared by execution and its verifiers.

Seconds are a wall-clock window from first work, including pauses and downtime.
Neither a new session, process nor lane grants another window. Quantity charges
are reservations before effects; failed/interrupted effects do not refund them.
This is control-plane state, not protection against an operator editing .apatch.
"""
from __future__ import annotations

import difflib
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock


class TaskBudgetError(RuntimeError):
    def __init__(self, reason: str, state: Mapping[str, Any] | None = None):
        self.reason = reason
        self.state = dict(state or {})
        super().__init__(reason)

    def to_dict(self) -> dict:
        return {"ok": False, "error_type": "SDD_TASK_BUDGET_EXHAUSTED",
                "error_code": self.reason, "budget": self.state,
                "recoverable": False, "mutation_performed": False,
                "recommended_action": ("inspect_task_budget_state" if self.reason.startswith("task_budget_")
                                       else "request_contract_amendment")}


class TaskBudget:
    def __init__(self, root: str, envelope: Mapping[str, Any]):
        from apatch.sdd_integrity import validate_task_envelope

        checked = validate_task_envelope(envelope)
        if not envelope.get("document_hash"):
            raise TaskBudgetError("budget_requires_frozen_envelope")
        self.identity = checked["document_hash"]
        self.limits = dict(checked["budgets"])
        # Preserve the signed-zero compatibility default, once for the task.
        self.limits["seconds"] = self.limits["seconds"] or 300
        # Deliberately workspace-wide: switching execution lanes cannot reset it.
        self.path = Path(root).resolve() / ".apatch" / "task_budgets" / (self.identity[7:] + ".json")

    def _load(self, now: float) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema": "apatch.task-budget.v1", "envelope_hash": self.identity,
                    "limits": self.limits, "started_at": now, "last_checked_at": now,
                    "deadline": now + self.limits["seconds"], "files": [],
                    "insertions": 0, "deletions": 0}
        except (OSError, ValueError) as exc:
            raise TaskBudgetError("task_budget_state_unreadable") from exc
        try:
            valid = (isinstance(state, dict) and state["schema"] == "apatch.task-budget.v1"
                     and state["envelope_hash"] == self.identity and state["limits"] == self.limits
                     and all(type(state[k]) in (int, float) and math.isfinite(state[k])
                             for k in ("started_at", "last_checked_at", "deadline"))
                     and state["deadline"] == state["started_at"] + self.limits["seconds"]
                     and state["started_at"] <= state["last_checked_at"]
                     and isinstance(state["files"], list)
                     and all(isinstance(p, str) for p in state["files"])
                     and len(set(state["files"])) == len(state["files"])
                     and all(type(state[k]) is int and state[k] >= 0 for k in ("insertions", "deletions")))
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise TaskBudgetError("task_budget_state_invalid")
        if now < state["last_checked_at"]:
            raise TaskBudgetError("task_budget_clock_regressed", state)
        return state

    def check(self) -> dict:
        return self._update()

    def reserve_write(self, path: str, before: str, after: str) -> dict:
        """Charge actual full-file line differences, not caller-supplied totals."""
        insertions = deletions = 0
        for tag, i, j, a, b in difflib.SequenceMatcher(
            a=before.splitlines(), b=after.splitlines(), autojunk=False
        ).get_opcodes():
            if tag in ("insert", "replace"):
                insertions += b - a
            if tag in ("delete", "replace"):
                deletions += j - i
        return self._update(path if before != after else None, insertions, deletions)

    def _update(self, path: str | None = None, insertions: int = 0, deletions: int = 0) -> dict:
        with exclusive_file_lock(str(self.path)):
            now = time.time()
            state = self._load(now)
            state["last_checked_at"] = now
            remaining = max(0.0, state["deadline"] - now)
            if remaining <= 0:
                atomic_write_json(str(self.path), state)
                raise TaskBudgetError("task_seconds_exhausted", state)
            files = sorted(set(state["files"]) | ({path} if path else set()))
            proposed = {"files": len(files), "insertions": state["insertions"] + insertions,
                        "deletions": state["deletions"] + deletions}
            for key, value in proposed.items():
                if value > self.limits[key]:
                    # Even a failed first reservation starts the task window.
                    atomic_write_json(str(self.path), state)
                    raise TaskBudgetError("task_" + key + "_exhausted", state)
            state.update(files=files, insertions=proposed["insertions"], deletions=proposed["deletions"])
            atomic_write_json(str(self.path), state)
            return {**state, "seconds_remaining": remaining}

    def remaining(self) -> float:
        return self.check()["seconds_remaining"]
