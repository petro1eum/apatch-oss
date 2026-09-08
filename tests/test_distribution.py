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
    assert "wheel>=0.43" in project["optional-dependencies"]["dev"]


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

    assert "License-Expression: MIT" in metadata
    assert "Classifier: License :: OSI Approved :: MIT License" not in metadata
    assert "License-File: LICENSE" in metadata
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
