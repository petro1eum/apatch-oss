"""Hard authorization gate for mutations owned by an executable SPEC.

TrustChain proves who changed a file. This module decides whether the active
workflow is authorized to generate that change in the first place.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from apatch.apatch_paths import normalize_rel

ERROR_SPEC_WORKFLOW_REQUIRED = "SPEC_WORKFLOW_REQUIRED"
ERROR_SPEC_OWNERSHIP_UNRESOLVED = "SPEC_OWNERSHIP_UNRESOLVED"
ERROR_SPEC_TARGET_NOT_DECLARED = "SPEC_TARGET_NOT_DECLARED"

_ALLOWED_CHANNELS = frozenset(
    {
        "apatch_execute_next",
        "apatch_spec_run",
        "apatch_remote_task_run:requirement",
        "apatch_remote_task_run:fix_forward",
    }
)
_SPEC_FILE_RE = re.compile(r"^docs/specs/(SPEC-[A-Za-z0-9_.-]+)\.md$")
_PATCH_FILE_RE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE
)


def authorize_spec_owned_needles(
    target_dir: str,
    needles: List[Any],
    *,
    governed_session_id: Optional[str] = None,
    created_by_tool: str = "apatch_generate_batch",
) -> Dict[str, Any]:
    """Reject SPEC-owned targets unless the exact requirement owns the session."""
    root = os.path.abspath(target_dir)
    state = _load_exact_session_state(root, governed_session_id)
    requirements = _bound_requirements(state)
    active_requirement = requirements[0] if len(requirements) == 1 else None
    active_spec = active_requirement.split("#", 1)[0] if active_requirement else None
    recovery_spec = (
        active_spec
        if created_by_tool == "apatch_remote_task_run:fix_forward"
        else None
    )
    resolved = resolve_spec_owned_targets(
        root, needles, recovery_spec=recovery_spec
    )
    if not resolved.get("ok"):
        return resolved

    owned = resolved.get("owned") or []
    if active_requirement and active_spec and _is_strict_spec(root, active_spec):
        declarations, declaration_errors = _strict_ownership_declarations(root)
        if declaration_errors:
            return {
                "ok": False,
                "error_type": ERROR_SPEC_OWNERSHIP_UNRESOLVED,
                "error": "Strict SPEC ownership declarations are invalid; mutation denied.",
                "ownership_errors": declaration_errors,
                "recoverable": True,
                "recommended_action": "Fix and lint the strict SPEC ownership declarations.",
            }
        undeclared = []
        for needle in needles:
            for rel, _action in _needle_paths(needle):
                matches = _declared_owners_for_path(rel, declarations)
                if not any(
                    row.get("requirement") == active_requirement for row in matches
                ):
                    undeclared.append(rel)
        if undeclared:
            return {
                "ok": False,
                "error_type": ERROR_SPEC_TARGET_NOT_DECLARED,
                "error": (
                    "The active strict requirement does not declare every mutation target."
                ),
                "recoverable": True,
                "recommended_action": (
                    "Remove the target or add it to the requirement's owns: declaration "
                    "through an authorized SPEC amendment."
                ),
                "active_requirement": active_requirement,
                "rejected_targets": sorted(set(undeclared)),
                "generation_started": False,
            }

    if created_by_tool == "apatch_spec_run_multi:shared_maintenance":
        return _authorize_shared_maintenance(
            state,
            needles,
            owned,
            created_by_tool=created_by_tool,
        )
    if not owned:
        return {"ok": True, "owned": [], "authorization": "unowned"}

    owners = sorted({str(row["spec"]) for row in owned})

    if len(owners) != 1 or active_spec != owners[0]:
        return _rejection(
            owners,
            owned,
            created_by_tool,
            active_requirement,
            reason=(
                "SPEC-owned targets require one active session bound to the exact "
                "owner requirement."
            ),
        )

    bootstrap = _bootstrap_authorization(
        root, state, owned, active_requirement, created_by_tool
    )
    if bootstrap is not None:
        return bootstrap

    if created_by_tool not in _ALLOWED_CHANNELS:
        return _rejection(
            owners,
            owned,
            created_by_tool,
            active_requirement,
            reason=(
                "A requirement artifact alone is insufficient: use spec_run or "
                "execute_next instead of standalone generate_batch/remote_task."
            ),
        )

    return {
        "ok": True,
        "owned": owned,
        "authorization": "spec_requirement",
        "requirement_token": active_requirement,
        "created_by_tool": created_by_tool,
    }


def _authorize_shared_maintenance(
    state: Dict[str, Any],
    needles: List[Any],
    owned: List[Dict[str, str]],
    *,
    created_by_tool: str,
) -> Dict[str, Any]:
    requirements = _bound_requirements(state)
    partition = state.get("artifact_files")
    expected_keys = {"spec:{}".format(token) for token in requirements}
    if (
        len({token.split("#", 1)[0] for token in requirements}) < 2
        or not isinstance(partition, dict)
        or set(partition) != expected_keys
    ):
        return _rejection(
            sorted({str(row["spec"]) for row in owned}),
            owned,
            created_by_tool,
            None,
            reason="Shared maintenance requires an exact signed artifact_files partition.",
        )

    path_tokens: Dict[str, str] = {}
    for artifact_key, raw_paths in partition.items():
        if not isinstance(raw_paths, list) or not raw_paths:
            return _rejection(
                sorted({str(row["spec"]) for row in owned}),
                owned,
                created_by_tool,
                None,
                reason="Every shared-maintenance requirement must own explicit files.",
            )
        token = str(artifact_key).split(":", 1)[1]
        for raw_path in raw_paths:
            rel = normalize_rel(str(raw_path))
            if not rel or rel in path_tokens:
                return _rejection(
                    sorted({str(row["spec"]) for row in owned}),
                    owned,
                    created_by_tool,
                    None,
                    reason="One target file cannot belong to multiple maintenance requirements.",
                )
            path_tokens[rel] = token

    needle_paths = {
        rel
        for needle in needles
        for rel, _action in _needle_paths(needle)
    }
    if needle_paths != set(path_tokens):
        return _rejection(
            sorted({str(row["spec"]) for row in owned}),
            owned,
            created_by_tool,
            None,
            reason="The artifact_files partition must cover every and only needle target.",
        )
    rejected = []
    conflicts = []
    for row in owned:
        token = path_tokens.get(str(row["path"]))
        if not token or token.split("#", 1)[0] != str(row["spec"]):
            rejected.append(row)
            conflicts.append({
                "path": row["path"],
                "expected_spec": row["spec"],
                "actual_requirement": token,
            })
    if rejected:
        result = _rejection(
            sorted({str(row["spec"]) for row in rejected}),
            rejected,
            created_by_tool,
            None,
            reason="A target partition is bound to the wrong SPEC owner.",
        )
        result["partition_conflicts"] = conflicts
        return result

    return {
        "ok": True,
        "owned": owned,
        "authorization": "partitioned_multi_spec_requirements",
        "requirement_tokens": requirements,
        "artifact_files": partition,
        "created_by_tool": created_by_tool,
    }


def resolve_spec_owned_targets(
    target_dir: str,
    needles: List[Any],
    *,
    recovery_spec: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve exact target -> slug -> SPEC ownership without fuzzy matching.

    recovery_spec is accepted only by the active remote fix-forward channel.
    It may authorize repair of the malformed contract file itself, never another
    file whose owner merely depends on that contract.
    """
    root = os.path.abspath(target_dir)
    slugs = _known_contract_slugs(root)
    declarations, declaration_errors = _strict_ownership_declarations(root)
    surfaces = {slug: _declared_slug_surface(root, slug) for slug in slugs}
    owned: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = list(declaration_errors)

    for needle in needles:
        for rel, action in _needle_paths(needle):
            direct = _direct_spec_owner(root, rel, action)
            if direct:
                owned.append({"path": rel, "spec": direct, "via": "spec_file"})
                continue

            declared = _declared_owners_for_path(rel, declarations)
            declared_specs = {row["spec"] for row in declared}
            if len(declared_specs) > 1:
                errors.append(
                    {
                        "path": rel,
                        "error": (
                            "strict SPEC ownership is ambiguous across "
                            + ", ".join(sorted(declared_specs))
                        ),
                    }
                )
                continue
            if declared:
                owned.extend({"path": rel, **row} for row in declared)
                continue

            canonical = _matched_contract_slugs(rel, slugs)
            explicit = [
                slug for slug, (surface, _error) in surfaces.items()
                if rel in surface
            ]
            path_owners = []
            invalid = {slug for slug, (_surface, error) in surfaces.items() if error}
            for slug in sorted(set(canonical) | set(explicit) | invalid):
                spec_id, error = _spec_for_slug(root, slug)
                error = error or surfaces[slug][1]
                if spec_id and not error:
                    path_owners.append(spec_id)
                if error and recovery_spec and _is_contract_path(rel, slug):
                    owned.append(
                        {
                            "path": rel,
                            "slug": slug,
                            "spec": recovery_spec,
                            "via": "active_fix_forward_recovery",
                        }
                    )
                elif error:
                    errors.append({"path": rel, "slug": slug, "error": error})
                elif spec_id:
                    owned.append(
                        {
                            "path": rel,
                            "slug": slug,
                            "spec": spec_id,
                            "via": "slug_contract",
                        }
                    )

            if len(set(path_owners)) > 1:
                errors.append({
                    "path": rel,
                    "error": "slug surface ownership is ambiguous across "
                    + ", ".join(sorted(set(path_owners))),
                })

    if errors:
        return {
            "ok": False,
            "error_type": ERROR_SPEC_OWNERSHIP_UNRESOLVED,
            "error": "SPEC ownership is ambiguous or invalid; mutation denied.",
            "ownership_errors": errors,
            "recoverable": True,
            "recommended_action": "Fix the SPEC owner or declared slug surface before mutation.",
            "generation_started": False,
        }

    unique: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for row in owned:
        unique[(row["path"], row["spec"], row.get("requirement", ""))] = row
    return {"ok": True, "owned": list(unique.values())}


def _rejection(
    owners: List[str],
    owned: List[Dict[str, str]],
    created_by_tool: str,
    active_requirement: Optional[str],
    *,
    reason: str,
) -> Dict[str, Any]:
    owner = owners[0] if len(owners) == 1 else None
    action = (
        f"apatch_spec_run(spec={owner!r}, requirements={{...}})"
        if owner
        else "apatch_spec_run_multi(specs=[...], requirements={...})"
    )
    return {
        "ok": False,
        "error_type": ERROR_SPEC_WORKFLOW_REQUIRED,
        "error": reason,
        "recoverable": True,
        "recommended_action": action,
        "required_specs": owners,
        "active_requirement": active_requirement,
        "created_by_tool": created_by_tool,
        "rejected_targets": owned,
        "generation_started": False,
    }


def _load_exact_session_state(
    root: str, governed_session_id: Optional[str]
) -> Dict[str, Any]:
    from apatch.session_state import load_session_state

    current = load_session_state(root)
    if not governed_session_id or str(current.get("session_id") or "") == governed_session_id:
        return current

    from apatch.lane import lane_state_path_for
    from apatch.lane_context import resolve_lane_for_session

    lane_id, _ = resolve_lane_for_session(root, governed_session_id, active_only=False)
    if not lane_id:
        return {}
    path = lane_state_path_for(root, lane_id, "session_state.json")
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _bound_requirements(state: Dict[str, Any]) -> List[str]:
    if not state.get("session_id") or state.get("ended_at"):
        return []
    found: Set[str] = set()
    for artifact in state.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("kind") not in {
            "spec",
            "spec-bootstrap",
        }:
            continue
        token = str(artifact.get("id") or "")
        if "#" in token:
            found.add(token.split("@", 1)[0])
    return sorted(found)


def _bootstrap_bound_spec(state: Dict[str, Any]) -> Optional[str]:
    """Return the SPEC id when the active session declares a spec-bootstrap requirement."""
    if not state.get("session_id") or state.get("ended_at"):
        return None
    for artifact in state.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("kind") != "spec-bootstrap":
            continue
        token = str(artifact.get("id") or "").split("@", 1)[0]
        if "#" in token:
            return token.split("#", 1)[0]
    return None


def _bootstrap_authorization(
    root: str,
    state: Dict[str, Any],
    owned: List[Dict[str, str]],
    active_requirement: Optional[str],
    created_by_tool: str,
) -> Optional[Dict[str, Any]]:
    """Authorize creating a not-yet-existing SPEC from a session bound to its bootstrap.

    A fresh contract lineage has no SPEC file yet, so ``execute_next`` and
    ``spec_run`` cannot run for it; the authoring session declares
    ``spec-bootstrap:<SPEC>#Rk`` instead (the same token the remote requirement
    channel uses). Only that SPEC file may be created, and only while it is absent;
    every other owned target keeps the exact-requirement channel rules.
    """
    bootstrap_spec = _bootstrap_bound_spec(state)
    if not bootstrap_spec or not owned:
        return None
    for row in owned:
        if row.get("via") != "spec_file" or row.get("spec") != bootstrap_spec:
            return None
        if os.path.exists(os.path.join(root, str(row.get("path") or ""))):
            return None
    return {
        "ok": True,
        "owned": owned,
        "authorization": "spec_bootstrap",
        "requirement_token": active_requirement,
        "created_by_tool": created_by_tool,
    }


def _is_strict_spec(root: str, spec_id: str) -> bool:
    from apatch.spec import parse_spec_file

    path = os.path.join(root, "docs", "specs", f"{spec_id}.md")
    try:
        return bool(parse_spec_file(path, spec_id=spec_id).strict_ownership)
    except (OSError, ValueError):
        return False


def _strict_ownership_declarations(
    root: str,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    from apatch.spec import parse_spec_file

    base = os.path.join(root, "docs", "specs")
    declarations: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    try:
        names = sorted(
            name
            for name in os.listdir(base)
            if name.startswith("SPEC-") and name.endswith(".md")
        )
    except OSError:
        return declarations, errors

    for name in names:
        path = os.path.join(base, name)
        try:
            parsed = parse_spec_file(path)
        except (OSError, ValueError) as exc:
            errors.append({"path": normalize_rel(path), "error": str(exc)})
            continue
        if not parsed.strict_ownership:
            continue
        for requirement in parsed.requirements:
            for raw_pattern in requirement.owns:
                pattern = normalize_rel(raw_pattern)
                invalid = (
                    not pattern
                    or raw_pattern.startswith(("/", "./", "../"))
                    or "/../" in f"/{raw_pattern.replace(chr(92), '/')}/"
                    or chr(92) in raw_pattern
                    or any(ch in pattern[:-3] for ch in "*?[")
                    or ("*" in pattern and not pattern.endswith("/**"))
                )
                if invalid:
                    errors.append(
                        {
                            "path": normalize_rel(path),
                            "error": (
                                f"{parsed.id}#{requirement.id} declares invalid "
                                f"ownership path {raw_pattern!r}"
                            ),
                        }
                    )
                    continue
                declarations.append(
                    {
                        "spec": parsed.id,
                        "requirement": f"{parsed.id}#{requirement.id}",
                        "pattern": pattern,
                        "via": "strict_requirement",
                    }
                )
    return declarations, errors


def _declared_owners_for_path(
    rel: str, declarations: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    matched: List[Dict[str, str]] = []
    for row in declarations:
        pattern = row["pattern"]
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            applies = rel == prefix or rel.startswith(prefix + "/")
        else:
            applies = rel == pattern
        if applies:
            matched.append(dict(row))
    return matched


def _known_contract_slugs(root: str) -> List[str]:
    base = os.path.join(root, "docs", "specs", "slug_contracts")
    try:
        return sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(base)
            if name.endswith((".yaml", ".yml")) and not name.startswith(".")
        )
    except OSError:
        return []


def _matched_contract_slugs(rel: str, slugs: List[str]) -> List[str]:
    """Return the most specific bounded legacy category surface.

    Prefix collisions in atomic/test basenames use the longest slug. Explicit
    YAML paths are resolved separately: length cannot erase a declared owner.
    """

    matched = [slug for slug in slugs if _path_matches_slug(rel, slug)]
    if not matched:
        return []
    longest = max(len(slug) for slug in matched)
    return [slug for slug in matched if len(slug) == longest]


def _load_slug_contract(root: str, slug: str) -> Tuple[Dict[str, Any], Optional[str]]:
    from apatch.spec_contract_resolver import _load_contract_yaml

    base = os.path.join(root, "docs", "specs", "slug_contracts", slug)
    paths = [base + suffix for suffix in (".yaml", ".yml")
             if os.path.isfile(base + suffix)]
    if len(paths) != 1:
        return {}, "slug must have exactly one .yaml or .yml contract"
    data, error = _load_contract_yaml(paths[0])
    return data or {}, error


def _declared_slug_surface(
    root: str, slug: str,
) -> Tuple[Set[str], Optional[str]]:
    """Read category-owned fields, never shared dependencies or arbitrary prose."""
    data, error = _load_slug_contract(root, slug)
    if error:
        return set(), error
    fields = {
        "runtime_pipeline": {
            "category_preprocessor": False, "category_behavior": False,
            "query_builder": False,
        },
        "atomics": {
            "category_sources": True, "schema_sources": True,
            "guardrail_sources": True,
        },
        "spec_generation": {"live_test_modules": True},
    }
    surface: Set[str] = set()
    for section, names in fields.items():
        mapping = data.get(section, {})
        if not isinstance(mapping, dict):
            return set(), f"{section} must be a mapping"
        for name, multiple in names.items():
            if name not in mapping:
                continue
            values = mapping[name]
            if multiple:
                if not isinstance(values, list):
                    return set(), f"{section}.{name} must be a list of paths"
            else:
                values = [values]
            for value in values:
                if (
                    not isinstance(value, str) or not value
                    or value != value.strip() or "\\" in value
                    or any(ch in value for ch in "*?[]:")
                    or any(part in {"", ".", ".."} for part in value.split("/"))
                ):
                    return set(), f"{section}.{name} declares invalid path {value!r}"
                surface.add(value)
    return surface, None


def _spec_for_slug(root: str, slug: str) -> Tuple[Optional[str], Optional[str]]:
    from apatch.spec_contract_resolver import _resolve_spec_id

    data, parse_error = _load_slug_contract(root, slug)
    if parse_error:
        return None, parse_error
    spec_id, _via, error = _resolve_spec_id(root, slug, None, data)
    if spec_id:
        return spec_id, None
    message = str((error or {}).get("error") or "")
    if "no spec owns this slug" in message:
        return None, None
    return None, message or "cannot resolve exact SPEC owner"


def _direct_spec_owner(root: str, rel: str, action: str) -> Optional[str]:
    match = _SPEC_FILE_RE.fullmatch(rel)
    if not match:
        return None
    # A not-yet-existing SPEC is still its own direct owner. The authorization
    # layer below requires the creating session to bind that exact SPEC#Rk.
    return match.group(1)


def _is_contract_path(rel: str, slug: str) -> bool:
    return rel in {
        f"docs/specs/slug_contracts/{slug}.yaml",
        f"docs/specs/slug_contracts/{slug}.yml",
    }


def _path_matches_slug(rel: str, slug: str) -> bool:
    # Preserve legacy case-insensitive protection, but only in bounded surfaces.
    rel, slug = rel.casefold(), slug.casefold()
    if _is_contract_path(rel, slug) or rel.startswith(f"categories/{slug}/"):
        return True
    if rel in {
        f"config/agent_schemas/{slug}.json",
        f"config/categories/{slug}.yaml", f"config/categories/{slug}.yml",
    }:
        return True
    escaped = re.escape(slug)
    return bool(
        re.fullmatch(rf"atomic/{escaped}(?:[_.-][^/]+)", rel)
        or re.fullmatch(rf"tests/(?:[^/]+/)*test_{escaped}(?:[_.-][^/]+)", rel)
    )


def _needle_paths(needle: Any) -> Iterable[Tuple[str, str]]:
    if not isinstance(needle, dict):
        return []
    action = str(needle.get("action") or needle.get("kind") or "replace").lower()
    paths: List[Tuple[str, str]] = []
    source = needle.get("source_file")
    target = needle.get("target_file")
    if source:
        paths.append((normalize_rel(str(source)), "delete"))
    if target:
        paths.append((normalize_rel(str(target)), action))
    for call in needle.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, dict):
            continue
        direct = arguments.get("TargetFile") or arguments.get("target_file")
        if direct:
            paths.append((normalize_rel(str(direct)), action))
        patch_input = arguments.get("input")
        if isinstance(patch_input, str):
            for match in _PATCH_FILE_RE.finditer(patch_input):
                paths.append((normalize_rel(match.group(1)), action))
    return [(path, kind) for path, kind in paths if path]
