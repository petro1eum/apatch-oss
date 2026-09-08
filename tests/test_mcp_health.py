import json
import os
import sys
import time

import pytest

# Health/repair asserts the recommended apatch.mcp.launcher is importable, which
# needs the mcp extra (Python >=3.10). Skip cleanly where mcp is absent (e.g. 3.9).
pytest.importorskip("mcp")

from apatch.mcp_health import (
    build_mcp_health,
    recommended_mcp_server_block,
    repair_mcp_config_if_needed,
    repair_mcp_configs_if_needed,
    write_project_mcp_config,
)


def test_recommended_mcp_config_uses_launcher_module():
    cfg = recommended_mcp_server_block()
    assert os.path.realpath(cfg["command"]) == os.path.realpath(sys.executable)
    assert cfg["args"] == ["-m", "apatch.mcp.launcher"]
    assert cfg["env"]["PYTHONIOENCODING"] == "utf-8"
    assert cfg["env"]["APATCH_MCP_GUIDANCE"] == "doctor_only"
    if sys.platform == "darwin" and "/opt/homebrew/" in sys.executable:
        assert "/Cellar/" not in cfg["command"]


def test_write_project_mcp_config_creates_file(tmp_path):
    path = write_project_mcp_config(str(tmp_path))
    assert path == str(tmp_path / ".apatch" / "mcp.json")
    data = json.loads((tmp_path / ".apatch" / "mcp.json").read_text(encoding="utf-8"))
    assert "apatch" in data["mcpServers"]
    assert data["mcpServers"]["apatch"]["args"] == ["-m", "apatch.mcp.launcher"]


def test_write_project_mcp_config_refuses_overwrite_without_force(tmp_path):
    apatch_d = tmp_path / ".apatch"
    apatch_d.mkdir(parents=True)
    (apatch_d / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": {"command": "/other/python", "args": []}}}),
        encoding="utf-8",
    )
    with pytest.raises(FileExistsError):
        write_project_mcp_config(str(tmp_path), overwrite=False)


def test_repair_mcp_config_preserves_explicit_valid_profile(tmp_path):
    apatch_d = tmp_path / ".apatch"
    apatch_d.mkdir(parents=True)
    cfg = recommended_mcp_server_block(str(tmp_path))
    cfg["env"]["APATCH_MCP_PROFILE"] = "full"
    (apatch_d / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": cfg}}),
        encoding="utf-8",
    )

    assert repair_mcp_configs_if_needed(str(tmp_path)) == []
    data = json.loads((apatch_d / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["apatch"]["env"]["APATCH_MCP_PROFILE"] == "full"


def test_build_mcp_health_includes_doctor_interpreter(tmp_path):
    health = build_mcp_health(str(tmp_path))
    assert "doctor_interpreter" in health
    assert "recommended_mcp_config" in health
    assert "mcp_extra_installed" in health
    assert health["doctor_interpreter"]["interpreter"]
    if health["mcp_extra_installed"]:
        assert health["doctor_interpreter"].get("tool_count", 0) > 0


def test_build_mcp_health_skips_subprocess_probe_in_stdio_mode(tmp_path, monkeypatch):
    """Regression: apatch_doctor inside MCP must not spawn a second Python (~1s)."""
    import apatch.mcp_health as mcp_health_mod

    monkeypatch.setenv("APATCH_MCP_STDIO", "1")
    calls: list[str] = []

    def _spy_probe(python_exe: str, *, timeout: float = 8.0):
        calls.append(python_exe)
        return {"python": python_exe, "ok": False, "error": "subprocess should be skipped"}

    monkeypatch.setattr(mcp_health_mod, "_probe_interpreter", _spy_probe)
    health = build_mcp_health(str(tmp_path))
    assert health["doctor_interpreter"].get("ok") is True
    assert calls == []


def test_run_doctor_includes_mcp_health(tmp_path):
    from apatch.doctor import run_doctor

    info = run_doctor(str(tmp_path))
    assert "mcp_health" in info
    install = info["mcp_health"]["recommended_install"]
    assert install.startswith(sys.executable) or install.startswith("/opt/homebrew/bin/")
    assert "apatch[mcp]" in install
    assert str(tmp_path) not in install


def test_recommended_install_editable_for_apatch_source(tmp_path):
    from apatch.mcp_health import _recommended_install_command

    (tmp_path / "pyproject.toml").write_text("[project]\nname='apatch'\n", encoding="utf-8")
    pkg = tmp_path / "apatch"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    cmd = _recommended_install_command(str(tmp_path))
    assert "-e" in cmd
    assert "[mcp,dev,yaml]" in cmd


def test_repair_mcp_config_fixes_stale_cellar_path(tmp_path):
    apatch_d = tmp_path / ".apatch"
    apatch_d.mkdir(parents=True)
    stale = {
        "mcpServers": {
            "apatch": {
                "command": "/opt/homebrew/Cellar/python@3.14/3.14.0/Frameworks/Python.framework/Versions/3.14/bin/python3.14",
                "args": ["-m", "apatch.mcp.launcher"],
            }
        }
    }
    (apatch_d / "mcp.json").write_text(json.dumps(stale), encoding="utf-8")
    repaired = repair_mcp_config_if_needed(str(tmp_path))
    assert repaired == str(apatch_d / "mcp.json")
    data = json.loads((apatch_d / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["apatch"]["command"] == recommended_mcp_server_block()["command"]
    health = build_mcp_health(str(tmp_path))
    assert health["ok"] is True


def test_repair_mcp_config_preserves_full_guidance_for_apatch_source(tmp_path):
    """Regression: repair must not downgrade APATCH_MCP_GUIDANCE in apatch source."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='apatch'\n", encoding="utf-8")
    pkg = tmp_path / "apatch"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")

    apatch_d = tmp_path / ".apatch"
    apatch_d.mkdir(parents=True)
    full_cfg = recommended_mcp_server_block(str(tmp_path))
    assert full_cfg["env"]["APATCH_MCP_GUIDANCE"] == "full"
    (apatch_d / "mcp.json").write_text(
        json.dumps({"mcpServers": {"apatch": full_cfg}}),
        encoding="utf-8",
    )

    repaired = repair_mcp_configs_if_needed(str(tmp_path))
    assert repaired == []
    data = json.loads((apatch_d / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["apatch"]["env"]["APATCH_MCP_GUIDANCE"] == "full"
    health = build_mcp_health(str(tmp_path))
    assert health["ok"] is True


def test_repair_mcp_config_writes_full_guidance_when_fixing_apatch_source(tmp_path):
    """Stale canonical config in apatch source must be repaired with full guidance."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='apatch'\n", encoding="utf-8")
    pkg = tmp_path / "apatch"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")

    apatch_d = tmp_path / ".apatch"
    apatch_d.mkdir(parents=True)
    stale = {
        "mcpServers": {
            "apatch": {
                "command": "/opt/homebrew/Cellar/python@3.14/3.14.0/Frameworks/Python.framework/Versions/3.14/bin/python3.14",
                "args": ["-m", "apatch.mcp.launcher"],
            }
        }
    }
    (apatch_d / "mcp.json").write_text(json.dumps(stale), encoding="utf-8")

    repaired = repair_mcp_configs_if_needed(str(tmp_path))
    assert repaired == [str(apatch_d / "mcp.json")]
    data = json.loads((apatch_d / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["apatch"]["env"]["APATCH_MCP_GUIDANCE"] == "full"


def test_bare_apatch_mcp_fails_env_match():
    from apatch.mcp_health import _config_matches_recommended

    cfg = {"command": "/opt/homebrew/bin/apatch-mcp", "args": []}
    if not os.path.isfile(cfg["command"]):
        pytest.skip("apatch-mcp not on PATH")
    assert _config_matches_recommended(cfg) is False
