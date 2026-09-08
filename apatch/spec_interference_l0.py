"""L0 tag pre-filter routing for cross-spec interference (RFP-014 Phase 4)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from apatch.spec_interference import _needle_target_file


def _tags_by_prefix(tags: List[str], prefix: str) -> Set[str]:
    needle = f"{prefix}:"
    out: Set[str] = set()
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith(needle):
            out.add(tag[len(needle) :])
    return out


def needle_planned_paths(needles: List[Dict[str, Any]]) -> Set[str]:
    paths: Set[str] = set()
    for needle in needles:
        rel = _needle_target_file(needle)
        if rel:
            paths.add(rel)
    return paths


def load_spec_tags(
    target_dir: str,
    spec_id: str,
    *,
    planned_override: Optional[Dict[str, Any]] = None,
) -> List[str]:
    if planned_override is not None:
        tags = planned_override.get("tags")
        if isinstance(tags, list):
            return [str(t) for t in tags]
    from apatch.spec_registry import load_spec_registry

    reg = load_spec_registry(target_dir, spec_id)
    if reg and isinstance(reg.get("tags"), list):
        return [str(t) for t in reg["tags"]]
    return []


def l0_pair_routing(
    spec_a: str,
    spec_b: str,
    tags_a: List[str],
    tags_b: List[str],
    paths_a: Set[str],
    paths_b: Set[str],
) -> Dict[str, Any]:
    """Phase 4 L0 pre-filter for one spec pair (routing, not proof)."""
    if not tags_a and not tags_b:
        return {
            "spec_a": spec_a,
            "spec_b": spec_b,
            "action": "default",
            "run_l1": True,
            "run_l2": True,
            "reason": "no_l0_tags",
            "shared_paths": sorted(paths_a & paths_b),
        }

    domains_a = _tags_by_prefix(tags_a, "domain")
    domains_b = _tags_by_prefix(tags_b, "domain")
    layers_a = _tags_by_prefix(tags_a, "layer")
    layers_b = _tags_by_prefix(tags_b, "layer")
    products_a = _tags_by_prefix(tags_a, "product")
    products_b = _tags_by_prefix(tags_b, "product")
    roles_a = _tags_by_prefix(tags_a, "role")
    roles_b = _tags_by_prefix(tags_b, "role")
    shared_paths = paths_a & paths_b

    def _full(reason: str) -> Dict[str, Any]:
        return {
            "spec_a": spec_a,
            "spec_b": spec_b,
            "action": "full",
            "run_l1": True,
            "run_l2": True,
            "reason": reason,
            "shared_paths": sorted(shared_paths),
        }

    def _skip(reason: str) -> Dict[str, Any]:
        return {
            "spec_a": spec_a,
            "spec_b": spec_b,
            "action": "skip",
            "run_l1": False,
            "run_l2": False,
            "reason": reason,
            "shared_paths": sorted(shared_paths),
        }

    def _l1_min(reason: str) -> Dict[str, Any]:
        return {
            "spec_a": spec_a,
            "spec_b": spec_b,
            "action": "l1_minimum",
            "run_l1": True,
            "run_l2": False,
            "reason": reason,
            "shared_paths": sorted(shared_paths),
        }

    if domains_a & domains_b and shared_paths:
        return _full("same_domain_overlapping_paths")

    cross_domain = bool(shared_paths) and (
        ("ux" in domains_a and "backend" in domains_b)
        or ("backend" in domains_a and "ux" in domains_b)
    )
    if cross_domain:
        return _full("cross_domain_ux_backend_shared_path")

    if layers_a & layers_b and roles_a and roles_b and roles_a.isdisjoint(roles_b):
        return _l1_min("same_layer_conflicting_roles")

    if layers_a & layers_b and domains_a and domains_b and domains_a.isdisjoint(domains_b):
        return _l1_min("same_layer_different_domain")

    if products_a & products_b and layers_a != layers_b and not shared_paths:
        return _skip("same_product_orthogonal_layers_no_path_overlap")

    if domains_a and domains_b and domains_a.isdisjoint(domains_b) and not shared_paths:
        return _skip("orthogonal_domains_no_path_overlap")

    if shared_paths:
        return _full("shared_paths_default_full")

    return _skip("no_overlap_default_skip")


def build_l0_routing_matrix(
    spec_ids: List[str],
    tags_by_spec: Dict[str, List[str]],
    paths_by_spec: Dict[str, Set[str]],
) -> Dict[str, Any]:
    pairs: List[Dict[str, Any]] = []
    for i, spec_a in enumerate(spec_ids):
        for spec_b in spec_ids[i + 1 :]:
            pairs.append(
                l0_pair_routing(
                    spec_a,
                    spec_b,
                    tags_by_spec.get(spec_a, []),
                    tags_by_spec.get(spec_b, []),
                    paths_by_spec.get(spec_a, set()),
                    paths_by_spec.get(spec_b, set()),
                )
            )
    return {
        "enabled": any(tags_by_spec.get(s) for s in spec_ids),
        "tags_by_spec": {s: tags_by_spec.get(s, []) for s in spec_ids},
        "planned_paths_by_spec": {s: sorted(paths_by_spec.get(s, set())) for s in spec_ids},
        "pairs": pairs,
    }


def l0_pair_lookup(pairs: List[Dict[str, Any]], spec_a: str, spec_b: str) -> Optional[Dict[str, Any]]:
    key = tuple(sorted((spec_a, spec_b)))
    for row in pairs:
        if tuple(sorted((str(row.get("spec_a")), str(row.get("spec_b"))))) == key:
            return row
    return None


def l0_pair_allows(row: Optional[Dict[str, Any]], *, for_l2: bool) -> bool:
    if row is None or row.get("action") == "default":
        return True
    if for_l2:
        return bool(row.get("run_l2", True))
    return bool(row.get("run_l1", True))


def l1_planned_file_overlaps(
    spec_ids: List[str],
    needles_by_spec: Dict[str, List[Dict[str, Any]]],
    l0_pairs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    per_spec_files: Dict[str, Set[str]] = {
        sid: needle_planned_paths(needles_by_spec.get(sid, [])) for sid in spec_ids
    }
    conflicts: List[Dict[str, Any]] = []
    for i, spec_a in enumerate(spec_ids):
        for spec_b in spec_ids[i + 1 :]:
            row = l0_pair_lookup(l0_pairs, spec_a, spec_b)
            if not l0_pair_allows(row, for_l2=False):
                continue
            shared = per_spec_files.get(spec_a, set()) & per_spec_files.get(spec_b, set())
            for rel in sorted(shared):
                conflicts.append(
                    {
                        "type": "file_overlap",
                        "spec_a": spec_a,
                        "spec_b": spec_b,
                        "file": rel,
                        "detail": "planned needles share file (L1 candidate)",
                        "severity": "low",
                        "source": "registry",
                    }
                )
    return conflicts