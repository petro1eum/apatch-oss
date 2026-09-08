"""RFP-016 Artifact Governance Layer — CORE invariants + Phase 1 registry (SPEC-HYGIENE-1)."""

from __future__ import annotations

import fnmatch
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

STATIC_REPLAY_CRITICAL_VERSION = 1

_STATIC_GLOBS_FLAT: tuple[str, ...] = (
    ".trustchain/**",
    ".apatch/events.jsonl",
    ".apatch/session_state.json",
    ".apatch/enforcement.json",
    ".apatch/sandbox.json",
    ".apatch/policy.lock.json",
    ".apatch/inclusion.jsonl",
    ".apatch/reality.jsonl",
)

_STATIC_GLOBS_STRUCTURED: tuple[str, ...] = (
    ".trustchain/**",
    ".apatch/ledger/**",
    ".apatch/state/**",
    ".apatch/inclusion.jsonl",
)

STATIC_REPLAY_CRITICAL_PATHS: FrozenSet[str] = frozenset(
    _STATIC_GLOBS_FLAT + _STATIC_GLOBS_STRUCTURED
)

REGISTRY_DIR = ".apatch/registry"
ARTIFACTS_REGISTRY_REL = f"{REGISTRY_DIR}/artifacts.jsonl"
ARTIFACTS_REGISTRY_FLAT = ".apatch/artifacts.jsonl"
PROVENANCE_REL = f"{REGISTRY_DIR}/provenance.jsonl"
PROVENANCE_FLAT = ".apatch/provenance.jsonl"

HISTORY_OVERFLOW_LIMIT = 50

_HYGIENE_REPAIR_ACTIONS: Dict[str, List[Dict[str, str]]] = {
    "HISTORY_OVERFLOW": [{"tool": "apatch_gc", "mode": "rotate"}],
    "INFERRED_ARTIFACTS": [{"tool": "apatch_gc", "mode": "reconcile"}],
    "ORPHAN_EPHEMERAL": [
        {"tool": "apatch_gc", "mode": "reconcile"},
        {"tool": "apatch_gc", "mode": "safe"},
    ],
}


def _compact_hygiene_issues(issues: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate debt classes and cap path samples in doctor/GC DTOs."""
    severity_rank = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for raw in issues:
        issue_type = str(raw.get("type") or "UNKNOWN")
        if issue_type not in merged:
            merged[issue_type] = dict(raw)
            merged[issue_type]["count"] = int(raw.get("count") or 0)
            merged[issue_type]["paths"] = []
            order.append(issue_type)
        else:
            merged[issue_type]["count"] += int(raw.get("count") or 0)
            current = str(merged[issue_type].get("severity") or "info")
            incoming = str(raw.get("severity") or "info")
            if severity_rank.get(incoming, 0) > severity_rank.get(current, 0):
                merged[issue_type]["severity"] = incoming
        samples = merged[issue_type]["paths"]
        for path in raw.get("paths") or []:
            if path not in samples and len(samples) < 10:
                samples.append(path)
        actions = _HYGIENE_REPAIR_ACTIONS.get(issue_type)
        if actions:
            merged[issue_type]["repair"] = {"calls": [dict(call) for call in actions]}
    return [merged[issue_type] for issue_type in order]


def _hygiene_repair_actions(issues: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "issue_type": str(issue.get("type")),
            "calls": [dict(call) for call in _HYGIENE_REPAIR_ACTIONS[str(issue.get("type"))]],
        }
        for issue in issues
        if str(issue.get("type")) in _HYGIENE_REPAIR_ACTIONS
    ]


class ArtifactClass(str, Enum):
    STATE = "STATE"
    LEDGER = "LEDGER"
    REGISTRY = "REGISTRY"
    RUN_STATE = "RUN_STATE"
    HISTORY = "HISTORY"
    EPHEMERAL = "EPHEMERAL"
    DEBUG = "DEBUG"
    GARBAGE = "GARBAGE"
    UNREGISTERED = "UNREGISTERED"
    UNCLASSIFIED = "UNCLASSIFIED"


_CANONICAL_PATH_CLASSES: Dict[str, str] = {
    ".apatch/conformance.json": ArtifactClass.STATE.value,
    ".apatch/project_index.json": ArtifactClass.REGISTRY.value,
    ".apatch/reality.jsonl": ArtifactClass.LEDGER.value,
    ".apatch/remote.json": ArtifactClass.STATE.value,
    ".apatch/request_journal.json": ArtifactClass.RUN_STATE.value,
}


class ArtifactTier(str, Enum):
    CORE = "CORE"
    MANAGED = "MANAGED"
    TRANSIENT = "TRANSIENT"


_CLASS_TIER: Dict[str, ArtifactTier] = {
    ArtifactClass.STATE.value: ArtifactTier.CORE,
    ArtifactClass.LEDGER.value: ArtifactTier.CORE,
    ArtifactClass.REGISTRY.value: ArtifactTier.CORE,
    ArtifactClass.RUN_STATE.value: ArtifactTier.MANAGED,
    ArtifactClass.HISTORY.value: ArtifactTier.MANAGED,
    ArtifactClass.EPHEMERAL.value: ArtifactTier.TRANSIENT,
    ArtifactClass.DEBUG.value: ArtifactTier.TRANSIENT,
    ArtifactClass.GARBAGE.value: ArtifactTier.TRANSIENT,
    ArtifactClass.UNREGISTERED.value: ArtifactTier.TRANSIENT,
    ArtifactClass.UNCLASSIFIED.value: ArtifactTier.TRANSIENT,
}

_DEFAULT_GC_ALLOWED: Dict[str, bool] = {
    ArtifactClass.STATE.value: False,
    ArtifactClass.LEDGER.value: False,
    ArtifactClass.REGISTRY.value: False,
    ArtifactClass.RUN_STATE.value: False,
    ArtifactClass.HISTORY.value: True,
    ArtifactClass.EPHEMERAL.value: True,
    ArtifactClass.DEBUG.value: True,
    ArtifactClass.GARBAGE.value: True,
    ArtifactClass.UNREGISTERED.value: False,
    ArtifactClass.UNCLASSIFIED.value: False,
}

_DEFAULT_REPLAY_CRITICAL: Dict[str, bool] = {
    ArtifactClass.STATE.value: True,
    ArtifactClass.LEDGER.value: True,
    ArtifactClass.REGISTRY.value: True,
    ArtifactClass.RUN_STATE.value: True,
    ArtifactClass.HISTORY.value: False,
    ArtifactClass.EPHEMERAL.value: False,
    ArtifactClass.DEBUG.value: False,
    ArtifactClass.GARBAGE.value: False,
    ArtifactClass.UNREGISTERED.value: False,
    ArtifactClass.UNCLASSIFIED.value: False,
}

INFERENCE_RULES: Tuple[Tuple[str, str], ...] = (
    ("patches-*.jsonl", "EPHEMERAL"),
    ("patches.jsonl", "EPHEMERAL"),
    (".apatch/_bench*.jsonl", "GARBAGE"),
    (".apatch/_big.jsonl", "GARBAGE"),
    (".apatch/_cold.jsonl", "GARBAGE"),
    (".apatch/_mcp_bench.jsonl", "GARBAGE"),
    (".apatch/backups/**", "HISTORY"),
    (".apatch/mcp_stderr.log", "DEBUG"),
    (".apatch/sandbox_violations.jsonl", "DEBUG"),
    (".apatch/tmp/**", "EPHEMERAL"),
    (".apatch/verify_jobs/**", "EPHEMERAL"),
    (".apatch/registry/**", "REGISTRY"),
    (".apatch/specs/*.json", "REGISTRY"),
    (".apatch/hygiene/**", "STATE"),
    (".apatch/state/**", "STATE"),
    (".apatch/ledger/**", "LEDGER"),
    (".apatch/lanes/**", "RUN_STATE"),
    (".apatch/diagnostics/**", "RUN_STATE"),
    (".apatch/staging/**", "RUN_STATE"),
    (".apatch/mcp.json", "STATE"),
    (".apatch/mcp_tool_fingerprint.json", "STATE"),
    (".apatch/verify_baseline.json", "STATE"),
    (".apatch/notarized_index.json", "REGISTRY"),
    (".apatch/replay_log.json", "RUN_STATE"),
    (".apatch/mcp_apply_report.json", "RUN_STATE"),
    (".apatch/report.html", "DEBUG"),
    (".apatch/enforcement.json", "STATE"),
    (".apatch/sandbox.json", "STATE"),
    (".apatch/agent-identity.json", "STATE"),
    (".apatch/lane_registry.json", "STATE"),
    (".apatch/session_state.json", "STATE"),
    (".apatch/policy.lock.json", "STATE"),
    (".apatch/inclusion.jsonl", "LEDGER"),
    (".apatch/events.jsonl", "LEDGER"),
    (".apatch/apply_session*.json", "RUN_STATE"),
    (".apatch/spec_run.json", "RUN_STATE"),
    (".apatch/console.json", "DEBUG"),
    (".apatch/doc-*.jsonl", "EPHEMERAL"),
    (".apatch/*needles*.json", "EPHEMERAL"),
    (".apatch/*needles*.py", "EPHEMERAL"),
    (".apatch/spec*run*.json", "EPHEMERAL"),
    (".apatch/*manifest*.json", "EPHEMERAL"),
    (".apatch/*.jsonl", "EPHEMERAL"),
)


class LineageContractError(ValueError):
    pass


class GCInvariantViolation(RuntimeError):
    pass


@dataclass
class RegistryEntry:
    path: str
    registered: bool = True
    replay_critical: bool = False
    gc_allowed: bool = False
    run_lease_id: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    class_name: str = "EPHEMERAL"
    artifact_id: Optional[str] = None
    lineage: Optional[Dict[str, Any]] = None


@dataclass
class InferenceSunsetPolicy:
    max_governed_ops: int = 100

    def should_block_governed(self, inferred_count: int, ops: int) -> bool:
        return inferred_count > 0 and ops >= self.max_governed_ops


def artifact_tier(class_name: str) -> ArtifactTier:
    return _CLASS_TIER.get(class_name, ArtifactTier.TRANSIENT)


def default_gc_allowed(class_name: str) -> bool:
    return _DEFAULT_GC_ALLOWED.get(class_name, False)


def default_replay_critical(class_name: str) -> bool:
    return _DEFAULT_REPLAY_CRITICAL.get(class_name, False)


def _normalize_path(path: str) -> str:
    p = path.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    while p.startswith("/"):
        p = p[1:]
    return p


def workspace_from_artifact_path(path: str) -> str:
    abs_path = os.path.abspath(path)
    norm = abs_path.replace("\\", "/")
    marker = "/.apatch/"
    if marker in norm:
        return norm.split(marker)[0]
    return os.path.dirname(abs_path)


def _rel_workspace_path(workspace: str, path: str) -> str:
    root = os.path.abspath(workspace)
    if os.path.isabs(path):
        try:
            return _normalize_path(os.path.relpath(path, root))
        except ValueError:
            return _normalize_path(path)
    return _normalize_path(path)


def _glob_match(path: str, pattern: str) -> bool:
    norm = _normalize_path(path)
    pat = _normalize_path(pattern)
    if pat.endswith("/**"):
        prefix = pat[:-3].rstrip("/")
        return norm == prefix or norm.startswith(prefix + "/")
    if fnmatch.fnmatch(norm, pat):
        return True
    # '**/' requires a literal '/' under fnmatch, excluding root-level files; also match
    # the de-prefixed pattern so '**/*.md' covers a root README.md (parity with path_matches).
    if pat.startswith("**/"):
        return fnmatch.fnmatch(norm, pat[3:])
    return False


def is_static_replay_critical(path: str) -> bool:
    norm = _normalize_path(path)
    for pat in STATIC_REPLAY_CRITICAL_PATHS:
        if _glob_match(norm, pat):
            return True
    return False


def validate_lineage(lineage: Mapping[str, Any]) -> None:
    if not isinstance(lineage, Mapping):
        raise LineageContractError("lineage must be a mapping")
    tool = lineage.get("created_by_tool")
    reason = lineage.get("reason")
    if not tool or not str(tool).strip():
        raise LineageContractError("lineage.created_by_tool required")
    if not reason or not str(reason).strip():
        raise LineageContractError("lineage.reason required")


def _validate_class_name(class_name: str) -> str:
    name = str(class_name).strip().upper()
    try:
        ArtifactClass(name)
    except ValueError as exc:
        raise LineageContractError(f"unknown artifact class: {class_name}") from exc
    return name


def resolve_registry_paths(workspace: str) -> Tuple[str, str, str]:
    """Return (artifacts_jsonl, provenance_jsonl, layout)."""
    root = os.path.abspath(workspace)
    structured_dir = os.path.join(root, REGISTRY_DIR)
    structured_art = os.path.join(root, ARTIFACTS_REGISTRY_REL)
    flat_art = os.path.join(root, ARTIFACTS_REGISTRY_FLAT)
    if os.path.isdir(structured_dir) or os.path.isfile(structured_art):
        return (
            structured_art,
            os.path.join(root, PROVENANCE_REL),
            "structured",
        )
    if os.path.isfile(flat_art):
        return flat_art, os.path.join(root, PROVENANCE_FLAT), "flat"
    return structured_art, os.path.join(root, PROVENANCE_REL), "structured"


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _bootstrap_registry_store(workspace: str) -> None:
    """Register registry store files as REGISTRY class (bootstrap, no recursion)."""
    art_abs, prov_abs, _ = resolve_registry_paths(workspace)
    art_rel = _rel_workspace_path(workspace, art_abs)
    prov_rel = _rel_workspace_path(workspace, prov_abs)
    existing = load_registry(workspace)
    ts = datetime.now(timezone.utc).isoformat()
    lineage = {
        "created_by_tool": "apatch_artifact_governance",
        "reason": "registry store bootstrap",
        "provenance": "registered",
    }
    for rel in (art_rel, prov_rel):
        if rel in existing:
            continue
        abs_path = os.path.join(os.path.abspath(workspace), rel)
        if not os.path.isfile(abs_path):
            continue
        store_id = uuid.uuid4().hex
        store_record = {
            "artifact_id": store_id,
            "path": rel,
            "class_name": ArtifactClass.REGISTRY.value,
            "registered": True,
            "replay_critical": True,
            "gc_allowed": False,
            "run_lease_id": None,
            "depends_on": [],
            "lineage": lineage,
            "ts": ts,
        }
        _append_jsonl(art_abs, store_record)
        _append_jsonl(
            prov_abs,
            {"artifact_id": store_id, "path": rel, "ts": ts, "lineage": lineage},
        )
        existing[rel] = _entry_from_record(store_record)


def _entry_from_record(rec: Mapping[str, Any]) -> RegistryEntry:
    path = _normalize_path(str(rec.get("path", "")))
    class_name = str(rec.get("class_name") or rec.get("class") or "EPHEMERAL")
    lineage = rec.get("lineage")
    if isinstance(lineage, dict):
        lineage = dict(lineage)
    replay = rec.get("replay_critical")
    if replay is None:
        replay = default_replay_critical(class_name) or is_static_replay_critical(path)
    gc_allowed = rec.get("gc_allowed")
    if gc_allowed is None:
        gc_allowed = default_gc_allowed(class_name)
    depends = rec.get("depends_on") or []
    if not isinstance(depends, list):
        depends = []
    return RegistryEntry(
        path=path,
        registered=True,
        replay_critical=bool(replay),
        gc_allowed=bool(gc_allowed),
        run_lease_id=rec.get("run_lease_id"),
        depends_on=[_normalize_path(str(d)) for d in depends],
        class_name=class_name,
        artifact_id=rec.get("artifact_id"),
        lineage=lineage,
    )


def load_registry(workspace: str) -> Dict[str, RegistryEntry]:
    art_path, _, _ = resolve_registry_paths(workspace)
    by_path: Dict[str, RegistryEntry] = {}
    for rec in _read_jsonl(art_path):
        entry = _entry_from_record(rec)
        if entry.path:
            by_path[entry.path] = entry
    return by_path


def register_artifact(
    workspace: str,
    path: str,
    contract: Mapping[str, Any],
) -> RegistryEntry:
    """Persist registry + provenance lines; abort caller on validation failure."""
    rel = _rel_workspace_path(workspace, path)
    class_name = _validate_class_name(str(contract.get("class_name", "EPHEMERAL")))
    lineage_raw = contract.get("lineage") or {}
    if not isinstance(lineage_raw, Mapping):
        raise LineageContractError("lineage must be a mapping")
    lineage = dict(lineage_raw)
    if lineage.get("provenance") != "inferred":
        lineage.setdefault("provenance", "registered")
    validate_lineage(lineage)

    replay_critical = contract.get("replay_critical")
    if replay_critical is None:
        replay_critical = default_replay_critical(class_name) or is_static_replay_critical(rel)
    gc_allowed = contract.get("gc_allowed")
    if gc_allowed is None:
        gc_allowed = default_gc_allowed(class_name)
    depends_on = contract.get("depends_on") or []
    if not isinstance(depends_on, list):
        depends_on = []
    run_lease_id = contract.get("run_lease_id")

    artifact_id = str(contract.get("artifact_id") or uuid.uuid4().hex)
    ts = datetime.now(timezone.utc).isoformat()
    art_path, prov_path, _ = resolve_registry_paths(workspace)
    os.makedirs(os.path.dirname(art_path), exist_ok=True)

    record: Dict[str, Any] = {
        "artifact_id": artifact_id,
        "path": rel,
        "class_name": class_name,
        "registered": True,
        "replay_critical": bool(replay_critical),
        "gc_allowed": bool(gc_allowed),
        "run_lease_id": run_lease_id,
        "depends_on": [_normalize_path(str(d)) for d in depends_on],
        "lineage": lineage,
        "ts": ts,
    }
    _append_jsonl(art_path, record)
    _append_jsonl(
        prov_path,
        {
            "artifact_id": artifact_id,
            "path": rel,
            "ts": ts,
            "lineage": lineage,
        },
    )
    _bootstrap_registry_store(workspace)
    return _entry_from_record(record)


def register_on_write(
    workspace: str,
    path: str,
    *,
    class_name: str,
    created_by_tool: str,
    reason: str,
    depends_on: Optional[List[str]] = None,
    run_lease_id: Optional[str] = None,
    replay_critical: Optional[bool] = None,
    gc_allowed: Optional[bool] = None,
    governed_session_id: Optional[str] = None,
    spec_run_id: Optional[str] = None,
    content_sha256: Optional[str] = None,
    runtime_namespace: Optional[Mapping[str, Any]] = None,
) -> RegistryEntry:
    lineage: Dict[str, Any] = {
        "created_by_tool": created_by_tool,
        "reason": reason,
        "provenance": "registered",
    }
    if governed_session_id:
        lineage["governed_session_id"] = governed_session_id
    if spec_run_id:
        lineage["spec_run_id"] = spec_run_id
    if content_sha256:
        lineage["content_sha256"] = content_sha256
    if runtime_namespace:
        lineage["runtime_namespace"] = dict(runtime_namespace)
    contract: Dict[str, Any] = {
        "class_name": class_name,
        "lineage": lineage,
        "depends_on": depends_on or [],
        "run_lease_id": run_lease_id,
    }
    if replay_critical is not None:
        contract["replay_critical"] = replay_critical
    elif run_lease_id:
        contract["replay_critical"] = True
    if gc_allowed is not None:
        contract["gc_allowed"] = gc_allowed
    elif run_lease_id:
        contract["gc_allowed"] = False
    return register_artifact(workspace, path, contract)


def _is_ephemeral_jsonl_basename(name: str) -> bool:
    return name == "patches.jsonl" or (
        name.startswith("patches-") and name.endswith(".jsonl")
    )


def resolve_ephemeral_logs_path(workspace: str, out_path: str) -> Tuple[str, str]:
    """Route patch JSONL into its exact session/requirement namespace."""
    root = os.path.abspath(workspace)
    abs_out = out_path if os.path.isabs(out_path) else os.path.join(root, out_path)
    rel = _rel_workspace_path(root, abs_out)
    norm = _normalize_path(rel)
    if norm.startswith(".apatch/tmp/"):
        return abs_out, rel
    base = os.path.basename(norm)
    if not _is_ephemeral_jsonl_basename(base):
        return abs_out, rel
    if "/" in norm and not norm.startswith("patches-"):
        return abs_out, rel
    from apatch.runtime.namespace import runtime_namespace

    namespace = runtime_namespace(root)
    rel = _normalize_path(f"{namespace['relative_dir']}/{base}")
    return os.path.join(root, rel), rel


def register_ephemeral_logs(
    workspace: str,
    rel_path: str,
    *,
    created_by_tool: str,
    reason: str = "patch JSONL staging",
    governed_session_id: Optional[str] = None,
    spec_run_id: Optional[str] = None,
) -> RegistryEntry:
    root = os.path.abspath(workspace)
    if governed_session_id is None:
        from apatch.session_state import load_session_state

        governed_session_id = (load_session_state(root) or {}).get("session_id")
    norm = _normalize_path(rel_path)
    existing = load_registry(root).get(norm)
    if existing is not None and governed_session_id:
        owner = str((existing.lineage or {}).get("governed_session_id") or "")
        if owner and owner != governed_session_id:
            from apatch.runtime.errors import SessionBindingError

            raise SessionBindingError(
                "Refusing to re-register another session's patch log.",
                error_type="PATCH_LOG_OWNER_MISMATCH",
                expected=governed_session_id,
                actual=owner,
            )
    abs_path = os.path.join(root, norm)
    content_sha256 = None
    if os.path.isfile(abs_path):
        from apatch.runtime.session_binding import file_sha256

        content_sha256 = file_sha256(abs_path)
    lease = f"lease_{governed_session_id}" if governed_session_id else None
    from apatch.runtime.namespace import runtime_namespace

    namespace = runtime_namespace(root, session_id=governed_session_id)
    return register_on_write(
        root,
        _normalize_path(rel_path),
        class_name=ArtifactClass.EPHEMERAL.value,
        created_by_tool=created_by_tool,
        reason=reason,
        governed_session_id=governed_session_id,
        spec_run_id=spec_run_id,
        content_sha256=content_sha256,
        runtime_namespace=namespace,
        run_lease_id=lease,
        gc_allowed=False if lease else True,
        replay_critical=bool(lease),
    )


def delete_gc_allowed_session_ephemerals(workspace: str, session_id: str) -> List[str]:
    """Remove on-disk EPHEMERAL logs released for ``session_id`` (post session_end)."""
    if not session_id:
        return []
    root = os.path.abspath(workspace)
    registry = load_registry(root)
    deleted: List[str] = []
    for path, entry in registry.items():
        if _is_atomic_lock_path(path):
            continue
        if entry.class_name != ArtifactClass.EPHEMERAL.value:
            continue
        if not entry.gc_allowed or entry.run_lease_id:
            continue
        lineage = entry.lineage or {}
        if lineage.get("governed_session_id") != session_id:
            continue
        abs_path = os.path.join(root, path)
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            deleted.append(path)
        try:
            os.remove(abs_path + ".lock")
        except OSError:
            pass
    return sorted(deleted)


def infer_class_for_path(path: str) -> Optional[str]:
    norm = _normalize_path(path)
    canonical = _CANONICAL_PATH_CLASSES.get(norm)
    if canonical:
        return canonical
    for pattern, class_name in INFERENCE_RULES:
        if _glob_match(norm, pattern):
            return class_name
    return None


def _is_atomic_lock_path(path: str) -> bool:
    norm = _normalize_path(path)
    return norm.startswith(".apatch/") and norm.endswith(".lock")


def classify_unregistered(workspace: str) -> Dict[str, Dict[str, Any]]:
    """Closed-path inference for on-disk paths absent from registry."""
    root = os.path.abspath(workspace)
    registry = load_registry(workspace)
    inferred: Dict[str, Dict[str, Any]] = {}

    def consider(rel: str) -> None:
        norm = _normalize_path(rel)
        if _is_atomic_lock_path(norm):
            return
        if norm in registry:
            return
        cls = infer_class_for_path(norm)
        if cls:
            inferred[norm] = {
                "path": norm,
                "class_name": cls,
                "provenance": "inferred",
            }

    for name in os.listdir(root):
        if name == "patches.jsonl" or (
            name.startswith("patches-") and name.endswith(".jsonl")
        ):
            consider(name)

    apatch_dir = os.path.join(root, ".apatch")
    if os.path.isdir(apatch_dir):
        for dirpath, _, filenames in os.walk(apatch_dir):
            rel_dir = _normalize_path(os.path.relpath(dirpath, root))
            for fname in filenames:
                consider(f"{rel_dir}/{fname}")

    return inferred


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / (1024 * 1024):.1f}MB"


def _file_size(workspace: str, rel_path: str) -> int:
    abs_path = os.path.join(workspace, rel_path)
    try:
        return os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
    except OSError:
        return 0


def _scan_workspace_paths(workspace: str) -> Set[str]:
    paths: Set[str] = set()
    root = os.path.abspath(workspace)
    for name in os.listdir(root):
        if name == "patches.jsonl" or (
            name.startswith("patches-") and name.endswith(".jsonl")
        ):
            paths.add(_normalize_path(name))
    apatch_dir = os.path.join(root, ".apatch")
    if os.path.isdir(apatch_dir):
        for dirpath, _, filenames in os.walk(apatch_dir):
            rel_dir = _normalize_path(os.path.relpath(dirpath, root))
            for fname in filenames:
                rel = _normalize_path(f"{rel_dir}/{fname}")
                if not _is_atomic_lock_path(rel):
                    paths.add(rel)
    return paths


def gc_report(workspace: str) -> Dict[str, Any]:
    """Advisory classification report (RFP-016 §4.2); no filesystem mutations."""
    root = os.path.abspath(workspace)
    registry = load_registry(root)
    inferred_map = classify_unregistered(root)
    on_disk = _scan_workspace_paths(root)
    disk_bound_classes = {
        ArtifactClass.HISTORY.value,
        ArtifactClass.EPHEMERAL.value,
        ArtifactClass.DEBUG.value,
        ArtifactClass.GARBAGE.value,
        ArtifactClass.UNREGISTERED.value,
    }

    classified: Dict[str, int] = {c.value: 0 for c in ArtifactClass}
    issues: List[Dict[str, Any]] = []
    reclaimable = 0
    artifact_count = 0
    inferred_count = 0
    unclassified_count = 0
    orphan_count = 0

    seen: Set[str] = set()
    all_paths = sorted(on_disk | set(registry.keys()) | set(inferred_map.keys()))

    for path in all_paths:
        if path in seen:
            continue
        seen.add(path)
        entry = registry.get(path)
        if entry:
            if path not in on_disk and entry.class_name in disk_bound_classes:
                continue
            cls = entry.class_name
            provenance = (entry.lineage or {}).get("provenance", "registered")
        elif path in inferred_map:
            cls = inferred_map[path]["class_name"]
            provenance = "inferred"
            inferred_count += 1
        else:
            cls = ArtifactClass.UNCLASSIFIED.value
            provenance = None
            if path in on_disk:
                unclassified_count += 1

        classified[cls] = classified.get(cls, 0) + 1
        artifact_count += 1

        if provenance == "inferred" and cls in (
            ArtifactClass.EPHEMERAL.value,
            ArtifactClass.GARBAGE.value,
        ):
            if "/" not in path or path.startswith("patches"):
                orphan_count += 1

        size = _file_size(root, path)
        if entry and gc_delete_allowed(entry, path, registry):
            reclaimable += size

    run_state_active = [
        p
        for p, e in registry.items()
        if e.run_lease_id
        and e.class_name == "RUN_STATE"
        and _file_exists(root, p)
    ]
    if run_state_active:
        issues.append(
            {
                "type": "RUN_STATE_ACTIVE",
                "count": len(run_state_active),
                "paths": run_state_active[:10],
                "severity": "info",
            }
        )

    orphan_paths = sorted(
        p
        for p, meta in inferred_map.items()
        if meta.get("class_name") == "EPHEMERAL"
        and (not p.startswith(".apatch/"))
    )
    if orphan_paths:
        issues.append(
            {
                "type": "ORPHAN_EPHEMERAL",
                "count": len(orphan_paths),
                "paths": orphan_paths[:10],
                "severity": "high",
            }
        )

    history_count = classified.get(ArtifactClass.HISTORY.value, 0)
    if history_count > HISTORY_OVERFLOW_LIMIT:
        issues.append(
            {
                "type": "HISTORY_OVERFLOW",
                "count": history_count,
                "limit": HISTORY_OVERFLOW_LIMIT,
                "severity": "medium",
            }
        )

    if inferred_count:
        issues.append(
            {
                "type": "INFERRED_ARTIFACTS",
                "count": inferred_count,
                "severity": "medium",
            }
        )

    if unclassified_count:
        issues.append(
            {
                "type": "UNCLASSIFIED",
                "count": unclassified_count,
                "severity": "medium",
            }
        )

    issues = _compact_hygiene_issues(issues)
    degraded = any(
        issue.get("severity") in ("medium", "high", "critical")
        for issue in issues
    )
    _, _, layout = resolve_registry_paths(root)

    return {
        "status": "degraded" if degraded else "clean",
        "artifact_count": artifact_count,
        "classified": classified,
        "issues": issues,
        "repair_actions": _hygiene_repair_actions(issues),
        "size_reclaimable": _format_size(reclaimable),
        "recommendation": "apatch gc --dry-run",
        "orphan_count": orphan_count,
        "unclassified_count": unclassified_count,
        "inferred_count": inferred_count,
        "layout": layout,
    }


INFERENCE_SUNSET_REL = ".apatch/hygiene/inference_sunset.json"


def inference_sunset_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), INFERENCE_SUNSET_REL)


def load_governed_ops_counter(workspace: str) -> int:
    path = inference_sunset_path(workspace)
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            return load_inference_sunset_state(json.load(fh))
    except (OSError, json.JSONDecodeError):
        return 0


def save_governed_ops_counter(workspace: str, ops: int) -> None:
    path = inference_sunset_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(save_inference_sunset_state(ops), fh, indent=2)
        fh.write("\n")
    register_on_write(
        workspace,
        INFERENCE_SUNSET_REL,
        class_name="STATE",
        created_by_tool="apatch_artifact_governance",
        reason="inference sunset counter",
    )


def increment_governed_ops_counter(workspace: str) -> int:
    ops = load_governed_ops_counter(workspace) + 1
    save_governed_ops_counter(workspace, ops)
    return ops


def assert_governed_hygiene_allowed(workspace: str) -> None:
    report = gc_report(workspace)
    ops = load_governed_ops_counter(workspace)
    inferred = int(report.get("inferred_count", 0))
    policy = InferenceSunsetPolicy()
    if inferred and policy.should_block_governed(inferred, ops):
        register_inferred_artifacts(workspace)
        report = gc_report(workspace)
        inferred = int(report.get("inferred_count", 0))
    if policy.should_block_governed(inferred, ops):
        from apatch.runtime.errors import HygieneCriticalError

        raise HygieneCriticalError(
            "inference sunset: resolve inferred artifacts before governed mutations",
            inferred_count=inferred,
            ops=ops,
        )


def build_doctor_hygiene(workspace: str) -> Dict[str, Any]:
    report = gc_report(workspace)
    ops = load_governed_ops_counter(workspace)
    inferred = int(report.get("inferred_count", 0))
    issues = list(report.get("issues") or [])
    registered_orphans = collect_registered_orphan_ephemeral(workspace)
    if registered_orphans:
        issues.append(
            {
                "type": "ORPHAN_EPHEMERAL",
                "count": len(registered_orphans),
                "paths": registered_orphans[:10],
                "severity": "high",
            }
        )
    issues = _compact_hygiene_issues(issues)
    status = report["status"]
    if registered_orphans and status == "clean":
        status = "degraded"
    if InferenceSunsetPolicy().should_block_governed(inferred, ops):
        status = "critical"
    recommendation = report.get("recommendation", "apatch gc --dry-run")
    if status == "critical":
        recommendation = "apatch gc --dry-run"
    compact_orphan_count = sum(
        int(issue.get("count") or 0)
        for issue in issues
        if issue.get("type") == "ORPHAN_EPHEMERAL"
    )
    return {
        "status": status,
        "issues": issues,
        "repair_actions": _hygiene_repair_actions(issues),
        "orphan_count": compact_orphan_count,
        "unclassified_count": report.get("unclassified_count", 0),
        "inferred_count": inferred,
        "governed_ops": ops,
        "gc_recommendation": recommendation,
        "layout": report.get("layout", "structured"),
    }


def gc_delete_allowed(
    entry: Optional[RegistryEntry],
    path: str,
    registry_by_path: Optional[Mapping[str, RegistryEntry]] = None,
) -> bool:
    if entry is None or not entry.registered:
        return False
    if _is_atomic_lock_path(path):
        return False
    if is_static_replay_critical(path):
        return False
    if entry.replay_critical:
        return False
    if not entry.gc_allowed:
        return False
    if entry.run_lease_id:
        return False
    if registry_by_path and entry.depends_on:
        for dep in entry.depends_on:
            dep_norm = _normalize_path(dep)
            dep_entry = registry_by_path.get(dep_norm)
            if dep_entry and dep_entry.run_lease_id:
                return False
    return True


def acquire_run_lease(
    entry: RegistryEntry, lease_id: str, *, class_name: str = "RUN_STATE"
) -> RegistryEntry:
    entry.class_name = class_name
    entry.run_lease_id = lease_id
    entry.gc_allowed = False
    entry.replay_critical = True
    return entry


def release_run_lease(entry: RegistryEntry) -> RegistryEntry:
    entry.run_lease_id = None
    entry.gc_allowed = True
    entry.replay_critical = False
    return entry


def persist_registry_entry(workspace: str, entry: RegistryEntry) -> RegistryEntry:
    """Append a registry snapshot; load_registry uses latest row per path."""
    art_path, prov_path, _ = resolve_registry_paths(workspace)
    os.makedirs(os.path.dirname(art_path), exist_ok=True)
    lineage = dict(entry.lineage or {})
    lineage.setdefault("provenance", "registered")
    lineage.setdefault("created_by_tool", "apatch_artifact_governance")
    lineage.setdefault("reason", "registry update")
    validate_lineage(lineage)
    ts = datetime.now(timezone.utc).isoformat()
    artifact_id = entry.artifact_id or uuid.uuid4().hex
    record: Dict[str, Any] = {
        "artifact_id": artifact_id,
        "path": entry.path,
        "class_name": entry.class_name,
        "registered": bool(entry.registered),
        "replay_critical": bool(entry.replay_critical),
        "gc_allowed": bool(entry.gc_allowed),
        "run_lease_id": entry.run_lease_id,
        "depends_on": list(entry.depends_on),
        "lineage": lineage,
        "ts": ts,
    }
    _append_jsonl(art_path, record)
    _append_jsonl(
        prov_path,
        {"artifact_id": artifact_id, "path": entry.path, "ts": ts, "lineage": lineage},
    )
    _bootstrap_registry_store(workspace)
    return _entry_from_record(record)


def sync_run_state_registry(
    workspace: str,
    path: str,
    *,
    lease_id: Optional[str],
    created_by_tool: str,
    reason: str,
    governed_session_id: Optional[str] = None,
    depends_on: Optional[List[str]] = None,
) -> RegistryEntry:
    rel = _rel_workspace_path(workspace, path)
    registry = load_registry(workspace)
    entry = registry.get(rel)
    if entry is None:
        lineage: Dict[str, Any] = {
            "created_by_tool": created_by_tool,
            "reason": reason,
            "provenance": "registered",
        }
        if governed_session_id:
            lineage["governed_session_id"] = governed_session_id
        entry = RegistryEntry(
            path=rel,
            class_name=ArtifactClass.RUN_STATE.value,
            lineage=lineage,
            depends_on=depends_on or [],
            gc_allowed=False,
            replay_critical=True,
        )
    if lease_id:
        acquire_run_lease(entry, lease_id)
    else:
        release_run_lease(entry)
    if governed_session_id:
        entry.lineage = {**(entry.lineage or {}), "governed_session_id": governed_session_id}
    if depends_on:
        entry.depends_on = list(depends_on)
    return persist_registry_entry(workspace, entry)

def release_spec_run_scoped_artifacts(
    workspace: str,
    *,
    session_id: str,
    spec_run_id: str,
) -> List[str]:
    """Release EPHEMERAL artifacts tied to a completed spec_run."""
    registry = load_registry(workspace)
    released: List[str] = []
    for path, entry in registry.items():
        if entry.class_name != ArtifactClass.EPHEMERAL.value:
            continue
        lineage = entry.lineage or {}
        if spec_run_id and lineage.get("spec_run_id") == spec_run_id:
            pass
        elif session_id and lineage.get("governed_session_id") == session_id:
            pass
        else:
            continue
        if entry.run_lease_id:
            release_run_lease(entry)
        entry.gc_allowed = True
        entry.replay_critical = False
        persist_registry_entry(workspace, entry)
        released.append(path)
    return released


def release_session_registry_cleanup(workspace: str, session_id: str) -> Dict[str, Any]:
    """Release leases and mark session EPHEMERAL/RUN_STATE rows gc-safe."""
    registry = load_registry(workspace)
    released: List[str] = []
    for path, entry in registry.items():
        lineage = entry.lineage or {}
        if lineage.get("governed_session_id") != session_id:
            continue
        if entry.class_name not in (
            ArtifactClass.EPHEMERAL.value,
            ArtifactClass.RUN_STATE.value,
        ):
            continue
        if entry.run_lease_id:
            release_run_lease(entry)
        elif entry.class_name == ArtifactClass.EPHEMERAL.value:
            entry.gc_allowed = True
            entry.replay_critical = False
        persist_registry_entry(workspace, entry)
        released.append(path)
    return {"session_id": session_id, "released_paths": sorted(released)}


def collect_registered_orphan_ephemeral(workspace: str) -> List[str]:
    """Registered EPHEMERAL with gc_allowed still on disk (post session_end)."""
    registry = load_registry(workspace)
    orphans: List[str] = []
    for path, entry in registry.items():
        if _is_atomic_lock_path(path):
            continue
        if entry.class_name != ArtifactClass.EPHEMERAL.value:
            continue
        if not entry.gc_allowed or entry.run_lease_id:
            continue
        if _file_exists(workspace, path):
            orphans.append(path)
    return sorted(orphans)


def plan_gc_deletes(
    candidates: Iterable[str],
    registry: Mapping[str, RegistryEntry],
    *,
    parse_run_state: bool = False,
) -> Set[str]:
    if parse_run_state:
        raise ValueError("parse_run_state must not be used in production GC planning")
    allowed: Set[str] = set()
    for raw in candidates:
        path = _normalize_path(raw)
        entry = registry.get(path)
        if gc_delete_allowed(entry, path, registry):
            allowed.add(path)
    return allowed


def save_inference_sunset_state(ops: int) -> Dict[str, Any]:
    return {"governed_ops_since_inference": ops, "version": 1}


def load_inference_sunset_state(data: Mapping[str, Any]) -> int:
    return int(data.get("governed_ops_since_inference", 0))


DEBUG_MAX_AGE_SECONDS = 7 * 24 * 3600
DEBUG_MAX_BYTES = 10 * 1024 * 1024
HISTORY_BACKUP_LIMIT = 50


def _file_exists(workspace: str, rel: str) -> bool:
    return os.path.isfile(os.path.join(os.path.abspath(workspace), rel))


def _protected_backup_rels(workspace):
    """Backup rel-prefixes referenced by active governed/run state — never gc."""
    import json as _json

    root = os.path.abspath(workspace)
    apatch_dir = os.path.join(root, ".apatch")
    ids = set()
    active_session_ids = set()

    def _read(state_path):
        try:
            with open(state_path, encoding="utf-8") as _fh:
                state = _json.load(_fh)
        except (OSError, ValueError):
            return None
        return state if isinstance(state, dict) else None

    def _collect_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"session_id", "checkpoint", "last_checkpoint", "checkpoints"}:
                    _collect_refs(item)
                elif isinstance(item, (dict, list, tuple)):
                    _collect_refs(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _collect_refs(item)
        elif value:
            ids.add(str(value))

    active_state_paths = [os.path.join(apatch_dir, "session_state.json")]
    lanes_dir = os.path.join(apatch_dir, "lanes")
    if os.path.isdir(lanes_dir):
        for name in os.listdir(lanes_dir):
            active_state_paths.append(
                os.path.join(lanes_dir, name, "session_state.json")
            )

    for state_path in active_state_paths:
        state = _read(state_path)
        if not state or state.get("ended_at"):
            continue
        session_id = state.get("session_id")
        if session_id:
            active_session_ids.add(str(session_id))
        _collect_refs(state)

    if active_session_ids and os.path.isdir(apatch_dir):
        for dirpath, dirnames, filenames in os.walk(apatch_dir):
            if os.path.abspath(dirpath).startswith(
                os.path.join(apatch_dir, "backups") + os.sep
            ):
                dirnames[:] = []
                continue
            for filename in filenames:
                normalized = filename.lower().replace("-", "_")
                if not (
                    normalized == "apply_session.json"
                    or normalized.startswith("apply_session")
                    or normalized == "spec_run.json"
                ):
                    continue
                state = _read(os.path.join(dirpath, filename))
                if not state or state.get("ended_at"):
                    continue
                run_session_id = str(state.get("session_id") or "")
                path_parts = set(os.path.normpath(dirpath).split(os.sep))
                if (
                    run_session_id in active_session_ids
                    or active_session_ids.intersection(path_parts)
                ):
                    _collect_refs(state)

    return {_normalize_path(os.path.join(".apatch", "backups", i)) for i in ids}


def _is_protected_backup(rel, protected):
    return any(rel == p or rel.startswith(p + "/") for p in protected)


def collect_gc_safe_candidates(
    workspace: str,
) -> Tuple[Set[str], Set[str], Dict[str, RegistryEntry]]:
    root = os.path.abspath(workspace)
    registry = load_registry(root)
    protected = _protected_backup_rels(root)
    inferred = classify_unregistered(root)
    on_disk = _scan_workspace_paths(root)
    delete: Set[str] = set()
    skipped: Set[str] = set()
    paths = sorted(on_disk | set(registry.keys()) | set(inferred.keys()))
    for path in paths:
        if not _file_exists(root, path):
            continue
        if _is_protected_backup(path, protected):
            skipped.add(path)
            continue
        entry = registry.get(path)
        if entry:
            if gc_delete_allowed(entry, path, registry):
                delete.add(path)
            else:
                skipped.add(path)
        elif path in inferred:
            cls = inferred[path]["class_name"]
            if (
                cls in (ArtifactClass.EPHEMERAL.value, ArtifactClass.GARBAGE.value)
                and default_gc_allowed(cls)
                and not is_static_replay_critical(path)
            ):
                delete.add(path)
            else:
                skipped.add(path)
        else:
            skipped.add(path)
    return delete, skipped, registry


def validate_gc_delete_plan(
    delete: Set[str], registry: Mapping[str, RegistryEntry]
) -> None:
    for path in delete:
        if is_static_replay_critical(path):
            raise GCInvariantViolation(
                f"static replay-critical path in delete plan: {path}"
            )
        entry = registry.get(path)
        if entry and not gc_delete_allowed(entry, path, registry):
            raise GCInvariantViolation(f"registry forbids delete: {path}")


def register_inferred_artifacts(
    workspace: str, *, dry_run: bool = False
) -> Dict[str, Any]:
    """Register inferred paths and repair canonical control-plane classes."""
    root = os.path.abspath(workspace)
    inferred_map = classify_unregistered(root)
    registry = load_registry(root)
    registered: List[str] = []
    reclassified: List[str] = []
    reconciled_run_states: List[str] = []
    skipped: List[str] = []

    for path, entry in registry.items():
        if (
            entry.class_name != ArtifactClass.RUN_STATE.value
            or not entry.run_lease_id
            or os.path.basename(path) != "apply_session.json"
            or not _file_exists(root, path)
        ):
            continue
        session_state_path = os.path.join(
            os.path.dirname(os.path.join(root, path)), "session_state.json"
        )
        try:
            with open(session_state_path, encoding="utf-8") as handle:
                session_state = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue
        if not session_state.get("ended_at"):
            continue
        if not dry_run:
            release_run_lease(entry)
            session_id = session_state.get("session_id")
            if session_id:
                entry.lineage = {
                    **(entry.lineage or {}),
                    "governed_session_id": session_id,
                }
            persist_registry_entry(root, entry)
        reconciled_run_states.append(path)

    for path, class_name in _CANONICAL_PATH_CLASSES.items():
        entry = registry.get(path)
        if (
            not _file_exists(root, path)
            or entry is None
            or entry.class_name == class_name
        ):
            continue
        if not dry_run:
            register_on_write(
                root,
                path,
                class_name=class_name,
                created_by_tool="apatch_gc_reconcile",
                reason="canonical control-plane class repair",
                gc_allowed=_DEFAULT_GC_ALLOWED[class_name],
                replay_critical=_DEFAULT_REPLAY_CRITICAL[class_name],
            )
        reclassified.append(path)

    for path in sorted(inferred_map.keys()):
        if not _file_exists(root, path):
            skipped.append(path)
            continue
        if dry_run:
            registered.append(path)
            continue
        cls = inferred_map[path]["class_name"]
        register_on_write(
            root,
            path,
            class_name=cls,
            created_by_tool="apatch_gc_reconcile",
            reason="inferred artifact backfill",
            gc_allowed=_DEFAULT_GC_ALLOWED.get(cls, False),
            replay_critical=_DEFAULT_REPLAY_CRITICAL.get(cls, False),
        )
        registered.append(path)
    return {
        "ok": True,
        "dry_run": dry_run,
        "registered": registered,
        "reclassified": reclassified,
        "reconciled_run_states": reconciled_run_states,
        "skipped": skipped,
        "registered_count": len(registered) + len(reclassified),
        "reclassified_count": len(reclassified),
        "reconciled_run_state_count": len(reconciled_run_states),
    }


def gc_safe(workspace: str) -> Dict[str, Any]:
    root = os.path.abspath(workspace)
    delete, skipped, registry = collect_gc_safe_candidates(root)
    validate_gc_delete_plan(delete, registry)
    deleted: List[str] = []
    reclaimed = 0
    for path in sorted(delete):
        abs_path = os.path.join(root, path)
        if os.path.isfile(abs_path):
            reclaimed += os.path.getsize(abs_path)
            os.remove(abs_path)
            deleted.append(path)
    return {
        "ok": True,
        "deleted": deleted,
        "skipped": sorted(skipped),
        "deleted_count": len(deleted),
        "reclaimed_bytes": reclaimed,
    }


def _backup_entries(workspace: str) -> List[Tuple[str, float, int]]:
    root = os.path.abspath(workspace)
    backups_dir = os.path.join(root, ".apatch/backups")
    rows: List[Tuple[str, float, int]] = []
    if not os.path.isdir(backups_dir):
        return rows
    for dirpath, _, filenames in os.walk(backups_dir):
        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            if not os.path.isfile(abs_path):
                continue
            rel = _normalize_path(os.path.relpath(abs_path, root))
            rows.append((rel, os.path.getmtime(abs_path), os.path.getsize(abs_path)))
    return rows


def _debug_entries(workspace: str, registry: Mapping[str, RegistryEntry]) -> List[Tuple[str, float, int]]:
    root = os.path.abspath(workspace)
    rows: List[Tuple[str, float, int]] = []
    for path in sorted(_scan_workspace_paths(root)):
        entry = registry.get(path)
        cls = entry.class_name if entry else infer_class_for_path(path)
        if cls != ArtifactClass.DEBUG.value:
            continue
        abs_path = os.path.join(root, path)
        if os.path.isfile(abs_path):
            rows.append((path, os.path.getmtime(abs_path), os.path.getsize(abs_path)))
    return rows


def gc_rotate(workspace: str) -> Dict[str, Any]:
    root = os.path.abspath(workspace)
    result = gc_safe(root)
    deleted = list(result["deleted"])
    skipped = set(result["skipped"])
    reclaimed = int(result["reclaimed_bytes"])
    registry = load_registry(root)
    protected = _protected_backup_rels(root)

    backups = _backup_entries(root)
    backups.sort(key=lambda row: row[1])
    excess = max(0, len(backups) - HISTORY_BACKUP_LIMIT)
    for rel, _, size in backups[:excess]:
        if _is_protected_backup(rel, protected):
            skipped.add(rel)
            continue
        entry = registry.get(rel)
        if entry and entry.run_lease_id:
            skipped.add(rel)
            continue
        if entry:
            if not gc_delete_allowed(entry, rel, registry):
                skipped.add(rel)
                continue
        elif not default_gc_allowed(ArtifactClass.HISTORY.value):
            skipped.add(rel)
            continue
        abs_path = os.path.join(root, rel)
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            deleted.append(rel)
            reclaimed += size

    debug_rows = _debug_entries(root, registry)
    total_debug = sum(size for _, _, size in debug_rows)
    now = time.time()
    for path, mtime, size in sorted(debug_rows, key=lambda row: row[1]):
        stale = (now - mtime) > DEBUG_MAX_AGE_SECONDS
        over_cap = total_debug > DEBUG_MAX_BYTES
        if not stale and not over_cap:
            continue
        entry = registry.get(path)
        if entry and not gc_delete_allowed(entry, path, registry):
            skipped.add(path)
            continue
        abs_path = os.path.join(root, path)
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            deleted.append(path)
            reclaimed += size
            total_debug -= size

    return {
        "ok": True,
        "deleted": deleted,
        "skipped": sorted(skipped),
        "deleted_count": len(deleted),
        "reclaimed_bytes": reclaimed,
    }
