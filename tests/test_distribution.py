from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
TRUSTCHAIN_MIT_SHA256 = "657376e03d6b7d1390bcddec4c006a3009876576c38aac112a7264c6e75799de"
AVATAR_CONTRACT_COMMIT = "e8cee1689e2b661b8b7258d9c939fa1a3839cefb"
AVATAR_CONTRACT_REQUIREMENT = (
    "avatar-contract @ git+https://github.com/petro1eum/avatar-contract.git@"
    + AVATAR_CONTRACT_COMMIT
)


def _copy_source(destination: Path) -> Path:
    base_ignored = shutil.ignore_patterns(
        ".git",
        ".trustchain",
        ".pytest_cache",
        "__pycache__",
        ".venv",
        "dist",
        "build",
        "*.egg-info",
        "*.pyc",
    )
    def ignored(directory, names):
        excluded = set(base_ignored(directory, names))
        if Path(directory) == ROOT / ".apatch":
            excluded.update(set(names) - {"sandbox.json", "enforcement.json", "conformance.json"})
        return excluded

    source = destination / "source"
    shutil.copytree(ROOT, source, ignore=ignored)
    return source


def _assert_distribution_members(names: set[str]) -> None:
    required = {
        "apatch/__init__.py",
        "apatch/extensions/host.py",
        "apatch/extensions/schemas/manifest-v1.json",
        "apatch/work_item_acceptance.py",
        "apatch_search_workflows/__init__.py",
        "apatch_search_workflows/slug_intake.py",
        "apatch_search_workflows/slug_ratify.py",
    }
    for suffix in required:
        assert any(name.endswith(suffix) for name in names), suffix

    lowered = "\n".join(sorted(names)).lower()
    for forbidden in (
        "apatch_pro/",
        "trustchain_pro/",
        "edcher_search/",
        "avatar_contract/",
        "docs/ct_ckba_036_2017/",
        "/.env",
    ):
        assert forbidden not in lowered


def test_source_license_matches_trustchain_oss() -> None:
    import hashlib

    license_bytes = (ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == TRUSTCHAIN_MIT_SHA256

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    # PEP 639 represents SPDX licenses as strings and omits legacy license classifiers.
    assert project["license"] == "MIT"
    assert not any(item.startswith("License ::") for item in project["classifiers"])
    # No `avatar` extra: an extra can reach avatar-contract only by a direct URL,
    # and an index refuses a distribution whose metadata carries one. The pin stays
    # on record in the file so the exact MIT contract commit is not lost.
    assert "avatar" not in project.get("optional-dependencies", {})
    assert AVATAR_CONTRACT_COMMIT in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # TrustChain is part of every APatch installation: signed contract evidence is
    # a base invariant, not an extra users must discover after MCP startup fails.
    assert "trustchain>=3.3.0" in project["dependencies"]
    assert project["optional-dependencies"]["trustchain"] == []
    assert "wheel>=0.43" in project["optional-dependencies"]["dev"]


def test_public_metadata_and_readme_links_target_oss_repository() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    public_root = "https://github.com/petro1eum/apatch-oss"
    assert project["urls"] == {
        "Homepage": public_root,
        "Documentation": public_root + "#readme",
        "Repository": public_root,
        "Issues": public_root + "/issues",
    }

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    markdown_targets = [
        chunk.split(")", 1)[0]
        for chunk in readme.split("](")[1:]
    ]
    relative = [
        target for target in markdown_targets
        if not target.startswith(("https://", "http://", "#", "mailto:"))
    ]
    assert relative == []
    assert public_root + "/blob/main/docs/README.md" in markdown_targets


def test_wheel_and_sdist_publish_mit_without_private_inputs(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    out_dir = tmp_path / "dist"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--sdist",
            "--outdir",
            str(out_dir),
        ],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    wheels = list(out_dir.glob("*.whl"))
    sdists = list(out_dir.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = set(archive.namelist())
        consumer_assets = {
            name.removeprefix("apatch/_consumer_assets/"): archive.read(name)
            for name in wheel_names if name.startswith("apatch/_consumer_assets/")
        }
        assert len(consumer_assets) == 20
        metadata_name = next(name for name in wheel_names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        embedded_license = next(
            name for name in wheel_names if name.endswith(".dist-info/licenses/LICENSE")
        )
        assert archive.read(embedded_license) == (ROOT / "LICENSE").read_bytes()

    with tarfile.open(sdists[0], "r:gz") as archive:
        sdist_names = {member.name for member in archive.getmembers() if member.isfile()}
        # Public source distributions must contain the actual documentation and
        # frozen test input, not just a README that points at unavailable files.
        for required in (
            "/README.pypi.md", "/docs/README.md", "/docs/getting-started.md",
            "/docs/specs/SPEC-SDD-INTEGRITY-1.md",
            "/docs/public-source-portability.json",
            "/tests/fixtures/public_contracts/RFP-044-owner-frozen.md",
            "/.apatch/enforcement.json", "/.apatch/sandbox.json",
            "/PUBLIC-SOURCE-MANIFEST.json",
        ):
            assert any(name.endswith(required) for name in sdist_names), required
        assert not any("/.apatch/agent-identity.json" in name for name in sdist_names)
        assert not any("/.trustchain/" in name for name in sdist_names)
        frozen_name = next(name for name in sdist_names if name.endswith("/tests/fixtures/public_contracts/RFP-044-owner-frozen.md"))
        import hashlib
        assert hashlib.sha256(archive.extractfile(frozen_name).read()).hexdigest() == (
            "deb673de57fef35d0f2369581641164c11645bc8d9a8232dab3abf172ce743ae"
        )
        license_name = next(name for name in sdist_names if name.endswith("/LICENSE"))
        extracted = archive.extractfile(license_name)
        assert extracted is not None
        assert extracted.read() == (ROOT / "LICENSE").read_bytes()
        prefix = license_name.removesuffix("LICENSE")
        for relative, content in consumer_assets.items():
            member = archive.extractfile(prefix + relative)
            assert member is not None, relative
            assert member.read() == content == (ROOT / relative).read_bytes()

    assert "License-Expression: MIT" in metadata
    assert "Classifier: License :: OSI Approved :: MIT License" not in metadata
    assert "License-File: LICENSE" in metadata
    assert "Requires-Dist: trustchain>=3.3.0" in metadata
    # The upload precondition, not a style rule: an index rejects any distribution
    # whose metadata names a dependency by URL instead of by name. Until this held,
    # apatch could be built and installed by hand but never published.
    direct = [
        line
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:") and ("git+" in line or " @ " in line)
    ]
    assert not direct, direct

    _assert_distribution_members(wheel_names)
    _assert_distribution_members(sdist_names)


def test_installed_wheel_initializes_complete_consumer(tmp_path: Path) -> None:
    """Exercise wheel bytes in an isolated install layout, not the source tree."""
    source = _copy_source(tmp_path)
    out_dir = tmp_path / "dist"
    built = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel",
         "--outdir", str(out_dir)],
        cwd=source, capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheel = next(out_dir.glob("*.whl"))
    installed = tmp_path / "installed" / "site-packages"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        _assert_distribution_members(names)
        # APatch is a purelib wheel: no relocation scheme or generated launcher
        # is needed to exercise its registered CLI callable in this test layout.
        assert not any(".data/" in name for name in names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts
                   for name in names)
        archive.extractall(installed)
    assert not (installed / "docs").exists()
    assert not (installed / "scripts").exists()

    bootstrap = (
        "import sys,pathlib;"
        "sys.path.insert(0,sys.argv.pop(1));"
        "import apatch;"
        "assert pathlib.Path(apatch.__file__).resolve().is_relative_to("
        "pathlib.Path(sys.path[0]).resolve());"
        "from apatch.cli import cli;cli()"
    )
    base = [sys.executable, "-I", "-c", bootstrap, str(installed),
            "init-consumer", "--no-with-mcp", "--with-ci",
            "--with-sandbox", "--with-enforcement", "--with-devcontainer",
            "--with-arch-rules", "--profile", "frontend"]
    consumer = tmp_path / "consumer"
    result = subprocess.run(
        base + ["--target-dir", str(consumer)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    resources = {
        "AGENTS.md": "docs/AGENTS.template.md",
        ".github/workflows/apatch.yml": "docs/ci/github-action-apatch.yml",
        "scripts/ci/apatch-sandbox-gate.sh": "scripts/ci/apatch-sandbox-gate.sh",
        "scripts/hooks/pre-commit-trustchain.sh": "scripts/hooks/pre-commit-trustchain.sh",
        ".cursor/hooks/apatch-deny-direct-edit.sh": "scripts/cursor-hooks/apatch-deny-direct-edit.sh",
        ".cursor/hooks/apatch-deny-shell-mutate.sh": "scripts/cursor-hooks/apatch-deny-shell-mutate.sh",
        ".cursor/hooks/apatch-deny-mcp-mutate.sh": "scripts/cursor-hooks/apatch-deny-mcp-mutate.sh",
        ".cursor/hooks.json": "scripts/cursor-hooks/hooks.json",
        ".devcontainer/devcontainer.json": "docs/devcontainer/devcontainer.json",
        "manifests/arch-rules.yaml": "docs/manifests/arch-rules.example.yaml",
        "manifests/semantic-verify.yaml": "docs/manifests/semantic-verify.example.yaml",
        "manifests/engineering-pipeline.example.json": "docs/manifests/engineering-pipeline.example.json",
        "manifests/PROFILE.frontend.md": "docs/profiles/frontend.md",
        "docs/design-partner-playbook.md": "docs/design-partner-playbook.md",
    }
    for destination, origin in resources.items():
        target = consumer / destination
        assert target.is_file(), destination
        asset = installed / "apatch/_consumer_assets" / origin
        assert asset.read_bytes() == (ROOT / origin).read_bytes(), origin
        if destination != "AGENTS.md":
            assert target.read_bytes() == asset.read_bytes(), destination
        if target.suffix == ".sh":
            assert target.stat().st_mode & 0o111, destination
    for path in [".apatch/sandbox.json", ".apatch/enforcement.json"]:
        assert (consumer / path).is_file(), path

    # Unrelated adjacent docs must never shadow an installed package resource.
    decoy = installed / "docs/AGENTS.template.md"
    decoy.parent.mkdir()
    decoy.write_text("UNRELATED_ADJACENT_DOC\n")
    agents = consumer / "AGENTS.md"
    project_marker = "<!-- apatch:project:start -->"
    assert project_marker in agents.read_text()
    agents.write_text(agents.read_text().replace(
        project_marker, project_marker + "\nKEEP_PROJECT_CONTEXT\n",
    ))
    ci = consumer / ".github/workflows/apatch.yml"
    ci.write_text(ci.read_text() + "\n# KEEP_USER_CI\n")
    refreshed = subprocess.run(
        base + ["--target-dir", str(consumer), "--refresh-agents"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
    assert "KEEP_PROJECT_CONTEXT" in agents.read_text()
    assert "UNRELATED_ADJACENT_DOC" not in agents.read_text()
    assert ci.read_text().endswith("# KEEP_USER_CI\n")

    (installed / "apatch/_consumer_assets/docs/AGENTS.template.md").unlink()
    untouched = tmp_path / "must-not-be-created"
    failed = subprocess.run(
        base + ["--target-dir", str(untouched)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert failed.returncode != 0
    assert "consumer resources" in failed.stdout + failed.stderr
    assert not untouched.exists()
