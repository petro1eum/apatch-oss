"""Central executable and PATH resolution for doctor, verify, and subprocesses.

All toolchain discovery and verify subprocess env MUST go through this module.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Well-known binary directories (macOS / Linux dev setups).
STANDARD_BIN_DIRS: Tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    os.path.expanduser("~/.local/bin"),
)

# Tools doctor / verify care about.
TOOL_NAMES: Tuple[str, ...] = (
    "alembic",
    "prisma",
    "npm",
    "node",
    "npx",
    "cmake",
    "pytest",
    "terraform",
    "az",
    "curl",
    "git",
)

# First token in verify shell chains → executable to resolve.
_VERIFY_LEAD_TOOLS = frozenset(
    {"npm", "npx", "node", "pytest", "python", "python3", "alembic", "prisma", "cmake", "terraform"}
)


def _unique_dirs(paths: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in paths:
        norm = os.path.normpath(raw)
        if norm and norm not in seen and os.path.isdir(norm):
            seen.add(norm)
            out.append(norm)
    return out


def _read_nvmrc_version(workspace: Optional[str]) -> Optional[str]:
    if not workspace:
        return None
    nvmrc = Path(workspace).resolve() / ".nvmrc"
    if not nvmrc.is_file():
        return None
    return nvmrc.read_text(encoding="utf-8").strip().lstrip("v") or None


def _nvm_npm_bins(home: Path, nvmrc_ver: Optional[str]) -> List[str]:
    node_root = home / ".nvm" / "versions" / "node"
    if not node_root.is_dir():
        return []
    bins: List[str] = []
    for npm_bin in sorted(node_root.glob("v*/bin/npm"), reverse=True):
        if nvmrc_ver and nvmrc_ver not in npm_bin.parts[-3]:
            continue
        bins.append(str(npm_bin))
    if not bins:
        for npm_bin in sorted(node_root.glob("v*/bin/npm"), reverse=True):
            bins.append(str(npm_bin))
    return bins


def _fnm_bins(home: Path, nvmrc_ver: Optional[str]) -> List[str]:
    out: List[str] = []
    aliases = home / ".fnm" / "aliases" / "default" / "bin"
    if aliases.is_dir():
        for name in ("npm", "node", "npx"):
            p = aliases / name
            if p.is_file():
                out.append(str(p))
    node_versions = home / ".fnm" / "node-versions"
    if node_versions.is_dir():
        for npm_bin in sorted(node_versions.glob("v*/installation/bin/npm"), reverse=True):
            if nvmrc_ver and nvmrc_ver not in npm_bin.parts[-3]:
                continue
            out.append(str(npm_bin))
    return out


def _volta_bins(home: Path) -> List[str]:
    volta = home / ".volta" / "bin"
    if not volta.is_dir():
        return []
    return [str(volta / name) for name in ("npm", "node", "npx") if (volta / name).is_file()]


def _mise_shims(home: Path) -> List[str]:
    shims = home / ".local" / "share" / "mise" / "shims"
    if not shims.is_dir():
        return []
    return [str(shims / name) for name in ("npm", "node", "npx") if (shims / name).is_file()]


def _asdf_shims(home: Path) -> List[str]:
    shims = home / ".asdf" / "shims"
    if not shims.is_dir():
        return []
    return [str(shims / name) for name in ("npm", "node", "npx") if (shims / name).is_file()]


def _workspace_local_bins(workspace: Optional[str]) -> List[str]:
    if not workspace:
        return []
    root = Path(workspace).resolve()
    bins: List[str] = []
    nm = root / "node_modules" / ".bin"
    if nm.is_dir():
        for child in nm.iterdir():
            if child.is_file():
                bins.append(str(child))
    return bins


def _fixed_tool_paths(name: str) -> List[str]:
    home = Path.home()
    candidates: List[str] = []
    for prefix in STANDARD_BIN_DIRS:
        candidates.append(os.path.join(prefix, name))
    if name in ("npm", "node", "npx"):
        candidates.extend(_nvm_npm_bins(home, None))
        candidates.extend(_fnm_bins(home, None))
        candidates.extend(_volta_bins(home))
        candidates.extend(_mise_shims(home))
        candidates.extend(_asdf_shims(home))
        nvmrc_ver = None
        candidates.extend(_nvm_npm_bins(home, nvmrc_ver))
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return [path]
    return []


def build_path_prefix(workspace: Optional[str] = None) -> List[str]:
    """Directories to prepend to PATH (most specific first)."""
    home = Path.home()
    nvmrc_ver = _read_nvmrc_version(workspace)
    dirs: List[str] = []

    for path in _workspace_local_bins(workspace):
        dirs.append(os.path.dirname(path))
    for path in _nvm_npm_bins(home, nvmrc_ver):
        dirs.append(os.path.dirname(path))
    for path in _fnm_bins(home, nvmrc_ver):
        dirs.append(os.path.dirname(path))
    for path in _volta_bins(home):
        dirs.append(str(home / ".volta" / "bin"))
    for path in _mise_shims(home):
        dirs.append(os.path.dirname(path))
    for path in _asdf_shims(home):
        dirs.append(os.path.dirname(path))
    dirs.extend(STANDARD_BIN_DIRS)
    return _unique_dirs(dirs)


def _is_tool_shim(path: str, name: str) -> bool:
    """True for real CLI entrypoints, not npm's internal *-cli.js scripts."""
    base = os.path.basename(path)
    if base == name:
        return True
    if name in ("npm", "npx") and base.endswith("-cli.js"):
        return False
    return base == name


def _collect_candidates(name: str, workspace: Optional[str]) -> List[str]:
    home = Path.home()
    nvmrc_ver = _read_nvmrc_version(workspace)
    candidates: List[str] = []

    prefix = build_path_prefix(workspace)
    env_path = os.pathsep.join(prefix + ([os.environ.get("PATH", "")] if os.environ.get("PATH") else []))
    found = shutil.which(name, path=env_path)
    if found and _is_tool_shim(found, name):
        candidates.append(found)

    for path in _workspace_local_bins(workspace):
        if os.path.basename(path) == name and _is_tool_shim(path, name):
            candidates.append(path)

    if name in ("npm", "node", "npx"):
        for group in (
            _nvm_npm_bins(home, nvmrc_ver),
            _fnm_bins(home, nvmrc_ver),
            _volta_bins(home),
            _mise_shims(home),
            _asdf_shims(home),
        ):
            candidates.extend(group)
        for npm_bin in _nvm_npm_bins(home, nvmrc_ver):
            sibling = os.path.join(os.path.dirname(npm_bin), name)
            if os.path.isfile(sibling):
                candidates.append(sibling)
        for prefix_dir in STANDARD_BIN_DIRS:
            candidates.append(os.path.join(prefix_dir, name))

    seen: set[str] = set()
    ordered: List[str] = []
    for raw in candidates:
        if not raw:
            continue
        norm = os.path.normpath(raw)
        if norm in seen or not os.path.isfile(norm):
            continue
        seen.add(norm)
        ordered.append(norm)
    return ordered


def resolve_executable(name: str, *, workspace: Optional[str] = None) -> Optional[str]:
    """Resolve a single executable — canonical entry point for tool discovery."""
    if not name:
        return None

    for path in _collect_candidates(name, workspace):
        if _is_tool_shim(path, name) and os.access(path, os.X_OK):
            return path
    return None


def resolve_all_tools(workspace: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Resolve all tracked toolchain binaries for a workspace."""
    return {name: resolve_executable(name, workspace=workspace) for name in TOOL_NAMES}


def _sanitize_pythonpath(env: Dict[str, str], workspace: str) -> None:
    """Drop foreign APatch checkouts without removing unrelated user imports."""

    raw = env.get("PYTHONPATH")
    if raw is None:
        return
    running_package = Path(__file__).resolve().parent
    kept: List[str] = []
    for entry in raw.split(os.pathsep):
        lookup_root = Path(workspace) if not entry else Path(entry).expanduser()
        if not lookup_root.is_absolute():
            lookup_root = Path(workspace) / lookup_root
        candidate = lookup_root / "apatch"
        if candidate.is_dir() and candidate.resolve() != running_package:
            continue
        kept.append(entry)
    if kept:
        env["PYTHONPATH"] = os.pathsep.join(kept)
    else:
        env.pop("PYTHONPATH", None)


def build_subprocess_env(
    workspace: str,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Augment PATH so verify subprocesses find npm/node and other toolchain binaries."""
    env = dict(base_env or os.environ)
    _sanitize_pythonpath(env, workspace)
    # This policy belongs to the MCP server boundary. Verify children must be able
    # to create and address their isolated test workspaces normally.
    env.pop("APATCH_MCP_TARGET_POLICY", None)
    prefix = build_path_prefix(workspace)
    for name in TOOL_NAMES:
        path = resolve_executable(name, workspace=workspace)
        if path:
            prefix.insert(0, os.path.dirname(path))
    prefix = _unique_dirs(prefix)
    existing = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(prefix + ([existing] if existing else []))
    return env


def materialize_verify_argv(argv: Sequence[str], workspace: str) -> List[str]:
    """Argv variant of materialize: resolve a known lead tool to an absolute path."""
    out = [str(a) for a in argv]
    if out and out[0] in _VERIFY_LEAD_TOOLS:
        resolved = resolve_executable(out[0], workspace=workspace)
        if resolved:
            out[0] = resolved
    return out


def materialize_verify_command(cmd: Union[str, Sequence[str]], workspace: str) -> str:
    """Rewrite leading toolchain tokens to absolute paths (stable for agents/MCP).

    Accepts a shell string or an argv list; lists are shlex-joined for display.
    """
    if isinstance(cmd, (list, tuple)):
        import shlex

        return shlex.join(materialize_verify_argv(cmd, workspace))
    if not cmd or not cmd.strip():
        return cmd
    parts = cmd.strip().split()
    if not parts:
        return cmd
    lead = parts[0]
    if lead not in _VERIFY_LEAD_TOOLS:
        return cmd
    resolved = resolve_executable(lead, workspace=workspace)
    if not resolved:
        return cmd
    parts[0] = resolved
    return " ".join(parts)


def run_shell_verify(verify_cmd: Union[str, Sequence[str]], cwd: str) -> Tuple[bool, str]:
    """Run a verify command with centralized PATH resolution.

    ``verify_cmd`` is a shell string (run with ``shell=True``) or an argv list (run
    without a shell — no quoting pitfalls: ``['pytest', '-k', 'not slow']``).
    """
    is_argv = isinstance(verify_cmd, (list, tuple))
    if is_argv:
        argv = materialize_verify_argv([a for a in verify_cmd if str(a) != ""], cwd)
        if not argv:
            return True, ""
    elif not verify_cmd or not verify_cmd.strip():
        return True, ""
    try:
        env = build_subprocess_env(cwd)
        if is_argv:
            res = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, env=env
            )
        else:
            cmd = materialize_verify_command(verify_cmd, cwd)
            res = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True, env=env
            )
        if res.returncode == 0:
            return True, ""
        parts = [f"verify failed (exit {res.returncode})"]
        if res.stdout.strip():
            parts.append(f"stdout:\n{res.stdout.rstrip()}")
        if res.stderr.strip():
            parts.append(f"stderr:\n{res.stderr.rstrip()}")
        return False, "\n".join(parts)
    except Exception as e:
        return False, str(e)


def tool_info(name: str, path: Optional[str], version: Optional[str] = None) -> Dict[str, Any]:
    return {"found": path is not None, "path": path, "version": version}


def _version_from_cmd(
    cmd: Sequence[str],
    *,
    env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    try:
        res = subprocess.run(list(cmd), capture_output=True, text=True, timeout=5, env=env)
        out = (res.stdout or res.stderr or "").strip().splitlines()
        if out:
            return out[0][:120]
    except Exception:
        return None
    return None


def detect_tool_versions(workspace: str) -> Dict[str, Dict[str, Any]]:
    """Build doctor toolchain.tools block using centralized resolution."""
    root = str(Path(workspace).resolve())
    env = build_subprocess_env(root)
    path_env = env.get("PATH", "")
    tools: Dict[str, Dict[str, Any]] = {}
    for name in ("alembic", "prisma", "npm", "cmake", "pytest", "terraform", "az", "curl"):
        path = resolve_executable(name, workspace=root)
        version = None
        if path and name == "npm":
            version = _version_from_cmd([path, "--version"], env=env)
        elif path and name == "cmake":
            version = _version_from_cmd([path, "--version"])
        elif path and name == "alembic":
            version = _version_from_cmd([path, "--version"])
        tools[name] = tool_info(name, path, version)
    return tools
