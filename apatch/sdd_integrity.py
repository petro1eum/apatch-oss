"""Opt-in next-generation SDD integrity contracts for governed APatch sessions.

The existing APatch runtime remains authoritative for sessions, mutations and
TrustChain evidence.  This module adds immutable judge and effect-admission
contracts; workspaces without an SDD binding retain their previous behaviour.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import stat
import tempfile
import time
import xml.etree.ElementTree as ET
from apatch.task_budget import TaskBudget, TaskBudgetError
import shutil
import signal
import sys
from contextvars import ContextVar

_VERIFIER_SANDBOX = ContextVar("apatch_verifier_sandbox", default=None)
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Set

MAX_WIRE_INTEGER = (1 << 53) - 1
WIRE_BUDGET_FIELDS = frozenset({"files", "insertions", "deletions", "seconds"})

OWNERSHIP = {
    "session": "apatch.runtime.session",
    "mutation": "apatch.apply_session",
    "evidence": "apatch TrustChain ledger",
}

REQUIRED_PERSPECTIVES = frozenset({"positive", "negative", "boundary", "regression"})
EFFECT_SURFACES = frozenset(
    {"mutation", "mcp_tool", "local_runner", "sandbox", "network", "remote", "service"}
)
INTEGRITY_FLOOR = frozenset(
    {"strict_envelope", "locked_judge", "meaningful_verification", "falsification"}
)
JUDGE_PATTERNS = (
    "docs/RFP-*.md",
    "docs/contracts/**",
    "docs/specs/**",
    "tests/**",
    "schemas/**",
)
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SddContractError(ValueError):
    """The requested SDD contract is incomplete, mutable or contradictory."""


class SddAdmissionError(PermissionError):
    """A mediated effect is outside the active task envelope."""

    def __init__(self, decision: Mapping[str, Any]):
        self.decision = dict(decision)
        reasons = ", ".join(str(item) for item in decision.get("reasons") or [])
        super().__init__(reasons or "SDD task envelope denied the effect")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error_type": "SDD_EFFECT_DENIED",
            "error": str(self),
            "recoverable": True,
            "recommended_action": "request_contract_amendment",
            **self.decision,
        }


def canonical_json(value: Any) -> str:
    """Return the canonical, Unicode-preserving JSON representation."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    payload = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _seal(body: Mapping[str, Any], *, hash_field: str = "document_hash") -> Dict[str, Any]:
    sealed = _clone(dict(body))
    sealed.pop(hash_field, None)
    sealed[hash_field] = canonical_hash(sealed)
    return sealed


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_HASH_RE.fullmatch(value))


def _require_hash(value: Any, label: str) -> str:
    if not _valid_hash(value):
        raise SddContractError(f"{label} must be a sha256 document hash")
    return str(value)


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SddContractError(f"{label} is required")
    return value.strip()


def _string_list(value: Any, label: str, *, allow_empty: bool = True) -> List[str]:
    if not isinstance(value, list):
        raise SddContractError(f"{label} must be a list")
    result: List[str] = []
    for item in value:
        result.append(_require_text(item, label))
    if not allow_empty and not result:
        raise SddContractError(f"{label} must not be empty")
    return result


def _safe_pattern(value: str, label: str) -> str:
    pattern = value.strip().replace("\\", "/")
    if os.path.isabs(pattern) or any(part == ".." for part in pattern.split("/")):
        raise SddContractError(f"{label} must be workspace-relative")
    return pattern


def _verify_seal(value: Mapping[str, Any], label: str) -> None:
    expected = value.get("document_hash")
    if not _valid_hash(expected):
        raise SddContractError(f"{label} is not hash-bound")
    body = dict(value)
    body.pop("document_hash", None)
    if canonical_hash(body) != expected:
        raise SddContractError(f"{label} hash does not match its content")


def load_profile_contract(target_dir: os.PathLike[str] | str) -> Dict[str, Any] | None:
    """Load the workspace's exact frozen SDD contract, if the owner enabled it."""

    intake_pending = Path(os.path.abspath(os.fspath(target_dir))) / ".apatch" / "sdd" / "contract-intake-pending.json"
    if intake_pending.exists() or intake_pending.is_symlink():
        raise SddContractError("finish or reconcile the exact intake preparation transaction before implementation")

    path = Path(os.path.abspath(os.fspath(target_dir))) / ".apatch" / "sdd_verification_contract.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SddContractError("SDD profile manifest is unreadable or invalid JSON") from exc
    if not isinstance(raw, Mapping):
        raise SddContractError("SDD profile manifest must contain one frozen contract")
    contract = _clone(dict(raw))
    if (
        contract.get("schema") != "apatch.sdd.verification-contract.v1"
        or contract.get("status") != "frozen"
        or contract.get("implementation_allowed") is not True
    ):
        raise SddContractError("SDD profile manifest is not an implementation-ready frozen contract")
    _verify_seal(contract, "SDD profile manifest")
    return contract


def profile_status(target_dir: os.PathLike[str] | str) -> Dict[str, Any]:
    """Report whether an SDD profile is active without changing legacy state."""

    root = os.path.abspath(os.fspath(target_dir))
    enabled = False
    contract_hash = None
    manifest_error = None
    try:
        from apatch.session_state import load_session_state

        state = load_session_state(root)
        sdd = state.get("sdd")
        if isinstance(sdd, Mapping):
            enabled = bool(sdd.get("contract_hash") and not state.get("ended_at"))
            contract_hash = sdd.get("contract_hash")
    except Exception:
        pass
    manifest_present = (Path(root) / ".apatch" / "sdd_verification_contract.json").is_file()
    if manifest_present:
        enabled = True
        try:
            manifest = load_profile_contract(root)
            if manifest is not None:
                contract_hash = manifest["document_hash"]
        except SddContractError as exc:
            manifest_error = str(exc)
    return {
        "schema": "apatch.sdd.profile-status.v1",
        "enabled": enabled,
        "contract_hash": contract_hash,
        "manifest_present": manifest_present,
        "manifest_valid": manifest_present and manifest_error is None,
        "manifest_error": manifest_error,
        "legacy_behavior": "unchanged",
        "ownership": dict(OWNERSHIP),
    }


def build_brief(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a content-safe, versioned assignment/technical-brief document."""

    body = {
        "schema": "apatch.sdd.brief.v1",
        "revision": int(request.get("revision") or 1),
        "author": _require_text(request.get("author"), "author"),
        "source": _require_text(request.get("source"), "source"),
        "objective": _require_text(request.get("objective"), "objective"),
        "outcomes": _string_list(request.get("outcomes"), "outcomes", allow_empty=False),
        "constraints": _string_list(request.get("constraints") or [], "constraints"),
        "non_goals": _string_list(request.get("non_goals") or [], "non_goals"),
        "created_at": _require_text(request.get("created_at"), "created_at"),
    }
    if body["revision"] < 1:
        raise SddContractError("revision must be positive")
    supersedes = request.get("supersedes_hash")
    if supersedes is not None:
        body["supersedes_hash"] = _require_hash(supersedes, "supersedes_hash")
    return _seal(body)


def _validate_judge_assets(raw: Any, asset_hashes: Sequence[str]) -> None:
    """Refuse a judge the executor would never be able to locate.

    Before trusting a verdict, the executor resolves every frozen asset hash to
    a file in the workspace and re-hashes it. A contract without that mapping
    can still be frozen, and then fails at implementation time, when the freeze
    is immutable and the authority has left. Reject it here instead, while the
    contract can still be corrected.
    """

    if not isinstance(raw, list) or not raw:
        raise SddContractError(
            "judge_assets must map every frozen asset hash to a workspace file"
        )
    seen: Set[str] = set()
    mapped: List[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SddContractError("each judge asset must map a path to a sha256")
        path = _safe_pattern(
            _require_text(item.get("path"), "judge asset path"), "judge asset path"
        )
        if path in seen:
            raise SddContractError("judge_assets must not repeat a path")
        seen.add(path)
        mapped.append(_require_hash(item.get("sha256"), "judge asset sha256"))
    if sorted(mapped) != sorted(asset_hashes):
        raise SddContractError("judge_assets must cover exactly the frozen asset_hashes")


def _validate_obligation(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obligation = _clone(dict(raw))
    _require_text(obligation.get("acceptance_id"), "acceptance_id")
    _require_text(obligation.get("test_id"), "test_id")
    _require_text(obligation.get("oracle"), "oracle")
    perspectives = set(_string_list(obligation.get("perspectives"), "perspectives"))
    missing = sorted(REQUIRED_PERSPECTIVES - perspectives)
    if missing:
        raise SddContractError(
            "perspectives must include " + ", ".join(sorted(REQUIRED_PERSPECTIVES))
        )
    assets = _string_list(obligation.get("asset_hashes"), "asset_hashes", allow_empty=False)
    for asset_hash in assets:
        _require_hash(asset_hash, "asset_hash")
    _validate_judge_assets(obligation.get("judge_assets"), assets)
    command = obligation.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        raise SddContractError("command must be a non-empty argv list")
    _require_hash(obligation.get("command_hash"), "command_hash")
    baseline = obligation.get("baseline")
    if not isinstance(baseline, Mapping) or baseline.get("kind") != "observed_red":
        raise SddContractError("baseline must record observed_red")
    _require_hash(baseline.get("result_hash"), "baseline result_hash")
    if type(obligation.get("material")) is not bool:
        raise SddContractError("material must be a boolean")
    if obligation["material"]:
        falsification = obligation.get("falsification")
        if not isinstance(falsification, Mapping):
            raise SddContractError("material obligation requires falsification")
        if falsification.get("expected") != "red":
            raise SddContractError("falsification must expect red")
        _require_hash(falsification.get("target_hash"), "falsification target_hash")
        _safe_pattern(
            _require_text(falsification.get("target_path"), "falsification target_path"),
            "falsification target_path",
        )
    _require_text(obligation.get("approver"), "approver")
    if "expected_case_ids" in obligation:
        case_ids = _string_list(obligation["expected_case_ids"], "expected_case_ids", allow_empty=False)
        for case_id in case_ids:
            _require_hash(case_id, "expected case id")
        if len(set(case_ids)) != len(case_ids):
            raise SddContractError("expected_case_ids must not repeat a case")
    return obligation


def freeze_contract(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Freeze the complete judge before an implementation mutation exists."""

    if "schema" in request and request["schema"] != "apatch.sdd.verification-contract.v1":
        raise SddContractError("unsupported verification contract schema")
    mutation_count = request.get("source_mutation_count", 0)
    if type(mutation_count) is not int:
        raise SddContractError("source_mutation_count must be an integer")
    if mutation_count != 0:
        raise SddContractError("verification contract must freeze before source mutation")
    coverage = request.get("coverage")
    if not isinstance(coverage, Mapping):
        raise SddContractError("coverage is required")
    if (
        coverage.get("complete") is not True
        or list(coverage.get("missing") or [])
        or list(coverage.get("ambiguous") or [])
    ):
        raise SddContractError("coverage must be complete and unambiguous")
    authority = request.get("authority")
    if not isinstance(authority, Mapping) or authority.get("role") != "authority":
        raise SddContractError("authority role is required")
    _require_text(authority.get("actor_id"), "authority actor_id")
    obligations_raw = request.get("obligations")
    if not isinstance(obligations_raw, list) or not obligations_raw:
        raise SddContractError("obligations are required")
    obligations = [_validate_obligation(item) for item in obligations_raw]
    for field in ("brief_hash", "rfp_hash", "spec_hash", "plan_hash", "baseline_hash"):
        _require_hash(request.get(field), field)

    body = _clone(dict(request))
    body.pop("source_mutation_count", None)
    body.update(
        {
            "schema": "apatch.sdd.verification-contract.v1",
            "status": "frozen",
            "implementation_allowed": True,
            "source_mutation_count_at_freeze": 0,
            "obligations": obligations,
        }
    )
    return _seal(body)


def validate_task_envelope(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and hash an exact, non-ambient task effect envelope."""

    if "document_hash" in request:
        _verify_seal(request, "task envelope")
    if request.get("schema") != "apatch.sdd.task-envelope.v1":
        raise SddContractError("task envelope schema must be apatch.sdd.task-envelope.v1")
    body = _clone(dict(request))
    body.pop("document_hash", None)
    _require_text(body.get("requirement"), "requirement")
    _require_hash(body.get("contract_hash"), "contract_hash")
    _require_hash(body.get("baseline_hash"), "baseline_hash")
    for field in (
        "allowed_reads",
        "allowed_writes",
        "allowed_symbols",
        "forbidden_paths",
        "tools",
        "network",
        "remote",
        "services",
        "checks",
    ):
        body[field] = _string_list(body.get(field), field)
    body["allowed_reads"] = [_safe_pattern(item, "allowed_reads") for item in body["allowed_reads"]]
    body["allowed_writes"] = [
        _safe_pattern(item, "allowed_writes") for item in body["allowed_writes"]
    ]
    body["forbidden_paths"] = [
        _safe_pattern(item, "forbidden_paths") for item in body["forbidden_paths"]
    ]
    commands = body.get("commands")
    if not isinstance(commands, list):
        raise SddContractError("commands must be a list")
    normalized_commands: List[List[str]] = []
    for command in commands:
        if not isinstance(command, list) or not command or not all(
            isinstance(item, str) and item for item in command
        ):
            raise SddContractError("each command must be a non-empty argv list")
        normalized_commands.append(list(command))
    body["commands"] = normalized_commands
    budgets = body.get("budgets")
    if not isinstance(budgets, Mapping) or set(budgets) != WIRE_BUDGET_FIELDS:
        raise SddContractError("budgets must contain exactly files, insertions, deletions, seconds")
    normalized_budgets: Dict[str, int] = {}
    for key in ("files", "insertions", "deletions", "seconds"):
        value = budgets[key]
        if type(value) is not int or not 0 <= value <= MAX_WIRE_INTEGER:
            raise SddContractError(f"budgets.{key} must be a nonnegative safe integer")
        normalized_budgets[key] = value
    body["budgets"] = normalized_budgets
    _require_text(body.get("rollback_owner"), "rollback_owner")
    if body.get("containment") not in ("mediated_only", "sealed"):
        raise SddContractError("containment must be mediated_only or sealed")
    checked = _seal(body)
    if "document_hash" in request and checked["document_hash"] != request["document_hash"]:
        raise SddContractError("sealed task envelope is noncanonical; normalization requires an explicit new document")
    return checked


_SCOPE_LIST_FIELDS = (
    "allowed_reads",
    "allowed_writes",
    "allowed_symbols",
    "tools",
    "commands",
    "network",
    "remote",
    "services",
    "checks",
)


def _identity(item: Any) -> str:
    return canonical_json(item)


def compare_envelopes(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> Dict[str, Any]:
    """Detect any authority-widening change between two envelope revisions."""

    old = validate_task_envelope(current)
    proposed_body = dict(proposed)
    proposed_body.pop("document_hash", None)
    new = validate_task_envelope(proposed_body)
    added: Dict[str, Any] = {}
    for field in _SCOPE_LIST_FIELDS:
        old_ids = {_identity(item) for item in old.get(field) or []}
        values = [item for item in new.get(field) or [] if _identity(item) not in old_ids]
        if values:
            added[field] = values

    new_forbidden = {_identity(item) for item in new.get("forbidden_paths") or []}
    removed_forbidden = [
        item for item in old.get("forbidden_paths") or [] if _identity(item) not in new_forbidden
    ]
    if removed_forbidden:
        added["removed_forbidden_paths"] = removed_forbidden
    budget_increases = {
        key: new["budgets"][key]
        for key in new["budgets"]
        if new["budgets"][key] > old["budgets"].get(key, 0)
    }
    if budget_increases:
        added["budgets"] = budget_increases
    changed_pins = [
        field
        for field in ("requirement", "contract_hash", "baseline_hash", "rollback_owner")
        if old.get(field) != new.get(field)
    ]
    if changed_pins:
        added["changed_pins"] = changed_pins
    if old.get("containment") == "sealed" and new.get("containment") != "sealed":
        added["containment"] = [new.get("containment")]
    return {
        "schema": "apatch.sdd.envelope-comparison.v1",
        "non_widening": not bool(added),
        "added": added,
        "current_hash": old["document_hash"],
        "proposed_hash": new["document_hash"],
    }


def issue_capability(
    contract: Mapping[str, Any],
    envelope: Mapping[str, Any],
    *,
    actor_id: str,
    role: str,
) -> Dict[str, Any]:
    """Issue a role-separated, hash-bound local capability projection."""

    if contract.get("schema") != "apatch.sdd.verification-contract.v1":
        raise SddContractError("unsupported verification contract schema")
    if contract.get("status") != "frozen" or contract.get("implementation_allowed") is not True:
        raise SddContractError("implementation capability requires a frozen contract")
    _verify_seal(contract, "verification contract")
    checked_envelope = validate_task_envelope(envelope)
    if checked_envelope["contract_hash"] != contract["document_hash"]:
        raise SddContractError(
            "task envelope contract_hash must match the exact frozen contract"
        )
    actor = _require_text(actor_id, "actor_id")
    if role not in ("implementation", "verifier", "authority"):
        raise SddContractError("unsupported capability role")
    permissions = {
        "implementation": ["read", "mutate_within_envelope", "request_amendment"],
        "verifier": ["read", "run_frozen_checks"],
        "authority": ["read", "approve_amendment", "accept_or_reject"],
    }[role]
    body = {
        "schema": "apatch.sdd.capability.v1",
        "actor_id": actor,
        "role": role,
        "contract_hash": contract["document_hash"],
        "envelope_hash": checked_envelope["document_hash"],
        "permissions": permissions,
        "envelope": checked_envelope,
        "judge_patterns": list(JUDGE_PATTERNS),
    }
    return _seal(body, hash_field="capability_hash")


def _workspace_relative(path: str, target_dir: str) -> str:
    """State a path the way an envelope states one.

    An apply resolves every target to an absolute path; an envelope declares
    workspace-relative patterns. Comparing the two forms directly never matches,
    so a strict workspace denies exactly the writes it was configured to allow,
    and the refusal reads as a scope violation rather than as the mismatch it is.
    """

    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return text
    root = os.path.abspath(os.fspath(target_dir)).replace("\\", "/")
    if not os.path.isabs(text):
        return text
    try:
        relative = os.path.relpath(os.path.abspath(text), root).replace("\\", "/")
    except ValueError:
        return text
    # A path outside the workspace stays absolute, so it keeps failing to match
    # rather than climbing out through a relative form.
    return text if relative.startswith("..") else relative


def _path_matches(path: str, pattern: str) -> bool:
    candidate = path.strip().replace("\\", "/").lstrip("./")
    normalized = pattern.strip().replace("\\", "/").lstrip("./")
    if fnmatch.fnmatchcase(candidate, normalized):
        return True
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return candidate == prefix or candidate.startswith(prefix + "/")
    return False


def _paths_for_effect(effect: Mapping[str, Any]) -> List[str]:
    values: List[str] = []
    for key in ("path", "source_path", "target_path"):
        value = effect.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    raw = effect.get("paths")
    if isinstance(raw, list):
        values.extend(str(item) for item in raw if item)
    return list(dict.fromkeys(values))


def _denial(capability: Mapping[str, Any], reasons: Iterable[str]) -> Dict[str, Any]:
    return {
        "schema": "apatch.sdd.effect-admission.v1",
        "decision": "denied",
        "reasons": sorted(set(reasons)),
        "blocks_attestation": True,
        "mutation_performed": False,
        "rollback_scope": "session:self",
        "actor_id": capability.get("actor_id"),
        "role": capability.get("role"),
        "contract_hash": capability.get("contract_hash"),
        "envelope_hash": capability.get("envelope_hash"),
    }


def admit_effect(capability: Mapping[str, Any], effect: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply the same fail-closed admission vocabulary to every mediated surface."""

    role = capability.get("role")
    envelope = capability.get("envelope")
    if not isinstance(envelope, Mapping):
        return _denial(capability, ["capability_missing_envelope"])
    surface = str(effect.get("surface") or "")
    action = str(effect.get("effect") or "")
    reasons: List[str] = []
    if surface not in EFFECT_SURFACES:
        reasons.append("unsupported_surface")
    if effect.get("plan_match") is False:
        reasons.append("plan_mismatch")
    paths = _paths_for_effect(effect)
    if any(_path_matches(path, ".apatch/task_budgets/**") for path in paths):
        reasons.append("task_budget_control_state")
    forbidden = list(envelope.get("forbidden_paths") or []) + list(
        capability.get("judge_patterns") or []
    )
    if any(_path_matches(path, pattern) for path in paths for pattern in forbidden):
        reasons.append("frozen_judge")
    if role == "verifier" and surface == "mutation":
        reasons.append("verifier_is_non_mutating")
    if role == "authority" and surface not in ("mcp_tool",):
        reasons.append("authority_is_not_implementation")

    if not reasons and role == "implementation":
        if surface == "mutation":
            if action not in ("write", "create", "replace", "delete", "rename", "chmod"):
                reasons.append("unsupported_effect")
            elif not paths:
                reasons.append("path_required")
            elif any(
                not any(_path_matches(path, pattern) for pattern in envelope["allowed_writes"])
                for path in paths
            ):
                reasons.append("path_outside_allowed_writes")
        elif surface == "sandbox":
            if action not in ("acquire", "write"):
                reasons.append("unsupported_effect")
            elif paths and any(
                not any(_path_matches(path, pattern) for pattern in envelope["allowed_writes"])
                for path in paths
            ):
                reasons.append("path_outside_allowed_writes")
        elif surface == "mcp_tool":
            tool = str(effect.get("tool") or "")
            if action != "invoke" or tool not in envelope["tools"]:
                reasons.append("tool_not_allowed")
        elif surface == "local_runner":
            command = effect.get("command")
            if action != "run" or not isinstance(command, list) or command not in envelope["commands"]:
                reasons.append("command_not_allowed")
        elif surface == "network":
            target = str(effect.get("target") or "")
            if action not in ("connect", "request") or target not in envelope["network"]:
                reasons.append("network_not_allowed")
        elif surface == "remote":
            target = str(effect.get("target") or effect.get("alias") or "")
            if action not in ("run", "mutate", "verify") or target not in envelope["remote"]:
                reasons.append("remote_not_allowed")
        elif surface == "service":
            service = str(effect.get("service") or "")
            if action not in ("status", "restart", "logs", "healthcheck", "exec", "run") or service not in envelope["services"]:
                reasons.append("service_not_allowed")
    elif not reasons and role == "verifier":
        if surface != "local_runner" or action != "run":
            reasons.append("verifier_may_only_run_frozen_checks")
        elif effect.get("command") not in envelope["commands"]:
            reasons.append("command_not_allowed")
    elif not reasons and role == "authority":
        if action not in ("approve_amendment", "accept", "reject"):
            reasons.append("authority_effect_not_allowed")

    if reasons:
        return _denial(capability, reasons)
    return {
        "schema": "apatch.sdd.effect-admission.v1",
        "decision": "allowed",
        "reasons": [],
        "blocks_attestation": False,
        "mutation_performed": False,
        "rollback_scope": "session:self",
        "actor_id": capability.get("actor_id"),
        "role": role,
        "contract_hash": capability.get("contract_hash"),
        "envelope_hash": capability.get("envelope_hash"),
    }


def build_session_binding(
    contract: Mapping[str, Any],
    envelope: Mapping[str, Any],
    actor: Mapping[str, Any],
) -> Dict[str, Any]:
    role = _require_text(actor.get("role"), "actor role")
    capability = issue_capability(
        contract,
        envelope,
        actor_id=_require_text(actor.get("actor_id"), "actor_id"),
        role=role,
    )
    checked_envelope = capability["envelope"]
    return {
        "schema": "apatch.sdd.session-binding.v1",
        "contract_hash": capability["contract_hash"],
        "verification_contract": _clone(dict(contract)),
        "envelope_hash": capability["envelope_hash"],
        "actor": {"actor_id": capability["actor_id"], "role": role},
        "capability": capability,
        "requirement": checked_envelope["requirement"],
        "containment": checked_envelope["containment"],
        "material_gate_required": any(
            bool(item.get("material")) for item in contract.get("obligations") or []
        ),
        "adherence": {"status": "compliant", "violations": []},
    }


def _record_denial(target_dir: str, raw: MutableMapping[str, Any], decision: Mapping[str, Any]) -> None:
    sdd = dict(raw.get("sdd") or {})
    adherence = dict(sdd.get("adherence") or {})
    violations = list(adherence.get("violations") or [])
    compact = {
        "reasons": list(decision.get("reasons") or []),
        "actor_id": decision.get("actor_id"),
        "role": decision.get("role"),
    }
    violations.append(compact)
    adherence.update({"status": "blocked", "violations": violations[-100:]})
    sdd["adherence"] = adherence
    updated = dict(raw)
    updated["sdd"] = sdd
    try:
        from apatch.session_state import save_session_state

        save_session_state(
            target_dir,
            updated,
            expected_session_id=raw.get("session_id"),
            expected_revision=int(raw.get("revision") or 0),
        )
    except Exception:
        # Admission already failed closed; diagnostics must not weaken that result.
        pass


def admit_session_effect(target_dir: str, effect: Mapping[str, Any]) -> Dict[str, Any]:
    """Admit an effect against the active session and fail closed for strict profiles."""

    from apatch.session_state import load_session_state

    raw = load_session_state(target_dir)
    try:
        manifest = load_profile_contract(target_dir)
    except SddContractError:
        decision = _denial({}, ["sdd_profile_invalid"])
        decision["profile_enabled"] = True
        _record_denial(target_dir, raw, decision)
        raise SddAdmissionError(decision)

    effect = dict(effect)
    paths = effect.get("paths")
    if isinstance(paths, (list, tuple)):
        effect["paths"] = [_workspace_relative(str(item), target_dir) for item in paths]
    for key in ("path", "source_path", "target_path"):
        if isinstance(effect.get(key), str):
            effect[key] = _workspace_relative(effect[key], target_dir)

    sdd = raw.get("sdd")
    if not isinstance(sdd, Mapping):
        if manifest is None:
            return {
                "schema": "apatch.sdd.effect-admission.v1",
                "decision": "allowed",
                "profile_enabled": False,
                "legacy_behavior": "unchanged",
                "blocks_attestation": False,
                "mutation_performed": False,
            }
        decision = _denial({}, ["sdd_contract_required"])
    elif manifest is not None and sdd.get("contract_hash") != manifest.get("document_hash"):
        decision = _denial(sdd.get("capability") or {}, ["sdd_contract_mismatch"])
    else:
        capability = sdd.get("capability")
        if not isinstance(capability, Mapping):
            decision = _denial({}, ["active_profile_missing_capability"])
        else:
            decision = admit_effect(capability, effect)
    decision["profile_enabled"] = True
    if decision["decision"] != "allowed":
        _record_denial(target_dir, raw, decision)
        raise SddAdmissionError(decision)
    try:
        decision["budget"] = TaskBudget(target_dir, sdd["capability"]["envelope"]).check()
    except TaskBudgetError as exc:
        decision = _denial(sdd.get("capability") or {}, [exc.reason])
        decision.update(exc.to_dict())
        _record_denial(target_dir, raw, decision)
        raise SddAdmissionError(decision) from exc
    return decision


def evaluate_verification(
    obligation: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    """Reject false-green or mutable verification results."""

    required = set(obligation.get("perspectives") or [])
    observed = set(result.get("perspectives") or [])
    reasons: List[str] = []
    collected = int(result.get("collected") or 0)
    executed = int(result.get("executed") or 0)
    failed = int(result.get("failed") or 0)
    skipped = int(result.get("skipped") or 0)
    if result.get("capability_role") != "verifier":
        reasons.append("wrong_capability_role")
    if bool(result.get("mutated")):
        reasons.append("verifier_mutated_assets")
    if collected <= 0:
        reasons.append("zero_collected")
    if executed <= 0 or skipped >= collected:
        reasons.append("all_skipped")
    if failed != 0 or int(result.get("passed") or 0) != executed:
        reasons.append("not_green")
    if result.get("command_failures") and "not_green" not in reasons:
        reasons.append("not_green")
    if result.get("content_snapshot_valid") is False:
        reasons.append("verification_content_unavailable")
    if skipped:
        reasons.append("skipped_required_tests")
    reasons.extend(str(code) for code in result.get("report_errors") or [])
    if bool(result.get("asset_drift")):
        reasons.append("asset_drift")
    if bool(result.get("tautological")):
        reasons.append("tautological_gate")
    if not required.issubset(observed):
        reasons.append("missing_perspectives")
    if bool(obligation.get("material")):
        falsification = result.get("falsification")
        if not isinstance(falsification, Mapping) or not (
            falsification.get("observed_red") is True
            and falsification.get("restored_green") is True
        ):
            reasons.append("falsification_not_observed")
    return {
        "schema": "apatch.sdd.verification-decision.v1",
        "accepted": not reasons,
        "reasons": reasons,
        "collected": collected,
        "executed": executed,
        "failed": failed,
        "skipped": skipped,
    }


def build_attestation_evidence(
    *,
    contract_hash: str,
    envelope_hash: str,
    actor: Mapping[str, Any],
    mutation_ids: Sequence[str],
    adherence: Mapping[str, Any],
    verification: Mapping[str, Any],
    falsification: Mapping[str, Any],
    environment_hash: str,
    checkpoint_refs: Sequence[str],
    ledger_refs: Sequence[str],
    task_envelope: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the complete causal evidence object embedded in attestation."""

    body = {
        "schema": "apatch.sdd.attestation-evidence.v1",
        "contract_hash": _require_hash(contract_hash, "contract_hash"),
        "envelope_hash": _require_hash(envelope_hash, "envelope_hash"),
        "actor": {
            "actor_id": _require_text(actor.get("actor_id"), "actor_id"),
            "role": _require_text(actor.get("role"), "actor role"),
        },
        "mutation_ids": _string_list(list(mutation_ids), "mutation_ids"),
        "adherence": _clone(dict(adherence)),
        "verification": _clone(dict(verification)),
        "falsification": _clone(dict(falsification)),
        "environment_hash": _require_hash(environment_hash, "environment_hash"),
        "checkpoint_refs": _string_list(list(checkpoint_refs), "checkpoint_refs"),
        "ledger_refs": _string_list(list(ledger_refs), "ledger_refs"),
    }
    if task_envelope is not None:
        body["task_envelope"] = validate_task_envelope(task_envelope)
    return _seal(body)




def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _workspace_file(target_dir: str, value: Any, label: str) -> tuple[str, Path]:
    relative = _safe_pattern(_require_text(value, label), label)
    root = Path(target_dir).resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SddContractError(f"{label} resolves outside the workspace") from exc
    return relative, candidate


def _is_pytest_argv(command: Sequence[str]) -> bool:
    if not command:
        return False
    if Path(command[0]).name == "env" and len(command) > 2 and command[1] in {"-i", "--ignore-environment"}:
        offset = 2
        while offset < len(command) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", command[offset], re.S):
            offset += 1
        return _is_pytest_argv(command[offset:])
    executable = Path(command[0]).name.lower()
    if executable in ("pytest", "py.test", "pytest.exe", "py.test.exe"):
        return True
    if not re.fullmatch(r"(?:python|pypy)[\d.]*(?:\.exe)?", executable):
        return False
    for index, argument in enumerate(command[1:], 1):
        if argument in ("-c", "--"):
            return False
        if argument == "-m":
            return index + 1 < len(command) and command[index + 1] == "pytest"
    return False


def _read_pytest_report(path: Path, run_id: str) -> Dict[str, Any]:
    """Read this run's JUnit facts, never a success-looking stdout summary.

    Only hashes of case identities leave the verifier: parameterized names and
    report output can contain private data. The argv and frozen judge stay exact.
    """
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("test report exceeds its bound")
    payload = path.read_bytes()
    root = ET.fromstring(payload)
    suites = list(root.iter("testsuite"))
    if len(suites) != 1 or suites[0].get("name") != run_id:
        raise ValueError("not this run's test report")
    suite = suites[0]
    cases: List[Dict[str, str]] = []
    observed = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    identities: Set[str] = set()
    for case in suite.findall("testcase"):
        name = case.get("name")
        if not name:
            raise ValueError("missing test case identity")
        case_id = canonical_hash({"classname": case.get("classname", ""), "name": name})
        if case_id in identities:
            raise ValueError("duplicate test case identity")
        identities.add(case_id)
        outcomes = [tag for tag in ("failure", "error", "skipped") if case.find(tag) is not None]
        if len(outcomes) > 1:
            raise ValueError("ambiguous test case outcome")
        outcome = {"failure": "failed"}.get(outcomes[0], outcomes[0]) if outcomes else "passed"
        observed[outcome] += 1
        cases.append({"case_id": case_id, "outcome": outcome})
    declared = {field: int(suite.attrib[field]) for field in ("tests", "failures", "errors", "skipped")}
    if declared != {"tests": len(cases), "failures": observed["failed"],
                    "errors": observed["error"], "skipped": observed["skipped"]}:
        raise ValueError("test report counts disagree with cases")
    return {
        "format": "pytest-junit",
        "report_hash": _sha256_bytes(payload),
        "collected": len(cases),
        "executed": len(cases) - observed["skipped"],
        "passed": observed["passed"],
        "failed": observed["failed"] + observed["error"],
        "errors": observed["error"],
        "skipped": observed["skipped"],
        "cases": cases,
    }


def _verification_content_snapshot(
    target_dir: str,
    contract: Mapping[str, Any],
    envelope: Mapping[str, Any],
    obligations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Pin declared workspace inputs by content, independently of Git status.

    The full manifest stays in local session state; evidence carries its hash.
    Runtime journals and generated caches are excluded from directory walks;
    an explicitly named file is still included. External/undeclared dependencies
    are outside this snapshot, and symlinks cannot silently escape its scope.
    """
    import platform
    import sys

    root = Path(target_dir).resolve(strict=True)
    patterns = set(envelope.get("allowed_reads") or []) | set(envelope.get("allowed_writes") or [])
    for obligation in obligations:
        patterns.update(asset["path"] for asset in obligation.get("judge_assets") or [])
        target = (obligation.get("falsification") or {}).get("target_path")
        if target:
            patterns.add(target)
    patterns = {_safe_pattern(pattern, "verification input") for pattern in patterns}
    roots: Set[Path] = set()
    for pattern in patterns:
        prefix: List[str] = []
        for part in Path(pattern).parts:
            if any(character in part for character in "*?["):
                break
            prefix.append(part)
        roots.add(root.joinpath(*prefix))
    transient = {".git", ".apatch", ".trustchain", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    selected: Set[Path] = set()
    walked: List[Path] = []

    def select(path: Path) -> None:
        relative = path.relative_to(root).as_posix()
        if any(_path_matches(relative, pattern) for pattern in patterns):
            selected.add(path)

    def walk_error(error: OSError) -> None:
        raise error

    for base in sorted(roots):
        if any(base == parent or parent in base.parents for parent in walked):
            continue
        # Reject symlinks in the selected root or any of its ancestry.
        if any(path.is_symlink() for path in (base, *base.parents) if path == root or root in path.parents):
            raise SddContractError("verification content snapshot cannot follow symlinks")
        if base.is_dir():
            walked.append(base)
            for folder, dirs, names in os.walk(base, followlinks=False, onerror=walk_error):
                dirs[:] = sorted(name for name in dirs if name not in transient)
                if any((Path(folder) / name).is_symlink() for name in dirs):
                    raise SddContractError("verification content snapshot cannot follow symlinks")
                for name in names:
                    select(Path(folder) / name)
        elif base.exists():
            select(base)

    files: Dict[str, Any] = {}

    def file_identity(info: os.stat_result) -> tuple:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    for path in sorted(selected):
        if path.is_symlink():
            raise SddContractError("verification content snapshot cannot follow symlinks")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise SddContractError("verification input must be a regular file")
            digest = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else None
            if digest is None:  # Python 3.10
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
                digest = hasher.hexdigest()
            if (file_identity(before) != file_identity(os.fstat(stream.fileno()))
                    or file_identity(before) != file_identity(path.stat())):
                raise SddContractError("verification content changed while taking snapshot")
        files[path.relative_to(root).as_posix()] = {
            "sha256": "sha256:" + digest, "mode": stat.S_IMODE(before.st_mode),
        }
    if not files:
        raise SddContractError("verification content snapshot has no input files")
    environment = contract.get("judge_environment") or {}
    if not isinstance(environment, Mapping) or not isinstance(environment.get("env") or {}, Mapping):
        raise SddContractError("verification content snapshot requires a valid judge environment")
    names = {"PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    names.update((environment.get("env") or {}).keys())
    if not all(isinstance(name, str) for name in names):
        raise SddContractError("verification environment names must be strings")
    return _seal({
        "schema": "apatch.sdd.verification-content.v1",
        "contract_hash": contract.get("document_hash"),
        "envelope_hash": envelope.get("document_hash"),
        "scope": sorted(patterns),
        "files": files,
        "environment_hash": canonical_hash({
            "python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": sys.platform, "executable": sys.executable,
            "env": {name: os.environ.get(name) for name in sorted(names)},
        }),
    })



def _persist_verifier_diagnostic(target_dir: str, payload: bytes) -> Dict[str, Any]:
    """Keep retrievable owner-local output, with secrets redacted before disk."""
    text = payload.decode("utf-8", errors="replace")
    for key, value in os.environ.items():
        if len(value) >= 4 and re.search(r"secret|token|password|private.?key", key, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:password|secret|token|api[_-]?key)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\b[A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD)[A-Za-z0-9_]*\b", "[REDACTED]", text)
    directory = Path(target_dir) / ".apatch" / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact_id = "verify-" + secrets.token_hex(16) + ".log"
    path = directory / artifact_id
    data = text.encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return {"diagnostic_ref": ".apatch/diagnostics/" + artifact_id,
            "diagnostic_sha256": _sha256_bytes(data), "diagnostic_bytes": len(data),
            "diagnostic_redacted": True}


def _prepare_verifier_workspace(target_dir, envelope, obligations, run_id):
    """Copy only admitted files; never place a mutant in the caller tree."""
    source = Path(target_dir).resolve()
    run = source / ".apatch" / "verification-runs" / run_id
    workspace = run / "workspace"
    workspace.mkdir(parents=True, mode=0o700)
    patterns = list(envelope.get("allowed_reads") or []) + list(envelope.get("allowed_writes") or [])
    exact = {str(asset.get("path") or "") for ob in obligations for asset in ob.get("judge_assets") or []}
    exact.update(str((ob.get("falsification") or {}).get("target_path") or "") for ob in obligations)
    for parent, directories, files in os.walk(source, followlinks=False):
        relative_parent = Path(parent).relative_to(source)
        directories[:] = [name for name in directories if name not in {".apatch", ".git", ".trustchain", "__pycache__"}]
        for name in files:
            original = Path(parent) / name
            relative = (relative_parent / name).as_posix()
            if relative not in exact and not any(_path_matches(relative, pattern) for pattern in patterns):
                continue
            if original.is_symlink():
                raise SddContractError("isolated verifier inputs must be regular workspace files")
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
    if sys.platform == "linux":
        from apatch.linux_verifier import capability, SandboxUnavailable
        try:
            backend = capability()
        except SandboxUnavailable as exc:
            raise SddContractError("isolated verifier write sandbox unavailable: " + str(exc)) from None
        policy = run / "sandbox.linux.json"
        policy.write_text(json.dumps({**backend, "workspace": str(workspace.resolve())}), encoding="utf-8")
    else:
        if not Path("/usr/bin/sandbox-exec").is_file():
            raise SddContractError("isolated verifier write sandbox is unavailable on this platform")
        policy = run / "sandbox.sb"
        policy.write_text('(version 1)\n(allow default)\n(deny file-write*)\n'
            '(allow file-write* (subpath ' + json.dumps(str(workspace)) + '))\n'
            '(allow file-write* (literal "/dev/null"))\n(deny network*)\n', encoding="utf-8")
    # This durable reference exists before execution, so crash/fault observers
    # can identify the actual isolated judge workspace without guessing paths.
    from apatch.runtime.atomic_io import atomic_write_json
    atomic_write_json(str(run / "execution.json"), {
        "schema": "apatch.sdd.isolated-execution.v1", "verification_run_id": run_id,
        "workspace": ".apatch/verification-runs/" + run_id + "/workspace",
        "state": "prepared", "caller_source_mutated": False,
    })
    return workspace, policy


def _verification_incidents(target_dir, contract_hash, acceptance_id, run_id, decision):
    """A retry cannot erase failure under the same frozen acceptance contract."""
    from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock, read_json_file
    path = str(Path(target_dir) / ".apatch" / "verification_incidents.json")
    key = canonical_hash({"contract_hash": contract_hash, "acceptance_id": acceptance_id})
    with exclusive_file_lock(path):
        history = read_json_file(path, {})
        records = list(history.get(key) or [])
        if decision.get("accepted") is not True:
            records.append({"verification_run_id": run_id, "decision_hash": canonical_hash(decision), "status": "unresolved"})
            history[key] = records
            atomic_write_json(path, history)
    return records


def _run_frozen_argv(
    target_dir: str,
    command: Sequence[str],
    *,
    timeout: float,
    budget: TaskBudget | None = None,
    deadline: float | None = None,
) -> Dict[str, Any]:
    """Run one authority-frozen argv without exposing its output."""

    try:
        if budget is not None:
            timeout = min(timeout, budget.remaining())
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise TaskBudgetError("verifier_runtime_cap_exhausted")
        # The report is an observation output inside the existing isolated
        # verifier workspace, so the OS write restriction remains intact.
        with tempfile.TemporaryDirectory(prefix=".apatch-sdd-report-", dir=target_dir if _VERIFIER_SANDBOX.get() else None) as report_dir:
            report_path = Path(report_dir) / "tests.xml"
            run_id = "apatch_sdd_" + secrets.token_hex(16)
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            observed_command = list(command)
            if _is_pytest_argv(command):
                clean_env = Path(command[0]).name == "env"
                offset, frozen_env = 2, {}
                if clean_env:
                    while offset < len(command) and "=" in command[offset]:
                        key, value = command[offset].split("=", 1)
                        frozen_env[key] = value
                        offset += 1
                env["PYTEST_ADDOPTS"] = " ".join(filter(None, (
                    (frozen_env if clean_env else env).get("PYTEST_ADDOPTS", ""),
                    "--junitxml=" + shlex.quote(str(report_path)),
                    "-o junit_suite_name=" + run_id,
                )))
                if clean_env:
                    observed_command[offset:offset] = [
                        "PYTHONDONTWRITEBYTECODE=1", "PYTEST_ADDOPTS=" + env["PYTEST_ADDOPTS"],
                    ]
            policy = _VERIFIER_SANDBOX.get()
            if policy and sys.platform == "linux":
                bound = json.loads(Path(policy).read_text(encoding="utf-8"))
                workspace = str(Path(target_dir).resolve())
                if bound.get("backend") != "landlock-seccomp" or bound.get("workspace") != workspace:
                    raise SddContractError("isolated verifier Linux policy binding mismatch")
                launcher = Path(__file__).with_name("linux_verifier.py")
                prefix = [sys.executable, "-I", "-B", str(launcher), workspace, "--"]
            else:
                prefix = ["/usr/bin/sandbox-exec", "-f", str(policy)] if policy else []
            argv = prefix + observed_command
            process = subprocess.Popen(
                argv, cwd=target_dir, env=env, shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"), close_fds=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                stdout, stderr = process.communicate(timeout=2)
                raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
            finally:
                # A successful judge must not leave delayed children running either.
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            payload = (completed.stdout or b"") + b"\x00" + (completed.stderr or b"")
            outcome = {
                "ok": completed.returncode == 0,
                "returncode": int(completed.returncode),
                "output_hash": _sha256_bytes(payload),
                "output_bytes": len(payload),
                **_persist_verifier_diagnostic(target_dir, payload),
            }
            if budget is not None:
                budget.check()
            if deadline is not None and time.monotonic() >= deadline:
                raise TaskBudgetError("verifier_runtime_cap_exhausted")
            try:
                outcome["test_report"] = _read_pytest_report(report_path, run_id)
            except FileNotFoundError:
                outcome["test_report_error"] = "test_report_missing"
            except (OSError, ValueError, KeyError, ET.ParseError):
                outcome["test_report_error"] = "test_report_invalid"
            return outcome
    except TaskBudgetError as exc:
        return {"ok": False, "returncode": 124, "budget_exhausted": exc.reason,
                "output_bytes": 0, "output_hash": _sha256_bytes(b"")}
    except subprocess.TimeoutExpired as exc:
        payload = (exc.stdout or b"") + b"\x00" + (exc.stderr or b"")
        return {
            "ok": False,
            "returncode": 124,
            "timed_out": True,
            "output_hash": _sha256_bytes(payload),
            "output_bytes": len(payload),
            **_persist_verifier_diagnostic(target_dir, payload),
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": 127,
            "launch_error": exc.__class__.__name__,
            "output_hash": _sha256_bytes(exc.__class__.__name__.encode("ascii")),
            "output_bytes": 0,
        }


def _git_source_fingerprint(target_dir: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=target_dir,
            shell=False,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return _sha256_bytes(completed.stdout or b"")


def _selected_frozen_obligations(
    contract: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for raw in contract.get("obligations") or []:
        if not isinstance(raw, Mapping):
            raise SddContractError("verification contract contains an invalid obligation")
        acceptance_id = _require_text(raw.get("acceptance_id"), "acceptance_id")
        if acceptance_id in by_id:
            raise SddContractError("verification contract contains duplicate acceptance ids")
        by_id[acceptance_id] = _clone(dict(raw))
    checks = _string_list(envelope.get("checks"), "checks", allow_empty=False)
    missing = [item for item in checks if item not in by_id]
    if missing:
        raise SddContractError(
            "task envelope references unknown frozen checks: " + ", ".join(missing)
        )
    return [by_id[item] for item in checks]


def _preflight_frozen_obligation(
    target_dir: str,
    obligation: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> str | None:
    command = obligation.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        return "invalid_frozen_command"
    if canonical_hash(command) != obligation.get("command_hash"):
        return "command_hash_mismatch"
    authorized_commands = {canonical_json(item) for item in envelope.get("commands") or []}
    if canonical_json(command) not in authorized_commands:
        return "command_not_authorized"

    assets = obligation.get("judge_assets")
    if not isinstance(assets, list) or not assets:
        return "judge_asset_mapping_required"
    expected_hashes = list(obligation.get("asset_hashes") or [])
    mapped_hashes: List[str] = []
    seen_paths: Set[str] = set()
    for raw in assets:
        if not isinstance(raw, Mapping):
            return "judge_asset_mapping_invalid"
        try:
            relative, path = _workspace_file(target_dir, raw.get("path"), "judge asset path")
            digest = _require_hash(raw.get("sha256"), "judge asset sha256")
        except SddContractError:
            return "judge_asset_mapping_invalid"
        if relative in seen_paths:
            return "judge_asset_mapping_duplicate"
        seen_paths.add(relative)
        mapped_hashes.append(digest)
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != digest:
            return "judge_asset_drift"
    if sorted(mapped_hashes) != sorted(expected_hashes):
        return "judge_asset_mapping_incomplete"

    if bool(obligation.get("material")):
        falsification = obligation.get("falsification")
        if not isinstance(falsification, Mapping):
            return "falsification_target_required"
        try:
            target_relative, target_path = _workspace_file(
                target_dir, falsification.get("target_path"), "falsification target_path"
            )
        except SddContractError:
            return "falsification_target_invalid"
        if not target_path.is_file():
            return "falsification_target_missing"
        allowed = list(envelope.get("allowed_writes") or [])
        if not any(_path_matches(target_relative, pattern) for pattern in allowed):
            return "falsification_target_not_authorized"
    return None


def _verification_input_binding(
    target_dir: str,
    envelope: Mapping[str, Any],
    obligations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Fingerprint admitted inputs, including the environment actually inherited.

    No raw environment value or source content is included in the result. The
    current frozen runner inherits all of os.environ, so the full mapping is an
    input until an explicitly isolated verifier environment is supported.
    """
    root = Path(target_dir).resolve()
    patterns = list(envelope.get("allowed_reads") or []) + list(
        envelope.get("allowed_writes") or []
    )
    exact = {
        str(asset.get("path") or "")
        for obligation in obligations
        for asset in obligation.get("judge_assets") or []
    }
    exact.update(
        str((obligation.get("falsification") or {}).get("target_path") or "")
        for obligation in obligations
    )
    files: Dict[str, Any] = {}
    for entry in sorted(root.rglob("*")):
        relative = entry.relative_to(root).as_posix()
        # Runtime bookkeeping is output, never a source dependency. Frozen
        # judge assets are preflighted separately and cannot use these roots.
        if relative.split("/", 1)[0] in {".apatch", ".trustchain", ".git"}:
            continue
        if relative not in exact and not any(
            _path_matches(relative, pattern) for pattern in patterns
        ):
            continue
        if entry.is_symlink():
            # Bind link topology as well as referent bytes. Do not follow links
            # outside the admitted workspace to discover additional inputs.
            resolved = entry.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise SddContractError("verification input resolves outside workspace") from exc
            files[relative] = {"link": canonical_hash(os.readlink(entry))}
        if entry.is_file():
            files.setdefault(relative, {}).update({
                "sha256": _sha256_bytes(entry.read_bytes()),
                "mode": entry.stat().st_mode & 0o7777,
            })
    return {
        "schema": "apatch.sdd.verification-inputs.v1",
        "files_hash": canonical_hash(files),
        "file_count": len(files),
        "environment_hash": canonical_hash(dict(os.environ)),
        "commands_hash": canonical_hash([
            list(obligation.get("command") or []) for obligation in obligations
        ]),
        "envelope_hash": envelope.get("document_hash"),
    }


def _run_frozen_falsification(
    target_dir: str,
    obligation: Mapping[str, Any],
    *,
    session_id: str,
    timeout: float,
    expected_case_ids: Set[str],
    budget: TaskBudget,
    deadline: float,
) -> Dict[str, Any]:
    falsification = obligation.get("falsification") or {}
    relative, path = _workspace_file(
        target_dir, falsification.get("target_path"), "falsification target_path"
    )
    command = list(obligation.get("command") or [])
    # Refuse before introducing a mutant when there is no time left.
    try:
        budget.remaining()
        if time.monotonic() >= deadline:
            raise TaskBudgetError("verifier_runtime_cap_exhausted")
    except TaskBudgetError as exc:
        return {"observed_red": False, "restored_green": False,
                "budget_exhausted": exc.reason,
                "red_result_hash": canonical_hash(exc.to_dict()),
                "restored_result_hash": canonical_hash(exc.to_dict())}
    before = path.read_bytes()
    before_mode = path.stat().st_mode
    if path.suffix == ".py":
        mutant = b'raise RuntimeError("apatch frozen-verifier mutant")\n' + before
    else:
        mutant = b"APATCH_FROZEN_VERIFIER_MUTANT_INVALID_$$$\n" + before

    from apatch.path_leases import acquire_path_lease, load_active_leases, release_path_leases

    owner_had_lease = any(
        str(item.get("governed_session_id") or "") == session_id
        for item in load_active_leases(target_dir)
    )
    lease = acquire_path_lease(
        target_dir,
        [relative],
        tool="apatch_sdd_verify:falsification",
        governed_session_id=session_id,
        max_seconds=max(60, timeout * 3),
    )
    try:
        try:
            path.write_bytes(mutant)
            os.chmod(path, before_mode)
            red = _run_frozen_argv(target_dir, command, timeout=timeout, budget=budget, deadline=deadline)
        finally:
            path.write_bytes(before)
            os.chmod(path, before_mode)
        # Byte/mode restoration above is unconditional. A restored GREEN still
        # needs remaining time; exhaustion cannot grant another test window.
        restored = _run_frozen_argv(target_dir, command, timeout=timeout, budget=budget, deadline=deadline)
    finally:
        if not owner_had_lease:
            release_path_leases(
                target_dir,
                lease_id=str(lease.get("lease_id") or ""),
                governed_session_id=session_id,
            )

    bytes_restored = path.read_bytes() == before
    mode_restored = path.stat().st_mode == before_mode
    red_report = red.get("test_report") or {}
    restored_report = restored.get("test_report") or {}
    restored_ids = {case["case_id"] for case in restored_report.get("cases") or []}
    return {
        "observed_red": red.get("ok") is False and int(red_report.get("failed") or 0) > 0,
        "restored_green": bool(
            restored.get("ok") and restored_report.get("executed")
            and not restored_report.get("failed") and not restored_report.get("skipped")
            and restored_ids == expected_case_ids and bytes_restored and mode_restored
        ),
        "red_result_hash": canonical_hash(red),
        "restored_result_hash": canonical_hash(restored),
        "budget_exhausted": red.get("budget_exhausted") or restored.get("budget_exhausted"),
        "bytes_restored": bytes_restored,
        "mode_restored": mode_restored,
        "restored_result": restored,
    }


def run_frozen_session_verification(
    target_dir: str,
    *,
    expected_session_id: str | None = None,
    timeout: int | None = None,
) -> Dict[str, Any]:
    """Execute and atomically record the exact judge bound to one SDD session."""

    from apatch.session_state import load_session_state

    root = os.path.abspath(target_dir)
    raw = load_session_state(root)
    session_id = str(raw.get("session_id") or "")
    if not session_id or raw.get("ended_at"):
        raise SddContractError("fixed verification requires one active governed session")
    if expected_session_id and session_id != str(expected_session_id):
        raise SddContractError("fixed verification requires the exact governed session")

    sdd = raw.get("sdd")
    if not isinstance(sdd, Mapping):
        raise SddContractError("active session has no SDD binding")
    contract = sdd.get("verification_contract")
    capability = sdd.get("capability")
    if not isinstance(contract, Mapping) or not isinstance(capability, Mapping):
        raise SddContractError("active session has no executable frozen judge")
    _verify_seal(contract, "verification contract")
    if contract.get("document_hash") != sdd.get("contract_hash"):
        raise SddContractError("stored verification contract does not match the session")
    envelope = capability.get("envelope")
    if not isinstance(envelope, Mapping):
        raise SddContractError("active session capability has no task envelope")
    # Only the exact frozen session capability may supply a verifier budget.
    capability_body = dict(capability)
    capability_hash = capability_body.pop("capability_hash", None)
    _verify_seal(
        {**capability_body, "document_hash": capability_hash},
        "session capability",
    )
    _verify_seal(envelope, "task envelope")
    checked_envelope = validate_task_envelope(envelope)
    if (
        checked_envelope["document_hash"] != sdd.get("envelope_hash")
        or checked_envelope["document_hash"] != capability.get("envelope_hash")
        or checked_envelope["contract_hash"] != contract["document_hash"]
        or capability.get("contract_hash") != contract["document_hash"]
    ):
        raise SddContractError("verifier budget requires the exact bound frozen envelope")
    budget = TaskBudget(root, checked_envelope)
    try:
        effective_timeout = budget.remaining()
    except TaskBudgetError as exc:
        return {**exc.to_dict(), "session_id": session_id}
    if timeout is not None:
        if type(timeout) is not int or timeout <= 0:
            raise SddContractError("runtime timeout must be a positive integer cap")
        effective_timeout = min(effective_timeout, timeout)
    timeout = effective_timeout
    deadline = time.monotonic() + timeout
    obligations = _selected_frozen_obligations(contract, checked_envelope)

    for obligation in obligations:
        error_code = _preflight_frozen_obligation(root, obligation, checked_envelope)
        if error_code:
            return {
                "ok": False,
                "error_type": "SDD_VERIFIER_ADMISSION_FAILED",
                "error_code": error_code,
                "mutation_performed": False,
                "session_id": session_id,
            }

    try:
        content_snapshot = _verification_content_snapshot(root, contract, checked_envelope, obligations)
    except (OSError, SddContractError):
        return {
            "ok": False, "error_type": "SDD_VERIFIER_ADMISSION_FAILED",
            "error_code": "verification_content_unavailable", "mutation_performed": False,
            "session_id": session_id,
        }
    before_inputs = _verification_input_binding(root, checked_envelope, obligations)
    verification_run_id = "sddvr_" + secrets.token_hex(16)
    execution_root, execution_policy = _prepare_verifier_workspace(root, checked_envelope, obligations, verification_run_id)
    execution_inputs = _verification_input_binding(str(execution_root), checked_envelope, obligations)
    sandbox_token = _VERIFIER_SANDBOX.set(str(execution_policy))
    commands: List[Dict[str, Any]] = []
    counts = dict.fromkeys(("collected", "executed", "passed", "failed", "skipped"), 0)
    command_failures = 0
    report_errors: List[str] = []
    try:
        for obligation in obligations:
            outcome = _run_frozen_argv(
                str(execution_root), list(obligation.get("command") or []), timeout=timeout, budget=budget, deadline=deadline
            )
            commands.append(
                {
                    "acceptance_id": obligation.get("acceptance_id"),
                    "command_hash": obligation.get("command_hash"),
                    **outcome,
                }
            )
            if not outcome.get("ok"):
                command_failures += 1
            report = outcome.get("test_report")
            if not isinstance(report, Mapping):
                report_errors.append(str(outcome.get("budget_exhausted") or outcome.get("test_report_error") or "test_report_missing"))
                continue
            for key in counts:
                counts[key] += report[key]
            if "expected_case_ids" in obligation and set(obligation["expected_case_ids"]) != {
                case["case_id"] for case in report["cases"]
            }:
                report_errors.append("test_case_mismatch")
            if outcome.get("timed_out") or outcome.get("budget_exhausted"):
                break

        material = [item for item in obligations if bool(item.get("material"))]
        falsification_results: List[Dict[str, Any]] = []
        if not command_failures and not report_errors and not counts["failed"] and not counts["skipped"] and counts["executed"]:
            for obligation in material:
                measured = next(item["test_report"] for item in commands
                                if item["acceptance_id"] == obligation["acceptance_id"])
                falsification_results.append(
                    _run_frozen_falsification(
                        str(execution_root), obligation, session_id=session_id, timeout=timeout,
                        expected_case_ids={case["case_id"] for case in measured["cases"]},
                        budget=budget, deadline=deadline,
                    )
                )
                if falsification_results[-1].get("budget_exhausted"):
                    break
        observed_red = not material or (
            len(falsification_results) == len(material)
            and all(item.get("observed_red") is True for item in falsification_results)
        )
        restored_green = not material or (
            len(falsification_results) == len(material)
            and all(item.get("restored_green") is True for item in falsification_results)
        )

    finally:
        _VERIFIER_SANDBOX.reset(sandbox_token)
    try:
        after_snapshot = _verification_content_snapshot(root, contract, checked_envelope, obligations)
    except (OSError, SddContractError):
        after_snapshot = None
    execution_drift = execution_inputs != _verification_input_binding(str(execution_root), checked_envelope, obligations)
    after_inputs = _verification_input_binding(root, checked_envelope, obligations)
    verifier_mutated = execution_drift or before_inputs != after_inputs or (after_snapshot is not None and after_snapshot != content_snapshot)
    perspectives = sorted(
        {
            str(perspective)
            for obligation in obligations
            for perspective in obligation.get("perspectives") or []
        }
    )
    result = {
        "capability_role": "verifier",
        "mutated": verifier_mutated,
        "content_snapshot_valid": after_snapshot is not None,
        **counts,
        "command_failures": command_failures,
        "report_errors": sorted(set(report_errors)),
        "perspectives": perspectives,
        "perspectives_source": "frozen_contract_declaration",
        "asset_drift": False,
        "commands": commands,
        "falsification": {
            "observed_red": observed_red,
            "restored_green": restored_green,
        },
    }
    budget_failure = next((item.get("budget_exhausted") for item in commands + falsification_results
                           if item.get("budget_exhausted")), None)
    try:
        result["budget"] = budget.check()
        if time.monotonic() >= deadline:
            raise TaskBudgetError("verifier_runtime_cap_exhausted")
    except TaskBudgetError as exc:
        budget_failure = exc.reason
    if budget_failure:
        result["report_errors"] = sorted(set(result["report_errors"] + [budget_failure]))
    aggregate = {
        "acceptance_id": "+".join(
            str(item.get("acceptance_id") or "") for item in obligations
        ),
        "perspectives": perspectives,
        "material": bool(material),
    }
    decision = evaluate_verification(aggregate, result)
    incidents = _verification_incidents(root, sdd.get("contract_hash"), aggregate["acceptance_id"], verification_run_id, decision)
    if incidents and decision.get("accepted") is True:
        decision = {**decision, "accepted": False, "reasons": list(decision.get("reasons") or []) + ["unresolved_verification_incident"]}
    verification = _seal(
        {
            "schema": "apatch.sdd.session-verification.v1",
            "obligation_id": aggregate["acceptance_id"],
            "verification_run_id": verification_run_id,
            "input_binding": after_inputs,
            "session_id": session_id,
            "content_snapshot_hash": content_snapshot["document_hash"],
            "decision": decision,
            "result": result,
        }
    )
    result_hashes = [
        item[key]
        for item in falsification_results
        for key in ("red_result_hash", "restored_result_hash")
    ]
    falsification = _seal(
        {
            "schema": "apatch.sdd.session-falsification.v1",
            "verification_run_id": verification_run_id,
            "observed_red": observed_red,
            "restored_green": restored_green,
            "bytes_restored": all(item.get("bytes_restored") is True for item in falsification_results),
            "mode_restored": all(item.get("mode_restored") is True for item in falsification_results),
            "restored_result": {"ok": all(item.get("restored_green") is True for item in falsification_results)},
            "result_hashes": result_hashes,
        }
    )
    updated_sdd = dict(sdd)
    updated_sdd.update(
        {
            "verification": verification,
            "verification_content_snapshot": content_snapshot,
            "unresolved_incidents": incidents,
            "falsification": falsification,
            "verification_run_id": verification_run_id,
        }
    )
    _save_sdd_binding(root, raw, updated_sdd)

    accepted = decision.get("accepted") is True
    error_code = None
    if not accepted:
        if after_snapshot is None:
            error_code = "verification_content_unavailable"
        elif verifier_mutated:
            error_code = "verifier_mutated_assets"
        elif report_errors:
            error_code = report_errors[0]
        elif counts["failed"] or command_failures:
            error_code = "frozen_check_failed"
        elif counts["skipped"]:
            error_code = "required_tests_skipped"
        else:
            error_code = "falsification_failed"
    response = {
        "ok": accepted,
        "verification_run_id": verification_run_id,
        "session_id": session_id,
        "verification": verification,
        "falsification": falsification,
        "execution_workspace": ".apatch/verification-runs/" + verification_run_id + "/workspace",
        "unresolved_incidents": incidents,
        "mutation_performed": False,
    }
    if error_code:
        response.update(
            {
                "error_type": "SDD_VERIFICATION_FAILED",
                "error_code": error_code,
                "recoverable": True,
                "recommended_action": "fix_forward",
            }
        )
    if budget_failure:
        response.update(TaskBudgetError(budget_failure).to_dict())
    return response


def _save_sdd_binding(target_dir: str, raw: MutableMapping[str, Any], sdd: Mapping[str, Any]) -> None:
    updated = dict(raw)
    updated["sdd"] = _clone(dict(sdd))
    from apatch.session_state import save_session_state

    save_session_state(
        target_dir,
        updated,
        expected_session_id=raw.get("session_id"),
        expected_revision=int(raw.get("revision") or 0),
    )


def record_session_verification(
    target_dir: str,
    obligation: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    """Persist a structured non-mutating verifier decision on the exact session."""

    from apatch.session_state import load_session_state

    raw = load_session_state(target_dir)
    sdd = raw.get("sdd")
    if not isinstance(sdd, Mapping):
        raise SddContractError("active session has no SDD binding")
    decision = evaluate_verification(obligation, result)
    record = _seal(
        {
            "schema": "apatch.sdd.session-verification.v1",
            "obligation_id": _require_text(
                obligation.get("acceptance_id"), "acceptance_id"
            ),
            "decision": decision,
            "result": _clone(dict(result)),
        }
    )
    updated_sdd = dict(sdd)
    updated_sdd["verification"] = record
    _save_sdd_binding(target_dir, raw, updated_sdd)
    return record


def record_session_falsification(
    target_dir: str,
    *,
    observed_red: bool,
    restored_green: bool,
    result_hashes: Sequence[str],
) -> Dict[str, Any]:
    """Bind reversible red-to-green evidence without performing the mutation."""

    from apatch.session_state import load_session_state

    raw = load_session_state(target_dir)
    sdd = raw.get("sdd")
    if not isinstance(sdd, Mapping):
        raise SddContractError("active session has no SDD binding")
    hashes = [_require_hash(item, "falsification result_hash") for item in result_hashes]
    record = _seal(
        {
            "schema": "apatch.sdd.session-falsification.v1",
            "observed_red": bool(observed_red),
            "restored_green": bool(restored_green),
            "result_hashes": hashes,
        }
    )
    updated_sdd = dict(sdd)
    updated_sdd["falsification"] = record
    _save_sdd_binding(target_dir, raw, updated_sdd)
    return record


def build_session_attestation_evidence(
    target_dir: str,
    state: Mapping[str, Any],
    ledger_entries: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Derive complete evidence from the authoritative session and signed ledger."""

    sdd = state.get("sdd")
    if not isinstance(sdd, Mapping):
        raise SddContractError("active session has no SDD binding")
    adherence = sdd.get("adherence")
    if not isinstance(adherence, Mapping) or adherence.get("status") != "compliant":
        raise SddContractError("scope adherence is not compliant")
    verification = sdd.get("verification")
    decision = verification.get("decision") if isinstance(verification, Mapping) else None
    if not isinstance(decision, Mapping) or decision.get("accepted") is not True:
        raise SddContractError("meaningful verifier result is required before attestation")
    falsification = sdd.get("falsification")
    if bool(sdd.get("material_gate_required")) and not (
        isinstance(falsification, Mapping)
        and falsification.get("observed_red") is True
        and falsification.get("restored_green") is True
    ):
        raise SddContractError("material gate requires observed red and restored green")

    snapshot = sdd.get("verification_content_snapshot")
    if not isinstance(snapshot, Mapping):
        raise SddContractError("verification content snapshot is required; rerun frozen verification")
    _verify_seal(verification, "session verification")
    _verify_seal(snapshot, "verification content snapshot")
    if (verification.get("content_snapshot_hash") != snapshot.get("document_hash")
            or verification.get("session_id") != state.get("session_id")):
        raise SddContractError("verification content snapshot does not match this GREEN and session")
    contract = sdd.get("verification_contract")
    envelope = (sdd.get("capability") or {}).get("envelope")
    if not isinstance(contract, Mapping) or not isinstance(envelope, Mapping):
        raise SddContractError("verification content snapshot requires its frozen contract and envelope")
    _verify_seal(contract, "verification contract")
    checked_envelope = validate_task_envelope(envelope)
    if (contract.get("document_hash") != sdd.get("contract_hash")
            or checked_envelope["contract_hash"] != contract.get("document_hash")
            or checked_envelope["document_hash"] != sdd.get("envelope_hash")):
        raise SddContractError("verification content snapshot requires the exact frozen binding")
    try:
        TaskBudget(target_dir, checked_envelope).check()
    except TaskBudgetError as exc:
        raise SddContractError("task budget blocks attestation: " + exc.reason) from exc
    try:
        current_snapshot = _verification_content_snapshot(
            target_dir, contract, checked_envelope,
            _selected_frozen_obligations(contract, checked_envelope),
        )
    except (OSError, SddContractError) as exc:
        raise SddContractError("verification content snapshot is unavailable; rerun frozen verification") from exc
    if current_snapshot != snapshot:
        raise SddContractError("verification content changed since GREEN; rerun frozen verification")

    if verification.get("input_binding") != _verification_input_binding(
        target_dir, checked_envelope, _selected_frozen_obligations(contract, checked_envelope)
    ):
        raise SddContractError("frozen verification inputs drifted; run the frozen verifier again")

    from apatch.traceability import build_traceability_index

    index = build_traceability_index([dict(item) for item in ledger_entries])
    session_id = str(state.get("session_id") or "")
    mutation_ids: List[str] = []
    ledger_refs: List[str] = []
    for op_id, summary in (index.get("op_id_index") or {}).items():
        if str(summary.get("governed_session_id") or "") != session_id:
            continue
        ledger_refs.append(str(op_id))
        if summary.get("role") == "mutation":
            mutation_ids.append(str(op_id))

    checkpoint_refs: List[str] = []
    checkpoint = state.get("checkpoint")
    if checkpoint:
        checkpoint_refs.append(str(checkpoint))
    for item in state.get("checkpoints") or []:
        if item and str(item) not in checkpoint_refs:
            checkpoint_refs.append(str(item))

    return build_attestation_evidence(
        contract_hash=str(sdd.get("contract_hash") or ""),
        envelope_hash=str(sdd.get("envelope_hash") or ""),
        actor=sdd.get("actor") or {},
        mutation_ids=mutation_ids,
        adherence=adherence,
        verification=verification,
        falsification=falsification or {},
        environment_hash=snapshot["environment_hash"],
        checkpoint_refs=checkpoint_refs,
        ledger_refs=ledger_refs,
        task_envelope=(sdd.get("capability") or {}).get("envelope"),
    )
def session_attestation_projection(state: Mapping[str, Any]) -> Dict[str, Any] | None:
    """Return the immutable SDD binding safe to include in a ledger payload."""

    sdd = state.get("sdd")
    if not isinstance(sdd, Mapping):
        return None
    return {
        "schema": "apatch.sdd.attestation-binding.v1",
        "contract_hash": sdd.get("contract_hash"),
        "envelope_hash": sdd.get("envelope_hash"),
        "actor": _clone(sdd.get("actor") or {}),
        "requirement": sdd.get("requirement"),
        "containment": sdd.get("containment"),
        "adherence": _clone(sdd.get("adherence") or {}),
        "verification": _clone(sdd.get("verification") or {}),
        "falsification": _clone(sdd.get("falsification") or {}),
    }


def amend_contract(
    contract: Mapping[str, Any],
    *,
    reason: str,
    authority: Mapping[str, Any],
    changed_acceptance_ids: Sequence[str],
    affected_requirements: Sequence[str],
    all_requirements: Sequence[str],
) -> Dict[str, Any]:
    """Create an additive amendment and a selective invalidation set."""

    _verify_seal(contract, "verification contract")
    if authority.get("role") != "authority":
        raise SddContractError("only authority may approve an amendment")
    _require_text(authority.get("actor_id"), "authority actor_id")
    affected = list(dict.fromkeys(str(item) for item in affected_requirements))
    all_ids = list(dict.fromkeys(str(item) for item in all_requirements))
    unknown = sorted(set(affected) - set(all_ids))
    if unknown:
        raise SddContractError("affected requirements are not in the contract")
    body = {
        "schema": "apatch.sdd.contract-amendment.v1",
        "supersedes_hash": contract["document_hash"],
        "reason": _require_text(reason, "reason"),
        "authority": _clone(dict(authority)),
        "changed_acceptance_ids": list(
            dict.fromkeys(str(item) for item in changed_acceptance_ids)
        ),
        "invalidated_requirement_ids": [item for item in all_ids if item in set(affected)],
        "preserved_requirement_ids": [item for item in all_ids if item not in set(affected)],
        "implementation_actor_may_approve": False,
    }
    return _seal(body)


def integrity_floor(edition: str) -> Set[str]:
    if str(edition).lower() not in ("oss", "pro"):
        raise SddContractError("edition must be oss or pro")
    return set(INTEGRITY_FLOOR)


def containment_claim(level: str, *, direct_shell_available: bool) -> Dict[str, Any]:
    if level not in ("mediated_only", "sealed"):
        raise SddContractError("unsupported containment level")
    unsupported = ["direct_shell"] if direct_shell_available else []
    contained = level == "sealed" and not unsupported
    return {
        "level": level,
        "contained": contained,
        "unsupported_channels": unsupported,
    }


def validate_native_snapshot(contract: Mapping[str, Any], envelopes: Mapping[str, Any]) -> None:
    """Read-only cowork-exact-v1 profile of the existing native v1 documents.

    APatch owns these native formats. Platform keeps its independent boundary
    validator; shared wire cases prove agreement without a runtime dependency.
    This validates content seals, not identity, consent or signatures.
    """
    def require(condition: bool, label: str) -> None:
        if not condition:
            raise SddContractError(label)

    def text(value: Any) -> bool:
        return isinstance(value, str) and bool(value) and value == value.strip() and all(ord(c) >= 32 for c in value)

    def strings(value: Any, nonempty: bool = False) -> bool:
        return (isinstance(value, list) and (bool(value) or not nonempty)
                and all(text(v) for v in value) and len(set(value)) == len(value))

    def path(value: Any) -> bool:
        return (text(value) and "\\" not in value and not PurePosixPath(value).is_absolute()
                and ".." not in PurePosixPath(value).parts and str(PurePosixPath(value)) == value and value != ".")

    require(isinstance(contract, Mapping) and contract.get("schema") == "apatch.sdd.verification-contract.v1",
            "unsupported verification contract schema")
    _verify_seal(contract, "verification contract")
    require(contract.get("status") == "frozen" and contract.get("implementation_allowed") is True
            and type(contract.get("source_mutation_count_at_freeze")) is int
            and contract["source_mutation_count_at_freeze"] == 0, "contract is not pre-implementation frozen")
    for key in ("brief_hash", "rfp_hash", "spec_hash", "plan_hash", "baseline_hash"):
        _require_hash(contract.get(key), key)
    coverage = contract.get("coverage")
    require(isinstance(coverage, Mapping) and coverage.get("complete") is True
            and all(coverage.get(k) == [] for k in ("missing", "ambiguous", "waivers")), "native coverage is incomplete")
    authority = contract.get("authority")
    require(isinstance(authority, Mapping) and authority.get("role") == "authority"
            and text(authority.get("actor_id")), "native authority is invalid")
    environment = contract.get("judge_environment")
    require(isinstance(environment, Mapping) and set(environment) == {"cwd", "env"}, "judge_environment is required by cowork-exact-v1")
    require(environment["cwd"] == "." or path(environment["cwd"]), "judge_environment.cwd is invalid")
    require(isinstance(environment["env"], Mapping) and all(
        isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
        and isinstance(v, str) and "\x00" not in v for k, v in environment["env"].items()), "judge_environment.env is invalid")
    obligations = contract.get("obligations")
    require(isinstance(obligations, list) and bool(obligations), "frozen obligations are required")
    checks: Dict[str, Mapping[str, Any]] = {}
    for raw in obligations:
        require(isinstance(raw, Mapping), "frozen obligation must be an object")
        item = _validate_obligation(raw)
        require(all(text(item.get(k)) for k in ("acceptance_id", "test_id", "oracle", "approver"))
                and item["acceptance_id"] not in checks, "frozen check identity/facts are invalid")
        require(strings(item.get("perspectives"), True), "frozen perspectives are invalid")
        require(all(_valid_hash(v) for v in item["asset_hashes"]), "frozen asset hashes are invalid")
        require(all(isinstance(a, Mapping) and set(a) == {"path", "sha256"} and path(a["path"])
                    for a in item["judge_assets"]), "frozen judge asset path is invalid")
        require(all(text(v) for v in item["command"]) and canonical_hash(item["command"]) == item["command_hash"], "frozen command_hash mismatch")
        if item["material"]:
            require(item["falsification"].get("kind") in {"reversible_seed", "reversible_builtin_mutant"}
                    and path(item["falsification"]["target_path"]), "unsupported falsification profile")
        checks[item["acceptance_id"]] = item
    require(isinstance(envelopes, Mapping) and bool(envelopes), "task envelopes are required")
    spec_id = None
    for rk, raw in envelopes.items():
        require(isinstance(raw, Mapping) and "document_hash" in raw, "task envelope must be sealed")
        envelope = validate_task_envelope(raw)
        match = re.fullmatch(r"(SPEC-[A-Z0-9][A-Z0-9-]*)#(R[0-9]+)", str(envelope.get("requirement")))
        require(match is not None and match[2] == rk, "task requirement does not match its key/profile")
        spec_id = match[1] if spec_id is None else spec_id
        require(match[1] == spec_id and envelope["contract_hash"] == contract["document_hash"]
                and envelope["baseline_hash"] == contract["baseline_hash"], "task contract/baseline pins do not match")
        for key in ("allowed_reads", "allowed_writes", "allowed_symbols", "forbidden_paths", "tools", "network", "remote", "services", "checks"):
            require(strings(envelope[key], key == "checks"), f"task {key} must be a unique string list")
        require(all(path(p) for key in ("allowed_reads", "allowed_writes", "forbidden_paths") for p in envelope[key]), "task scope path is invalid")
        require(set(envelope["checks"]) <= checks.keys(), "task references a missing frozen check")
        require(all(all(text(v) for v in argv) for argv in envelope["commands"])
                and {canonical_hash(argv) for argv in envelope["commands"]}
                == {checks[c]["command_hash"] for c in envelope["checks"]}, "task argv do not match selected checks")
        require(text(envelope["rollback_owner"]), "task rollback_owner is invalid")

