"""Bounded own-file recovery after a signing exception; ledger evidence is retained."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from apatch.runtime.effects import EffectRecoveryError


class NotarizationFailure(EffectRecoveryError):
    def __init__(self, result):
        super().__init__(result['error_type'], result['error'])
        self.result = result


def _image(path):
    if not path.exists() and not path.is_symlink():
        return None
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('unsafe file')
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'mode': stat.S_IMODE(path.stat().st_mode)}


def _canonical(path, root):
    if path != path.resolve() or not path.is_relative_to(root) or path == root:
        raise ValueError('outside own workspace')
    return path


def capture_targets(tui):
    root = Path(tui.target_dir).resolve()
    return {str(_canonical(Path(p), root)): _image(Path(p))
            for p in tui._final_applied_paths()}


def recover_notarization_exception(tui, error, head_before, targets):
    """Never reset the ledger, sign again, or claim rollback without byte checks."""
    from apatch.strict_existing_signer import ExistingSignerRefused
    root = Path(tui.target_dir).resolve()
    checkpoint = tui.backup_mgr.session_id
    known = isinstance(error, ExistingSignerRefused)
    typed = isinstance(error, (ExistingSignerRefused, EffectRecoveryError))
    code = getattr(error, 'code', '') if typed else ''
    code = code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,95}', code) else 'NOTARIZATION_EXCEPTION'
    try:
        head_after = tui.tc_helper.ledger_head()
    except Exception:
        head_after = None
    ambiguous = (not known or error.reconciliation_required or head_before is None
                 or head_after is None or head_after != head_before)
    restored, failed, expected = [], [], {}
    try:
        folder = _canonical(root/'.apatch/backups'/checkpoint, root)
        metadata = json.loads((folder/'metadata.json').read_bytes())
        owner = tui.backup_mgr.governed_session_id
        if not owner or metadata.get('session_id') != checkpoint or metadata.get('governed_session_id') != owner:
            raise ValueError('backup ownership mismatch')
        for entry in metadata['backups']:
            original = _canonical(Path(entry['original_file']), root)
            if str(original) in expected:
                continue  # Earliest image is the chunk's actual preimage.
            action = entry.get('action')
            if action == 'created':
                expected[str(original)] = (None, None)
            elif action in ('modified', 'deleted'):
                name = entry.get('backup_file')
                if not isinstance(name, str) or Path(name).name != name:
                    raise ValueError('unsafe backup')
                backup = _canonical(folder/name, folder)
                image = _image(backup)
                if image is None:
                    raise ValueError('missing backup')
                expected[str(original)] = (backup, image)
            else:
                raise ValueError('unknown backup action')
        # prepare_effect has already backed up its not-yet-written target.
        # Such an extra entry may be left alone only if its exact preimage
        # is still present; arbitrary additional changed backups fail closed.
        for name in set(expected) - set(targets):
            if _image(Path(name)) != expected[name][1]:
                raise ValueError('untracked changed backup target')
            del expected[name]
        if set(expected) != set(targets):
            raise ValueError('incomplete backup set')
        for name, (backup, image) in expected.items():
            path = Path(name);temporary = None
            try:
                if image is not None:
                    descriptor, temporary = tempfile.mkstemp(prefix='.apatch-restore-', dir=path.parent)
                    os.close(descriptor)
                    shutil.copy2(backup, temporary)
                    with open(temporary, 'rb') as stream:
                        os.fsync(stream.fileno())
                scope = tui._write_scope(name) if hasattr(tui, '_write_scope') else nullcontext()
                with scope:
                    if _image(path) != targets[name]:
                        raise ValueError('target changed after signing attempt')
                    if image is None:
                        if path.exists():path.unlink()
                    else:
                        os.replace(temporary, path);temporary = None
                    if _image(path) != image:
                        raise ValueError('restored image mismatch')
                restored.append(str(path.relative_to(root)))
            except Exception:
                failed.append(str(path.relative_to(root)))
            finally:
                if temporary is not None:
                    try:os.unlink(temporary)
                    except OSError:pass
    except Exception:
        failed = sorted(str(Path(p).relative_to(root)) for p in targets)
    complete = not failed and len(restored) == len(targets)
    tui.verify_rollback = complete
    for entry in tui.report_entries:
        if entry.get('outcome') == 'applied' and entry.get('resolved_path') in targets:
            entry['outcome'] = 'rolled_back' if str(Path(entry['resolved_path']).relative_to(root)) in restored else 'error'
            entry['reason'] = 'notarization_exception' if complete else 'notarization_exception_recovery_incomplete'
    result = {'ok': False, 'continue': False, 'phase': 'blocked',
              'error_type': 'NOTARIZATION_FAILED' if complete else 'NOTARIZATION_ROLLBACK_INCOMPLETE',
              'cause_type': 'EXISTING_SIGNER_REFUSED' if known else 'EFFECT_RECOVERY_ERROR' if typed else 'NOTARIZATION_EXCEPTION',
              'cause_code': code, 'committed': None if ambiguous else False,
              'reconciliation_required': ambiguous, 'ledger_preserved': True,
              'checkpoint': checkpoint, 'rollback_performed': complete and bool(targets),
              'source_restored': complete,
              'verify_rollback': complete, 'restored_files': sorted(restored),
              'failed_restore_files': sorted(set(failed)), 'recoverable': True,
              'recommended_action': 'reconcile_native_commit' if ambiguous else 'verify_existing_signer_binding',
              'error': 'Signing failed; own file recovery evidence retained. No signing retry was performed.'}
    evidence = getattr(tui.tc_helper, 'last_commit_evidence', None)
    if isinstance(evidence, dict):
        for field in ('signing_request', 'signing_result'):
            if isinstance(evidence.get(field), dict):
                result[field] = evidence[field]
    if not complete:result['recommended_action'] = 'recover_owned_checkpoint'
    tui.notarization_failure = result
    tui._write_report()
    return NotarizationFailure(result)
