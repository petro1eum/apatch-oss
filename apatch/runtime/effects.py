"""Signed pre-effect intents and recovery of interrupted native apply chunks.

The journal is a locator, never authority. Recovery uses authenticated SDK rows,
exact postimages, native session capability and a reacquired path lease. It never
accepts a phase marker as proof that a source mutation was notarized.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from apatch.runtime.atomic_io import atomic_write_json, exclusive_file_lock
from apatch.sdd_integrity import canonical_hash

SCHEMA = 'apatch.source-effect.v1'


class EffectRecoveryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _image(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise EffectRecoveryError('EFFECT_PATH_UNSAFE', 'Effect target must be an exact regular file')
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for data in iter(lambda: source.read(1 << 20), b''):
            digest.update(data)
    return {'sha256': digest.hexdigest(), 'mode': stat.S_IMODE(info.st_mode)}


def _path(root, relative):
    if not isinstance(relative, str) or not relative:
        raise EffectRecoveryError("EFFECT_PATH_UNSAFE", "Invalid relative effect target")
    path = Path(root) / relative
    if (Path(relative).is_absolute()
            or '..' in Path(relative).parts or path.parent.resolve() != path.parent.absolute()
            or path.relative_to(root).as_posix() != relative):
        raise EffectRecoveryError('EFFECT_PATH_UNSAFE', 'Effect target escaped the exact workspace')
    return path


def _journal(root, plan):
    for field in ['governed_session_id', 'checkpoint']:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', plan[field]) or plan[field] in {'.', '..'}:
            raise EffectRecoveryError('EFFECT_ID_INVALID', 'Unsafe effect session identifier')
    effect_id = canonical_hash(plan)[7:]
    path = Path(root) / '.apatch/source-effects' / plan['governed_session_id'] / (effect_id + '.json')
    for parent in [path.parent, path.parent.parent, path.parent.parent.parent]:
        if parent.exists() and parent.resolve() != parent.absolute():
            raise EffectRecoveryError('EFFECT_PATH_UNSAFE', 'Effect journal cannot follow aliases')
    return path


def _signed_rows(root, *, tool_ids, session_id):
    """Authenticate native file-ledger rows with the current captured identity."""
    from apatch.trustchain_helper import TrustChainHelper
    helper = TrustChainHelper(str(root), auto_init=False)
    if Path(helper.workspace_root).resolve() != Path(root).resolve():
        raise EffectRecoveryError('EFFECT_WORKSPACE_MISMATCH', 'Recovery ledger is outside the exact workspace')
    identity = helper._commit_identity()
    if identity is None:
        raise EffectRecoveryError('EFFECT_SIGNER_MISSING', 'Recovery requires the configured native signing identity')
    rows = []
    objects = Path(helper.trustchain_dir) / 'objects'
    if objects.resolve() != objects.absolute():
        raise EffectRecoveryError('EFFECT_PATH_UNSAFE', 'Recovery ledger cannot follow aliases')
    for path in sorted(objects.glob('op_*.json')):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise EffectRecoveryError('EFFECT_PATH_UNSAFE', 'Recovery receipt is not a regular file')
        wrapped = json.loads(path.read_bytes())
        raw = wrapped.get('value', wrapped)
        if (raw.get('tool') not in tool_ids
                or (raw.get('data') or {}).get('governed_session_id') != session_id):
            continue
        matches, valid = helper._pinned_record(raw, identity)
        if not matches or not valid:
            raise EffectRecoveryError('EFFECT_SIGNATURE_INVALID', 'A native mutation record failed configured-key verification')
        rows.append({'payload': raw.get('data') or {}, 'signature': raw['signature'],
                     'object_path': 'objects/' + path.name, 'timestamp': raw.get('timestamp') or ''})
    return rows


def prepare_effect(tui, target, after, action):
    """Sign exact pre/postimages before the atomic effect; no source write here."""
    if not tui.write_lease or not tui.tc_helper.has_trustchain():
        return None
    from apatch.apply_session import default_session_path, load_session
    from apatch.session_state import load_session_state
    cursor = load_session(default_session_path(tui.target_dir))
    # Non-chunked component users retain their existing path; this journal
    # qualifies resumable apply_session chunks, never unrelated operations.
    if cursor is None:
        return None
    state = load_session_state(tui.target_dir)
    sid = str(state.get('session_id') or '')
    if not sid or sid != tui.write_lease.get('governed_session_id'):
        raise EffectRecoveryError('EFFECT_SESSION_MISMATCH', 'Effect lease and governed session differ')
    if cursor.get('governed_session_id') != sid:
        raise EffectRecoveryError('EFFECT_SESSION_MISMATCH', 'Active cursor and governed session differ')
    root = Path(tui.target_dir).resolve()
    relative = Path(target).absolute().relative_to(root).as_posix()
    path = _path(root, relative)
    logs = Path(cursor['logs_path'])
    index = int(cursor['chunk_index'])
    steps = sorted(set(candidate.step_index for candidate in tui.candidates))
    if steps != sorted(cursor['chunks'][index]):
        raise EffectRecoveryError('EFFECT_CHUNK_MISMATCH', 'Active candidates differ from the durable chunk')
    plan = {'schema': SCHEMA, 'workspace': str(root), 'governed_session_id': sid,
            'checkpoint': tui.backup_mgr.session_id, 'target': relative, 'action': action,
            'before': _image(path), 'after': after, 'candidate_index': tui._effect_candidate_index,
            'candidate_count': len(tui.candidates), 'chunk_index': index, 'chunk_steps': steps,
            'logs_path': str(logs), 'logs_sha256': hashlib.sha256(logs.read_bytes()).hexdigest(),
            'report_path': os.path.relpath(tui.report_path, root) if tui.report_path else None}
    effect_id = canonical_hash(plan)
    payload = {'action': 'effect_prepared', 'session_id': tui.backup_mgr.session_id,
               'governed_session_id': sid, 'effect_id': effect_id, 'effect_plan': plan}
    from apatch.notarization_failure import capture_targets, recover_notarization_exception
    targets = capture_targets(tui)
    try:
        head_before = tui.tc_helper.ledger_head()
    except Exception:
        head_before = None
    try:
        if not tui.tc_helper.commit_action('apatch', payload):
            raise EffectRecoveryError('EFFECT_PREPARATION_UNSIGNED', 'No source effect may precede its durable signed intent')
        evidence = tui.tc_helper.last_commit_evidence
        if not evidence.get('persisted'):
            raise EffectRecoveryError('EFFECT_PREPARATION_UNSIGNED', 'Signed effect intent was not durably observed')
    except Exception as error:
        # Earlier candidates in this chunk may already have written source.
        raise recover_notarization_exception(tui, error, head_before, targets) from None
    atomic_write_json(str(_journal(root, plan)), {'plan': plan, 'effect_id': effect_id,
                     'state': 'prepared', 'prepare_receipt': evidence})
    return plan


def assert_effect_preimage(root, plan):
    if plan is not None and _image(_path(Path(root), plan['target'])) != plan['before']:
        raise EffectRecoveryError('EFFECT_PREIMAGE_CHANGED', 'Source changed after the exact effect was signed')


def written_effect(tui, plan):
    if plan is None:
        return
    path = _path(Path(tui.target_dir), plan['target'])
    if _image(path) != plan['after']:
        raise EffectRecoveryError('EFFECT_POSTIMAGE_CHANGED', 'Written source differs from the signed effect')
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    effect_id = canonical_hash(plan)
    atomic_write_json(str(_journal(tui.target_dir, plan)), {'plan': plan, 'effect_id': effect_id, 'state': 'written'})
    tui._effect_ids.append(effect_id)


def recover_source_effects(runtime):
    binding = runtime._capture_binding('recover_source_effects')
    if binding is None:
        raise EffectRecoveryError('EFFECT_SESSION_MISMATCH', 'Native recovery capability is required')
    # Serialize receipt reconciliation for this exact session, independently of
    # the locator. A killed recovery releases this OS lock; its signed receipt
    # survives and suppresses another signing operation on retry.
    lock = _journal(runtime.target_dir, {'governed_session_id': binding.session_id,
                                        'checkpoint': 'recovery'})
    with exclusive_file_lock(str(lock)):
        return _recover_source_effects(runtime)


def _recover_source_effects(runtime):
    """Notarize only an authenticated complete interrupted chunk, exactly once.

    Incomplete candidate sets and divergent postimages are explicit blocks. They
    cannot be silently promoted to a completed cursor or an attestation.
    """
    from apatch.apply_session import default_session_path, load_session, save_session
    from apatch.enforcement import record_notarized_files
    from apatch.sandbox import acquire_lease, release_lease
    from apatch.trustchain_helper import TrustChainHelper
    root = Path(runtime.target_dir).resolve()
    binding = runtime._capture_binding('recover_source_effects')
    if binding is None:
        raise EffectRecoveryError('EFFECT_SESSION_MISMATCH', 'Recovery requires the exact native session capability')
    cursor_path = default_session_path(str(root))
    cursor = load_session(cursor_path)
    if (not cursor or cursor.get('governed_session_id') != binding.session_id
            or int(cursor.get('chunk_index', 0)) >= len(cursor.get('chunks') or [])):
        return {'ok': True, 'recovery': 'not_applicable'}
    index = int(cursor['chunk_index'])
    rows = _signed_rows(str(root), tool_ids={'apatch'}, session_id=binding.session_id)
    plans = {}
    for row in rows:
        payload = row['payload']
        plan = payload.get('effect_plan')
        if payload.get('action') != 'effect_prepared' or not isinstance(plan, dict):
            continue
        if plan.get('governed_session_id') != binding.session_id or plan.get('chunk_index') != index:
            continue
        effect_id = canonical_hash(plan)
        if (plan.get('schema') != SCHEMA or plan.get('workspace') != str(root)
                or payload.get('governed_session_id') != binding.session_id
                or payload.get('effect_id') != effect_id):
            raise EffectRecoveryError('EFFECT_INTENT_INVALID', 'Signed effect scope differs from this workspace and session')
        if effect_id in plans:
            raise EffectRecoveryError('EFFECT_INTENT_AMBIGUOUS', 'Duplicate signed effect intent requires owner reconciliation')
        plans[effect_id] = plan
    if not plans:
        return {'ok': True, 'recovery': 'not_applicable'}
    ordered = sorted(plans.items(), key=lambda item: item[1]['candidate_index'])
    first = ordered[0][1]
    count = first['candidate_count']
    if not isinstance(count, int) or count < 1 or count > 10000:
        raise EffectRecoveryError('EFFECT_CHUNK_INVALID', 'Signed candidate count is invalid')
    common = ['checkpoint', 'candidate_count', 'chunk_index', 'chunk_steps', 'logs_path', 'logs_sha256', 'report_path']
    if any(any(plan[field] != first[field] for field in common) for _, plan in ordered):
        raise EffectRecoveryError('EFFECT_CHUNK_AMBIGUOUS', 'Interrupted effects belong to different apply attempts')
    if [plan['candidate_index'] for _, plan in ordered] != list(range(1, count + 1)):
        raise EffectRecoveryError('EFFECT_CHUNK_INCOMPLETE', 'Only part of the interrupted chunk has signed effect intents; retain checkpoint')
    if (first['chunk_steps'] != sorted(cursor['chunks'][index])
            or first['logs_path'] != cursor['logs_path']
            or hashlib.sha256(Path(cursor['logs_path']).read_bytes()).hexdigest() != first['logs_sha256']):
        raise EffectRecoveryError('EFFECT_LOG_DRIFT', 'Interrupted apply cursor or signed patch log changed')
    from apatch.runtime.atomic_io import read_json_file
    # The signed checkpoint must still be an owned rollback route. Never
    # advance a cursor based on another session's backup metadata.
    _journal(root, first)
    checkpoint_dir = root / '.apatch/backups' / first['checkpoint']
    if checkpoint_dir.resolve() != checkpoint_dir.absolute():
        raise EffectRecoveryError('EFFECT_PATH_UNSAFE', 'Backup checkpoint cannot follow aliases')
    backup = read_json_file(str(checkpoint_dir / 'metadata.json'), {})
    if backup.get('governed_session_id') != binding.session_id or backup.get('session_id') != first['checkpoint']:
        raise EffectRecoveryError('EFFECT_CHECKPOINT_MISMATCH', 'Interrupted checkpoint belongs to another governed session')
    latest = {}
    for _, plan in ordered:
        target = plan['target']
        _path(root, target)
        if target in latest and latest[target]['after'] != plan['before']:
            raise EffectRecoveryError('EFFECT_CHAIN_INVALID', 'Same-file effects do not form an exact signed sequence')
        latest[target] = plan
    # The existing notarized index has no authenticated deletion representation.
    # Keep that branch fail-closed instead of inventing an accepted delete hash.
    if any(plan['after'] is None for plan in latest.values()):
        raise EffectRecoveryError('EFFECT_DELETE_RECONCILIATION_REQUIRED', 'Interrupted deletion requires native checkpoint rollback')
    lease = acquire_lease(str(root), sorted(latest), tool='apatch_recover_session',
                          governed_session_id=binding.session_id, session_checkpoint=first['checkpoint'])
    try:
        for target, plan in latest.items():
            if _image(_path(root, target)) != plan['after']:
                raise EffectRecoveryError('EFFECT_POSTIMAGE_CHANGED', 'Interrupted source is not the exact signed postimage; preserve foreign bytes')
        files = {target: {'sha256': plan['after']['sha256'], 'mode': f"{plan['after']['mode']:03o}"}
                 for target, plan in latest.items()}
        matching = [row for row in rows if row['payload'].get('governed_session_id') == binding.session_id
                    and set(plans).issubset(set(row['payload'].get('effect_ids') or []))
                    and isinstance(row['payload'].get('files'), dict)
                    and all(row['payload']['files'].get(target, {}).get('sha256') == meta['sha256']
                            for target, meta in files.items())]
        if len(matching) > 1:
            # Native incremental and final chunk summaries may both contain the
            # exact effect set. They are existing receipts, never another sign.
            matching.sort(key=lambda row: row['timestamp'])
        if matching:
            receipt = matching[-1]
        else:
            helper = TrustChainHelper(str(root), auto_init=False)
            payload = {'action': 'recovered_source_effects', 'session_id': first['checkpoint'],
                       'governed_session_id': binding.session_id, 'checkpoint_ref': first['checkpoint'],
                       'effect_ids': list(plans), 'applied_patches': len(plans), 'skipped_patches': 0, 'files': files}
            if not helper.commit_action('apatch', payload):
                raise EffectRecoveryError('EFFECT_RECEIPT_UNCONFIRMED', 'Recovery signing outcome must be reconciled before retry')
            found = [row for row in _signed_rows(str(root), tool_ids={'apatch'}, session_id=binding.session_id)
                     if row['payload'].get('governed_session_id') == binding.session_id
                     and row['payload'].get('action') == 'recovered_source_effects'
                     and row['payload'].get('effect_ids') == list(plans)
                     and row['payload'].get('files') == files]
            if len(found) != 1:
                raise EffectRecoveryError('EFFECT_RECEIPT_UNCONFIRMED', 'Recovery requires one exact authenticated mutation receipt')
            receipt = found[0]
        for target, plan in latest.items():
            if _image(_path(root, target)) != plan['after']:
                raise EffectRecoveryError('EFFECT_POSTIMAGE_CHANGED', 'Source changed while recovery was being signed')
        record_notarized_files(str(root), files, signature=receipt['signature'], object_path=receipt['object_path'],
                               governed_session_id=binding.session_id)
        with exclusive_file_lock(cursor_path):
            current = load_session(cursor_path)
            if current != cursor:
                raise EffectRecoveryError('EFFECT_CURSOR_CHANGED', 'Another operation changed the apply cursor; retain signed receipt')
            cursor['chunk_index'] = index + 1
            cursor['last_checkpoint'] = first['checkpoint']
            cursor['checkpoints'] = list(dict.fromkeys([*cursor.get('checkpoints', []), first['checkpoint']]))
            cursor['recovered_effect_ids'] = list(plans)
            save_session(cursor_path, cursor)
        more = cursor['chunk_index'] < len(cursor['chunks'])
        return runtime._finish('apatch_apply_session', {'ok': True, 'continue': more,
                    'checkpoint': first['checkpoint'], 'phase': 'apply' if more else 'done',
                    'recovery': 'source_effects_notarized', 'mutation_receipt': receipt['object_path'],
                    'chunk_result': {'applied': len(plans), 'failed': 0, 'skipped': 0}})
    finally:
        release_lease(str(root), lease_id=lease['lease_id'], governed_session_id=binding.session_id)
