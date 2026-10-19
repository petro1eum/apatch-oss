# apatch package

from __future__ import annotations

from pathlib import Path


def _package_version() -> str:
    """Prefer pyproject.toml in source checkout; fall back to installed metadata."""
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data["project"]["version"])
    except Exception:
        pass
    try:
        from importlib.metadata import version

        return version("apatch")
    except Exception:
        return "0.0.0"


__version__ = _package_version()


def _source_identity() -> str:
    """Identify the exact source tree present when this package was loaded."""
    import hashlib
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


# Capture before submodules load. Re-reading disk at inspection time cannot be
# mistaken for the identity of code already imported by this process.
_LOADED_SOURCE_IDENTITY = _source_identity()

