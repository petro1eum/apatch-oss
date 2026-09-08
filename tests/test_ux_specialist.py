"""Tests for SPEC-UX-SPECIALIST-1 (RFP-015)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "manifests" / "ux-audit.schema.json"


def test_r1_schema_file() -> None:
    assert SCHEMA_PATH.is_file(), "manifests/ux-audit.schema.json missing"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema.get("spec_ref") == "SPEC-UX-SPECIALIST-1"
    assert schema["properties"]["schema_version"]["const"] == 1
    layer_req = schema["properties"]["layers"]["required"]
    assert layer_req == ["L1", "L2", "L3", "L4", "L5"]
    assert "domain:ux" in json.dumps(schema["properties"]["tags"])


def test_r2_ux_audit_module() -> None:
    from apatch.ux_audit import LAYER_KEYS, validate_ux_audit_artifact

    sample = {
        "schema_version": 1,
        "product_id": "sales-pipeline-jason",
        "audited_at": "2026-06-10",
        "tags": ["domain:ux", "layer:L1"],
        "layers": {
            "L1": {
                "roles": [{"code": "other", "label": "x", "primary_domain": "y"}],
                "tasks": [
                    {"id": "T1", "label": "a", "roles": ["other"], "frequency": "daily"},
                    {"id": "T2", "label": "b", "roles": ["other"], "frequency": "daily"},
                    {"id": "T3", "label": "c", "roles": ["other"], "frequency": "daily"},
                ],
                "ux_clarity_index": 42,
                "nav_item_count": 12,
            },
            "L5": {
                "signals": [
                    {"id": f"S{i}", "source": "s", "target": "t", "bridge_cost": 1, "urgency": "today"}
                    for i in range(1, 6)
                ],
                "patterns": ["actionable_signals", "contextual_surfacing", "intent_router"],
                "bridge_cost_distribution": {"direct_pct": 23, "broken_pct": 26, "dead_end_pct": 16},
                "ranked_fixes": [{"rank": 1, "title": "fix", "signals_repaired": ["S1"], "effort": "low"}],
            },
        },
    }
    for k in LAYER_KEYS:
        if k not in ("L1", "L5"):
            sample["layers"][k] = {}
    ok, errors = validate_ux_audit_artifact(sample, layer="L1")
    assert ok, errors
    ok5, errors5 = validate_ux_audit_artifact(sample, layer="L5")
    assert ok5, errors5


def test_r3_methodology_index() -> None:
    readme = ROOT / "docs" / "methodology" / "ux-specialist" / "README.md"
    assert readme.is_file(), "methodology index missing"
    text = readme.read_text(encoding="utf-8")
    for layer in ("L1", "L2", "L3", "L4", "L5"):
        assert f"## {layer}" in text, f"missing layer section {layer}"
    assert "reference.json" in text
    assert "Agent workflow" in text

def test_r4_reference_artifact() -> None:
    ref = ROOT / "tests" / "fixtures" / "ux_audit" / "sales_pipeline" / "reference.json"
    assert ref.is_file()
    data = json.loads(ref.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["product_id"] == "sales-pipeline-jason"
    assert len(data["layers"]["L5"]["signals"]) == 31
    assert data["layers"]["L1"]["ux_clarity_index"] == 42
    dist = data["layers"]["L5"]["bridge_cost_distribution"]
    assert dist["direct_pct"] == 23 and dist["broken_pct"] == 26
    from apatch.ux_audit import validate_ux_audit_artifact
    ok, errors = validate_ux_audit_artifact(data)
    assert ok, errors

def test_r5_layer_validators() -> None:
    from apatch.ux_audit import LAYER_KEYS, validate_ux_audit_artifact, validate_ux_audit_file
    ref = ROOT / "tests" / "fixtures" / "ux_audit" / "sales_pipeline" / "reference.json"
    for layer in LAYER_KEYS:
        out = validate_ux_audit_file(str(ref), layer=layer)
        assert out["ok"], (layer, out["errors"])
    data = json.loads(ref.read_text(encoding="utf-8"))
    for layer in LAYER_KEYS:
        ok, errors = validate_ux_audit_artifact(data, layer=layer)
        assert ok, (layer, errors)

def test_r6_rfp015() -> None:
    doc = ROOT / "docs" / "RFP-015-ux-specialist.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "SPEC-UX-SPECIALIST-1" in text
    assert "domain:ux" in text
    for layer in ("L1", "L2", "L3", "L4", "L5"):
        assert layer in text
    assert "ux-audit.schema.json" in text

def test_r7_phase4_tags() -> None:
    doc = ROOT / "docs" / "RFP-014-spec-interference-detection.md"
    text = doc.read_text(encoding="utf-8")
    assert "domain:ux" in text
    assert "layer:L1" in text and "layer:L5" in text
    assert "role:" in text and "product:" in text
    assert "Phase 4" in text



