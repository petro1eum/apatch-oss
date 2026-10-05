"""Proposed public SDK port contract. Real disposable signing; no owner grants.

These tests are additive. No previously frozen judge is edited or replaced.
The fixture snapshot is copied byte-for-byte from the qualified Studio source.
Source-tree RED and installed-wheel qualification are recorded separately.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

_fixture_path = Path(__file__).with_name("native_intake_fixture.py")
_fixture_spec = importlib.util.spec_from_file_location("portable_native_intake_fixture", _fixture_path)
fixtures = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(fixtures)

PRIVATE_MIGRATIONS = (
    "apatch.sdd_draft_amendment", "apatch.sdd_master_r6_owner_review",
    "apatch.sdd_shared_toolchain_amendment", "apatch.sdd_judge_amendment",
    "apatch.sdd_successor", "apatch.sdd_authoring",
)


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    return fixtures.make_workspace(tmp_path, monkeypatch)


def signed_rows(f):
    return [row for row in fixtures.native_records(f) if row["tool"] == fixtures.TOOL]


def assert_prepared(f, result):
    assert result["ok"] is True
    assert result["functional_acceptance"] is False
    assert result["implementation_allowed"] is False
    assert result["owner_freeze_required"] is True
    expected = {item["path"]: fixtures.digest(item["content"].encode()) for item in f.files}
    assert result["files"] == expected
    for item in f.files:
        assert (f.root / item["path"]).read_bytes() == item["content"].encode()
    rows = signed_rows(f)
    assert len(rows) == 2 and {row["data"]["stage"] for row in rows} == {"approved", "completed"}
    assert all(row["key_id"] == fixtures.OWNER["id"] for row in rows)
    assert all(row["data"]["review_hash"] == result["review_hash"] for row in rows)
    fixtures.preserved(f)


def test_review_is_read_only_and_retains_exact_fixed_ids(consumer):
    f = consumer
    before = fixtures.snapshot(f.root)
    review = fixtures.propose(f)
    assert fixtures.snapshot(f.root) == before
    assert (review["spec_id"], review["rfp_id"]) == (fixtures.SPEC, fixtures.RFP)
    assert review["authority_id"] == fixtures.OWNER["id"]
    assert review["active_contract_hash"] == f.contract["document_hash"]
    assert review["implementation_allowed"] is False
    assert review["functional_acceptance"] is False
    assert review["owner_freeze_required"] is True


@pytest.mark.parametrize("mode", ["create", "replace", "restart_replay"])
def test_native_public_prepare_approve_and_exact_replay(consumer, mode):
    f = consumer
    if mode == "replace":
        fixtures.write(f.root, f.files[0]["path"], b"# Previous independent draft\r\n")
    review = fixtures.propose(f)
    result = fixtures.approve(f, review)
    assert_prepared(f, result)
    if mode == "replace":
        row = next(row for row in review["files"] if row["path"] == f.files[0]["path"])
        assert row["operation"] == "REPLACE" and row["before_hash"]
    if mode == "restart_replay":
        before = fixtures.snapshot(f.root)
        module = importlib.reload(fixtures.api())
        replay = module.approve_contract_intake(f.root, json.loads(json.dumps(review)),
            owner=fixtures.OWNER, expected_snapshot=review["document_hash"])
        assert replay == result
        assert fixtures.snapshot(f.root) == before


@pytest.mark.parametrize("bad_owner", ["foreign", "uncertified"])
def test_owner_denial_signs_and_installs_nothing(consumer, bad_owner):
    f = consumer
    review = fixtures.propose(f)  # Missing API or permanent denial must be RED.
    owner = dict(fixtures.OWNER)
    if bad_owner == "foreign":
        owner["id"] = "foreign-disposable-fixture"
    else:
        owner["certified"] = False
    before = fixtures.snapshot(f.root)
    with pytest.raises(ValueError):
        fixtures.approve(f, review, owner=owner)
    assert fixtures.snapshot(f.root) == before and signed_rows(f) == []
    assert f.key.read_bytes() == f.key_bytes


@pytest.mark.parametrize("drift", ["review", "expected_snapshot", "profile", "frozen_judge"])
def test_snapshot_and_active_contract_drift_fail_before_signature(consumer, drift):
    f = consumer
    review = fixtures.propose(f)
    expected = review["document_hash"]
    if drift == "review":
        review = copy.deepcopy(review)
        review["files"][0]["content"] += "\nChanged after review\n"
    elif drift == "expected_snapshot":
        expected = "sha256:" + "0" * 64
    elif drift == "profile":
        path = f.root / fixtures.PROFILE
        value = json.loads(path.read_bytes())
        value["authority"]["actor_id"] = "foreign-disposable-fixture"
        path.write_text(json.dumps(value))
    else:
        (f.root / "tests/frozen/test_existing.py").write_text("def test_existing():\n    assert False\n")
    before = fixtures.snapshot(f.root)
    with pytest.raises(ValueError):
        fixtures.approve(f, review, snapshot_hash=expected)
    assert fixtures.snapshot(f.root) == before and signed_rows(f) == []


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "traversal", "runtime_source"])
def test_file_boundary_preserves_positive_control_and_rejects_unsafe_inputs(consumer, unsafe, tmp_path):
    f = consumer
    fixtures.propose(f)
    files = copy.deepcopy(f.files)
    if unsafe == "traversal":
        files[-1]["path"] = "tests/../outside.py"
    elif unsafe == "runtime_source":
        files[-1]["path"] = "apatch/runtime/runtime.py"
    else:
        target = f.root / files[-1]["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "unrelated-input.json"
        outside.write_bytes(b'{"original":true}\n')
        if unsafe == "symlink":
            target.symlink_to(outside)
        else:
            import os
            os.link(outside, target)
    before = fixtures.snapshot(f.root)
    with pytest.raises(ValueError):
        fixtures.propose(f, files=files)
    assert fixtures.snapshot(f.root) == before
    if unsafe in {"symlink", "hardlink"}:
        assert outside.read_bytes() == b'{"original":true}\n'


def test_unknown_pending_fences_implementation_without_deleting_evidence(consumer):
    f = consumer
    fixtures.propose(f)
    fixtures.write(f.root, fixtures.PENDING, {"stage": "signing_approved", "review_hash": "sha256:" + "0" * 64})
    before = fixtures.snapshot(f.root)
    from apatch.sdd_integrity import load_profile_contract
    with pytest.raises(ValueError, match="intake|preparation|transaction"):
        load_profile_contract(f.root)
    assert fixtures.snapshot(f.root) == before


def test_generic_runtime_works_without_shipping_or_loading_private_migrations(consumer):
    f = consumer
    # Availability is checked first: absent generic functionality cannot pass a
    # merely negative import-boundary test.
    result = fixtures.approve(f, fixtures.propose(f))
    assert_prepared(f, result)
    for name in PRIVATE_MIGRATIONS:
        assert importlib.util.find_spec(name) is None, "Private historical migration is shipped: " + name
        assert name not in sys.modules, "Generic intake loaded private migration: " + name


def test_extracted_generic_helpers_keep_exact_prequalified_function_bodies():
    # This supplementary code-integrity gate never substitutes the real native
    # approval/replay/denial checks above.
    fixtures.api()
    name = "apatch.sdd_preparation_io"
    assert importlib.util.find_spec(name) is not None, "Generic helpers are still inside private migrations"
    module = importlib.import_module(name)
    import hashlib
    import inspect
    pins = json.loads(Path(__file__).with_name("helper-pins.json").read_bytes())
    assert set(pins) == {"_path", "_read", "_hash", "_capture", "freeze_lock",
                         "assert_can_freeze", "_identity", "_immutable"}
    for function, expected in pins.items():
        raw = inspect.getsource(getattr(module, function)).strip().encode()
        assert hashlib.sha256(raw).hexdigest() == expected, function + " changed during extraction"


@pytest.mark.parametrize("missing", ["session", "origin"])
def test_public_studio_keeps_native_intake_route_inside_security_guard(consumer, missing):
    fixtures.api()  # The candidate Core, not a fallback runner, must exist.
    from apatch_studio.app import create_app
    from fastapi.testclient import TestClient
    f = consumer
    subprocess.run(["git", "init", "-q", str(f.root)], check=True)
    app = create_app(str(f.root), identity_root=f.root / ".apatch")
    route = "/api/v1/contract-intake/" + fixtures.SPEC
    route_template = "/api/v1/contract-intake/{spec_id}"
    methods = {(r.path, m) for r in app.routes for m in getattr(r, "methods", ())}
    assert (route_template, "GET") in methods and (route_template, "POST") in methods
    assert (route_template + "/approve", "POST") in methods
    headers = {"x-apatch-studio-session": app.state.studio_guard.token,
               "origin": "http://127.0.0.1:8765"}
    headers.pop("x-apatch-studio-session" if missing == "session" else "origin")
    before = fixtures.snapshot(f.root)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(route, headers=headers, json={
            "request_id": "apsreq_port_guard", "rfp_id": fixtures.RFP,
            "actor_id": fixtures.ACTOR, "reason": "Exact disposable package",
            "files": f.files})
    assert response.status_code == (401 if missing == "session" else 403), response.text
    assert fixtures.snapshot(f.root) == before and signed_rows(f) == []


def test_installed_pair_and_resource_paths_are_exact_not_a_source_checkout():
    from importlib.metadata import version
    import apatch
    import apatch_studio
    assert version("apatch") == "0.8.50"
    assert version("apatch-studio") == "0.1.5"
    for module in (apatch, apatch_studio):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(Path(sys.prefix).resolve())
        assert "site-packages" in path.parts
    fixtures.api()
