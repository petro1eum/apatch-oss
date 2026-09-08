"""Strip dry-run cache (R13)."""

import hashlib
import json

from apatch.strip import StripSpec
from apatch.strip_cache import cache_key, load_cached, save_cached
from apatch.strip_pipeline import StripPipelineConfig, run_strip_pipeline
from apatch.trustchain_helper import TrustChainHelper


def test_cache_key_changes_with_manifest(tmp_path):
    f = tmp_path / "a.tsx"
    f.write_text("x", encoding="utf-8")
    m1 = tmp_path / "m1.json"
    m2 = tmp_path / "m2.json"
    m1.write_text("[]", encoding="utf-8")
    m2.write_text('[{"start":"a","until":"b"}]', encoding="utf-8")
    fp = "abc"
    k1 = cache_key(str(f), str(m1), fp)
    k2 = cache_key(str(f), str(m2), fp)
    assert k1 != k2


def test_save_and_load_cached(tmp_path):
    ws = str(tmp_path)
    key = "deadbeef"
    payload = {"exported_meta": [{"label": "x"}], "dangling_references": []}
    save_cached(ws, key, payload)
    loaded = load_cached(ws, key)
    assert loaded == payload


def test_pipeline_uses_cache_on_second_dry_run(tmp_path):
    from tests.fixtures.planning_tsx import PLANNING_TSX

    page = tmp_path / "P.tsx"
    page.write_text(
        PLANNING_TSX.replace("Planning", "Widget"),
        encoding="utf-8",
    )
    specs = [
        StripSpec(
            start="// --- HANDLERS START ---",
            until="// --- HANDLERS END ---",
            label="handlers",
        )
    ]
    workspace = TrustChainHelper.resolve_workspace_root(str(page))
    fingerprint = hashlib.sha256(
        json.dumps([s.__dict__ for s in specs], sort_keys=True).encode()
    ).hexdigest()[:16]
    key = cache_key(str(page), None, fingerprint)

    cfg = StripPipelineConfig(
        file_path=str(page),
        specs=specs,
        dry_run=True,
        out_dir=str(tmp_path / "out"),
        as_json=True,
        use_cache=True,
    )
    r1 = run_strip_pipeline(cfg)
    assert r1.ok
    assert load_cached(workspace, key) is not None

    cfg2 = StripPipelineConfig(
        file_path=str(page),
        specs=specs,
        dry_run=True,
        out_dir=str(tmp_path / "out"),
        as_json=True,
        use_cache=True,
    )
    r2 = run_strip_pipeline(cfg2)
    assert r2.ok
    assert r2.exported_meta == r1.exported_meta
