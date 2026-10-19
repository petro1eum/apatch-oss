"""Codex PreToolUse adapter: native host context, existing key, fresh permission.

There is no enrollment, key creation, caller-selected actor, or self-issued
operation authority. The mandatory MCP gate also rejects calls if this hook is
skipped, fails or times out. Hook input is local host evidence, not provider
attestation. Only public commitment data reaches the Platform authorization API.
"""
from __future__ import annotations
import base64
import errno
import os
import re
import socket
import stat
import sys
import time
import urllib.request
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key,Encoding,PublicFormat
from apatch.mcp.call_admission import DOMAIN,UUID,canonical,decode,sha,read_config,public_failure,refusal_from_response


def host_context(event):
    if (event.get('hook_event_name')!='PreToolUse'
            or not isinstance(event.get('session_id'),str) or not UUID.fullmatch(event['session_id'])
            or not isinstance(event.get('turn_id'),str) or not UUID.fullmatch(event['turn_id'])
            or not isinstance(event.get('tool_use_id'),str) or not 0<len(event['tool_use_id'])<=256
            or not isinstance(event.get('tool_name'),str)
            or re.fullmatch(r'mcp__apatch[A-Za-z0-9_-]*__apatch_[a-z0-9_]+',event['tool_name']) is None):
        raise ValueError('trusted host event shape refused')
    return {'source':'codex-pre-tool-use','session_id':event['session_id'],
            'turn_id':event['turn_id'],'tool_use_id':event['tool_use_id'],'tool_name':event['tool_name']}


def load_key(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        item=os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_uid!=os.getuid() or item.st_mode & 0o077 or item.st_size>8192:
            raise ValueError('existing private key custody refused')
        key=load_pem_private_key(os.read(fd,8192),password=None)
    finally:os.close(fd)
    if not isinstance(key,Ed25519PrivateKey):raise ValueError('existing Ed25519 key required')
    return key


def _private_directory(path):
    path=Path(path)
    item=path.lstat()
    if (not path.is_absolute() or not stat.S_ISDIR(item.st_mode)
            or path.is_symlink() or item.st_uid!=os.getuid()
            or stat.S_IMODE(item.st_mode)!=0o700):
        raise ValueError('challenge namespace custody refused')
    return path


def _endpoints(gateway):
    if 'namespace_dir' in gateway:
        namespace=_private_directory(gateway['namespace_dir'])
        directory=_private_directory(namespace/'endpoints')
        with os.scandir(directory) as scan:
            paths=[]
            for entry in scan:
                if len(paths)>=64:raise ValueError('challenge endpoint count exceeded')
                if re.fullmatch(r'[A-Za-z0-9_-]{1,64}\.sock',entry.name) is None:
                    raise ValueError('unexpected challenge endpoint')
                paths.append(Path(entry.path))
        if not paths:raise ValueError('no challenge endpoint is available')
        return sorted(paths),True
    # A legacy explicit endpoint remains readable only without a namespace.
    path=Path(gateway['socket_path'])
    _private_directory(path.parent)
    return [path],False


def _issue(gateway,tool,arguments,host):
    endpoints,shared=_endpoints(gateway)
    deadline=time.monotonic()+60
    refusal=None
    for endpoint in endpoints:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ValueError('challenge endpoint discovery timed out')
        try:
            item=endpoint.lstat()
            if (not stat.S_ISSOCK(item.st_mode) or item.st_uid!=os.getuid()
                    or stat.S_IMODE(item.st_mode)!=0o600 or endpoint.is_symlink()):
                raise ValueError('challenge endpoint custody refused')
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as transport:
                transport.settimeout(min(remaining,60));transport.connect(str(endpoint))
                transport.sendall(canonical({'tool':tool,'arguments':arguments,'host_context':host})+b'\n')
                reply=transport.makefile('rb').readline(2_097_153)
        except OSError as exc:
            if shared and exc.errno in {errno.ENOENT,errno.ECONNREFUSED}:
                continue
            raise
        if len(reply)>2_097_152 or not reply.endswith(b'\n'):
            raise ValueError('challenge response size refused')
        issued=decode(reply)
        if issued.get('ok') is True:return issued
        if refusal is None:refusal=refusal_from_response(issued)
        if not shared:raise refusal
    if refusal is not None:raise refusal
    raise ValueError('no live relying party accepted the challenge')


def answer_event(event,config,*,workspace_root=None):
    host=host_context(event)
    if host['session_id'] not in config['subjects']:raise ValueError('chat has no passport enrollment')
    name=host['tool_name'].split('__')
    server,tool=name[1],name[2]
    gateway=config['gateways'].get(server.replace('-','_'))
    if not isinstance(gateway,dict):raise ValueError('server has no configured mandatory gate')
    arguments=event.get('tool_input')
    if not isinstance(arguments,dict) or 'caller_passport' in arguments:raise ValueError('tool input refused')
    issued=_issue(gateway,tool,arguments,host)
    challenge=issued['challenge']
    if (challenge['chat_id']!=host['session_id'] or challenge['turn_id']!=host['turn_id']
            or challenge['tool_use_id']!=host['tool_use_id'] or challenge['tool']!=tool
            or challenge['server'].replace('-','_')!=server.replace('-','_')
            or issued['authorization_url']!=config['authorization_base']+'/api/runtime/authorize'):
        raise ValueError('challenge or authority audience changed')
    if workspace_root is not None and challenge.get('workspace_sha256') != sha(str(Path(workspace_root).resolve(strict=True)).encode()):
        raise ValueError('challenge workspace changed')
    custody=Path(config['key_base'])/host['session_id']
    for path in [custody.parent,custody]:
        item=path.lstat()
        if not stat.S_ISDIR(item.st_mode) or path.is_symlink() or item.st_uid!=os.getuid() or item.st_mode&0o077:
            raise ValueError('existing key directory custody refused')
    key=load_key(custody/'executor.key')
    pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    if sha(pub)[:32]!=challenge['key_id']:raise ValueError('current holder key mismatch')
    response={'challenge_id':challenge['challenge_id'],
              'holder_signature':base64.b64encode(key.sign(DOMAIN+canonical(challenge))).decode()}
    request=issued['authorization_request']
    if request['agent_id']!=config['subjects'][host['session_id']]['agent_id'] or request['tenant_id']!=config['subjects'][host['session_id']]['tenant_id']:
        raise ValueError('authority identity selection refused')
    request={**request,'signature':key.sign(canonical(request)).hex()}
    req=urllib.request.Request(issued['authorization_url'],data=canonical(request),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=8) as remote:
        decision=decode(remote.read(1_048_577))
        if remote.status!=200 or decision.get('decision')!='allowed':raise ValueError('operation permission refused')
    response['grant']=decision['grant']
    return {**issued['normalized_arguments'],'caller_passport':response}


def main():
    try:
        if len(sys.argv)==3:
            config=read_config(sys.argv[1],sys.argv[2])
            event=decode(sys.stdin.buffer.read(2_097_153))
            updated=answer_event(event,config)
        elif len(sys.argv)==7 and sys.argv[1::2]==['--projects','--trust-anchor','--trust-anchor-sha256']:
            from apatch.mcp.project_admission import resolve_project_admission
            event=decode(sys.stdin.buffer.read(2_097_153))
            host_context(event)
            config,root=resolve_project_admission(event,sys.argv[2],sys.argv[4],sys.argv[6])
            updated=answer_event(event,config,workspace_root=root)
        else:
            raise ValueError('pinned hook configuration or stable project mode required')
        output={'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'allow','updatedInput':updated}}
    except Exception as exc:
        diagnostic=public_failure(exc)
        output={'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny',
                   'permissionDecisionReason':'TrustChain refused admission: '+diagnostic['cause_code']+'. Next action: '+diagnostic['next_action']+'.'}}
    sys.stdout.buffer.write(canonical(output)+b'\n')


if __name__=='__main__':main()
