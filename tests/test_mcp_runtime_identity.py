"""Regression contract: Python environments are not executable symlink targets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import venv

from click.testing import CliRunner
import pytest

from apatch import mcp_health as health


def config_at(root, block):
    path = root / '.apatch' / 'mcp.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'extra': True, 'mcpServers': {
        'apatch': block, 'other': {'command': 'leave-this-alone'}}}))
    return path


def test_real_venv_identity_is_not_base_binary(tmp_path):
    envdir = tmp_path / 'venv with spaces'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(envdir)
    python = envdir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    source = str(Path(health.__file__).resolve().parents[1])
    code = ('import sys,json; sys.path.insert(0,' + repr(source) + '); '
            'from apatch.mcp_health import _stable_mcp_python_command; '
            'print(json.dumps([sys.executable,sys.prefix,sys.base_prefix,'
            '_stable_mcp_python_command()]))')
    proc = subprocess.run([str(python), '-I', '-c', code], capture_output=True,
                          text=True, check=True, timeout=20)
    executable, prefix, base, selected = json.loads(proc.stdout)
    assert prefix != base
    if os.name != 'nt':
        assert os.path.realpath(executable) == os.path.realpath(sys.executable)
    assert selected == executable


def test_config_match_does_not_collapse_distinct_venvs(tmp_path, monkeypatch):
    first, second = tmp_path / 'a' / 'python', tmp_path / 'b' / 'python'
    for path in (first, second):
        path.parent.mkdir()
        path.symlink_to(sys.executable)
    monkeypatch.setattr(health, '_stable_mcp_python_command', lambda: str(first))
    cfg = health.recommended_mcp_server_block(str(tmp_path))
    cfg['command'] = str(second)
    assert not health._config_command_matches(cfg, str(tmp_path))


def test_forced_sync_preserves_user_configuration(tmp_path):
    block = health.recommended_mcp_server_block(str(tmp_path))
    block['command'] = '/old/runtime/bin/python'
    block['env'].update(APATCH_MCP_PROFILE='full', CUSTOM_VALUE='keep-me')
    block['disabled'] = True
    path = config_at(tmp_path, block)
    ide = tmp_path / 'ide.json'
    ide.write_text(json.dumps({'mcpServers': {'apatch': {
        'command': '/old/runtime/bin/python',
        'args': ['-I', '-m', 'apatch.mcp.workspace_launcher'],
        'env': {'CUSTOM_IDE_VALUE': 'preserved', 'APATCH_WORKSPACE': str(tmp_path)},
        'timeout': 99}}}))
    health.sync_mcp_configs(str(tmp_path), overwrite=True, ide_paths=[str(ide)])
    first = path.read_bytes(), ide.read_bytes()
    got = json.loads(first[0])
    updated = got['mcpServers']['apatch']
    assert updated['env']['APATCH_MCP_PROFILE'] == 'full'
    assert updated['env']['CUSTOM_VALUE'] == 'keep-me'
    assert updated['disabled'] is True
    assert got['extra'] is True
    assert got['mcpServers']['other'] == {'command': 'leave-this-alone'}
    stub = json.loads(first[1])['mcpServers']['apatch']
    assert stub['env']['CUSTOM_IDE_VALUE'] == 'preserved'
    assert stub['timeout'] == 99
    assert '-I' in stub['args']
    health.sync_mcp_configs(str(tmp_path), overwrite=True, ide_paths=[str(ide)])
    assert first == (path.read_bytes(), ide.read_bytes())


def test_profile_changes_only_by_explicit_selection(tmp_path):
    block = health.recommended_mcp_server_block(str(tmp_path))
    block['env']['APATCH_MCP_PROFILE'] = 'full'
    path = config_at(tmp_path, block)
    health.sync_mcp_configs(str(tmp_path), overwrite=True, auto_ide=False,
                            profile='spec')
    assert json.loads(path.read_text())['mcpServers']['apatch']['env']['APATCH_MCP_PROFILE'] == 'spec'


def test_auto_sync_does_not_rebind_foreign_workspace(tmp_path, monkeypatch):
    foreign = tmp_path / 'foreign-ide.json'
    payload = {'mcpServers': {'apatch': {
        'command': '/foreign/python', 'args': ['-m', 'apatch.mcp.workspace_launcher'],
        'env': {'APATCH_WORKSPACE': str(tmp_path / 'another-project')}}}}
    foreign.write_text(json.dumps(payload))
    before = foreign.read_bytes()
    monkeypatch.setattr(health, 'default_ide_stub_paths', lambda _: [str(foreign)])
    paths = health.sync_mcp_configs(str(tmp_path), overwrite=True)
    assert str(foreign) not in paths
    assert foreign.read_bytes() == before


@pytest.mark.parametrize('raw', ['{', '[]', '{"mcpServers": []}',
                                    '{"mcpServers": {"apatch": "bad"}}'])
def test_force_does_not_destroy_malformed_config(tmp_path, raw):
    path = tmp_path / '.apatch' / 'mcp.json'
    path.parent.mkdir()
    path.write_text(raw)
    with pytest.raises(ValueError):
        health.write_project_mcp_config(str(tmp_path), overwrite=True)
    assert path.read_text() == raw


def test_running_mcp_does_not_claim_unmeasured_child_health(tmp_path, monkeypatch):
    health.write_project_mcp_config(str(tmp_path))
    monkeypatch.setenv('APATCH_MCP_STDIO', '1')
    report = health.build_mcp_health(str(tmp_path))
    assert report['doctor_interpreter']['ok']
    assert report['ok'] is False
    assert report['configured_runtime']['status'] == 'not_checked_in_stdio'
    assert report['host_tool_availability'] == 'not_observable'


def test_doctor_does_not_repair_config(tmp_path, monkeypatch):
    from apatch.doctor import run_doctor
    path = config_at(tmp_path, {'command': '/old/python',
        'args': ['-m', 'apatch.mcp.launcher'], 'env': {'APATCH_MCP_PROFILE': 'full'}})
    before = path.read_bytes()
    monkeypatch.setenv('APATCH_MCP_STDIO', '1')
    report = run_doctor(str(tmp_path))
    assert path.read_bytes() == before
    assert report['mcp_health']['ok'] is False


def test_probe_isolated_and_not_cached_by_binary(tmp_path, monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps({'ok': True}), '')
    monkeypatch.setattr(health.subprocess, 'run', run)
    health.clear_mcp_probe_cache()
    for label in ('a', 'b'):
        exe = tmp_path / label / 'python'
        exe.parent.mkdir()
        exe.symlink_to(sys.executable)
        health._probe_interpreter(str(exe))
    assert len(calls) == 2
    assert all('-I' in argv for argv, _ in calls)
    assert calls[0][0][0] != calls[1][0][0]


def test_diagnostics_preserve_executable_and_prefix(tmp_path):
    from apatch.path_leases import writer_protocol_status
    current = health._probe_current_interpreter()
    assert current['interpreter'] == os.path.abspath(sys.executable)
    assert current['prefix'] == sys.prefix
    assert current['base_prefix'] == sys.base_prefix
    assert current['binary_realpath'] == os.path.realpath(sys.executable)
    runtime = writer_protocol_status(str(tmp_path))['runtime']
    assert runtime['python_executable'] == os.path.abspath(sys.executable)
    assert runtime['prefix'] == sys.prefix


def test_sync_cli_failure_is_nonzero(tmp_path, monkeypatch):
    from apatch.cli import cli
    monkeypatch.setattr(health, 'sync_mcp_configs', lambda *a, **k: [])
    monkeypatch.setattr(health, 'build_mcp_health', lambda *a, **k: {
        'ok': False, 'warnings': ['configured child did not serve MCP']})
    result = CliRunner().invoke(cli, ['mcp', 'sync', '--target-dir', str(tmp_path), '--no-auto-ide'])
    assert result.exit_code != 0
    assert 'MCP health OK' not in result.output


def test_mcp_probe_checks_real_jsonrpc_not_just_exit_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(health, '_probe_interpreter', lambda *a, **k: {'ok': True, 'tool_count': 124})
    from apatch.mcp.runtime_probe import probe_configured_server
    path = config_at(tmp_path, {'command': sys.executable,
        'args': ['-c', 'print("not MCP")'], 'env': {}})
    result = probe_configured_server(str(tmp_path), timeout=3)
    assert result['ok'] is False
    assert result['canonical_config'] == str(path)


def test_mcp_probe_times_out_and_reaps_child(tmp_path, monkeypatch):
    monkeypatch.setattr(health, '_probe_interpreter', lambda *a, **k: {'ok': True, 'tool_count': 124})
    from apatch.mcp.runtime_probe import probe_configured_server
    config_at(tmp_path, {'command': sys.executable,
        'args': ['-c', 'import time; time.sleep(60)'], 'env': {}})
    result = probe_configured_server(str(tmp_path), timeout=3)
    assert result.get('child_pid')
    assert result['ok'] is False
    assert result['status'] == 'timeout'
    assert result['child_reaped'] is True


@pytest.mark.parametrize('block', [
    {'command': []}, {'args': '-m apatch.mcp.launcher'}, {'args': [None]},
    {'env': []}, {'env': {'APATCH_MCP_PROFILE': []}},
    {'env': {'APATCH_MCP_PROFILE': 'unknown'}},
])
def test_malformed_config_is_red_without_rewrite(tmp_path, monkeypatch, block):
    from apatch.mcp.runtime_probe import probe_configured_server
    path = config_at(tmp_path, block)
    before = path.read_bytes()
    monkeypatch.setenv('APATCH_MCP_STDIO', '1')
    report = health.build_mcp_health(str(tmp_path))
    assert report['ok'] is False
    assert report['ide_configs'][0].get('error')
    direct = probe_configured_server(str(tmp_path), timeout=1)
    assert direct['ok'] is False
    assert path.read_bytes() == before


def test_canonical_snapshot_change_fails_closed(tmp_path, monkeypatch):
    from apatch.mcp.runtime_probe import probe_configured_server
    path = config_at(tmp_path, {'command': sys.executable})
    def changed(*args, **kwargs):
        path.write_text('{}')
        return {'ok': False, 'error': 'synthetic identity failure'}
    monkeypatch.setattr(health, '_probe_interpreter', changed)
    report = probe_configured_server(str(tmp_path), timeout=1)
    assert report['ok'] is False
    assert report['status'] == 'config_changed'


def test_agent_guidance_distinguishes_runtime_and_host_availability():
    root = Path(health.__file__).resolve().parents[1]
    for name in ('docs/mcp_setup.md', 'docs/AGENTS.template.md',
                 'docs/agent-onboarding.md', 'apatch/consumer_profiles.py'):
        text = (root / name).read_text()
        assert 'not_checked_in_stdio' in text
        assert 'host tool availability' in text
        assert 'mcp check' in text
