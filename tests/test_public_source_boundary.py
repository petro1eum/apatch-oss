from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _checker():
    spec = importlib.util.spec_from_file_location("public_docs_checker", ROOT / "scripts/check_public_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPECTED_REVIEWED_PATHS = set(['CHANGELOG.md', 'SOURCE-EXPORT.md', 'apatch/consumer_profiles.py', 'apatch/mcp/server.py', 'apatch/spec_ownership.py', 'apatch/trust_identity.py', 'apatch/trustchain_helper.py', 'docs/AGENTS.template.md', 'docs/README.md', 'docs/RFP-039-spec-owned-mutation-gate.md', 'docs/mcp_setup.md', 'docs/oss-verification-profiles.json', 'docs/specs/SPEC-SPEC-OWNERSHIP-GATE-1.md', 'pyproject.toml', 'tests/test_mcp_workspace_signer_scope.py', 'tests/test_public_source_boundary.py', 'tests/test_spec_owned_mutation_gate.py', 'tests/test_workspace_signer_scope.py', 'tests/test_workspace_signer_scope_stdio.py'])
EXPECTED_NEW_PATHS = set(['tests/test_mcp_workspace_signer_scope.py', 'tests/test_workspace_signer_scope.py', 'tests/test_workspace_signer_scope_stdio.py'])
EXPECTED_MODIFIED_RUNTIME = set(['apatch/consumer_profiles.py', 'apatch/mcp/server.py', 'apatch/spec_ownership.py', 'apatch/trust_identity.py', 'apatch/trustchain_helper.py'])
HISTORICAL_MANIFEST_SHA256 = '14a82cc09a58436e58c3a8e07d1d75d7418e2cb704da4899ba63612ee2d18402'


def _validate_release_manifest(manifest):
    assert manifest['schema'] == 'apatch.public-source.v2'
    assert manifest['version'] == '0.8.49'
    historical = manifest['historical_public_source']
    assert historical['public_commit'] == '9b20e82e7ea343bdfb8784bdb20fda2b908f841b'
    assert historical['manifest_sha256'] == HISTORICAL_MANIFEST_SHA256
    old = historical['manifest']
    assert hashlib.sha256((json.dumps(old, indent=2) + '\n').encode()).hexdigest() == HISTORICAL_MANIFEST_SHA256
    assert old['version'] == '0.8.48'
    assert old['baseline_commit'] == '3561c67961f80703621a6d224673d6f2070a5d66'
    assert old['runtime_files_total'] == old['runtime_files_unchanged'] == 266
    assert old['runtime_files_modified'] == []
    assert len(old['reviewed_source_commits']) == 38
    assert manifest['baseline_commit'] == old['baseline_commit']
    assert manifest['reviewed_source_commits'] == old['reviewed_source_commits']
    assert manifest['private_git_history_included'] is False
    assert manifest['runtime_files_total'] == 266
    assert manifest['runtime_files_unchanged'] == 261
    assert set(manifest['runtime_files_modified']) == EXPECTED_MODIFIED_RUNTIME
    old_files = {item['path']: item for item in old['files']}
    files = {item['path']: item for item in manifest['files']}
    assert len(files) == len(manifest['files'])
    assert set(files) == set(old_files) | EXPECTED_NEW_PATHS
    delta = {item['path']: item for item in manifest['release_delta']}
    assert len(delta) == len(manifest['release_delta'])
    assert set(delta) == EXPECTED_REVIEWED_PATHS
    observed_delta = {p for p in files if p not in old_files or files[p] != old_files[p]}
    assert observed_delta == EXPECTED_REVIEWED_PATHS
    runtime = {p for p, item in files.items() if old_files.get(p, {}).get('runtime_baseline_unchanged')}
    assert len(runtime) == 266
    changed_runtime = {p for p in runtime if files[p]['sha256'] != old_files[p]['sha256']}
    assert changed_runtime == EXPECTED_MODIFIED_RUNTIME
    assert sum(files[p]['runtime_baseline_unchanged'] for p in runtime) == 261
    for p, item in files.items():
        path = ROOT / p
        assert path.is_file() and not path.is_symlink(), p
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == item['sha256'], p
        assert len(content) == item['bytes'], p
        # Git exports only the executable bit; local owner rw permissions may be 0600.
        canonical_mode = '0o755' if path.stat().st_mode & 0o111 else '0o644'
        assert canonical_mode == item['mode'], p
        if p in delta:
            assert delta[p]['before_sha256'] == old_files.get(p, {}).get('sha256')
            assert delta[p]['after_sha256'] == item['sha256']


def test_selected_public_source_matches_recorded_manifest():
    _validate_release_manifest(json.loads((ROOT / 'PUBLIC-SOURCE-MANIFEST.json').read_text()))


@pytest.mark.parametrize('tamper', ['historical_hash', 'historical_inventory', 'unreviewed_delta', 'wrong_runtime_count', 'wrong_executable_mode', 'missing_file'])
def test_public_release_inventory_rejects_forged_history_or_scope(tamper):
    manifest = copy.deepcopy(json.loads((ROOT / 'PUBLIC-SOURCE-MANIFEST.json').read_text()))
    if tamper == 'historical_hash':
        manifest['historical_public_source']['manifest_sha256'] = '0' * 64
    elif tamper == 'historical_inventory':
        manifest['historical_public_source']['manifest']['files'][0]['sha256'] = '0' * 64
    elif tamper == 'unreviewed_delta':
        manifest['release_delta'].append({'path': 'apatch/cli.py', 'before_sha256': '0' * 64, 'after_sha256': '0' * 64})
    elif tamper == 'wrong_runtime_count':
        manifest['runtime_files_unchanged'] = 266
    elif tamper == 'wrong_executable_mode':
        next(item for item in manifest['files'] if item['path'] == 'pyproject.toml')['mode'] = '0o755'
    else:
        manifest['files'].pop()
    with pytest.raises(AssertionError):
        _validate_release_manifest(manifest)


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
