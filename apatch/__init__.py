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
