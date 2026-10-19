"""Mandatory MCP caller admission, separate from the governed executor identity.

The relying party owns each 20-second challenge. The local host hook signs it
with the existing chat key and obtains a purpose-separated Platform permit.
Neither possession nor an identity presentation constitutes an operation grant.
Private arguments stay local; only a salted commitment reaches the authority.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID, ObjectIdentifier

DOMAIN = b'TrustChain-Apatch-Caller-Possession\x00v1\x00'
CONFIG_SCHEMA = 'apatch.passport-admission.config.v1'
UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
CAPS = ObjectIdentifier('1.3.6.1.4.1.99999.4')


# Public diagnostics are an allowlist. Never serialize exception text, URLs,
# response bodies, identities, local paths, arguments, certificates or keys.
_CAUSE_ACTIONS = {
    'admission_refused': 'inspect_local_admission_configuration',
    'actor_not_registered': 'register_configured_actor',
    'wrong_actor': 'use_configured_actor_and_chat',
    'challenge_expired': 'request_fresh_challenge',
    'challenge_replayed': 'request_fresh_challenge',
    'scope_denied': 'use_authorized_workspace_and_tool',
    'certificate_revoked': 'renew_actor_enrollment',
    'certificate_inactive': 'refresh_actor_enrollment',
    'certificate_expired': 'renew_actor_certificate',
    'invalid_signature': 'check_registered_signer',
    'permission_expired': 'request_fresh_permission',
    'approval_required': 'obtain_required_human_approval',
    'clock_mismatch': 'synchronize_registry_and_host_clocks',
    'revocation_data_stale': 'refresh_registry_revocation_data',
    'challenge_unavailable': 'check_isolated_challenge_endpoint',
    'transport_timeout': 'check_authority_connectivity_then_retry',
    'transport_unavailable': 'check_authority_connectivity_then_retry',
    'http_401': 'check_authority_authentication',
    'http_403': 'check_authority_permission',
    'http_404': 'check_configured_authority_endpoint',
    'http_429': 'retry_after_authority_rate_limit',
    'http_500': 'retry_after_authority_recovery',
    'http_502': 'retry_after_authority_recovery',
    'http_503': 'retry_after_authority_recovery',
    'http_504': 'retry_after_authority_recovery',
    'http_error': 'inspect_authority_http_status',
}
_REASON_CAUSES = {
    'actor_not_registered': 'actor_not_registered',
    'wrong_actor': 'wrong_actor',
    'challenge_expired': 'challenge_expired',
    'scope_denied': 'scope_denied',
    'chat has no passport enrollment': 'actor_not_registered',
    'chat has no configured passport scope': 'scope_denied',
    'identity mismatch': 'wrong_actor',
    'passport signer mismatch': 'wrong_actor',
    'passport chat binding refused': 'wrong_actor',
    'authority identity selection refused': 'wrong_actor',
    'current holder key mismatch': 'wrong_actor',
    'holder certificate changed': 'wrong_actor',
    'challenge policy or subject binding refused': 'wrong_actor',
    'workspace is outside caller mandate': 'scope_denied',
    'workspace changed after challenge': 'scope_denied',
    'host tool audience mismatch': 'scope_denied',
    'effective policy refused': 'scope_denied',
    'operation permission refused': 'scope_denied',
    'holder challenge expired': 'challenge_expired',
    'holder challenge expired during validation': 'challenge_expired',
    'challenge unavailable or already consumed': 'challenge_replayed',
    'challenge already consumed': 'challenge_replayed',
    'revoked certificate': 'certificate_revoked',
    'actor certificate revoked': 'certificate_revoked',
    'inactive or replaced certificate': 'certificate_inactive',
    'expired certificate': 'certificate_expired',
    'permission is not fresh': 'permission_expired',
    'human approval is required by current policy': 'approval_required',
    'CRL clock mismatch': 'clock_mismatch',
    'CRL freshness refused': 'revocation_data_stale',
    'no challenge endpoint is available': 'challenge_unavailable',
    'no live relying party accepted the challenge': 'challenge_unavailable',
    'challenge endpoint discovery timed out': 'transport_timeout',
}


class AdmissionRefused(ValueError):
    """No native tool body has run when this admission error is returned."""
    def __init__(self, reason, *, cause_code=None):
        super().__init__(reason)
        selected = cause_code if cause_code is not None else _REASON_CAUSES.get(reason)
        self.cause_code = selected if selected in _CAUSE_ACTIONS else 'admission_refused'


def public_failure(exc):
    """Bounded stable diagnostics; never forward arbitrary exception contents."""
    from cryptography.exceptions import InvalidSignature
    import urllib.error
    if isinstance(exc, AdmissionRefused):
        code = exc.cause_code
    elif isinstance(exc, urllib.error.HTTPError):
        candidate = 'http_' + str(exc.code) if type(exc.code) is int else ''
        code = candidate if candidate in _CAUSE_ACTIONS else 'http_error'
    elif isinstance(exc, TimeoutError):
        code = 'transport_timeout'
    elif isinstance(exc, urllib.error.URLError):
        code = 'transport_timeout' if isinstance(exc.reason, TimeoutError) else 'transport_unavailable'
    elif isinstance(exc, ConnectionError):
        code = 'transport_unavailable'
    elif isinstance(exc, InvalidSignature):
        code = 'invalid_signature'
    elif type(exc) is ValueError and len(exc.args) == 1 and isinstance(exc.args[0], str):
        # Compatibility with the pre-existing host adapter's literal errors.
        code = _REASON_CAUSES.get(exc.args[0], 'admission_refused')
    else:
        code = 'admission_refused'
    if code not in _CAUSE_ACTIONS:
        code = 'admission_refused'
    return {'cause_code': code, 'next_action': _CAUSE_ACTIONS[code]}


def refusal_from_response(response):
    code = response.get('cause_code') if isinstance(response, dict) else None
    if not isinstance(code, str) or code not in _CAUSE_ACTIONS:
        code = 'admission_refused'
    return AdmissionRefused('relying party refused challenge', cause_code=code)


def require(condition, reason):
    if not condition:
        raise AdmissionRefused(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now_ms():
    return time.time_ns() // 1_000_000


def decode(raw):
    def pairs(items):
        obj = {}
        for key, value in items:
            require(key not in obj, 'duplicate JSON field')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(AdmissionRefused('non-finite JSON')))


def secure_directory(path):
    path = Path(path)
    require(path.is_absolute(), 'absolute admission state directory required')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    item = path.lstat()
    require(stat.S_ISDIR(item.st_mode) and not path.is_symlink()
            and item.st_uid == os.getuid() and stat.S_IMODE(item.st_mode) == 0o700,
            'admission state custody refused')
    return path


def read_config(path, expected_hash):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        item = os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid == os.getuid()
                and not item.st_mode & 0o077 and item.st_size < 1_048_576,
                'admission configuration custody refused')
        raw = os.read(fd, 1_048_576)
    finally:
        os.close(fd)
    require(sha(raw) == expected_hash, 'admission configuration drift')
    return decode(raw)


def resolve_target(target):
    from apatch.mcp.bound_workspace import resolve_target_dir
    return str(Path(resolve_target_dir(target)).resolve())


class PublicRegistry:
    def __init__(self, base, root_pin, fetch=None):
        require(base.startswith('https://') and not base.endswith('/'), 'registry HTTPS required')
        require(re.fullmatch('[0-9a-f]{64}', root_pin), 'invalid configured root pin')
        self.base, self.root_pin = base, root_pin
        self.fetch = fetch or self._fetch

    def _fetch(self, route):
        require(route.startswith('/api/pub/'), 'unexpected public registry route')
        try:
            with urllib.request.urlopen(self.base + route, timeout=8) as response:
                raw = response.read(1_048_577)
                require(response.status == 200 and len(raw) <= 1_048_576,
                        'public registry response refused')
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and route.startswith('/api/pub/agents/'):
                raise AdmissionRefused('actor_not_registered') from None
            raise

    def snapshot(self, agent_id, proof_id, previous=None):
        """Fresh, bounded parallel public reads for ONE accept operation.

        Cache immutable bytes, never an authorization decision. The snapshot is
        not retained on the registry, challenge, disk or across requests. Every
        consumer still verifies signatures, pinned roots and current validity.
        """
        require(isinstance(agent_id, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,253}', agent_id),
                'invalid configured identity')
        require(isinstance(proof_id, str) and re.fullmatch('[0-9a-f]{16}', proof_id),
                'invalid configured passport')
        routes = ['/api/pub/root-ca', '/api/pub/ca', '/api/pub/crl',
                  '/api/pub/proofs/' + proof_id]
        for agent in (agent_id, 'platform-runtime-authorizer'):
            routes.extend(['/api/pub/agents/' + agent + '/cert', '/api/pub/agents/' + agent])
        routes = tuple(dict.fromkeys(routes))
        documents = {}
        if previous is not None:
            require(isinstance(previous, PublicRegistry)
                    and previous.base == self.base and previous.root_pin == self.root_pin,
                    'public registry snapshot binding refused')
            documents.update(previous._snapshot_documents)
            # Observe revocation/replacement after the initial validation and
            # any clock wait. Issuer/certificate bytes stay consistent, while
            # current status serials reject replacement of either identity.
            routes = ('/api/pub/crl', '/api/pub/agents/' + agent_id,
                      '/api/pub/agents/platform-runtime-authorizer')
        def read(route):
            raw = self.fetch(route)
            require(isinstance(raw, bytes) and len(raw) <= 1_048_576,
                    'public registry response refused')
            return raw
        # At most eight GETs, each retaining the transport's eight-second limit.
        # No executor survives the call and a failed future cannot dispatch.
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix='apatch-public') as pool:
            documents.update(zip(routes, pool.map(read, routes)))
        documents = MappingProxyType(documents)
        result = PublicRegistry(self.base, self.root_pin, documents.__getitem__)
        result._snapshot_documents = documents
        return result

    def check_current(self):
        # One final wall-clock sample after ALL waits. These immutable bytes
        # already passed issuer, root-pin and signature validation in identity.
        # This guard performs neither I/O nor another wait.
        current = datetime.now(timezone.utc)
        for route, raw in self._snapshot_documents.items():
            if route in ('/api/pub/root-ca', '/api/pub/ca') or route.endswith('/cert'):
                certificate = x509.load_pem_x509_certificate(raw)
                require(certificate.not_valid_before_utc <= current < certificate.not_valid_after_utc,
                        'expired certificate')
        crl = x509.load_pem_x509_crl(self._snapshot_documents['/api/pub/crl'])
        require(crl.next_update_utc is not None
                and crl.last_update_utc <= current < crl.next_update_utc,
                'CRL freshness refused')

    def identity(self, agent_id, capability=None):
        require(isinstance(agent_id, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,253}', agent_id),
                'invalid configured identity')
        root, inter, leaf = [x509.load_pem_x509_certificate(self.fetch(route))
                             for route in ['/api/pub/root-ca', '/api/pub/ca',
                                           '/api/pub/agents/' + agent_id + '/cert']]
        current = datetime.now(timezone.utc)
        require(sha(root.public_bytes(Encoding.DER)) == self.root_pin, 'untrusted issuer root')
        for child, issuer, ca in [(root, root, True), (inter, root, True), (leaf, inter, False)]:
            require(isinstance(issuer.public_key(), Ed25519PublicKey), 'unsupported issuer key')
            require(child.issuer == issuer.subject, 'issuer mismatch')
            issuer.public_key().verify(child.signature, child.tbs_certificate_bytes)
            require(child.not_valid_before_utc <= current < child.not_valid_after_utc, 'expired certificate')
            require(child.extensions.get_extension_for_class(x509.BasicConstraints).value.ca == ca,
                    'certificate role mismatch')
        for issuer in [root, inter]:
            require(issuer.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign,
                    'issuer cannot certify')
        require(inter.extensions.get_extension_for_class(x509.KeyUsage).value.crl_sign
                and leaf.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature,
                'certificate key usage refused')
        require([a.value for a in leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)] == [agent_id]
                and isinstance(leaf.public_key(), Ed25519PublicKey), 'identity mismatch')
        crl = x509.load_pem_x509_crl(self.fetch('/api/pub/crl'))
        require(crl.issuer == inter.subject, 'CRL issuer mismatch')
        inter.public_key().verify(crl.signature, crl.tbs_certlist_bytes)
        # This existing registry issues its CRL at whole-second resolution.
        ahead = (crl.last_update_utc - datetime.now(timezone.utc)).total_seconds()
        require(ahead <= 5, 'CRL clock mismatch')
        if ahead > 0:
            time.sleep(min(ahead + .01, 5))
        require(crl.next_update_utc is not None and crl.last_update_utc <= datetime.now(timezone.utc) < crl.next_update_utc,
                'CRL freshness refused')
        for child in [leaf, inter]:
            require(crl.get_revoked_certificate_by_serial_number(child.serial_number) is None,
                    'revoked certificate')
        status = decode(self.fetch('/api/pub/agents/' + agent_id))
        require(status.get('status') != 'revoked', 'actor certificate revoked')
        require(status.get('agent_id') == agent_id and status.get('status') == 'active'
                and str(status.get('serial')) == str(leaf.serial_number), 'inactive or replaced certificate')
        try:
            caps = decode(leaf.extensions.get_extension_for_oid(CAPS).value.value)
        except x509.ExtensionNotFound:
            caps = []
        require(isinstance(caps, list), 'invalid certified capabilities')
        if capability:
            require(capability in caps, 'authority certificate lacks its signing purpose')
        # Fetches and a future CRL wait may have crossed a certificate expiry.
        # Recheck every certificate at return time, not the pre-network clock.
        current = datetime.now(timezone.utc)
        for certificate in (root, inter, leaf):
            require(certificate.not_valid_before_utc <= current < certificate.not_valid_after_utc,
                    'expired certificate')
        raw_key = leaf.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return {'leaf': leaf, 'key_id': sha(raw_key)[:32], 'public_key': raw_key,
                'certificate_digest': sha(leaf.public_bytes(Encoding.DER)), 'capabilities': caps}

    def passport(self, subject, chat):
        agent = subject['agent_id']
        identity = self.identity(agent)
        proof_id = subject['passport_proof_id']
        require(re.fullmatch('[0-9a-f]{16}', proof_id), 'invalid configured passport')
        proof = decode(self.fetch('/api/pub/proofs/' + proof_id))
        require(proof.get('agent_id') == agent, 'passport signer mismatch')
        data, signature = proof['data'], proof['signature']
        identity['leaf'].public_key().verify(base64.b64decode(signature, validate=True), data.encode())
        require(sha((signature + data).encode())[:16] == proof_id, 'passport proof id mismatch')
        card = decode(data)
        require(card.get('schema') == 'trustchain.executor.chat-passport.v1'
                and card.get('purpose') == 'passport-presentation'
                and card.get('Учётная запись') == agent and card.get('Чат') == chat,
                'passport chat binding refused')
        require(card.get('Сертификат') == str(identity['leaf'].serial_number)
                and card.get('Отпечаток сертификата') == identity['certificate_digest']
                and card.get('Ключ') == identity['key_id'], 'passport certificate binding refused')
        require(card.get('execution_grant') is False and card.get('Полномочия в сертификате') == identity['capabilities'],
                'passport is not an operation mandate')
        return identity


class CallAdmission:
    def __init__(self, policy, mcp, *, fetch=None, publish=True):
        require(policy.get('schema') == CONFIG_SCHEMA, 'unsupported admission policy')
        require(re.fullmatch('[A-Za-z0-9_-]{1,96}', policy.get('server', '')), 'invalid server audience')
        require(policy['authorization_base'].startswith('https://'), 'authorization HTTPS required')
        require(isinstance(policy.get('subjects'), dict) and policy['subjects'], 'no authorized chat subjects')
        for chat, subject in policy['subjects'].items():
            require(UUID.fullmatch(chat) and subject['agent_id'] == 'codex-chat-' + chat
                    and isinstance(subject.get('tenant_id'), str) and subject['tenant_id'], 'invalid subject scope')
        self.policy = policy
        self.registry = PublicRegistry(policy['registry_base'], policy['root_pin'], fetch)
        self.state = secure_directory(policy['state_dir'])
        self.challenges = secure_directory(self.state / 'challenges')
        self.used = secure_directory(self.state / 'used')
        self.policy_digest = sha(canonical(policy))
        self.roots = frozenset(str(Path(r).resolve()) for r in policy['workspace_roots'])
        require(self.roots, 'no authorized workspace roots')
        self.mcp, self.publish = mcp, publish
        self.pending = {}
        self.lock = threading.RLock()
        self.tools = dict(mcp._tool_manager._tools)
        self.argument_models = {n: t.fn_metadata.arg_model for n, t in self.tools.items()}

    @contextmanager
    def _namespace_lock(self):
        # This inode must never be replaced or unlinked while the namespace lives.
        with self.lock:
            for directory in [self.state, self.challenges, self.used]:
                secure_directory(directory)
            fd = os.open(self.state / 'namespace.lock',
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                self._private_file(fd)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    @staticmethod
    def _private_file(fd):
        item = os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid == os.getuid()
                and stat.S_IMODE(item.st_mode) == 0o600 and item.st_nlink == 1
                and item.st_size <= 1_048_576, 'challenge record custody refused')

    @staticmethod
    def _sync_directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _pack_record(self, record):
        return {'schema': 'apatch.passport-admission.pending.v1',
                'policy_digest': record['policy_digest'],
                'challenge': record['challenge'], 'request': record['request'],
                'subject': record['subject'],
                'salt_b64': base64.b64encode(record['salt']).decode(),
                'consumed': record['consumed']}

    def _read_record(self, challenge_id):
        require(isinstance(challenge_id, str)
                and re.fullmatch('[0-9a-f]{32}', challenge_id), 'invalid challenge id')
        fd = os.open(self.challenges / (challenge_id + '.json'),
                     os.O_RDONLY | os.O_NOFOLLOW)
        try:
            self._private_file(fd)
            raw = os.read(fd, 1_048_577)
        finally:
            os.close(fd)
        data = decode(raw)
        require(set(data) == {'schema', 'policy_digest', 'challenge', 'request',
                             'subject', 'salt_b64', 'consumed'}
                and data['schema'] == 'apatch.passport-admission.pending.v1'
                and data['challenge']['challenge_id'] == challenge_id
                and data['consumed'] is False, 'invalid pending challenge record')
        salt = base64.b64decode(data['salt_b64'], validate=True)
        require(len(salt) == 32, 'invalid local commitment salt')
        record = {key: data[key] for key in ['policy_digest', 'challenge', 'request', 'subject', 'consumed']}
        record['salt'] = salt
        return record, sha(raw)

    def _write_private(self, path, data):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            self._private_file(fd)
            raw = canonical(data)
            with os.fdopen(fd, 'wb', closefd=False) as output:
                output.write(raw)
                output.flush()
            os.fsync(fd)
        finally:
            os.close(fd)
        self._sync_directory(path.parent)

    def _claim(self, challenge_id, record_digest, admitted):
        # O_EXCL is the irreversible cross-process claim. Even a partial marker
        # burns the nonce; subsequent failures cannot make it reusable.
        with self._namespace_lock():
            record, current_digest = self._read_record(challenge_id)
            marker = self.used / (challenge_id + '.json')
            require(not os.path.lexists(marker), 'challenge already consumed')
            self._write_private(marker, {'challenge_id': challenge_id,
                                'record_sha256': record_digest,
                                'consumed_at_ms': now_ms()})
            local = self.pending.get(challenge_id)
            if local is not None:
                local['consumed'] = True
            require(current_digest == record_digest, 'pending challenge changed during validation')
            if admitted:
                require(now_ms() < record['challenge']['expires_at_ms'],
                        'holder challenge expired during validation')

    def normalize(self, tool, arguments):
        require(tool in self.tools and isinstance(arguments, dict), 'unknown tool or arguments')
        require('caller_passport' not in arguments, 'pre-populated proof refused')
        require(set(arguments).issubset(self.argument_models[tool].model_fields), 'unknown tool input field')
        require(len(canonical(arguments)) <= 2_097_152, 'tool arguments exceed local limit')
        return self.argument_models[tool].model_validate(arguments).model_dump_one_level()

    def issue(self, tool, arguments, host):
        require(set(host) == {'source', 'session_id', 'turn_id', 'tool_use_id', 'tool_name'}
                and host['source'] == 'codex-pre-tool-use', 'invalid host adapter context')
        chat = host['session_id']
        require(isinstance(chat, str) and UUID.fullmatch(chat) and chat in self.policy['subjects'],
                'chat has no configured passport scope')
        require(UUID.fullmatch(host['turn_id']) and isinstance(host['tool_use_id'], str)
                and 0 < len(host['tool_use_id']) <= 256, 'invalid turn or call binding')
        expected_name = 'mcp__' + self.policy['server'].replace('-', '_') + '__' + tool
        require(host['tool_name'].replace('-', '_') == expected_name, 'host tool audience mismatch')
        normalized = self.normalize(tool, arguments)
        target = str(Path(resolve_target(normalized.get('target_dir', '.'))).resolve())
        require(target in self.roots, 'workspace is outside caller mandate')
        subject = self.policy['subjects'][chat]
        identity = self.registry.passport(subject, chat)
        issued = now_ms()
        nonce, salt = secrets.token_hex(32), secrets.token_bytes(32)
        challenge = {'schema': 'trustchain.apatch.caller-challenge.v1',
                     'challenge_id': secrets.token_hex(16), 'nonce': nonce,
                     'agent_id': subject['agent_id'], 'chat_id': chat,
                     'turn_id': host['turn_id'], 'tool_use_id': host['tool_use_id'],
                     'passport_proof_id': subject['passport_proof_id'],
                     'key_id': identity['key_id'], 'certificate_digest': identity['certificate_digest'],
                     'server': self.policy['server'], 'tool': tool, 'workspace_sha256': sha(target.encode()),
                     'arguments_commitment': sha(salt + canonical(normalized)),
                     'issued_at_ms': issued, 'expires_at_ms': issued + 20_000}
        public_arguments = {'schema': 'trustchain.apatch.operation-commitment.v1',
                            'challenge': challenge}
        request = {'schema': 'trustchain.runtime_authorization.request.v1',
                   'tenant_id': subject['tenant_id'], 'agent_id': subject['agent_id'],
                   'tool': self.policy['server'] + '/' + tool, 'arguments': public_arguments,
                   'arguments_sha256': sha(canonical(public_arguments)), 'requested_at_ms': issued,
                   'nonce': 'tcra_' + nonce[:32], 'approval_id': None, 'model_hash': '', 'prompt_hash': ''}
        with self._namespace_lock():
            self.pending = {k: v for k, v in self.pending.items() if v['challenge']['expires_at_ms'] > issued}
            paths = list(self.challenges.iterdir())
            require(len(paths) <= 4096, 'pending challenge directory limit reached')
            active = 0
            for path in paths:
                require(re.fullmatch('[0-9a-f]{32}\\.json', path.name), 'unexpected challenge record')
                previous, _ = self._read_record(path.stem)
                if previous['challenge']['expires_at_ms'] <= issued:
                    path.unlink()
                else:
                    active += 1
            require(active < 1024, 'pending challenge limit reached')
            record = {'challenge': challenge, 'salt': salt, 'policy_digest': self.policy_digest,
                      'subject': dict(subject), 'request': request, 'consumed': False}
            self._write_private(self.challenges / (challenge['challenge_id'] + '.json'),
                                self._pack_record(record))
            self.pending[challenge['challenge_id']] = record
        return {'challenge': challenge, 'normalized_arguments': normalized,
                'authorization_request': request,
                'authorization_url': self.policy['authorization_base'] + '/api/runtime/authorize'}

    def _grant(self, grant, record, identity, registry=None):
        require(isinstance(grant, dict) and set(grant) == {'claims', 'signature'}, 'invalid permission envelope')
        claims, signature = grant['claims'], grant['signature']
        registry = self.registry if registry is None else registry
        authority = registry.identity('platform-runtime-authorizer', 'platform:runtime-authorize')
        require(signature['algorithm'] == 'Ed25519'
                and base64.b64decode(signature['public_key_b64'], validate=True) == authority['public_key']
                and signature['key_id'] == 'sha256:' + sha(authority['public_key'])
                and signature['certificate_fingerprint'] == authority['certificate_digest'][:24]
                and x509.load_pem_x509_certificate(signature['certificate_pem'].encode()).public_bytes(Encoding.DER)
                    == authority['leaf'].public_bytes(Encoding.DER), 'permission issuer mismatch')
        authority['leaf'].public_key().verify(base64.b64decode(signature['value'], validate=True), canonical(claims))
        expected = record['request']
        require(claims['schema'] == 'trustchain.runtime_authorization.grant.v1'
                and claims['tenant_id'] == expected['tenant_id'] and claims['agent_id'] == expected['agent_id']
                and claims['tool'] == expected['tool'] and claims['arguments_sha256'] == expected['arguments_sha256']
                and claims['request_nonce'] == expected['nonce']
                and claims['agent_certificate_fingerprint'] == identity['certificate_digest'][:24],
                'permission operation binding refused')
        issued = datetime.fromisoformat(claims['issued_at'].replace('Z', '+00:00'))
        expires = datetime.fromisoformat(claims['expires_at'].replace('Z', '+00:00'))
        require(issued.tzinfo and expires.tzinfo and issued.timestamp()*1000 >= record['challenge']['issued_at_ms']-1000
                and 0 < (expires-issued).total_seconds() <= 30
                and issued.timestamp()*1000 < record['challenge']['expires_at_ms']
                and (issued.timestamp()*1000-now_ms()) <= 5_000,
                'permission is not fresh')
        effective = claims['effective_policy']
        require(sha(canonical(effective)) == claims['effective_policy_sha256']
                and effective.get('lease_expired') is False
                and expected['tool'] not in effective.get('deny', [])
                and (not effective.get('allow') or expected['tool'] in effective['allow']), 'effective policy refused')
        require(expected['tool'] not in effective.get('hitl_tools', []) or claims.get('approval'),
                'human approval is required by current policy')
        # A verified authority can be slightly ahead of the local wall clock.
        # Wait only after signature, exact operation and policy validation;
        # never grant future authority or extend the holder challenge.
        ahead = issued.timestamp() - now_ms()/1000
        require(ahead <= 5, 'permission is not fresh')
        if ahead > 0:
            time.sleep(min(ahead + .01, 5))
        require(issued.timestamp()*1000 <= now_ms()
                and now_ms()+1000 < expires.timestamp()*1000,
                'permission is not fresh')

    def _journal(self, event):
        fd = os.open(self.state/'admission.jsonl', os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW, 0o600)
        try:
            item=os.fstat(fd)
            require(stat.S_ISREG(item.st_mode) and item.st_uid==os.getuid() and not item.st_mode & 0o077,
                    'admission journal custody refused')
            os.write(fd, canonical(event)+b'\n');os.fsync(fd)
        finally:
            os.close(fd)

    def accept(self, tool, arguments, proof):
        require(isinstance(proof, dict) and set(proof) == {'challenge_id', 'holder_signature', 'grant'},
                'fresh caller passport answer required')
        with self._namespace_lock():
            record, record_digest = self._read_record(proof['challenge_id'])
            require(not os.path.lexists(self.used / (proof['challenge_id'] + '.json')),
                    'challenge unavailable or already consumed')
            local = self.pending.get(proof['challenge_id'])
        try:
            if local is not None:
                require(canonical(self._pack_record(local)) == canonical(self._pack_record(record)),
                        'local and persisted challenge disagree')
            challenge=record['challenge']
            subject = self.policy['subjects'].get(challenge['chat_id'])
            require(record['policy_digest'] == self.policy_digest and record['subject'] == subject
                    and challenge['server'] == self.policy['server']
                    and challenge['agent_id'] == subject['agent_id']
                    and challenge['passport_proof_id'] == subject['passport_proof_id'],
                    'challenge policy or subject binding refused')
            expected_arguments = {'schema': 'trustchain.apatch.operation-commitment.v1',
                                  'challenge': challenge}
            request = record['request']
            require(request['schema'] == 'trustchain.runtime_authorization.request.v1'
                    and request['tenant_id'] == subject['tenant_id']
                    and request['agent_id'] == subject['agent_id']
                    and request['tool'] == self.policy['server'] + '/' + tool
                    and request['arguments'] == expected_arguments
                    and request['arguments_sha256'] == sha(canonical(expected_arguments))
                    and request['nonce'] == 'tcra_' + challenge['nonce'][:32]
                    and request['requested_at_ms'] == challenge['issued_at_ms'],
                    'persisted permission request binding refused')
            require(challenge['tool'] == tool and sha(record['salt']+canonical(arguments)) == challenge['arguments_commitment'],
                    'tool or arguments changed after challenge')
            require(challenge['issued_at_ms'] <= now_ms() < challenge['expires_at_ms'], 'holder challenge expired')
            require(sha(str(Path(resolve_target(arguments.get('target_dir','.'))).resolve()).encode()) == challenge['workspace_sha256'],
                    'workspace changed after challenge')
            # Never reuse issue-time material: revocation and replacement must
            # be observed by each new answer, including across processes.
            snapshot = getattr(self.registry, 'snapshot', None)
            registry = (snapshot(subject['agent_id'], subject['passport_proof_id'])
                        if snapshot is not None else self.registry)
            identity=registry.passport(record['subject'],challenge['chat_id'])
            require(identity['certificate_digest'] == challenge['certificate_digest'], 'holder certificate changed')
            identity['leaf'].public_key().verify(base64.b64decode(proof['holder_signature'],validate=True),DOMAIN+canonical(challenge))
            self._grant(proof['grant'],record,identity,registry)
            # Fetch revocation and both status documents again after any grant
            # wait; reuse only this operation's pinned issuer/certificate bytes.
            if snapshot is not None:
                registry = snapshot(subject['agent_id'], subject['passport_proof_id'], previous=registry)
            registry.identity(subject['agent_id'])
            self._grant(proof['grant'],record,identity,registry)
            if snapshot is not None:
                registry.check_current()
            # Recheck after network validation; no expired answer reaches dispatch.
            require(now_ms() < challenge['expires_at_ms'], 'holder challenge expired during validation')
            admitted={'schema':'trustchain.apatch.caller-admission.v1','challenge':challenge,
                      'holder_signature':proof['holder_signature'],'authorization_request':record['request'],
                      'grant':proof['grant'],'admitted_at_ms':now_ms(),'native_tool_started':False}
        except Exception:
            self._claim(proof['challenge_id'], record_digest, admitted=False)
            raise
        self._claim(proof['challenge_id'], record_digest, admitted=True)
        self._journal(admitted)
        return admitted,record['salt']

    def completed(self, admitted, salt, result):
        receipt={**admitted,'schema':'trustchain.apatch.caller-execution.v1',
                 'native_tool_started':True,'completed_at_ms':now_ms(),
                 'result_commitment':sha(salt+canonical(result)),
                 'host_evidence':'local-software-adapter','provider_attestation':False,
                 'qualified_ai_release':False}
        self._journal(receipt)
        report={'mandatory_gate':True,'fresh_holder_verified':True,'permissions_verified':True,
                'agent_id':admitted['challenge']['agent_id'],'chat_id':admitted['challenge']['chat_id'],
                'passport_url':'https://clients.trust-chain.ai/proof/'+admitted['challenge']['passport_proof_id'],
                'challenge_id':admitted['challenge']['challenge_id'],'native_tool_executed':True,
                'proof_url':None,'proof_status':'local_verified'}
        if self.publish:
            try:
                report['proof_url']=self._publish(receipt)
                report['proof_status']='published'
            except Exception as exc:
                report['proof_status']='pending'
                self._journal({'schema':'trustchain.apatch.proof-publication-pending.v1',
                               'challenge_id':admitted['challenge']['challenge_id'],'error_class':type(exc).__name__})
        if isinstance(result,dict):
            return {**result,'caller_admission':report}
        return result

    def _publication_identity(self):
        from apatch.trust_identity import LocalIdentity, _PemKeyProvider, load_local_identity
        configured=self.policy.get('publication_identity_dir')
        if configured is None:
            return load_local_identity(),None
        require(isinstance(configured,str) and Path(configured).is_absolute(),
                'absolute publication identity directory required')
        directory=Path(configured)
        item=directory.lstat()
        require(stat.S_ISDIR(item.st_mode) and not directory.is_symlink()
                and item.st_uid == os.getuid() and not item.st_mode & 0o022,
                'publication identity directory custody refused')
        def checked_file(path, *, private=False, read=False):
            path=Path(path)
            require(path.is_absolute() and path.parent.resolve() == directory.resolve(),
                    'publication identity file escaped configured directory')
            fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
            try:
                entry=os.fstat(fd)
                require(stat.S_ISREG(entry.st_mode) and entry.st_uid == os.getuid()
                        and not entry.st_mode & (0o077 if private else 0o022)
                        and 0 < entry.st_size < 65536,
                        'publication identity file custody refused')
                return os.read(fd,65536) if read else None
            finally:
                os.close(fd)
        profile=decode(checked_file(directory / 'identity.json',read=True))
        require(isinstance(profile,dict) and set(profile) == {'agent_id','key','cert'}
                and isinstance(profile['agent_id'],str)
                and re.fullmatch('[A-Za-z0-9_-]{1,128}',profile['agent_id'])
                and isinstance(profile['key'],str) and isinstance(profile['cert'],str),
                'publication identity profile refused')
        checked_file(profile['key'],private=True)
        local_certificate=x509.load_pem_x509_certificate(checked_file(profile['cert'],read=True))
        provider=_PemKeyProvider(profile['key'],key_id=profile['agent_id'])
        identity=LocalIdentity(agent_id=profile['agent_id'],key_path=profile['key'],
                               cert_path=profile['cert'],key_provider=provider,backend='pem')
        return identity,local_certificate

    def _publish(self, receipt):
        identity,local_certificate=self._publication_identity()
        require(identity is not None,'enrolled execution signer unavailable')
        public=self.registry.identity(identity.agent_id)
        if local_certificate is not None:
            require(local_certificate.public_bytes(Encoding.DER) == public['leaf'].public_bytes(Encoding.DER),
                    'publication certificate differs from current registered enrollment')
            require(identity.key_provider.get_public_key() == public['leaf'].public_key().public_bytes(Encoding.Raw,PublicFormat.Raw),
                    'publication key differs from current registered enrollment')
        data=canonical(receipt).decode()
        signature=identity.key_provider.sign(data.encode())
        public['leaf'].public_key().verify(signature,data.encode())
        request={'agent_id':identity.agent_id,'data':data,'signature':base64.b64encode(signature).decode()}
        req=urllib.request.Request(self.policy['registry_base']+'/api/pub/proofs',data=canonical(request),
                                   headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=8) as response:
            reply=decode(response.read(1_048_576))
            expected=sha((request['signature']+data).encode())[:16]
            require(response.status==200 and reply.get('proof_id')==expected,'proof publication binding refused')
        return 'https://clients.trust-chain.ai/proof/'+expected

    def dispatch(self, tool, arguments, proof, invoke):
        try:
            admitted,salt=self.accept(tool,arguments,proof)
        except Exception as exc:
            diagnostic = public_failure(exc)
            self._journal({'schema':'trustchain.apatch.caller-denial.v1','tool':tool,
                           'native_tool_started':False,'error_class':type(exc).__name__,'at_ms':now_ms(),
                           **diagnostic})
            return {'ok':False,'error_type':'CALLER_PASSPORT_REJECTED',
                    'error':'Caller passport, fresh holder answer or operation permission refused.',
                    'native_tool_executed':False,'mandatory_gate':True,**diagnostic}
        result=invoke(arguments)
        return self.completed(admitted,salt,result)
