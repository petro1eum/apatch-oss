"""Tests for R47 refactor bundle and R48 semantic verify."""

import json
import subprocess

import pytest

from apatch.workflows import refactor_run_manifest, semantic_verify_workspace


def _git_init(root):
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _git_commit(root, msg="init"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True, capture_output=True)


@pytest.fixture
def git_project(tmp_path):
    _git_init(tmp_path)
    yield tmp_path


def test_refactor_rename_symbol_dry_run(git_project):
    (git_project / "app").mkdir()
    (git_project / "app" / "models.py").write_text("class CustomerDTO: pass\n", encoding="utf-8")
    (git_project / "app" / "api.py").write_text(
        "from app.models import CustomerDTO\n\ndef get() -> CustomerDTO:\n    return CustomerDTO()\n",
        encoding="utf-8",
    )
    manifest = {
        "kind": "refactor-bundle",
        "operation": "rename_symbol",
        "symbol": "CustomerDTO",
        "to": "AccountDTO",
        "scope": {"globs": ["**/*.py"]},
        "phases": [
            {"action": "impact", "target": "CustomerDTO"},
            {"action": "generate", "match_mode": "literal", "replace_all": True},
            {"action": "plan"},
            {"action": "apply", "no_trustchain": True},
        ],
    }
    mpath = git_project / "refactor.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    result = refactor_run_manifest(str(mpath), str(git_project), dry_run=True)
    assert result["ok"] is True
    gen = next(p for p in result["phases"] if p["action"] == "generate")
    assert gen["patch_count"] >= 2
    impact = result["impact"]
    assert "app/api.py" in impact.get("affected_files", [])


def test_refactor_apply_rename(git_project):
    (git_project / "app").mkdir()
    (git_project / "app" / "models.py").write_text("class CustomerDTO: pass\n", encoding="utf-8")
    (git_project / "app" / "api.py").write_text(
        "from app.models import CustomerDTO\n", encoding="utf-8"
    )
    manifest = {
        "kind": "refactor-bundle",
        "operation": "rename_symbol",
        "symbol": "CustomerDTO",
        "to": "AccountDTO",
        "scope": {"globs": ["**/*.py"]},
        "phases": [
            {"action": "impact"},
            {"action": "generate", "replace_all": True},
            {"action": "apply", "no_trustchain": True},
        ],
    }
    mpath = git_project / "refactor.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    result = refactor_run_manifest(str(mpath), str(git_project))
    assert result["ok"] is True
    assert "AccountDTO" in (git_project / "app" / "models.py").read_text(encoding="utf-8")
    assert "CustomerDTO" not in (git_project / "app" / "api.py").read_text(encoding="utf-8")


def test_semantic_verify_route_removed(git_project):
    pytest.importorskip("yaml")
    manifests = git_project / "manifests"
    manifests.mkdir()
    (manifests / "semantic-verify.yaml").write_text(
        "version: 1\nroutes:\n  patterns:\n    - '@app.get\\([^)]+\\)'\n",
        encoding="utf-8",
    )
    api = git_project / "api.py"
    api.write_text("@app.get('/users')\ndef users(): pass\n", encoding="utf-8")
    _git_commit(git_project)
    api.write_text("def users(): pass\n", encoding="utf-8")

    result = semantic_verify_workspace(str(git_project))
    assert result["ok"] is False
    assert any(v["type"] == "route_removed" for v in result["violations"])


def test_semantic_verify_openapi_path(git_project):
    pytest.importorskip("yaml")
    manifests = git_project / "manifests"
    manifests.mkdir()
    (manifests / "semantic-verify.yaml").write_text(
        "version: 1\nopenapi:\n  path: openapi.yaml\n",
        encoding="utf-8",
    )
    spec = git_project / "openapi.yaml"
    spec.write_text("openapi: 3.0.0\npaths:\n  /users:\n    get: {}\n", encoding="utf-8")
    _git_commit(git_project)
    spec.write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")

    result = semantic_verify_workspace(str(git_project))
    assert result["ok"] is False
    assert any(v["type"] == "openapi_path_removed" for v in result["violations"])
