"""Bounded configured-command health, isolated from user sessions and registries.

This proves an isolated bootstrap/stdio handshake, not host tool availability or
project acceptance. It neither repairs configuration nor invokes mutation tools.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any


def probe_configured_server(workspace: str, *, timeout: float = 15.0) -> dict[str, Any]:
    from apatch.mcp.workspace_launcher import canonical_exec_argv
    from apatch.mcp_health import _probe_interpreter, _validate_config

    root = Path(workspace).absolute()
    config = root / '.apatch' / 'mcp.json'
    result: dict[str, Any] = {
        'ok': False, 'status': 'failed', 'canonical_config': str(config),
        'scope': 'configured_command_in_disposable_workspace',
        'host_tool_availability': 'not_observable',
        'child_reaped': True, 'handshake_performed': False,
    }
    temporary = None
    process = None
    reader = None
    deadline = time.monotonic() + timeout
    if not 0 < timeout <= 60:
        result['error'] = 'probe timeout must be in (0, 60] seconds'
        return result
    try:
        original = config.read_bytes()
        result['config_sha256'] = hashlib.sha256(original).hexdigest()
        data = _validate_config(json.loads(original), str(config))
        cfg = data['mcpServers'].get('apatch')
        if not isinstance(cfg, dict):
            raise ValueError('canonical apatch block is missing')
        command, argv = canonical_exec_argv(cfg)
        result['selected_executable'] = command
        result['selected_argv'] = argv
        temporary = tempfile.TemporaryDirectory(prefix='apatch-mcp-health-')
        temp = temporary.name
        probe_root = Path(temp)
        (probe_root / '.apatch').mkdir()
        # Only the selected block; no user ledger, key, lease or alias registry.
        probe_cfg = dict(cfg)
        configured_env = {str(k): str(v) for k, v in cfg.get('env', {}).items() if v is not None}
        configured_env['APATCH_WORKSPACE_REGISTRY'] = str(probe_root / 'workspaces.json')
        configured_env['APATCH_MCP_STDERR_LOG'] = str(probe_root / 'stderr.log')
        probe_cfg['env'] = configured_env
        (probe_root / '.apatch' / 'mcp.json').write_text(
            json.dumps({'mcpServers': {'apatch': probe_cfg}}), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k in {
            'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'TEMP', 'TMP', 'SYSTEMROOT',
            'WINDIR', 'COMSPEC', 'PATHEXT', 'HOME', 'USERPROFILE'}}
        # The actual workspace bootstrap applies the configured env to its child.
        env['APATCH_WORKSPACE'] = str(probe_root)
        env['APATCH_WORKSPACE_REGISTRY'] = str(probe_root / 'workspaces.json')
        env['APATCH_MCP_STDERR_LOG'] = str(probe_root / 'stderr.log')
        selected_env = dict(env, **configured_env)
        selected_env.pop('PYTHONPATH', None)
        selected_env.pop('PYTHONHOME', None)
        identity = _probe_interpreter(command, timeout=max(0.01, deadline - time.monotonic()),
                                      env=selected_env, cwd=str(probe_root))
        result['runtime'] = identity
        if not identity.get('ok'):
            result['status'] = 'timeout' if identity.get('timed_out') else 'runtime_failed'
            result['error'] = identity.get('error', 'configured interpreter is not ready')
            return result

        # Do not run the canonical command directly: exercise both bootstrap layers.
        with open(probe_root / 'bootstrap-stderr.log', 'wb') as errors:
            process = subprocess.Popen(
                [sys.executable, '-I', '-m', 'apatch.mcp.workspace_launcher'],
                cwd=str(probe_root), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=errors, start_new_session=(os.name != 'nt'),
            )
            result['child_pid'] = process.pid
            result['child_reaped'] = False
            messages: queue.Queue[Any] = queue.Queue(maxsize=128)

            def receive():
                try:
                    while True:
                        line = process.stdout.readline(4 * 1024 * 1024 + 1)
                        if not line:
                            messages.put_nowait(RuntimeError('MCP child closed stdout'))
                            return
                        if len(line) > 4 * 1024 * 1024:
                            raise ValueError('oversized MCP response')
                        messages.put_nowait(json.loads(line))
                except (ValueError, OSError, queue.Full) as exc:
                    try:
                        messages.put_nowait(exc)
                    except queue.Full:
                        pass

            reader = threading.Thread(target=receive, daemon=True)
            reader.start()
            request_id = 0

            def rpc(method, params):
                nonlocal request_id
                request_id += 1
                process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': request_id,
                    'method': method, 'params': params}) + '\n').encode())
                process.stdin.flush()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('configured MCP handshake timed out')
                    try:
                        response = messages.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise TimeoutError('configured MCP handshake timed out') from exc
                    if isinstance(response, BaseException):
                        raise RuntimeError('configured child did not return valid MCP JSON-RPC')
                    if not isinstance(response, dict) or response.get('jsonrpc') != '2.0':
                        raise ValueError('invalid MCP response envelope')
                    if 'method' in response and 'id' not in response:
                        continue
                    if response.get('id') != request_id or 'error' in response:
                        raise ValueError('unexpected MCP response or RPC error')
                    if not isinstance(response.get('result'), dict):
                        raise ValueError('invalid MCP result')
                    return response['result']

            init = rpc('initialize', {'protocolVersion': '2025-06-18',
                'capabilities': {}, 'clientInfo': {'name': 'apatch-health', 'version': '1'}})
            if not isinstance(init.get('serverInfo'), dict) or not init.get('protocolVersion'):
                raise ValueError('invalid MCP initialize result')
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
            names, cursors = set(), set()
            cursor = None
            for _ in range(32):
                catalog = rpc('tools/list', {'cursor': cursor} if cursor else {})
                if not isinstance(catalog.get('tools'), list):
                    raise ValueError('invalid MCP tool catalog')
                for tool in catalog['tools']:
                    name = tool.get('name') if isinstance(tool, dict) else None
                    if not isinstance(name, str) or not name or name in names:
                        raise ValueError('duplicate or invalid MCP tool')
                    names.add(name)
                cursor = catalog.get('nextCursor')
                if not cursor:
                    break
                if not isinstance(cursor, str) or cursor in cursors:
                    raise ValueError('invalid MCP pagination')
                cursors.add(cursor)
            else:
                raise ValueError('MCP pagination limit exceeded')
            if 'apatch_doctor' not in names or len(names) != identity.get('tool_count'):
                raise ValueError('MCP catalog does not match selected runtime/profile')
            result.update(ok=True, status='ready', handshake_performed=True,
                          tool_count=len(names), protocol_version=init['protocolVersion'],
                          mcp_server_version=init['serverInfo'].get('version'))
    except TimeoutError:
        result.update(ok=False, status='timeout', error='configured MCP handshake timed out')
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(ok=False, status='failed', error=str(exc))
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    if os.name == 'nt':
                        process.terminate()
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            result['child_reaped'] = process.poll() is not None
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if reader:
                reader.join(timeout=1)
            if process.stdout:
                process.stdout.close()
        if temporary is not None:
            temporary.cleanup()
        try:
            unchanged = hashlib.sha256(config.read_bytes()).hexdigest() == result.get('config_sha256')
        except OSError:
            unchanged = False
        if 'config_sha256' in result and not unchanged:
            result.update(ok=False, status='config_changed', error='canonical config changed during probe')
    return result
