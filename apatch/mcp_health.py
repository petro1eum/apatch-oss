"""MCP install/path diagnostics — canonical ``.apatch/mcp.json`` per project."""

from __future__ import annotations

import importlib.util
from importlib import metadata
from functools import lru_cache
import sysconfig
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from apatch import __version__


def mcp_extra_installed() -> bool:
    return importlib.util.find_spec("mcp") is not None


@lru_cache(maxsize=16)
def _equivalent_base_python(candidate: str, prefix: str, purelib: str, platlib: str) -> bool:
    """Binary aliases are safe only when the isolated Python environment agrees."""
    code = (
        "import json,os,sys,sysconfig; print(json.dumps(["
        "sys.prefix == sys.base_prefix, os.path.realpath(sys.prefix),"
        "os.path.realpath(sysconfig.get_path('purelib')),"
        "os.path.realpath(sysconfig.get_path('platlib'))]))"
    )
    try:
        proc = subprocess.run([candidate, "-I", "-c", code], capture_output=True,
                              text=True, timeout=3, check=False)
        return proc.returncode == 0 and json.loads(proc.stdout) == [True, prefix, purelib, platlib]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _executable_path(command: str) -> str:
    """Keep the final symlink: resolving it discards venv identity."""
    selected = command if os.path.isabs(command) else (shutil.which(command) or command)
    return os.path.abspath(selected)


def _stable_mcp_python_command() -> str:
    """Preserve venv identity; normalize only proven equivalent base Python paths."""
    executable = os.path.abspath(sys.executable)
    if sys.prefix != sys.base_prefix or hasattr(sys, "real_prefix"):
        return executable
    exe_real = os.path.realpath(executable)
    base = os.path.basename(exe_real)
    if base.startswith("python"):
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = os.path.join(prefix, base)
            if (os.path.isfile(candidate) and os.path.realpath(candidate) == exe_real
                    and _equivalent_base_python(candidate, os.path.realpath(sys.prefix),
                        os.path.realpath(sysconfig.get_path("purelib")),
                        os.path.realpath(sysconfig.get_path("platlib")))):
                return candidate
    return executable

def _pip_install_flags() -> str:
    if sys.platform == "darwin" and "/opt/homebrew/" in sys.executable:
        return " --break-system-packages"
    return ""


def _recommended_install_command(workspace: Optional[str] = None) -> str:
    py = _stable_mcp_python_command()
    flags = _pip_install_flags()
    if workspace and is_apatch_source_workspace(workspace):
        root = os.path.abspath(workspace)
        return (
            f"{py} -m pip install{flags} "
            f'-e "{root}[mcp,dev,yaml]"'
        )
    return f"{py} -m pip install{flags} 'apatch[mcp]'"


def is_apatch_source_workspace(workspace: str) -> bool:
    """True when workspace is the apatch tool repo (dogfood / core dev)."""
    root = Path(os.path.abspath(workspace))
    return (root / "pyproject.toml").is_file() and (root / "apatch" / "__init__.py").is_file()


def mcp_tool_catalog() -> Dict[str, Any]:
    """Registered MCP tool names + fingerprint (for doctor / stale-server detection)."""
    if not mcp_extra_installed():
        return {"count": 0, "tools": [], "fingerprint": ""}
    try:
        import hashlib

        import apatch.mcp.server as mcp_server

        tm = getattr(mcp_server.mcp, "_tool_manager", None)
        names = sorted(getattr(tm, "_tools", {}) or {})
        fp = hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]
        return {"count": len(names), "tools": names, "fingerprint": fp}
    except Exception:
        return {"count": 0, "tools": [], "fingerprint": ""}


def _mcp_fingerprint_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), ".apatch", "mcp_tool_fingerprint.json")


def read_stored_mcp_fingerprint(workspace: str) -> Dict[str, Any]:
    path = _mcp_fingerprint_path(workspace)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_mcp_fingerprint(workspace: str, *, catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Persist tool catalog fingerprint when MCP starts (detect stale IDE cache)."""
    cat = catalog or mcp_tool_catalog()
    from apatch.mcp.profiles import mcp_profile_name
    from apatch.path_leases import writer_protocol_status

    protocol = writer_protocol_status(workspace)
    payload = {
        "fingerprint": cat.get("fingerprint") or "",
        "tool_count": cat.get("count") or 0,
        "apatch_version": __version__,
        "mcp_profile": mcp_profile_name(),
        "writer_protocol_version": protocol["writer_protocol_version"],
        "path_lease_protocol": protocol["protocol"],
        "path_lease_api": protocol["path_lease_api"],
        "runtime_path": protocol["runtime"]["apatch_path"],
        "runtime_pid": protocol["runtime"]["pid"],
    }
    path = _mcp_fingerprint_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return payload


def build_tooling_refresh(workspace: str) -> Dict[str, Any]:
    """Human-only steps after apatch source changes — agents embed this in doctor/apply responses."""
    workspace = os.path.abspath(workspace)
    if not is_apatch_source_workspace(workspace):
        return {"applies": False}
    catalog = mcp_tool_catalog()
    stored = read_stored_mcp_fingerprint(workspace)
    if (
        stored.get("fingerprint") == catalog.get("fingerprint")
        and int(stored.get("tool_count") or 0) == int(catalog.get("count") or 0)
    ):
        return {
            "applies": False,
            "tool_count": catalog.get("count"),
            "fingerprint": catalog.get("fingerprint"),
            "note": "MCP catalog matches last server start; IDE tool count may lag — use apatch_spec_lint for doc+needles+rfp gates.",
        }
    pip_cmd = (
        f"{_stable_mcp_python_command()} -m pip install{_pip_install_flags()} "
        f'-e "{workspace}[mcp,dev,yaml]"'
    )
    return {
        "applies": True,
        "reason": (
            "apatch source repo: the running MCP server keeps old Python modules in memory "
            "until restarted. Shell pip/MCP restart are human-only (sandbox enforce)."
        ),
        "human_only": True,
        "agent_must_not": [
            "pip install / python -m pip (blocked by sandbox)",
            "Restart MCP server from agent",
        ],
        "agent_should": (
            "Copy human_steps below to the user and wait. Do not retry pip in shell."
        ),
        "human_steps": [
            pip_cmd,
            "Restart apatch MCP in your IDE (Cursor: Settings → MCP; Antigravity: MCP Store / MCP settings)",
        ],
        "pip_command": pip_cmd,
        "mcp_restart": "Restart apatch MCP server in Cursor or Antigravity (not a shell command)",
        "note": (
            "For .py-only edits, MCP restart alone is often enough; "
            "pip -e refreshes metadata and entry points when pyproject.toml changed."
        ),
    }


def self_edit_restart_signal(workspace: str, applied_paths) -> Optional[Dict[str, Any]]:
    """RFP-027 U27-H: flag MCP restart when applied files touch apatch modules the
    running server already imported (pure-logic edits the fingerprint check misses)."""
    import sys

    if not is_apatch_source_workspace(os.path.abspath(workspace)):
        return None
    touched = []
    for raw in applied_paths or []:
        norm = str(raw).replace("\\", "/")
        if not norm.endswith(".py") or "apatch/" not in norm:
            continue
        mod = norm[norm.index("apatch/"):][:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        if mod in sys.modules:
            touched.append(mod)
    if not touched:
        return None
    return {
        "restart_required": True,
        "reason": (
            "applied changes touch apatch modules already loaded by the running MCP "
            "server; restart MCP for them to take effect (sandbox: human-only)."
        ),
        "stale_modules": sorted(set(touched)),
        "human_steps": [
            "Restart apatch MCP server in your IDE (.py-only edit — pip not required "
            "unless pyproject.toml changed)."
        ],
    }


def recommended_mcp_server_block(workspace: Optional[str] = None) -> Dict[str, Any]:
    """Canonical apatch MCP server entry (same interpreter as ``pip install``)."""
    profile = "compact"
    guidance = "doctor_only"
    if workspace and is_apatch_source_workspace(workspace):
        guidance = "full"
    return {
        "command": _stable_mcp_python_command(),
        "args": ["-m", "apatch.mcp.launcher"],
        "env": {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "APATCH_MCP_GUIDANCE": guidance,
            "APATCH_MCP_PROFILE": profile,
            "APATCH_LANE": "auto",
        },
    }


def recommended_cursor_mcp_json(workspace: Optional[str] = None) -> Dict[str, Any]:
    return {"mcpServers": {"apatch": recommended_mcp_server_block(workspace)}}


def canonical_mcp_config_path(workspace: str) -> str:
    """Project-local MCP config (same tree as sandbox, session_state, …)."""
    return os.path.join(os.path.abspath(workspace), ".apatch", "mcp.json")


def recommended_ide_mcp_server_block(workspace: Optional[str] = None) -> Dict[str, Any]:
    """Minimal IDE entry — reads ``.apatch/mcp.json`` via ``workspace_launcher``."""
    env: Dict[str, str] = {}
    if workspace:
        env["APATCH_WORKSPACE"] = os.path.abspath(workspace)
    return {
        "command": _stable_mcp_python_command(),
        "args": ["-m", "apatch.mcp.workspace_launcher"],
        "env": env,
    }


def recommended_ide_mcp_json() -> Dict[str, Any]:
    return {"mcpServers": {"apatch": recommended_ide_mcp_server_block()}}


_LAUNCHER_ARG_SETS = (
    ["-m", "apatch.mcp.launcher"],
    ["-m", "apatch.mcp.workspace_launcher"],
)


def resolve_mcp_command() -> Optional[str]:
    """Backward-compatible PATH lookup (may differ from IDE config)."""
    found = shutil.which("apatch-mcp")
    if found:
        return found
    scripts_dir = os.path.join(os.path.dirname(sys.executable), "apatch-mcp")
    if os.path.isfile(scripts_dir):
        return scripts_dir
    return None


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _shebang_interpreter(script_path: str) -> Optional[str]:
    try:
        with open(script_path, encoding="utf-8") as f:
            first = f.readline().strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    interp = first[2:].strip()
    if " " in interp:
        interp = interp.split()[0]
    return interp if interp else None


def _config_command_matches(cfg: Dict[str, Any], workspace: Optional[str] = None) -> bool:
    rec = recommended_mcp_server_block(workspace)
    command = cfg.get("command")
    if not command:
        return False
    rec_real = _executable_path(rec["command"])
    try:
        cmd_real = _executable_path(command)
    except OSError:
        return False
    if cmd_real == rec_real:
        pass
    elif os.path.basename(str(command)) == "apatch-mcp" and os.path.isfile(command):
        shebang = _shebang_interpreter(command)
        if not shebang or _executable_path(shebang) != rec_real:
            return False
    else:
        return False
    args = [arg for arg in (cfg.get("args") or []) if arg != "-I"]
    if args in _LAUNCHER_ARG_SETS:
        return True
    return args == rec["args"] or args == []


def _config_env_matches(cfg: Dict[str, Any], workspace: Optional[str] = None) -> bool:
    rec_env = recommended_mcp_server_block(workspace).get("env") or {}
    env = cfg.get("env") or {}
    for key, val in rec_env.items():
        actual = env.get(key)
        if key == "APATCH_MCP_PROFILE":
            if actual not in {"compact", "core", "spec", "full"}:
                return False
            continue
        if actual != val:
            return False
    return True


def _config_matches_recommended(cfg: Dict[str, Any], workspace: Optional[str] = None) -> bool:
    return _config_command_matches(cfg, workspace) and _config_env_matches(cfg, workspace)


def discover_mcp_configs(workspace: str) -> List[Dict[str, Any]]:
    """Canonical project config only (``.apatch/mcp.json``)."""
    path = canonical_mcp_config_path(workspace)
    if not os.path.isfile(path):
        return [
            {
                "host": "apatch",
                "scope": "canonical",
                "path": path,
                "error": "missing — run: apatch mcp sync --target-dir .",
            }
        ]
    try:
        data = _config_for_update(path)
    except ValueError as exc:
        return [{"host": "apatch", "scope": "canonical", "path": path, "error": str(exc)}]
    apatch_cfg = (data.get("mcpServers") or {}).get("apatch")
    if not apatch_cfg:
        return [
            {
                "host": "apatch",
                "scope": "canonical",
                "path": path,
                "error": "missing apatch entry",
            }
        ]
    row = {
        "host": "apatch",
        "scope": "canonical",
        "path": path,
        "command": apatch_cfg.get("command"),
        "args": apatch_cfg.get("args") or [],
        "env": apatch_cfg.get("env") or {},
    }
    row["command_ok"] = _config_command_matches(apatch_cfg, workspace)
    row["env_ok"] = _config_env_matches(apatch_cfg, workspace)
    row["matches_recommended"] = row["command_ok"] and row["env_ok"]
    row["has_apatch"] = True
    return [row]


def discover_cursor_mcp_configs(workspace: str) -> List[Dict[str, Any]]:
    """Backward-compatible Cursor-only view."""
    return [c for c in discover_mcp_configs(workspace) if c.get("host") == "cursor"]


def _discover_apatch_mcp_binaries() -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    path_dirs = (os.environ.get("PATH") or "").split(os.pathsep)
    path_dirs.append(os.path.dirname(sys.executable))
    for d in path_dirs:
        if not d:
            continue
        candidate = os.path.join(d, "apatch-mcp")
        if os.path.isfile(candidate):
            real = _executable_path(candidate)
            if real not in seen:
                seen.add(real)
                out.append(real)
    return out


_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_mcp_probe_cache() -> None:
    """Drop cached interpreter probe results (tests)."""
    _PROBE_CACHE.clear()


def _probe_interpreter(python_exe: str, *, timeout: float = 8.0,
                       env: Optional[Dict[str, str]] = None,
                       cwd: Optional[str] = None) -> Dict[str, Any]:
    # Fresh on every check: installed distributions may change without the Python
    # executable changing. Never key package health by a shared binary realpath.
    executable = _executable_path(python_exe)
    code = (
        "import json,sys,os; from importlib import metadata\n"
        "out={'python':sys.version.split()[0],'interpreter':sys.executable,"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
        "'binary_realpath':os.path.realpath(sys.executable),'ok':False}\n"
        "try:\n"
        " import apatch\n"
        " out['apatch_version']=apatch.__version__\n"
        " out['apatch_path']=apatch.__file__\n"
        " out['installed_version']=metadata.version('apatch')\n"
        " if out['apatch_version'] != out['installed_version']:\n"
        "  raise ValueError('loaded/installed APatch version mismatch')\n"
        " import apatch.mcp.server as s\n"
        " from apatch.mcp.profiles import apply_tool_profile\n"
        " apply_tool_profile(s.mcp)\n"
        " out['mcp_ok']=s.mcp is not None\n"
        " tm=getattr(s.mcp,'_tool_manager',None)\n"
        " tools=getattr(tm,'_tools',None) if tm else None\n"
        " out['tool_count']=len(tools) if tools else 0\n"
        " out['ok']=bool(out.get('mcp_ok') and out.get('tool_count',0)>0)\n"
        "except Exception as e:\n"
        " out['error']=str(e)\n"
        "print(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run([executable, "-I", "-c", code], capture_output=True,
                              text=True, timeout=timeout, check=False, env=env, cwd=cwd)
        if proc.returncode != 0:
            return {"interpreter": executable, "ok": False,
                    "error": "isolated interpreter probe exited {}".format(proc.returncode)}
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
            raise ValueError("malformed interpreter probe")
        data["selected_executable"] = executable
        return data
    except subprocess.TimeoutExpired:
        return {"interpreter": executable, "ok": False, "timed_out": True,
                "error": "isolated interpreter probe timed out"}
    except (ValueError, IndexError, OSError) as exc:
        return {"interpreter": executable, "ok": False, "error": str(exc)}

def _probe_current_interpreter() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "interpreter": os.path.abspath(sys.executable),
        "binary_realpath": os.path.realpath(sys.executable),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "ok": False,
    }
    if not mcp_extra_installed():
        out["error"] = "mcp extra not installed — pip install 'apatch[mcp]'"
        return out
    try:
        import apatch.mcp.server as mcp_server

        import apatch as apatch_pkg

        out["apatch_version"] = __version__
        out["apatch_path"] = apatch_pkg.__file__
        out["installed_version"] = metadata.version("apatch")
        if out["installed_version"] != __version__:
            raise ValueError("loaded/installed APatch version mismatch")
        out["mcp_ok"] = mcp_server.mcp is not None
        tool_manager = getattr(mcp_server.mcp, "_tool_manager", None)
        tools = getattr(tool_manager, "_tools", None) if tool_manager else None
        out["tool_count"] = len(tools) if tools else 0
        out["ok"] = bool(out["mcp_ok"] and out["tool_count"] > 0)
    except Exception as e:
        out["error"] = str(e)
    return out


def _config_warning(cfg: Dict[str, Any]) -> str:
    host = cfg.get("host", "unknown")
    scope = cfg.get("scope", "unknown")
    path = cfg.get("path", "")
    cmd = cfg.get("command")
    if cfg.get("error"):
        return f"{host} MCP config ({scope}) invalid JSON at {path!r}"
    if not cfg.get("command_ok"):
        return (
            f"{host} MCP config ({scope}) uses {cmd!r} at {path!r} — "
            "recommended: python -m apatch.mcp.launcher (same interpreter as pip install)"
        )
    if not cfg.get("env_ok"):
        missing = [
            k
            for k, v in (recommended_mcp_server_block().get("env") or {}).items()
            if (cfg.get("env") or {}).get(k) != v
        ]
        return (
            f"{host} MCP config ({scope}) at {path!r} missing env {missing!r} — "
            "run: apatch mcp sync --target-dir . --force"
        )
    return ""


def build_mcp_health(workspace: str) -> Dict[str, Any]:
    """Full MCP diagnostics for doctor / CI."""
    workspace = os.path.abspath(workspace)
    recommended = recommended_mcp_server_block(workspace)
    current = _probe_current_interpreter()
    configs = discover_mcp_configs(workspace)
    binaries = _discover_apatch_mcp_binaries()

    candidates: List[Dict[str, Any]] = []
    probed_paths: set[str] = set()

    def add_candidate(label: str, python_exe: Optional[str], *, script: Optional[str] = None) -> None:
        if not python_exe:
            return
        real = _executable_path(python_exe)
        if real in probed_paths:
            return
        probed_paths.add(real)
        probe = _probe_interpreter(real)
        probe["label"] = label
        if script:
            probe["script"] = script
        candidates.append(probe)

    from apatch.mcp.stdio_guard import is_mcp_stdio_mode

    if is_mcp_stdio_mode() and current.get("ok"):
        inproc = dict(current)
        inproc["label"] = "doctor_interpreter"
        candidates.append(inproc)
        probed_paths.add(_executable_path(sys.executable))
    else:
        add_candidate("doctor_interpreter", sys.executable)
    if not current.get("ok"):
        for script in binaries:
            add_candidate("apatch-mcp", _shebang_interpreter(script), script=script)

    warnings: List[str] = []
    if not mcp_extra_installed():
        warnings.append(
            "MCP SDK missing — HUMAN: "
            f"{sys.executable} -m pip install -e '/path/to/apatch[mcp]' then restart apatch MCP. "
            "AGENT: do not run pip; report to user."
        )
    elif not current.get("ok"):
        warnings.append(
            (current.get("error") or "Current Python cannot load apatch MCP server")
            + " — HUMAN: reinstall apatch[mcp] + restart MCP. AGENT: do not shell-retry."
        )

    unique_apatch_paths = {
        c.get("apatch_path")
        for c in candidates
        if c.get("ok") and c.get("apatch_path")
    }
    if len(unique_apatch_paths) > 1:
        warnings.append(
            "Multiple apatch installs detected across apatch-mcp binaries — "
            "pin IDE MCP config to recommended_mcp_config (python -m apatch.mcp.launcher)"
        )

    for cfg in configs:
        msg = _config_warning(cfg)
        if msg:
            warnings.append(msg)

    canonical = next((c for c in configs if c.get("scope") == "canonical"), None)
    if canonical and not canonical.get("error"):
        configured_ok = True
    elif canonical and canonical.get("error"):
        warnings.append(f"{canonical['error']} ({canonical.get('path')})")
        configured_ok = False
    else:
        warnings.append("No .apatch/mcp.json — run: apatch mcp sync --target-dir .")
        configured_ok = False

    install_cmd = _recommended_install_command(workspace)
    if is_mcp_stdio_mode():
        configured_runtime = {
            "ok": False, "status": "not_checked_in_stdio",
            "canonical_config": canonical_mcp_config_path(workspace),
            "scope": "current_process_only",
        }
        warnings.append(
            "Configured-child readiness was not checked inside this running MCP. "
            "Run 'apatch mcp check --target-dir <workspace>' for an isolated bootstrap check; "
            "host tool availability is a separate client observation."
        )
    elif canonical and not canonical.get("error"):
        from apatch.mcp.runtime_probe import probe_configured_server
        configured_runtime = probe_configured_server(workspace)
        if not configured_runtime.get("ok"):
            warnings.append("Configured MCP child failed: " + str(
                configured_runtime.get("error") or configured_runtime.get("status")))
    else:
        configured_runtime = {"ok": False, "status": "config_invalid",
                              "canonical_config": canonical_mcp_config_path(workspace)}

    ok = bool(configured_ok and configured_runtime.get("ok"))

    from apatch.path_leases import writer_protocol_status

    writer_protocol = writer_protocol_status(workspace)
    stored_fingerprint = read_stored_mcp_fingerprint(workspace)
    return {
        "ok": ok,
        "mcp_extra_installed": mcp_extra_installed(),
        "doctor_interpreter": current,
        "configured_runtime": configured_runtime,
        "health_scope": "configured_command_in_disposable_workspace",
        "host_tool_availability": "not_observable",
        "recommended_mcp_config": recommended,
        "recommended_install": install_cmd,
        "mcp_command_path": resolve_mcp_command(),
        "ide_configs": configs,
        "cursor_configs": discover_cursor_mcp_configs(workspace),
        "candidates": candidates,
        "warnings": warnings,
        "canonical_mcp_path": canonical_mcp_config_path(workspace),
        "recommended_ide_mcp_config": recommended_ide_mcp_server_block(),
        "ide_setup_hint": "Pin IDE Python and canonical .apatch/mcp.json; use [-I, -m, apatch.mcp.workspace_launcher].",
        "sync_command": "apatch mcp sync --target-dir .",
        "mcp_tool_catalog": mcp_tool_catalog(),
        "stored_mcp_fingerprint": stored_fingerprint,
        "writer_protocol": writer_protocol,
        "tooling_refresh": build_tooling_refresh(workspace),
    }


def _config_for_update(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"mcpServers": {}}
    return _validate_config(_read_json(path), path)


def _validate_config(data: Any, path: str) -> Dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError("Invalid MCP config; refusing to overwrite: {}".format(path))
    servers = data.setdefault("mcpServers", {})
    block = servers.get("apatch", {})
    if not isinstance(block, dict) or not isinstance(block.get("env", {}), dict):
        raise ValueError("Invalid apatch block; refusing to overwrite: {}".format(path))
    if ("command" in block and not isinstance(block["command"], str)) or (
            not isinstance(block.get("args", []), list)
            or any(not isinstance(arg, str) for arg in block.get("args", []))):
        raise ValueError("Invalid command/args; refusing to overwrite: {}".format(path))
    profile = block.get("env", {}).get("APATCH_MCP_PROFILE")
    if profile is not None and (not isinstance(profile, str) or profile not in {"compact", "core", "spec", "full"}):
        raise ValueError("Invalid MCP profile; review the config: {}".format(path))
    return data


def _merged_server_block(existing: Dict[str, Any], recommended: Dict[str, Any],
                         *, profile: Optional[str] = None) -> Dict[str, Any]:
    updated = dict(existing)
    updated["command"] = recommended["command"]
    args = list(recommended["args"])
    if "-I" in (existing.get("args") or []):
        args.insert(0, "-I")
    updated["args"] = args
    updated["env"] = dict(recommended.get("env") or {}, **(existing.get("env") or {}))
    if profile is not None:
        if profile not in {"compact", "core", "spec", "full"}:
            raise ValueError("Unknown MCP profile: {}".format(profile))
        updated["env"]["APATCH_MCP_PROFILE"] = profile
    return updated


def _save_mcp_config(path: str, payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if os.path.isfile(path) and Path(path).read_text(encoding="utf-8") == text:
        return path
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Same-directory replace: readers see the old or the complete new config.
    import tempfile
    import stat
    mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o600
    fd, temp = tempfile.mkstemp(prefix=".apatch-mcp-", dir=parent or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return path


def _write_mcp_config_at(path: str, *, overwrite: bool = False,
                         workspace: Optional[str] = None,
                         profile: Optional[str] = None) -> str:
    payload = _config_for_update(path)
    existing = payload["mcpServers"].get("apatch", {})
    if existing and not overwrite:
        if _config_matches_recommended(existing, workspace) and (
                profile is None or profile == existing.get("env", {}).get("APATCH_MCP_PROFILE")):
            return path
        raise FileExistsError("apatch entry already exists in {}; use --force to update".format(path))
    payload["mcpServers"]["apatch"] = _merged_server_block(
        existing, recommended_mcp_server_block(workspace), profile=profile)
    return _save_mcp_config(path, payload)

def write_project_mcp_config(target_dir: str, *, overwrite: bool = False,
                             profile: Optional[str] = None) -> str:
    """Write ``.apatch/mcp.json`` with the canonical apatch server block."""
    root = os.path.abspath(target_dir)
    path = canonical_mcp_config_path(root)
    written = _write_mcp_config_at(path, overwrite=overwrite, workspace=root, profile=profile)
    try:
        from apatch.artifact_governance import register_on_write

        register_on_write(
            root,
            written,
            class_name="STATE",
            created_by_tool="apatch_mcp_sync",
            reason="canonical MCP server config",
        )
    except Exception:
        pass
    return written


def default_ide_stub_paths(workspace: str) -> List[str]:
    """IDE MCP files to update: project ``.cursor/`` if present; user paths only if file exists."""
    workspace = os.path.abspath(workspace)
    home = os.path.expanduser("~")
    out: List[str] = []
    if os.path.isdir(os.path.join(workspace, ".cursor")):
        out.append(os.path.join(workspace, ".cursor", "mcp.json"))
    for user_rel in (
        ".cursor/mcp.json",
        ".gemini/config/mcp_config.json",
        ".gemini/antigravity-ide/mcp_config.json",
    ):
        p = os.path.join(home, *user_rel.split("/"))
        if os.path.isfile(p) and p not in out:
            out.append(p)
    return out


def write_user_mcp_config(*, overwrite: bool = False) -> str:
    """Legacy: merge IDE stub into ``~/.cursor/mcp.json``."""
    path = os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json")
    return write_ide_mcp_stub(path, overwrite=overwrite)


def _ide_stub_upgrade_needed(apatch_cfg: Dict[str, Any]) -> bool:
    args = list(apatch_cfg.get("args") or [])
    if args == ["-m", "apatch.mcp.workspace_launcher"]:
        return False
    return args in _LAUNCHER_ARG_SETS or args == []


def write_ide_mcp_stub(path: str, *, overwrite: bool = False, workspace: Optional[str] = None) -> str:
    """Update the runtime pointer, retaining user env and unrelated block fields."""
    payload = _config_for_update(path)
    existing = payload["mcpServers"].get("apatch", {})
    recommended = recommended_ide_mcp_server_block(workspace)
    normalized_args = [arg for arg in existing.get("args", []) if arg != "-I"]
    if existing and not overwrite:
        if normalized_args == ["-m", "apatch.mcp.workspace_launcher"]:
            if (_executable_path(existing.get("command") or "") == _executable_path(recommended["command"])
                    and (not workspace or existing.get("env", {}).get("APATCH_WORKSPACE") == os.path.abspath(workspace))):
                return path
            raise FileExistsError("apatch IDE runtime differs in {}; use --force".format(path))
        if normalized_args not in _LAUNCHER_ARG_SETS and normalized_args != []:
            raise FileExistsError("apatch entry already exists in {}; use --force".format(path))
    updated = _merged_server_block(existing, recommended)
    if workspace:
        updated["env"]["APATCH_WORKSPACE"] = os.path.abspath(workspace)
    payload["mcpServers"]["apatch"] = updated
    return _save_mcp_config(path, payload)

def sync_mcp_configs(
    target_dir: str,
    *,
    overwrite: bool = False,
    ide_paths: Optional[Sequence[str]] = None,
    auto_ide: bool = True,
    profile: Optional[str] = None,
) -> List[str]:
    """Write ``.apatch/mcp.json`` and merge IDE stubs (auto-detect when ``auto_ide``)."""
    paths: List[str] = [write_project_mcp_config(target_dir, overwrite=overwrite, profile=profile)]
    stubs = (
        list(ide_paths)
        if ide_paths is not None
        else (default_ide_stub_paths(target_dir) if auto_ide else [])
    )
    root = os.path.abspath(target_dir)
    for raw in stubs:
        abs_path = os.path.abspath(os.path.expanduser(raw))
        if ide_paths is None:
            data = _config_for_update(abs_path)
            bound = data["mcpServers"].get("apatch", {}).get("env", {}).get("APATCH_WORKSPACE")
            if bound and os.path.realpath(os.path.expanduser(bound)) != os.path.realpath(root):
                continue
        paths.append(write_ide_mcp_stub(abs_path, overwrite=overwrite, workspace=root))
    return paths


def repair_mcp_config_if_needed(workspace: str) -> Optional[str]:
    """Rewrite stale apatch MCP entries in project Cursor config (legacy API)."""
    repaired = repair_mcp_configs_if_needed(workspace)
    canonical = canonical_mcp_config_path(workspace)
    for path in repaired:
        if path == canonical:
            return path
    if not repaired and not os.path.isfile(canonical):
        return write_project_mcp_config(workspace, overwrite=False)
    return repaired[0] if len(repaired) == 1 else (repaired[0] if repaired else None)


def repair_mcp_configs_if_needed(workspace: str) -> List[str]:
    """Rewrite any stale apatch entry across known IDE MCP config files."""
    workspace = os.path.abspath(workspace)
    if not mcp_extra_installed():
        return []
    current = _probe_interpreter(sys.executable)
    if not current.get("ok"):
        return []

    repaired: List[str] = []
    seen_paths: set[str] = set()
    for cfg in discover_mcp_configs(workspace):
        path = cfg.get("path")
        if not path or path in seen_paths or cfg.get("error"):
            continue
        seen_paths.add(path)
        data = _read_json(path) or {}
        apatch_cfg = (data.get("mcpServers") or {}).get("apatch")
        if apatch_cfg and not _config_matches_recommended(apatch_cfg, workspace):
            repaired.append(_write_mcp_config_at(path, overwrite=True, workspace=workspace))

    canonical = canonical_mcp_config_path(workspace)
    if not os.path.isfile(canonical) and canonical not in seen_paths:
        repaired.append(write_project_mcp_config(workspace, overwrite=False))
    return repaired
