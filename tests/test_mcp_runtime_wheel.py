"""Installed-wheel bootstrap tests in real venvs (no mock interpreter identity)."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv
import zipfile

import pytest


def run(python, *args, cwd, env=None, timeout=60):
    clean = {k: v for k, v in os.environ.items() if k in {
        'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'TEMP', 'TMP', 'SYSTEMROOT',
        'HOME', 'USERPROFILE', 'WINDIR', 'COMSPEC', 'PATHEXT'}}
    clean.update(env or {})
    return subprocess.run([str(python), '-I', *args], cwd=cwd, env=clean,
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope='module')
def wheel_runtime(tmp_path_factory):
    root = tmp_path_factory.mktemp('mcp-installed-wheel')
    source = Path(__file__).resolve().parents[1]
    build_root = root / 'source'
    build_root.mkdir()
    for relative in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE'):
        shutil.copy2(source / relative, build_root / relative)
    for package in ('apatch', 'apatch_search_workflows'):
        shutil.copytree(source / package, build_root / package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    parsed = ast.parse((source / 'setup.py').read_text())
    assets = next(ast.literal_eval(node.value) for node in parsed.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
        and t.id == 'CONSUMER_ASSETS' for t in node.targets))
    for relative in assets:
        target = build_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    # Standard PEP 517 isolated build also installs the declared build dependencies.
    built = run(sys.executable, '-m', 'build', '--wheel', '--outdir', str(root / 'dist'),
                cwd=build_root, timeout=180)
    assert built.returncode == 0, built.stdout + built.stderr
    wheel = next((root / 'dist').glob('*.whl'))
    envdir = root / 'runtime with spaces'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(envdir)
    python = envdir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site_result = run(python, '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))', cwd=root)
    assert site_result.returncode == 0
    site = Path(site_result.stdout.strip())
    # Dependencies are explicitly shared with the test runner; APatch itself is
    # installed into this venv and must win over every ambient/source installation.
    # A runner venv may itself expose system site-packages, so preserve every
    # concrete site-packages entry instead of assuming sysconfig names the one
    # that actually supplied click/mcp.
    dependency_sites = [path for path in sys.path if path.endswith(('site-packages', 'dist-packages'))]
    assert dependency_sites
    (site / 'test-dependencies.pth').write_text('\n'.join(dependency_sites) + '\n')
    installed = run(sys.executable, '-m', 'pip', '--python', str(python), 'install',
                    '--no-index', '--no-deps', '--ignore-installed', str(wheel), cwd=root, timeout=60)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    with zipfile.ZipFile(wheel) as archive:
        for name in ('apatch/mcp_health.py', 'apatch/mcp/runtime_probe.py', 'apatch/doctor.py'):
            assert archive.read(name) == (source / name).read_bytes() == (site / name).read_bytes()
    return root, python, site


def write_config(root, python, profile='full'):
    path = root / '.apatch' / 'mcp.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'mcpServers': {'apatch': {
        'command': str(python), 'args': ['-m', 'apatch.mcp.launcher'],
        'env': {'APATCH_MCP_PROFILE': profile, 'CUSTOM_SETTING': 'preserve'}}}}))
    return path


def test_installed_wheel_sync_and_fresh_stdio(wheel_runtime, tmp_path):
    _, python, site = wheel_runtime
    workspace = tmp_path / 'consumer'
    path = write_config(workspace, '/old/base/python')
    hostile = tmp_path / 'hostile'
    (hostile / 'apatch').mkdir(parents=True)
    (hostile / 'apatch' / '__init__.py').write_text('raise RuntimeError("HOSTILE IMPORT")')
    env = {'PYTHONPATH': str(hostile)}
    synced = run(python, '-m', 'apatch.cli', 'mcp', 'sync', '--target-dir', str(workspace),
                 '--force', '--no-auto-ide', cwd=hostile, env=env)
    assert synced.returncode == 0, synced.stdout + synced.stderr
    assert 'MCP health OK' in synced.stdout
    block = json.loads(path.read_text())['mcpServers']['apatch']
    assert block['command'] == str(python)
    assert block['env']['APATCH_MCP_PROFILE'] == 'full'
    assert block['env']['CUSTOM_SETTING'] == 'preserve'
    before = path.read_bytes()
    repair = run(python, '-c', 'import sys,json; from apatch.mcp_health import '
        'repair_mcp_configs_if_needed; print(json.dumps(repair_mcp_configs_if_needed(sys.argv[1])))',
        str(workspace), cwd=hostile, env=env)
    assert repair.returncode == 0, repair.stderr
    assert json.loads(repair.stdout) == []
    assert path.read_bytes() == before
    code = '''
import asyncio,json,os,sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    workspace=sys.argv[1]
    env=dict(os.environ,APATCH_WORKSPACE=workspace,
             APATCH_WORKSPACE_REGISTRY=os.path.join(workspace,'test-registry.json'))
    server=StdioServerParameters(command=sys.executable,
        args=['-I','-m','apatch.mcp.workspace_launcher'],cwd=os.getcwd(),env=env)
    async with stdio_client(server) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            catalog=await session.list_tools()
            assert len(catalog.tools)==129
            result=await session.call_tool('apatch_doctor',{'target_dir':'.'})
            assert not result.isError
            doctor=json.loads(result.content[0].text)
            runtime=doctor['writer_protocol']['runtime']
            assert runtime['python_executable']==sys.executable
            assert runtime['prefix']==sys.prefix
            assert runtime['apatch_path'].startswith(sys.prefix+os.sep)
            assert runtime['apatch_version']==runtime['installed_version']
            assert doctor['writer_protocol']['ready']
            assert not doctor['mcp_health']['ok']
            assert doctor['mcp_health']['configured_runtime']['status']=='not_checked_in_stdio'
            print(json.dumps({'tools':len(catalog.tools),'runtime':runtime}))
asyncio.run(asyncio.wait_for(main(),30))
'''
    smoke = run(python, '-c', code, str(workspace), cwd=hostile, env=env)
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert json.loads(smoke.stdout)['tools'] == 129
    assert path.read_bytes() == before


@pytest.mark.parametrize('mismatch', ['installed_metadata', 'editable_source'])
def test_installed_wheel_rejects_other_venv_version_and_editable_shadow(wheel_runtime, tmp_path, mismatch):
    _, python, site = wheel_runtime
    bad = tmp_path / 'bad-venv'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(bad)
    bad_python = bad / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    result = run(bad_python, '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))', cwd=tmp_path)
    bad_site = Path(result.stdout.strip())
    # A real editable-style .pth points at a package with a different source version;
    # installed APatch metadata and MCP dependencies still come from the wheel venv.
    editable = tmp_path / 'editable'
    shutil.copytree(site / 'apatch', editable / 'apatch')
    init = editable / 'apatch' / '__init__.py'
    if mismatch == 'editable_source':
        init.write_text(init.read_text() + '\n__version__ = "0.0.0-runtime-repro"\n')
    else:
        expected = run(python, '-c', 'import apatch; print(apatch.__version__)', cwd=tmp_path)
        assert expected.returncode == 0
        (editable / 'pyproject.toml').write_text(
            '[project]\nversion = ' + json.dumps(expected.stdout.strip()) + '\n')
        metadata = bad_site / 'apatch-0.0.0.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: apatch\nVersion: 0.0.0\n')
    (bad_site / 'editable-repro.pth').write_text(str(editable) + '\n' + str(site) + '\n' +
                                               sysconfig.get_path('purelib') + '\n')
    if os.name != 'nt':
        assert os.path.realpath(python) == os.path.realpath(bad_python)
    workspace = tmp_path / 'consumer'
    path = write_config(workspace, bad_python)
    original = path.read_bytes()
    checked = run(python, '-m', 'apatch.cli', 'mcp', 'check', '--target-dir', str(workspace),
                  '--json', cwd=tmp_path)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    report = json.loads(checked.stdout)
    selected = report['configured_runtime']
    assert not report['ok']
    assert selected['status'] == 'runtime_failed'
    assert selected['runtime']['prefix'] == str(bad)
    assert selected['runtime']['apatch_version'] != selected['runtime']['installed_version']
    # The original startup refusal is still live, not replaced by a diagnostic-only check.
    refused = run(python, '-m', 'apatch.mcp.workspace_launcher', cwd=tmp_path,
                  env={'APATCH_WORKSPACE': str(workspace),
                       'APATCH_WORKSPACE_REGISTRY': str(tmp_path / 'empty-registry.json')})
    assert refused.returncode == 78
    assert 'runtime mismatch' in (workspace / '.apatch' / 'mcp_stderr.log').read_text()
    assert path.read_bytes() == original


@pytest.mark.parametrize('profile', ['compact', 'core', 'spec', 'full'])
def test_installed_wheel_check_does_not_touch_project_state(wheel_runtime, tmp_path, profile):
    _, python, _ = wheel_runtime
    workspace = tmp_path / 'consumer'
    write_config(workspace, python, profile)
    for name in ('write_leases.json', 'session_state.json'):
        (workspace / '.apatch' / name).write_text('do not interpret or repair me')
    before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    checked = run(python, '-m', 'apatch.cli', 'mcp', 'check', '--target-dir', str(workspace),
                  '--json', cwd=tmp_path)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    report = json.loads(checked.stdout)
    assert report['configured_runtime']['scope'] == 'configured_command_in_disposable_workspace'
    assert report['configured_runtime']['handshake_performed']
    assert report['host_tool_availability'] == 'not_observable'
    after = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    assert after == before
