"""UX audit validation (SPEC-UX-SPECIALIST-1 R2 / RFP-015)."""

from __future__ import annotations

import json as _json
import os
from typing import Any, Dict, List, Optional, Tuple

LAYER_KEYS = ("L1", "L2", "L3", "L4", "L5")


def _err(path: str, msg: str) -> Dict[str, str]:
    return {"path": path, "message": msg}


def validate_layer_l1(layer: Dict[str, Any]) -> List[Dict[str, str]]:
    e: List[Dict[str, str]] = []
    if not isinstance(layer.get("roles"), list) or len(layer["roles"]) < 1:
        e.append(_err("layers.L1.roles", "at least one role"))
    if not isinstance(layer.get("tasks"), list) or len(layer["tasks"]) < 3:
        e.append(_err("layers.L1.tasks", "at least three tasks"))
    return e


def validate_layer_l5(layer: Dict[str, Any]) -> List[Dict[str, str]]:
    e: List[Dict[str, str]] = []
    if not isinstance(layer.get("signals"), list) or len(layer["signals"]) < 5:
        e.append(_err("layers.L5.signals", "at least five signals"))
    for p in ("actionable_signals", "contextual_surfacing", "intent_router"):
        if p not in (layer.get("patterns") or []):
            e.append(_err("layers.L5.patterns", f"missing {p}"))
    return e


def validate_layer_l2(layer: Dict[str, Any]) -> List[Dict[str, str]]:
    e: List[Dict[str, str]] = []
    if not layer.get("canonical_object"):
        e.append(_err("layers.L2.canonical_object", "required"))
    if not isinstance(layer.get("context_switch_violations"), list) or len(layer["context_switch_violations"]) < 1:
        e.append(_err("layers.L2.context_switch_violations", "at least one"))
    return e


def validate_layer_l3(layer: Dict[str, Any]) -> List[Dict[str, str]]:
    e: List[Dict[str, str]] = []
    if not isinstance(layer.get("dimensions"), list) or len(layer["dimensions"]) < 3:
        e.append(_err("layers.L3.dimensions", "at least three"))
    if len(str(layer.get("conflict_diagnosis") or "")) < 20:
        e.append(_err("layers.L3.conflict_diagnosis", "min length 20"))
    return e


def validate_layer_l4(layer: Dict[str, Any]) -> List[Dict[str, str]]:
    e: List[Dict[str, str]] = []
    rfo = layer.get("role_focal_objects")
    if not isinstance(rfo, dict) or len(rfo) < 2:
        e.append(_err("layers.L4.role_focal_objects", "at least two roles"))
    return e


LAYER_VALIDATORS = {
    "L1": validate_layer_l1,
    "L2": validate_layer_l2,
    "L3": validate_layer_l3,
    "L4": validate_layer_l4,
    "L5": validate_layer_l5,
}


def validate_ux_audit_artifact(
    data: Dict[str, Any], *, layer: Optional[str] = None
) -> Tuple[bool, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if data.get("schema_version") != 1:
        errors.append(_err("schema_version", "must be 1"))
    if not data.get("product_id"):
        errors.append(_err("product_id", "required"))
    tags = data.get("tags") or []
    if "domain:ux" not in tags:
        errors.append(_err("tags", "must include domain:ux"))
    layers = data.get("layers")
    if not isinstance(layers, dict):
        errors.append(_err("layers", "required object"))
        return False, errors
    keys = [layer] if layer else list(LAYER_KEYS)
    for key in keys:
        if key not in layers:
            errors.append(_err(f"layers.{key}", "missing"))
            continue
        sub = layers[key]
        if not isinstance(sub, dict):
            errors.append(_err(f"layers.{key}", "must be object"))
            continue
        fn = LAYER_VALIDATORS.get(key)
        if fn:
            errors.extend(fn(sub))
    return not errors, errors


def load_ux_audit_artifact(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return _json.load(f)


def validate_ux_audit_file(path: str, *, layer: Optional[str] = None) -> Dict[str, Any]:
    data = load_ux_audit_artifact(path)
    ok, errors = validate_ux_audit_artifact(data, layer=layer)
    return {"ok": ok, "path": os.path.abspath(path), "errors": errors}


def ux_audit_lint_workspace(
    target_dir: str = ".", artifact_path: str = "", layer: Optional[str] = None
) -> Dict[str, Any]:
    root = os.path.abspath(target_dir)
    path = artifact_path or os.path.join(
        root, "tests", "fixtures", "ux_audit", "sales_pipeline", "reference.json"
    )
    if not os.path.isfile(path):
        return {"ok": False, "error": f"artifact not found: {path}"}
    out = validate_ux_audit_file(path, layer=layer)
    out["schema_path"] = os.path.join(root, "manifests", "ux-audit.schema.json")
    return out