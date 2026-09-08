"""Shared helpers for SPEC-HYGIENE-1 integration tests."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def registry_artifacts_path(workspace: str) -> str:
    structured = os.path.join(workspace, ".apatch", "registry", "artifacts.jsonl")
    flat = os.path.join(workspace, ".apatch", "artifacts.jsonl")
    if os.path.isfile(structured) or os.path.isdir(os.path.join(workspace, ".apatch", "registry")):
        return structured
    return flat


def provenance_path(workspace: str) -> str:
    structured = os.path.join(workspace, ".apatch", "registry", "provenance.jsonl")
    flat = os.path.join(workspace, ".apatch", "provenance.jsonl")
    if os.path.isfile(structured) or os.path.isdir(os.path.join(workspace, ".apatch", "registry")):
        return structured
    return flat
