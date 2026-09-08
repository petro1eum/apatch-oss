"""Architecture rules loader (R44)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def default_rules_path(target_dir: str) -> str:
    return os.path.join(target_dir, "manifests", "arch-rules.yaml")


def load_arch_rules(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"arch rules not found: {path}")

    try:
        import yaml
    except ImportError as e:
        raise RuntimeError("PyYAML required: pip install apatch[yaml]") from e

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError("arch rules must be a YAML mapping")
    data.setdefault("version", 1)
    data.setdefault("forbidden_imports", [])
    data.setdefault("layer_rules", [])
    data.setdefault("service_rules", [])
    return data
