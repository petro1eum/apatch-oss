"""RFP-046 qualification evidence must remain complete and fail closed."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = 'SPEC-OSS-VERIFICATION-PROFILES-1'


def qualifier():
    spec = importlib.util.spec_from_file_location('oss_qualification', ROOT / 'scripts/qualify_oss.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory():
    return json.loads((ROOT / 'docs/oss-verification-profiles.json').read_text())


def source_fixture(tmp_path, inv):
    source = tmp_path / 'source'
    for name in {**inv['runtime_files'], **inv['source_files']}:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return source


def test_traceability():
    from apatch.rfp_coverage import rfp_spec_coverage_workspace
    from apatch.spec import parse_spec

    result = rfp_spec_coverage_workspace(str(ROOT), rfp='RFP-046', spec=SPEC)
    assert result['ok'] is True, result
    parsed = parse_spec((ROOT / 'docs/specs' / (SPEC + '.md')).read_text())
    assert parsed.strict_ownership
    assert {row.id for row in parsed.requirements} == {'R0', 'R1', 'R2', 'R3', 'R4', 'R5'}
    assert all(row.verify and row.owns for row in parsed.requirements)


@pytest.mark.parametrize('change', [
    'none', 'unknown_key', 'unknown_dependency', 'wildcard', 'whole_module',
    'duplicate_failure', 'duplicate_skip', 'duplicate_requirement', 'unknown_collection_skip',
    'missing_reason', 'missing_message', 'missing_hash', 'bad_hash', 'missing_file', 'changed_file',
    'runtime_changed', 'runtime_added', 'runtime_removed', 'symlink', 'parent_symlink',
    'wrong_command', 'wrong_test', 'unknown_requirement', 'timeout', 'invalid_exit_type',
    'unknown_unproven', 'ambiguous_skip', 'claim_acceptance', 'duplicate_json', 'malformed_json',
])
def test_inventory_is_exact_and_hash_bound(tmp_path, change):
    q = qualifier()
    inv = inventory()
    assert len(inv['absent_peer_failures']) == 40
    assert len(inv['peer_requirements']) == 22
    assert len(inv['runtime_files']) == 241
    assert len(inv['source_files']) == 19
    source = source_fixture(tmp_path, inv)
    first = inv['absent_peer_failures'][0]
    path = first['node'].split('::')[0]
    if change == 'none':
        report = q.verify_inventory_source(inv, source)
        assert report['inventory_validated'] is True
        assert report['profile_passed'] is False
        assert report['release_authorized'] is False
        assert report['external_acceptance'] == 'not_checked'
        return
    if change in {'duplicate_json', 'malformed_json'}:
        raw = tmp_path / 'inventory.json'
        raw.write_text('{"schema":1,"schema":2}' if change == 'duplicate_json' else '{')
        with pytest.raises(q.InventoryError):
            q.load_inventory(raw)
        return
    if change == 'unknown_key':
        inv['exclude_modules'] = ['tests/test_avatar_delivery.py']
    elif change == 'unknown_dependency':
        first['dependency'] = 'private_unreviewed_peer'
    elif change == 'wildcard':
        first['node'] = 'tests/test_avatar_*.py::test_anything'
    elif change == 'whole_module':
        first['node'] = path
    elif change == 'duplicate_failure':
        inv['absent_peer_failures'].append(copy.deepcopy(first))
    elif change == 'duplicate_skip':
        inv['existing_optional_skips'].append(copy.deepcopy(inv['existing_optional_skips'][0]))
    elif change == 'duplicate_requirement':
        inv['peer_requirements'].append(copy.deepcopy(inv['peer_requirements'][0]))
    elif change == 'unknown_collection_skip':
        inv['existing_optional_skips'][0].update(node=path, collection_skip=True)
    elif change == 'missing_reason':
        first['reason'] = ''
    elif change == 'missing_message':
        first['observed_message'] = ''
    elif change == 'missing_hash':
        inv['source_files'].pop(path)
    elif change == 'bad_hash':
        inv['source_files'][path] = 'not-a-sha256'
    elif change == 'missing_file':
        (source / path).unlink()
    elif change == 'changed_file':
        (source / path).write_text('def test_replaced(): pass\n')
    elif change == 'runtime_changed':
        (source / 'apatch/__init__.py').write_text('changed = True\n')
    elif change == 'runtime_added':
        (source / 'apatch/unreviewed.py').write_text('changed = True\n')
    elif change == 'runtime_removed':
        (source / 'apatch/__init__.py').unlink()
    elif change == 'symlink':
        original = source / path
        other = tmp_path / 'other.py'
        original.rename(other)
        original.symlink_to(other)
    elif change == 'parent_symlink':
        (source / 'tests').rename(tmp_path / 'outside-tests')
        (source / 'tests').symlink_to(tmp_path / 'outside-tests', target_is_directory=True)
    elif change == 'wrong_command':
        inv['peer_requirements'][0]['command'] += ' -k nothing'
    elif change == 'wrong_test':
        first['node'] = path + '::test_not_present'
    elif change == 'unknown_requirement':
        inv['peer_requirements'][0]['requirement'] = 'SPEC-AVATAR-EVIDENCE-1#R999'
    elif change == 'timeout':
        inv['peer_requirements'][0]['observed_exit_code'] = 'timeout'
    elif change == 'invalid_exit_type':
        inv['peer_requirements'][0]['observed_exit_code'] = True
    elif change == 'unknown_unproven':
        inv['unproven_external_specs'][0]['spec'] = 'SPEC-LOCAL-REGRESSION-1'
    elif change == 'ambiguous_skip':
        inv['existing_optional_skips'][0]['node'] = first['node']
    elif change == 'claim_acceptance':
        inv['qualification_scope'] = 'full_contract_accepted'
    with pytest.raises(q.InventoryError):
        q.verify_inventory_source(inv, source)


def _public_fixture(tmp_path):
    q = qualifier()
    source = tmp_path / 'public'
    (source / 'tests').mkdir(parents=True)
    (source / 'tests/test_example.py').write_text(
        'import pytest\ndef test_pass(): assert True\n'
        'def test_fail(): assert False, "observable failure"\n'
        'def test_skip(): pytest.skip("observable optional dependency")\n')
    payload = {'private_git_history_included': False, 'files': [
        {'path': 'tests/test_example.py',
         'sha256': q.sha256((source / 'tests/test_example.py').read_bytes())}]}
    (source / 'PUBLIC-SOURCE-MANIFEST.json').write_text(json.dumps(payload))
    return source


def _suite_evidence(tmp_path):
    q = qualifier()
    node = 'tests/test_example.py::test_pass'
    observation = {'run_id': 'fresh', 'finished': True, 'exit_code': 0,
        'collected': [node], 'outcomes': {node: {'outcome': 'passed'}},
        'deselected': [], 'collection_errors': [], 'collection_skips': {},
        'selection': {'args': ['tests/'], 'keyword': '', 'markexpr': '',
                      'maxfail': 0, 'collectonly': False, 'ignore': [],
                      'ignore_glob': [], 'deselect': []}}
    process = {'exit_code': 0, 'timed_out': False}
    junit = tmp_path / 'suite.xml'
    junit.write_text('<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0">'
                     '<testcase classname="tests.test_example" name="test_pass"/>'
                     '</testsuite></testsuites>')
    return q, observation, process, junit


@pytest.mark.parametrize('case', [
    'real_unfiltered_run', 'complete_run_snapshots', 'snapshots', 'source_drift', 'manifest_drift', 'manifest_escape',
    'symlink_input', 'environment', 'caller_filter', 'timeout', 'commands',
    'valid_observation', 'wrong_run', 'interrupted', 'keyword', 'markexpr', 'maxfail',
    'collectonly', 'ignore', 'ignore_glob', 'deselect', 'deselected', 'collection_error',
    'missing_outcome', 'duplicate_test', 'unknown_outcome', 'wrong_exit', 'wrong_junit',
    'wrong_junit_identity',
    'valid_contract', 'contract_filtered', 'contract_not_live', 'contract_partial',
    'contract_missing_command', 'contract_wrong_command', 'contract_hidden_failure',
])
def test_full_run_is_unfiltered_and_isolated(tmp_path, monkeypatch, case):
    import os
    import sys
    q = qualifier()
    if case == 'complete_run_snapshots':
        source = _public_fixture(tmp_path)
        observed = []
        def collect_contract(root, output, **kwargs):
            observed.append(root)
            output.mkdir()
            return {'raw_standing_gate_fixture': True}, {'exit_code': 0}
        monkeypatch.setattr(q, 'collect_contract', collect_contract)
        result = q.collect_complete_run(source, tmp_path / 'evidence', timeout=30)
        assert result['complete'] and result['suite_process']['exit_code'] == 1
        assert result['suite_source'] != result['contract_source']
        assert observed == [Path(result['contract_source'])]
        assert (tmp_path / 'evidence/complete-run.json').is_file()
        assert not (source / '.git').exists()
        with pytest.raises(FileExistsError):
            q.collect_complete_run(source, tmp_path / 'evidence', timeout=30)
        with pytest.raises(q.InventoryError):
            q.collect_complete_run(source, source / 'evidence', timeout=30)
        return
    if case == 'real_unfiltered_run':
        source = _public_fixture(tmp_path)
        output = tmp_path / 'evidence'
        observations, process = q.collect_suite(source, output, timeout=30)
        assert len(observations['collected']) == 3
        assert sorted(row['outcome'] for row in observations['outcomes'].values()) == [
            'failed', 'passed', 'skipped']
        assert process['exit_code'] == 1
        assert (output / 'suite.stdout').is_file() and (output / 'suite.stderr').is_file()
        assert (output / 'suite-process.json').is_file()
        assert not (source / '.apatch').exists()
        return
    if case in {'snapshots', 'source_drift', 'manifest_drift', 'manifest_escape', 'symlink_input'}:
        source = _public_fixture(tmp_path)
        if case == 'snapshots':
            (source / '.trustchain').mkdir()
            (source / '.trustchain/private-key').write_text('must not copy')
            first, second = tmp_path / 'suite-source', tmp_path / 'contract-source'
            one = q.snapshot_source(source, first)
            two = q.snapshot_source(source, second)
            assert one == two
            assert not (first / '.trustchain').exists()
            assert not (second / '.trustchain').exists()
            assert (first / '.git').is_dir() and (second / '.git').is_dir()
            (first / 'tests/test_example.py').write_text('changed')
            q.source_unchanged(second, two)
            with pytest.raises(q.InventoryError):
                q.source_unchanged(first, one)
            return
        if case in {'source_drift', 'manifest_drift'}:
            (source / 'tests/test_example.py').write_text('changed')
        elif case == 'manifest_escape':
            manifest = json.loads((source / 'PUBLIC-SOURCE-MANIFEST.json').read_text())
            manifest['files'][0]['path'] = '../outside.py'
            (source / 'PUBLIC-SOURCE-MANIFEST.json').write_text(json.dumps(manifest))
        else:
            target = source / 'tests/test_example.py'
            target.rename(tmp_path / 'outside.py')
            target.symlink_to(tmp_path / 'outside.py')
        with pytest.raises(q.InventoryError):
            q.snapshot_source(source, tmp_path / 'destination')
        return
    if case in {'environment', 'caller_filter'}:
        monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
        monkeypatch.delenv('PYTEST_PLUGINS', raising=False)
        monkeypatch.setenv('SENSITIVE_SERVICE_TOKEN', 'not-for-the-child')
        monkeypatch.setenv('APATCH_WORKSPACE_REGISTRY', '/not-our-registry')
        if case == 'caller_filter':
            monkeypatch.setenv('PYTEST_ADDOPTS', '-k subset')
            with pytest.raises(q.InventoryError):
                q.verification_environment(tmp_path)
        else:
            env = q.verification_environment(tmp_path)
            assert 'SENSITIVE_SERVICE_TOKEN' not in env
            assert env['HOME'] == str(tmp_path / 'home')
            assert env['APATCH_WORKSPACE_REGISTRY'] == str(tmp_path / 'workspaces.json')
            assert 'PYTHONPATH' not in env
        return
    if case == 'timeout':
        code = ('import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-I","-c",'
                '"import time; time.sleep(60)"]); print(child.pid,flush=True); time.sleep(60)')
        process = q.run_capture([sys.executable, '-I', '-c', code],
            cwd=tmp_path, env=q.verification_environment(tmp_path), output=tmp_path,
            label='bounded', timeout=0.5)
        assert process['timed_out'] and process['exit_code'] < 0
        assert (tmp_path / 'bounded-process.json').is_file()
        child_pid = int((tmp_path / 'bounded.stdout').read_text().strip())
        import subprocess
        child = subprocess.run(['ps', '-p', str(child_pid), '-o', 'stat='],
                               capture_output=True, text=True)
        assert not child.stdout.strip() or child.stdout.strip().startswith('Z')
        return
    if case == 'commands':
        command = q.suite_command(tmp_path)
        assert command[1:5] == ['-B', '-m', 'pytest', 'tests/']
        assert not {'-k', '-m', '--ignore', '--deselect', '-x', '--maxfail'}.intersection(command[4:])
        gate = q.contract_command()
        assert {'--live', '--ci-safe', '--exhaustive', '--json'} <= set(gate)
        return
    if case.startswith('contract_') or case == 'valid_contract':
        expected = {'SPEC-LOCAL-1': {'R1': 'python -m pytest tests/test_local.py -q'}}
        contract = {'enabled': True, 'live': True, 'ci_safe': True, 'fail_fast': False,
                    'verified_live': 1, 'gated': 1, 'contract_holds': True,
                    'per_spec': [{'spec': 'SPEC-LOCAL-1', 'verify_ran': 1,
                                  'conformance': 'conformant'}]}
        row = contract['per_spec'][0]
        if case == 'valid_contract':
            q.validate_contract_observation(contract, expected)
            return
        if case == 'contract_filtered': contract['fail_fast'] = True
        elif case == 'contract_not_live': contract['live'] = False
        elif case == 'contract_partial': contract['verified_live'] = 0
        elif case == 'contract_missing_command': row['verify_ran'] = 0
        elif case == 'contract_wrong_command':
            row.update(conformance='drifted', verify_details=[{'id': 'R1', 'cmd': 'true'}])
        else:
            row['verify_details'] = [{'id': 'R1', 'cmd': expected['SPEC-LOCAL-1']['R1']}]
        with pytest.raises(q.InventoryError): q.validate_contract_observation(contract, expected)
        return
    q, observation, process, junit = _suite_evidence(tmp_path)
    if case == 'valid_observation':
        q.validate_suite_observation(observation, junit, process, 'fresh')
        return
    if case == 'wrong_run': observation['run_id'] = 'stale'
    elif case == 'interrupted': observation['finished'] = False
    elif case in {'keyword', 'markexpr', 'maxfail', 'collectonly', 'ignore', 'ignore_glob', 'deselect'}:
        observation['selection'][case] = 'filtered'
    elif case == 'deselected': observation['deselected'] = ['tests/test_removed.py::test_one']
    elif case == 'collection_error': observation['collection_errors'] = ['tests/test_bad.py']
    elif case == 'missing_outcome': observation['outcomes'] = {}
    elif case == 'duplicate_test': observation['collected'] *= 2
    elif case == 'unknown_outcome': next(iter(observation['outcomes'].values()))['outcome'] = 'unknown'
    elif case == 'wrong_exit': process['exit_code'] = 1
    elif case == 'wrong_junit': junit.write_text('<malformed')
    elif case == 'wrong_junit_identity': junit.write_text(junit.read_text().replace('test_pass', 'test_forged'))
    else: raise AssertionError(case)
    with pytest.raises(q.InventoryError):
        q.validate_suite_observation(observation, junit, process, 'fresh')


def _standalone_evidence():
    import fnmatch
    import shlex
    inv = inventory()
    failures = {row['node']: {'outcome': 'failed', 'phase': 'call',
                             'message': row['observed_message']} for row in inv['absent_peer_failures']}
    outcomes = dict(failures, **{'tests/test_avatar_delivery.py::test_local_without_peer': {'outcome': 'passed'}})
    collection = {}
    for row in inv['existing_optional_skips']:
        marker = 'avatar_contract' if row['dependency'] == 'avatar_contract' else 'tree-sitter-java'
        if row['collection_skip']: collection[row['node']] = 'could not import ' + marker
        else: outcomes[row['node']] = {'outcome': 'skipped', 'message': 'missing ' + marker}
    specs = {}
    for declaration in inv['peer_requirements']:
        spec_id, req_id = declaration['requirement'].split('#')
        row = specs.setdefault(spec_id, {'spec': spec_id, 'conformance': 'drifted', 'verify_details': []})
        detail = {'id': req_id, 'cmd': declaration['command'], 'kind': declaration['kind'],
                  'exit_code': declaration['observed_exit_code'], 'stderr_tail': ''}
        selectors = [arg for arg in shlex.split(declaration['command']) if arg.startswith('tests/')]
        if declaration['kind'] == 'broken':
            row['conformance'] = 'broken'
            detail.update(stdout_tail='1 skipped in 0.03s\n',
                          stderr_tail='ERROR: found no collectors for /snapshot/' + selectors[0] + '\n')
        else:
            selected = [node for node in failures if any(
                node == selector or ('::' not in selector and fnmatch.fnmatchcase(node.split('::')[0], selector))
                for selector in selectors)]
            detail['stdout_tail'] = '\n'.join('FAILED ' + node + ' - observed missing peer' for node in selected)
            detail['stdout_tail'] += '\n' + str(len(selected)) + ' failed, 3 passed in 0.05s\n'
        row['verify_details'].append(detail)
    specs['SPEC-LOCAL-ONLY-1'] = {'spec': 'SPEC-LOCAL-ONLY-1', 'conformance': 'conformant'}
    for declaration in inv['unproven_external_specs']:
        specs[declaration['spec']] = {'spec': declaration['spec'], 'conformance': 'unproven'}
    evidence = {'complete': True, 'source_files': {**inv['runtime_files'], **inv['source_files']},
        'suite': {'dependencies': {'avatar_contract': False, 'tree_sitter_java': False,
                                  'trustchain': True, 'mcp': True, 'cryptography': True},
                  'outcomes': outcomes, 'collection_skips': collection},
        'contract': {'contract_holds': False, 'per_spec': list(specs.values())}}
    return inv, evidence


@pytest.mark.parametrize('case', [
    'reviewed', 'java_installed', 'incomplete', 'source_changed', 'peer_present',
    'public_dependency_missing', 'dependency_unknown', 'failure_added', 'failure_missing',
    'failure_changed', 'setup_failure', 'skip_added', 'skip_missing', 'skip_unexplained',
    'collection_unexplained', 'requirement_added', 'requirement_missing', 'requirement_changed',
    'requirement_extra_failure', 'requirement_missing_failure', 'requirement_count_forged',
    'requirement_skips', 'requirement_error', 'broken_unexplained', 'unproven_added',
    'duplicate_spec', 'unknown_status', 'missing_raw_verdict',
])
def test_standalone_fails_closed(case):
    q = qualifier()
    inv, evidence = _standalone_evidence()
    suite, contract = evidence['suite'], evidence['contract']
    first = inv['absent_peer_failures'][0]['node']
    node = inv['existing_optional_skips'][0]['node']
    detail = next(d for s in contract['per_spec'] for d in s.get('verify_details', []) if d['kind'] == 'failure')
    if case == 'java_installed':
        suite['dependencies']['tree_sitter_java'] = True
        suite['outcomes']['tests/test_matcher.py::test_evaluate_java_body_match'] = {'outcome': 'passed'}
    if case in {'reviewed', 'java_installed'}:
        result = q.qualify_standalone(inv, evidence)
        assert result['profile_passed'] is True
        assert result['raw_suite_passed'] is False and result['raw_contract_holds'] is False
        assert result['avatar_acceptance'] == 'unavailable'
        assert result['release_authorized'] is False and result['external_acceptance'] == 'not_checked'
        assert len(result['classified_failures']) == 40
        return
    if case == 'incomplete': evidence['complete'] = False
    elif case == 'source_changed': evidence['source_files']['apatch/__init__.py'] = '0' * 64
    elif case == 'peer_present': suite['dependencies']['avatar_contract'] = True
    elif case == 'public_dependency_missing': suite['dependencies']['trustchain'] = False
    elif case == 'dependency_unknown': suite['dependencies']['avatar_contract'] = None
    elif case == 'failure_added': suite['outcomes']['tests/test_local.py::test_regression'] = dict(suite['outcomes'][first])
    elif case == 'failure_missing': suite['outcomes'][first] = {'outcome': 'passed'}
    elif case == 'failure_changed': suite['outcomes'][first]['message'] = 'unrelated regression'
    elif case == 'setup_failure': suite['outcomes'][first]['phase'] = 'setup'
    elif case == 'skip_added': suite['outcomes']['tests/test_local.py::test_skipped'] = {'outcome': 'skipped'}
    elif case == 'skip_missing': suite['outcomes'][node] = {'outcome': 'passed'}
    elif case == 'skip_unexplained': suite['outcomes'][node]['message'] = 'disabled by author'
    elif case == 'collection_unexplained': suite['collection_skips']['tests/test_contribution_event.py'] = 'disabled'
    elif case == 'requirement_added':
        contract['per_spec'].append({'spec': 'SPEC-REGRESSION-1', 'conformance': 'drifted',
                                     'verify_details': [copy.deepcopy(detail)]})
    elif case == 'requirement_missing':
        next(s for s in contract['per_spec'] if s.get('verify_details'))['verify_details'].pop()
    elif case == 'requirement_changed': detail['exit_code'] = 2
    elif case == 'requirement_extra_failure': detail['stdout_tail'] += 'FAILED tests/test_local.py::test_regression - not peer\n'
    elif case == 'requirement_missing_failure': detail['stdout_tail'] = '1 failed in 1s\n'
    elif case == 'requirement_count_forged': detail['stdout_tail'] += '\n999 failed in 1s\n'
    elif case == 'requirement_skips': detail['stdout_tail'] += '1 skipped in 1s\n'
    elif case == 'requirement_error': detail['stderr_tail'] = 'ERROR: cannot load plugin'
    elif case == 'broken_unexplained':
        next(d for s in contract['per_spec'] for d in s.get('verify_details', []) if d['kind'] == 'broken')['stderr_tail'] = 'ImportError: unrelated'
    elif case == 'unproven_added': contract['per_spec'].append({'spec': 'SPEC-UNPROVEN-1', 'conformance': 'unproven'})
    elif case == 'duplicate_spec': contract['per_spec'].append(copy.deepcopy(contract['per_spec'][0]))
    elif case == 'unknown_status': contract['per_spec'][-1]['conformance'] = 'unknown'
    elif case == 'missing_raw_verdict': contract.pop('contract_holds')
    else: raise AssertionError(case)
    with pytest.raises(q.InventoryError):
        q.qualify_standalone(inv, evidence)


def _canonical_fixture(tmp_path):
    import subprocess
    import sys
    import venv
    checkout, source = tmp_path / 'canonical', tmp_path / 'public'
    (checkout / 'avatar_contract').mkdir(parents=True)
    (checkout / 'tests').mkdir()
    source.mkdir()
    (checkout / 'avatar_contract/__init__.py').write_text('SCHEMA = 3\n')
    (checkout / 'tests/test_schema.py').write_text('from avatar_contract import SCHEMA\ndef test_schema(): assert SCHEMA == 3\n')
    (checkout / 'pyproject.toml').write_text('[project]\nname="avatar-contract"\nversion="0.0.1"\n')
    for args in (['init', '-b', 'main'], ['add', '.'],
                 ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                  '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(checkout), *args], check=True, capture_output=True)
    pin = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    (source / 'pyproject.toml').write_text('# avatar-contract @ git+https://github.com/petro1eum/avatar-contract.git@' + pin + '\n')
    runtime = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / 'bin/python'
    site = Path(subprocess.check_output([str(python), '-I', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    shutil.copytree(checkout / 'avatar_contract', site / 'avatar_contract')
    dist = site / 'avatar_contract-0.0.1.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: avatar-contract\nVersion: 0.0.1\n')
    return source, checkout, python, site, pin


@pytest.mark.parametrize('case', [
    'canonical', 'missing_checkout', 'missing_pin', 'ambiguous_pin', 'wrong_commit', 'dirty',
    'hidden_tracked_change', 'missing_install', 'wrong_install_version', 'changed_install',
    'extra_installed_module', 'installed_symlink',
    'profile_passes', 'profile_missing_shared', 'profile_missing_peer', 'profile_failed_test',
    'profile_avatar_skip', 'profile_collection_skip', 'profile_red_contract', 'profile_broken_contract',
    'profile_missing_tests', 'profile_unknown_outcome', 'profile_unbound', 'profile_empty_gate',
    'profile_empty_shared', 'shared_pass', 'shared_failed', 'shared_skipped',
    'shared_requirement_failed', 'shared_tampered', 'shared_inside_public',
])
def test_avatar_requires_canonical_peer(tmp_path, monkeypatch, case):
    q = qualifier()
    if case.startswith('shared_'):
        source, peer = tmp_path / 'public', tmp_path / 'private-peer'
        (source / 'docs/specs').mkdir(parents=True)
        (peer / 'tests').mkdir(parents=True)
        body = 'def test_schema(): assert True\n'
        if case == 'shared_failed': body = 'def test_schema(): assert False\n'
        if case == 'shared_skipped': body = 'import pytest\ndef test_schema(): pytest.skip("not allowed")\n'
        (peer / 'tests/test_schema.py').write_text(body)
        command = 'python3 -c "raise SystemExit(' + ('1' if case == 'shared_requirement_failed' else '0') + ')"'
        name = 'docs/specs/SPEC-AVATAR-CONTRACT-1.md'
        (source / name).write_text('# SPEC-AVATAR-CONTRACT-1\n\n## R1 Canonical check\n\n(verify: ' + command + ')\n')
        manifest = {'private_git_history_included': False, 'files': [
            {'path': name, 'sha256': q.sha256((source / name).read_bytes())}]}
        (source / 'PUBLIC-SOURCE-MANIFEST.json').write_text(json.dumps(manifest))
        prerequisite = {'pin': 'b' * 40, 'checkout': str(peer), 'source_files': {
            'tests/test_schema.py': q.sha256((peer / 'tests/test_schema.py').read_bytes())}}
        monkeypatch.setattr(q, 'avatar_prerequisite', lambda *args: prerequisite)
        output = tmp_path / 'shared'
        if case == 'shared_inside_public': output = source / 'private-evidence'
        if case == 'shared_tampered': (peer / 'tests/test_schema.py').write_text('changed\n')
        if case != 'shared_pass':
            with pytest.raises(q.InventoryError):
                q.collect_shared_contract(source, output, prerequisite, timeout=30)
            return
        result = q.collect_shared_contract(source, output, prerequisite, timeout=30)
        assert result['shared_contract_passed'] is True and result['requirements'] == ['R1']
        assert len(result['canonical_suite']['collected']) == 1
        assert (output / 'shared-R1-process.json').is_file()
        assert not (source / 'avatar-contract').exists()
        assert (output / 'avatar-contract/tests/test_schema.py').read_text() == body
        return
    if case.startswith('profile_'):
        inv, evidence = _standalone_evidence()
        suite, contract = evidence['suite'], evidence['contract']
        suite['dependencies']['avatar_contract'] = True
        suite['collection_skips'] = {}
        suite['outcomes'] = {node: {'outcome': 'passed'} for node in suite['outcomes']}
        suite['outcomes']['tests/test_matcher.py::test_evaluate_java_body_match'] = {
            'outcome': 'skipped', 'message': 'tree-sitter-java grammar not installed'}
        contract['contract_holds'] = True
        for row in contract['per_spec']:
            row['conformance'] = 'unproven' if row['spec'] == 'SPEC-AVATAR-CONTRACT-1' else 'conformant'
            row.pop('verify_details', None)
        shared = {'shared_contract_passed': True, 'pin': 'b' * 40,
                  'requirements': ['R0', 'R1', 'R2', 'R3', 'R4', 'R5']}
        if case == 'profile_passes':
            result = q.qualify_avatar(inv, evidence, shared)
            assert result['profile_passed'] and result['raw_suite_passed']
            assert result['canonical_shared_contract_passed']
            assert result['release_authorized'] is False and result['external_acceptance'] == 'not_checked'
            return
        if case == 'profile_missing_shared': shared['shared_contract_passed'] = False
        elif case == 'profile_missing_peer': suite['dependencies']['avatar_contract'] = False
        elif case == 'profile_failed_test': suite['outcomes']['tests/test_avatar.py::test_failed'] = {'outcome': 'failed'}
        elif case == 'profile_avatar_skip': suite['outcomes']['tests/test_avatar.py::test_skipped'] = {'outcome': 'skipped'}
        elif case == 'profile_collection_skip': suite['collection_skips']['tests/test_contribution_event.py'] = 'missing avatar_contract'
        elif case == 'profile_red_contract': contract['contract_holds'] = False
        elif case == 'profile_broken_contract': contract['per_spec'][0]['conformance'] = 'broken'
        elif case == 'profile_missing_tests': suite['outcomes'] = {}
        elif case == 'profile_unknown_outcome': suite['outcomes']['tests/test_unknown.py::test_unknown'] = {'outcome': 'unknown'}
        elif case == 'profile_unbound': evidence['source_files'] = {}
        elif case == 'profile_empty_gate': contract['per_spec'] = []
        elif case == 'profile_empty_shared': shared['requirements'] = []
        with pytest.raises(q.InventoryError): q.qualify_avatar(inv, evidence, shared)
        return
    import subprocess
    source, checkout, python, site, pin = _canonical_fixture(tmp_path)
    monkeypatch.setattr(q.sys, 'executable', str(python))
    if case == 'canonical':
        result = q.avatar_prerequisite(source, checkout)
        assert result['prerequisite_passed'] and result['pin'] == pin
        assert result['installed']['version'] == '0.0.1'
        return
    if case == 'missing_checkout': checkout = None
    elif case == 'missing_pin': (source / 'pyproject.toml').write_text('[project]\nname="apatch"\n')
    elif case == 'ambiguous_pin':
        path = source / 'pyproject.toml'; path.write_text(path.read_text() * 2)
    elif case == 'wrong_commit':
        path = source / 'pyproject.toml'; path.write_text(path.read_text().replace(pin, '0' * 40))
    elif case == 'dirty': (checkout / 'untracked.py').write_text('dirty')
    elif case == 'hidden_tracked_change':
        subprocess.run(['git', '-C', str(checkout), 'update-index', '--assume-unchanged', 'avatar_contract/__init__.py'], check=True)
        (checkout / 'avatar_contract/__init__.py').write_text('SCHEMA = 0\n')
    elif case == 'missing_install': (site / 'avatar_contract').rename(site / 'unrelated_package')
    elif case == 'wrong_install_version': (site / 'avatar_contract-0.0.1.dist-info/METADATA').write_text('Metadata-Version: 2.1\nName: avatar-contract\nVersion: 999\n')
    elif case == 'changed_install': (site / 'avatar_contract/__init__.py').write_text('SCHEMA = 0\n')
    elif case == 'extra_installed_module': (site / 'avatar_contract/unreviewed.py').write_text('unknown = True\n')
    elif case == 'installed_symlink': (site / 'avatar_contract/foreign.py').symlink_to(checkout / 'avatar_contract/__init__.py')
    else: raise AssertionError(case)
    with pytest.raises(q.InventoryError): q.avatar_prerequisite(source, checkout)


@pytest.mark.parametrize('case', [
    'report_pass', 'report_new_failure', 'report_prerequisite', 'report_runtime_changed',
    'report_peer_prerequisite', 'report_no_private_standalone', 'report_timeout', 'report_reused_output',
    'policy_valid', 'policy_mode', 'policy_block_on', 'policy_holds', 'policy_ok', 'policy_buckets',
    'runtime_valid', 'runtime_old', 'runtime_changed', 'runtime_extra', 'runtime_version',
    'runtime_missing_dependency', 'runtime_peer_present', 'runtime_console', 'runtime_assets',
    'runtime_runner', 'runtime_probe', 'cli_help', 'cli_missing_source', 'cli_no_imported_report',
    'english_docs_ci',
])
def test_report_never_claims_global_acceptance(tmp_path, monkeypatch, capsys, case):
    q = qualifier()
    if case == 'english_docs_ci':
        text = (ROOT / 'docs/oss-verification.md').read_text()
        assert text.startswith('# OSS release verification')
        for field in ('profile_passed', 'raw_suite_passed', 'raw_contract_holds',
                      'external_acceptance', 'release_authorized'):
            assert '`' + field + '`' in text
        workflow = text.split('```yaml\n')[1].split('```')[0]
        assert 'scripts/qualify_oss.py --profile standalone' in workflow
        assert 'actions/upload-artifact@' in workflow and 'if: always()' in workflow
        assert 'continue-on-error' not in workflow and '|| true' not in workflow
        assert 'shared/' not in workflow and 'suite-source/' not in workflow
        assert 'contract/*.stdout' in workflow and 'suite/*.xml' in workflow
        return
    if case.startswith('cli_'):
        if case == 'cli_help':
            with pytest.raises(SystemExit) as error: q.main(['--help'])
            assert error.value.code == 0
            assert '--profile' in capsys.readouterr().out
        elif case == 'cli_no_imported_report':
            with pytest.raises(SystemExit) as error:
                q.main(['--profile', 'standalone', '--source', '.', '--output', str(tmp_path / 'out'),
                        '--report', 'old-pass.json'])
            assert error.value.code == 2
        else:
            assert q.main(['--profile', 'standalone', '--source', str(tmp_path / 'absent'),
                           '--output', str(tmp_path / 'out')]) == 1
            report = json.loads(capsys.readouterr().out)
            assert report['profile_passed'] is False and report['release_authorized'] is False
        return
    if case.startswith('policy_'):
        (tmp_path / '.apatch').mkdir()
        (tmp_path / '.apatch/conformance.json').write_text(json.dumps({
            'enabled': True, 'mode': 'blocking', 'block_on': ['drifted']}))
        raw = {'mode': 'blocking', 'block_on': ['drifted'], 'contract_holds': False, 'ok': False,
               'buckets': {'conformant': 1, 'drifted': 1}, 'per_spec': [
                   {'spec': 'SPEC-PASS-1', 'conformance': 'conformant'},
                   {'spec': 'SPEC-FAIL-1', 'conformance': 'drifted'}]}
        if case == 'policy_valid':
            q.validate_contract_policy(tmp_path, raw)
            return
        if case == 'policy_mode': raw['mode'] = 'advisory'
        elif case == 'policy_block_on': raw['block_on'] = []
        elif case == 'policy_holds': raw['contract_holds'] = True
        elif case == 'policy_ok': raw['ok'] = True
        else: raw['buckets'] = {'conformant': 2}
        with pytest.raises(q.InventoryError): q.validate_contract_policy(tmp_path, raw)
        return
    if case == 'runtime_probe':
        result = q.inspect_installed_runtime()
        assert result['ok'] and 'apatch/__init__.py' in result['files']
        assert result['versions']['apatch'] and result['dependencies']['trustchain']
        return
    if case.startswith('runtime_'):
        source = tmp_path / 'public'
        (source / 'scripts').mkdir(parents=True)
        (source / 'docs').mkdir()
        (source / 'scripts/qualify_oss.py').write_bytes(Path(q.__file__).read_bytes())
        (source / 'docs/template.md').write_text('canonical resource')
        (source / 'setup.py').write_text("CONSUMER_ASSETS = ('docs/template.md',)\n")
        (source / 'pyproject.toml').write_text('[project]\nversion="0.0.1"\n')
        files = {path.relative_to(source).as_posix(): q.sha256(path.read_bytes())
                 for path in source.rglob('*') if path.is_file()}
        (source / 'PUBLIC-SOURCE-MANIFEST.json').write_text(json.dumps({
            'private_git_history_included': False,
            'files': [{'path': name, 'sha256': digest} for name, digest in files.items()]}))
        inv = {'runtime_files': {'apatch/__init__.py': 'b' * 64}}
        installed = {'ok': True, 'files': {**inv['runtime_files'],
            'apatch/_consumer_assets/docs/template.md': files['docs/template.md']},
            'versions': {'apatch': '0.0.1'}, 'dependencies': {
                name: name != 'avatar_contract' for name in
                ('avatar_contract', 'trustchain', 'mcp', 'cryptography', 'pytest', 'build')}}
        binary = tmp_path / 'bin'
        binary.mkdir()
        executable = str(binary / 'python')
        (binary / 'apatch').write_text('#!' + executable + '\nfrom apatch.cli import cli\n')
        monkeypatch.setattr(q.sys, 'executable', executable)
        monkeypatch.setattr(q, 'verify_inventory_source', lambda *args: {'inventory_validated': True})
        monkeypatch.setattr(q, 'inspect_installed_runtime', lambda: installed)
        if case == 'runtime_valid':
            assert q.qualification_prerequisite(source, inv, 'standalone') == installed
            return
        if case == 'runtime_old': installed['files'] = {}
        elif case == 'runtime_changed': installed['files']['apatch/__init__.py'] = 'c' * 64
        elif case == 'runtime_extra': installed['files']['apatch/foreign.py'] = 'b' * 64
        elif case == 'runtime_version': installed['versions']['apatch'] = 'old'
        elif case == 'runtime_missing_dependency': installed['dependencies']['trustchain'] = False
        elif case == 'runtime_peer_present': installed['dependencies']['avatar_contract'] = True
        elif case == 'runtime_console': (binary / 'apatch').write_text('#!/wrong/python\n')
        elif case == 'runtime_assets': installed['files']['apatch/_consumer_assets/docs/template.md'] = 'c' * 64
        elif case == 'runtime_runner': (source / 'scripts/qualify_oss.py').write_text('unreviewed')
        else: raise AssertionError(case)
        with pytest.raises(q.InventoryError): q.qualification_prerequisite(source, inv, 'standalone')
        return
    source, output = tmp_path / 'public', tmp_path / 'evidence'
    (source / 'docs').mkdir(parents=True)
    inv, evidence = _standalone_evidence()
    (source / 'docs/oss-verification-profiles.json').write_text(json.dumps(inv))
    evidence['suite_process'] = {'exit_code': 1}
    evidence['source_files']['PUBLIC-SOURCE-MANIFEST.json'] = 'a' * 64
    calls = []
    before = {'ok': True, 'versions': {'apatch': 'fixture'}}
    def prerequisite(*args):
        calls.append('prerequisite')
        if case == 'report_prerequisite': raise q.InventoryError('not installed')
        if case == 'report_runtime_changed' and calls.count('prerequisite') > 1: return {'changed': True}
        return before
    def collect(root, destination, **kwargs):
        calls.append('collect')
        destination.mkdir()
        (destination / 'raw-failure.stdout').write_text('original raw evidence')
        if case == 'report_timeout': raise q.InventoryError('full suite timed out')
        return evidence
    def peer(*args):
        calls.append('peer')
        raise q.InventoryError('canonical checkout does not match declared pin')
    monkeypatch.setattr(q, 'qualification_prerequisite', prerequisite)
    monkeypatch.setattr(q, 'collect_complete_run', collect)
    monkeypatch.setattr(q, 'avatar_prerequisite', peer)
    if case == 'report_new_failure':
        evidence['suite']['outcomes']['tests/test_new.py::test_regression'] = {'outcome': 'failed'}
    if case == 'report_reused_output':
        output.mkdir()
        (output / 'qualification.json').write_text('preserve existing evidence')
        with pytest.raises(FileExistsError): q.run_profile('standalone', source, output)
        assert (output / 'qualification.json').read_text() == 'preserve existing evidence'
        assert calls == []
        return
    profile = 'avatar' if case == 'report_peer_prerequisite' else 'standalone'
    report = q.run_profile(profile, source, output,
        avatar_checkout=tmp_path / 'private' if case == 'report_no_private_standalone' else None)
    assert json.loads((output / 'qualification.json').read_text()) == report
    assert report['release_authorized'] is False and report['external_acceptance'] == 'not_checked'
    assert report['profile_passed'] is (case == 'report_pass')
    assert report['complete'] is (case == 'report_pass')
    if case in {'report_prerequisite', 'report_peer_prerequisite', 'report_no_private_standalone'}:
        assert 'collect' not in calls
        assert report['raw_suite_passed'] is None and report['raw_contract_holds'] is None
    else:
        assert (output / 'complete/raw-failure.stdout').read_text() == 'original raw evidence'
        if case != 'report_timeout':
            assert report['raw_suite_passed'] is False and report['raw_contract_holds'] is False
