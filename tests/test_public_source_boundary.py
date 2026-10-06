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


EXPECTED_REVIEWED_PATHS = set(['CHANGELOG.md', 'SOURCE-EXPORT.md', 'apatch/consumer_profiles.py', 'apatch/mcp/server.py', 'apatch/spec_ownership.py', 'apatch/trust_identity.py', 'apatch/trustchain_helper.py', 'docs/AGENTS.template.md', 'docs/README.md', 'docs/RFP-039-spec-owned-mutation-gate.md', 'docs/mcp_setup.md', 'docs/oss-verification-profiles.json', 'docs/specs/SPEC-SPEC-OWNERSHIP-GATE-1.md', 'pyproject.toml', 'tests/test_artifact.py', 'tests/test_mcp_workspace_signer_scope.py', 'tests/test_public_source_boundary.py', 'tests/test_spec_owned_mutation_gate.py', 'tests/test_strip_lifecycle_imports.py', 'tests/test_workspace_signer_scope.py', 'tests/test_workspace_signer_scope_stdio.py'])
EXPECTED_NEW_PATHS = set(['tests/test_mcp_workspace_signer_scope.py', 'tests/test_workspace_signer_scope.py', 'tests/test_workspace_signer_scope_stdio.py'])
EXPECTED_MODIFIED_RUNTIME = set(['apatch/consumer_profiles.py', 'apatch/mcp/server.py', 'apatch/spec_ownership.py', 'apatch/trust_identity.py', 'apatch/trustchain_helper.py'])
HISTORICAL_MANIFEST_SHA256 = '14a82cc09a58436e58c3a8e07d1d75d7418e2cb704da4899ba63612ee2d18402'

# Owner-frozen additive release boundary for the exact 0.8.50 intake transfer.
# The existing v2 validator below and all historical test bodies remain intact.
INTAKE_PUBLIC_COMMIT = "77a5e19572df0fc14526881b8f9ecf6fdb6905a7"
INTAKE_PUBLIC_REQUIREMENTS = {
    "apatch/sdd_preparation_io.py": "SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1#R1",
    "apatch/strict_existing_signer.py": "SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1#R1",
    "apatch/sdd_contract_intake.py": "SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1#R1",
    "apatch/sdd_integrity.py": "SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1#R1",
    "apatch/trustchain_helper.py": "SPEC-SPEC-OWNERSHIP-GATE-1#R15",
}
INTAKE_RUNTIME_ADDED = {
    "apatch/sdd_preparation_io.py",
    "apatch/strict_existing_signer.py",
    "apatch/sdd_contract_intake.py",
}
INTAKE_NEW_PATHS = INTAKE_RUNTIME_ADDED | {
    "docs/RFP-050-OSS-CONTRACT-INTAKE-PORT.md",
    "docs/specs/SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1.md",
    "tests/portable_intake/test_portable_intake.py",
    "tests/portable_intake/native_intake_fixture.py",
    "tests/portable_intake/helper-pins.json",
    "tests/test_spec_self_metadata_repair.py",
}
INTAKE_FROZEN_INPUTS = {
    "docs/RFP-050-OSS-CONTRACT-INTAKE-PORT.md":
        "13d8b752ffad7874e5085971afa6e397eb812d6d995040ad773e2efac85eeda2",
    "tests/portable_intake/test_portable_intake.py":
        "66a468ab368467613a8c84b4603caf70ec6634e899ceaefdfb776e189c0fa23f",
    "tests/portable_intake/native_intake_fixture.py":
        "1a29ab298d55313b4564d1c638f8650572fead79b1f32b1967d30673391f5ab4",
    "tests/portable_intake/helper-pins.json":
        "859b0a5a19d26054c1d2b0040d5f80284e934397be8cdc5417ebf8023fa246ac",
    "tests/test_spec_self_metadata_repair.py":
        "aa7bf85905099dc85e4c794d6ff94cfb6aaaf57fa2ec94dca01e2e11e9fdac73",
}


def _git_at(root, *args):
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, timeout=30,
        env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    assert result.returncode == 0, (args, result.stderr.decode(errors="replace"))
    return result.stdout


def _validate_intake_release_manifest(manifest, root):
    assert manifest["schema"] == "apatch.public-source.v3"
    assert manifest["version"] == "0.8.50"
    historical = manifest["historical_public_source"]
    assert historical["public_commit"] == "9b20e82e7ea343bdfb8784bdb20fda2b908f841b"
    assert historical["manifest_sha256"] == HISTORICAL_MANIFEST_SHA256
    old = historical["manifest"]
    assert hashlib.sha256((json.dumps(old, indent=2) + "\n").encode()).hexdigest() == HISTORICAL_MANIFEST_SHA256
    assert old["version"] == "0.8.48"
    assert old["baseline_commit"] == "3561c67961f80703621a6d224673d6f2070a5d66"
    assert old["runtime_files_total"] == old["runtime_files_unchanged"] == 266
    assert old["runtime_files_modified"] == []
    assert len(old["reviewed_source_commits"]) == 38
    assert manifest["baseline_commit"] == old["baseline_commit"]
    assert manifest["reviewed_source_commits"] == old["reviewed_source_commits"]
    assert manifest["private_git_history_included"] is False
    assert manifest["generated_artifacts_excluded"] is True
    assert manifest["self_excluded"] == "PUBLIC-SOURCE-MANIFEST.json"
    assert manifest["runtime_files_total"] == 269
    assert manifest["runtime_files_unchanged"] == 260
    assert set(manifest["runtime_files_added"]) == INTAKE_RUNTIME_ADDED
    assert len(manifest["runtime_files_added"]) == 3
    modified = EXPECTED_MODIFIED_RUNTIME | {"apatch/sdd_integrity.py"}
    assert set(manifest["runtime_files_modified"]) == modified
    assert len(manifest["runtime_files_modified"]) == 6

    old_files = {item["path"]: item for item in old["files"]}
    files = {item["path"]: item for item in manifest["files"]}
    assert len(files) == len(manifest["files"])
    assert set(files) == set(old_files) | EXPECTED_NEW_PATHS | INTAKE_NEW_PATHS
    delta = {item["path"]: item for item in manifest["release_delta"]}
    assert len(delta) == len(manifest["release_delta"])
    reviewed = (EXPECTED_REVIEWED_PATHS | INTAKE_NEW_PATHS | {"apatch/sdd_integrity.py"}
                | set(["scripts/qualify_oss.py","tests/test_oss_verification_profiles.py","docs/specs/SPEC-OSS-VERIFICATION-PROFILES-1.md","docs/oss-verification.md"]))
    assert set(delta) == reviewed
    assert {p for p in files if files[p] != old_files.get(p)} == reviewed
    baseline_runtime = {p for p, item in old_files.items() if item["runtime_baseline_unchanged"]}
    assert len(baseline_runtime) == 266
    assert {p for p in baseline_runtime if files[p]["sha256"] != old_files[p]["sha256"]} == modified
    assert sum(files[p]["runtime_baseline_unchanged"] for p in baseline_runtime) == 260

    for p, item in files.items():
        relative = Path(p)
        assert not relative.is_absolute() and ".." not in relative.parts, p
        assert set(item) == {"path", "sha256", "bytes", "mode", "runtime_baseline_unchanged"}, p
        expected_unchanged = p in baseline_runtime and item["sha256"] == old_files[p]["sha256"]
        assert type(item["runtime_baseline_unchanged"]) is bool, p
        assert item["runtime_baseline_unchanged"] is expected_unchanged, p
        path = root / p
        assert path.is_file() and not path.is_symlink(), p
        assert path.resolve().is_relative_to(root.resolve()), p
        assert path.stat().st_nlink == 1, p
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == item["sha256"], p
        assert len(content) == item["bytes"], p
        assert ("0o755" if path.stat().st_mode & 0o111 else "0o644") == item["mode"], p
        if p in delta:
            assert delta[p]["before_sha256"] == old_files.get(p, {}).get("sha256"), p
            assert delta[p]["after_sha256"] == item["sha256"], p
    for path, expected in INTAKE_FROZEN_INPUTS.items():
        assert files[path]["sha256"] == expected, path

    binding = manifest["contract_intake_source"]
    assert set(binding) == {"schema", "source_commit", "files"}
    assert binding["schema"] == "apatch.public-intake-source.v1"
    assert binding["source_commit"] == INTAKE_PUBLIC_COMMIT
    _git_at(root, "merge-base", "--is-ancestor", INTAKE_PUBLIC_COMMIT, "HEAD")
    assert set(binding["files"]) == set(INTAKE_PUBLIC_REQUIREMENTS)
    for path, requirement in INTAKE_PUBLIC_REQUIREMENTS.items():
        record = binding["files"][path]
        assert set(record) == {"requirement", "sha256", "bytes"}, path
        assert record["requirement"] == requirement, path
        source = _git_at(root, "show", f"{INTAKE_PUBLIC_COMMIT}:{path}")
        assert hashlib.sha256(source).hexdigest() == record["sha256"] == files[path]["sha256"], path
        assert len(source) == record["bytes"] == files[path]["bytes"], path
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    assert tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"] == "0.8.50"



def _validate_release_manifest(manifest):
    if manifest.get('schema') == 'apatch.public-source.v3':
        return _validate_intake_release_manifest(manifest, ROOT)
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


@pytest.fixture(scope="module")
def intake_inventory_fixture(tmp_path_factory):
    """Real public Git history and files; never a functional wheel qualification."""
    base = tmp_path_factory.mktemp("public-intake-inventory")
    root = base / "candidate"
    _git_at(ROOT, "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(root))
    _git_at(root, "checkout", "--quiet", "--detach", INTAKE_PUBLIC_COMMIT)
    manifest = json.loads((root / "PUBLIC-SOURCE-MANIFEST.json").read_text())
    old = manifest["historical_public_source"]["manifest"]
    old_files = {item["path"]: item for item in old["files"]}
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text()
    assert 'version = "0.8.49"' in text
    pyproject.write_text(text.replace('version = "0.8.49"', 'version = "0.8.50"', 1))
    # Judge bytes in this disposable repository are the proposed/current judge,
    # not altered runtime files and not artificial installed package metadata.
    (root / "tests/test_public_source_boundary.py").write_bytes(Path(__file__).read_bytes())
    # The versioned qualification amendment is judge/support data only.
    for p in ["scripts/qualify_oss.py","tests/test_oss_verification_profiles.py","docs/specs/SPEC-OSS-VERIFICATION-PROFILES-1.md","docs/oss-verification.md"]:
        (root / p).write_bytes((ROOT / p).read_bytes())
    paths = sorted(set(old_files) | EXPECTED_NEW_PATHS | INTAKE_NEW_PATHS)
    baseline_runtime = {p for p, item in old_files.items() if item["runtime_baseline_unchanged"]}
    files = []
    for p in paths:
        source = root / p
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        files.append({
            "path": p, "sha256": digest, "bytes": len(content),
            "mode": "0o755" if source.stat().st_mode & 0o111 else "0o644",
            "runtime_baseline_unchanged": p in baseline_runtime and digest == old_files[p]["sha256"],
        })
    manifest.update({
        "schema": "apatch.public-source.v3", "version": "0.8.50",
        "runtime_files_total": 269, "runtime_files_unchanged": 260,
        "runtime_files_modified": sorted(EXPECTED_MODIFIED_RUNTIME | {"apatch/sdd_integrity.py"}),
        "runtime_files_added": sorted(INTAKE_RUNTIME_ADDED),
        "files": files,
        "release_delta": [
            {"path": item["path"], "before_sha256": old_files.get(item["path"], {}).get("sha256"),
             "after_sha256": item["sha256"]}
            for item in files if item != old_files.get(item["path"])
        ],
        "contract_intake_source": {
            "schema": "apatch.public-intake-source.v1", "source_commit": INTAKE_PUBLIC_COMMIT,
            "files": {
                p: {"requirement": requirement,
                    "sha256": hashlib.sha256((root / p).read_bytes()).hexdigest(),
                    "bytes": len((root / p).read_bytes())}
                for p, requirement in INTAKE_PUBLIC_REQUIREMENTS.items()
            },
        },
    })
    _validate_intake_release_manifest(manifest, root)
    return root, manifest


def test_intake_release_inventory_accepts_exact_public_transfer(intake_inventory_fixture, monkeypatch):
    root, manifest = intake_inventory_fixture
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", root)
    _validate_release_manifest(manifest)


def test_historical_0_8_49_inventory_still_uses_original_validator(tmp_path, monkeypatch):
    root = tmp_path / "historical"
    _git_at(ROOT, "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(root))
    _git_at(root, "checkout", "--quiet", "--detach", "8908c3c4c38b5b297b80340dc52c31bc72f18b1e")
    manifest = json.loads((root / "PUBLIC-SOURCE-MANIFEST.json").read_text())
    assert manifest["schema"] == "apatch.public-source.v2" and manifest["version"] == "0.8.49"
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", root)
    _validate_release_manifest(manifest)


@pytest.mark.parametrize("tamper", [
    "wrong_schema", "wrong_version", "historical_hash", "historical_inventory",
    "runtime_total", "runtime_unchanged", "runtime_added", "modified_runtime_scope",
    "duplicate_file", "missing_file", "extra_private_file", "duplicate_delta",
    "incorrect_delta_before", "incorrect_delta_after", "incorrect_file_size",
    "incorrect_file_mode", "fake_source_commit", "foreign_source_commit",
    "wrong_requirement_owner", "forged_source_hash",
])
def test_intake_release_inventory_rejects_one_manifest_tamper(
    intake_inventory_fixture, monkeypatch, tamper,
):
    root, original = intake_inventory_fixture
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", root)
    _validate_release_manifest(original)  # Missing/always-denying API cannot pass.
    manifest = copy.deepcopy(original)
    if tamper == "wrong_schema":
        manifest["schema"] = "apatch.public-source.v999"
    elif tamper == "wrong_version":
        manifest["version"] = "0.8.49"
    elif tamper == "historical_hash":
        manifest["historical_public_source"]["manifest_sha256"] = "0" * 64
    elif tamper == "historical_inventory":
        manifest["historical_public_source"]["manifest"]["files"][0]["sha256"] = "0" * 64
    elif tamper == "runtime_total":
        manifest["runtime_files_total"] = 268
    elif tamper == "runtime_unchanged":
        manifest["runtime_files_unchanged"] = 261
    elif tamper == "runtime_added":
        manifest["runtime_files_added"].pop()
    elif tamper == "modified_runtime_scope":
        manifest["runtime_files_modified"].append("apatch/cli.py")
    elif tamper == "duplicate_file":
        manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    elif tamper == "missing_file":
        manifest["files"].pop()
    elif tamper == "extra_private_file":
        item = copy.deepcopy(manifest["files"][0])
        item["path"] = "apatch_pro/leaked.py"
        manifest["files"].append(item)
    elif tamper == "duplicate_delta":
        manifest["release_delta"].append(copy.deepcopy(manifest["release_delta"][0]))
    elif tamper == "incorrect_delta_before":
        manifest["release_delta"][0]["before_sha256"] = "0" * 64
    elif tamper == "incorrect_delta_after":
        manifest["release_delta"][0]["after_sha256"] = "0" * 64
    elif tamper == "incorrect_file_size":
        manifest["files"][0]["bytes"] += 1
    elif tamper == "incorrect_file_mode":
        item = next(item for item in manifest["files"] if item["path"] == "pyproject.toml")
        item["mode"] = "0o755"
    elif tamper == "fake_source_commit":
        manifest["contract_intake_source"]["source_commit"] = "0" * 40
    elif tamper == "foreign_source_commit":
        manifest["contract_intake_source"]["source_commit"] = "8908c3c4c38b5b297b80340dc52c31bc72f18b1e"
    elif tamper == "wrong_requirement_owner":
        manifest["contract_intake_source"]["files"]["apatch/trustchain_helper.py"]["requirement"] = (
            "SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1#R1"
        )
    else:
        manifest["contract_intake_source"]["files"]["apatch/sdd_contract_intake.py"]["sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _validate_release_manifest(manifest)


@pytest.mark.parametrize("tamper", ["contents", "symlink", "hardlink", "executable_mode"])
def test_intake_release_inventory_rejects_actual_filesystem_tamper(
    intake_inventory_fixture, monkeypatch, tmp_path, tamper,
):
    import os
    root, manifest = intake_inventory_fixture
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", root)
    _validate_release_manifest(manifest)
    path = root / "apatch/sdd_contract_intake.py"
    content, mode = path.read_bytes(), path.stat().st_mode
    outside = tmp_path / "same-bytes.py"
    outside.write_bytes(content)
    try:
        if tamper == "contents":
            path.write_bytes(content + b"\n# unexpected source change\n")
        elif tamper == "symlink":
            path.unlink()
            path.symlink_to(outside)
        elif tamper == "hardlink":
            path.unlink()
            os.link(outside, path)
        else:
            path.chmod(mode | 0o111)
        with pytest.raises(AssertionError):
            _validate_release_manifest(manifest)
    finally:
        if path.is_symlink() or path.stat().st_nlink != 1:
            path.unlink()
        path.write_bytes(content)
        path.chmod(mode)
    _validate_release_manifest(manifest)


def test_intake_release_inventory_requires_actual_public_ancestor(
    intake_inventory_fixture, monkeypatch,
):
    root, manifest = intake_inventory_fixture
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", root)
    _validate_release_manifest(manifest)
    head = _git_at(root, "rev-parse", "HEAD").decode().strip()
    tree = _git_at(root, "rev-parse", "HEAD^{tree}").decode().strip()
    # Real orphan commit of the same tree: valid Git object and identical files,
    # but no predecessor relationship to the agreed public transfer.
    orphan = _git_at(
        root, "-c", "user.name=Release Judge", "-c", "user.email=judge@example.invalid",
        "commit-tree", tree, "-m", "isolated unrelated history",
    ).decode().strip()
    try:
        _git_at(root, "update-ref", "HEAD", orphan)
        with pytest.raises(AssertionError):
            _validate_release_manifest(manifest)
    finally:
        _git_at(root, "update-ref", "HEAD", head)
    _validate_release_manifest(manifest)
