from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


def _checker():
    spec = importlib.util.spec_from_file_location("public_docs_checker", ROOT / "scripts/check_public_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_public_source_matches_recorded_manifest():
    manifest = json.loads((ROOT / "PUBLIC-SOURCE-MANIFEST.json").read_text())
    assert manifest["runtime_files_total"] == 239
    assert manifest["runtime_files_unchanged"] == 224
    assert set(manifest["runtime_files_modified"]) == {
        "apatch/agent_guidance.py", "apatch/cli.py", "apatch/cli_conformance.py",
        "apatch/consumer_profiles.py", "apatch/doctor.py", "apatch/spec_ownership.py",
        "apatch/mcp/runtime_probe.py", "apatch/mcp_health.py", "apatch/path_leases.py",
        "apatch/runtime/runtime.py", "apatch/spec_coverage.py", "apatch/spec_rebind.py",
        "apatch/spec_reverification.py", "apatch/spec_run.py", "apatch_search_workflows/slug_ratify.py",
    }
    assert len(manifest["reviewed_source_commits"]) == 6
    assert manifest["private_git_history_included"] is False
    paths = [item["path"] for item in manifest["files"]]
    assert len(paths) == len(set(paths))
    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file() and not path.is_symlink(), item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["path"]


def test_private_components_and_operational_state_are_not_selected():
    manifest = json.loads((ROOT / "PUBLIC-SOURCE-MANIFEST.json").read_text())
    paths = {item["path"] for item in manifest["files"]}
    forbidden = ("apatch_pro/", "trustchain_pro/", "avatar_contract/", "docs/ct_ckba_036_2017/",
                 ".git/", ".trustchain/", "docs/incidents/", ".claude/")
    for path in paths:
        assert not path.startswith(forbidden), path
        assert path not in {".apatch/agent-identity.json", ".apatch/reality.jsonl",
                            ".cursor/mcp.json", ".mcp.json", "manifests/apatch-inclusion.jsonl"}
    assert {".apatch/enforcement.json", ".apatch/sandbox.json", ".apatch/conformance.json"} <= paths


def test_public_documentation_targets_and_anchors_resolve():
    result = _checker().check(ROOT)
    assert result["ok"], result["errors"]


def test_pypi_description_has_no_relative_or_private_links():
    links = list(_checker().markdown_links(ROOT / "README.pypi.md"))
    assert links
    assert all(urlsplit(link).scheme == "https" for link in links)
    for link in links:
        parsed = urlsplit(link)
        if parsed.hostname == "github.com":
            assert parsed.path == "/petro1eum/apatch-oss" or parsed.path.startswith("/petro1eum/apatch-oss/")
    assert any("/apatch-oss/blob/main/docs/README.md" in link for link in links)
    text = (ROOT / "README.pypi.md").read_text()
    assert "executable contracts for AI agents" in text
    assert "mediated-only" in text


def test_docs_checker_rejects_missing_target(tmp_path):
    (tmp_path / "README.md").write_text("[Missing](docs/absent.md)")
    assert not _checker().check(tmp_path)["ok"]


def test_docs_checker_rejects_missing_anchor(tmp_path):
    (tmp_path / "README.md").write_text("# Start\n\n[Missing](#not-a-heading)")
    assert not _checker().check(tmp_path)["ok"]


def test_docs_checker_rejects_private_repository_link(tmp_path):
    (tmp_path / "README.md").write_text("[Private](https://github.com/petro1eum/apatch)")
    assert not _checker().check(tmp_path)["ok"]


def test_docs_checker_distinguishes_approved_public_repository(tmp_path):
    (tmp_path / "README.md").write_text("[Public](https://github.com/petro1eum/apatch-oss/blob/main/docs/README.md)")
    assert _checker().check(tmp_path)["ok"]
    (tmp_path / "README.md").write_text("[Private](https://github.com/petro1eum/apatch/blob/master/README.md)")
    assert not _checker().check(tmp_path)["ok"]
