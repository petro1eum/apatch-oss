"""Opt-in mandatory passport MCP entrypoint; the original launcher is unchanged.

This entrypoint exposes the original apatch tools through a fail-closed outer
admission wrapper. Challenge issuance is a local private socket, not an apatch
tool exemption. Only authenticated calls enter the original governed lifecycle.
"""
from __future__ import annotations
import fcntl
import functools
import json
import os
import re
import secrets
import socketserver
import stat
import threading
from pathlib import Path
from pydantic import create_model
from apatch.mcp.call_admission import CallAdmission, canonical, decode, read_config, require, secure_directory, public_failure


def install_admission(mcp, gate):
    for name, tool in mcp._tool_manager._tools.items():
        require(not tool.is_async, 'unsupported asynchronous native tool')
        original=tool.fn
        model=create_model(tool.fn_metadata.arg_model.__name__+'CallerPassport',
                           __base__=tool.fn_metadata.arg_model,
                           caller_passport=(dict | None,None))
        tool.fn_metadata.arg_model=model
        tool.parameters=model.model_json_schema(by_alias=True)
        @functools.wraps(original)
        def guarded(*args,__original=original,__name=name,**kwargs):
            require(not args, 'MCP keyword arguments required')
            proof=kwargs.pop('caller_passport',None)
            return gate.dispatch(__name,kwargs,proof,lambda arguments:__original(**arguments))
        tool.fn=guarded
        tool.description=(tool.description or '')+' Requires a fresh caller_passport from the trusted host hook; missing/invalid proof is refused before native tool execution.'


class ChallengeServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    allow_reuse_address=False
    def __init__(self,path,gate):
        self.gate=gate
        self.endpoint_lease_fd=None
        super().__init__(path,ChallengeHandler)

    def server_close(self):
        try:
            super().server_close()
        finally:
            if self.endpoint_lease_fd is not None:
                os.close(self.endpoint_lease_fd)
                self.endpoint_lease_fd=None


class ChallengeHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        try:
            raw=self.rfile.readline(2_097_153)
            require(len(raw)<=2_097_152 and raw.endswith(b'\n'),'challenge request size refused')
            request=decode(raw)
            require(set(request)=={'tool','arguments','host_context'},'challenge request shape refused')
            result=self.server.gate.issue(request['tool'],request['arguments'],request['host_context'])
            self.wfile.write(canonical({'ok':True,**result})+b'\n')
        except Exception as exc:
            self.wfile.write(canonical({'ok':False,'error_type':'CALLER_PASSPORT_REJECTED',
                                       **public_failure(exc)})+b'\n')


def _remove_stale_endpoints(gate,endpoints,leases):
    paths=list(endpoints.iterdir())
    require(len(paths)<=4096,'challenge endpoint cleanup limit exceeded')
    for path in paths:
        # Only our managed names and an existing private lease identify an
        # endpoint that this namespace is authorized to reclaim.
        if re.fullmatch(r'[0-9]+-[0-9a-f]{12}\.sock',path.name) is None:
            continue
        try:
            fd=os.open(leases/(path.name+'.lock'),os.O_RDWR|os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        try:
            gate._private_file(fd)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            lease=decode(os.read(fd,1025))
            require(lease=={'schema':'apatch.passport-endpoint-lease.v1',
                            'endpoint':path.name,'policy_digest':gate.policy_digest},
                    'challenge endpoint lease binding refused')
            item=path.lstat()
            require(stat.S_ISSOCK(item.st_mode) and item.st_uid==os.getuid()
                    and stat.S_IMODE(item.st_mode)==0o600 and not path.is_symlink(),
                    'stale challenge endpoint custody refused')
            again=path.lstat()
            require((again.st_dev,again.st_ino)==(item.st_dev,item.st_ino),
                    'stale challenge endpoint changed')
            path.unlink()
            gate._sync_directory(endpoints)
        finally:
            # Never unlink the lease inode: another process may hold this file.
            os.close(fd)


def start_challenge_server(gate):
    endpoints=secure_directory(gate.state/'endpoints')
    leases=secure_directory(gate.state/'endpoint-leases')
    # Codex can run multiple MCP clients for one project. Each process owns
    # only its unique endpoint; admission and single-use claims share the state.
    path=endpoints/(str(os.getpid())+'-'+secrets.token_hex(6)+'.sock')
    require(len(os.fsencode(path)) < 104, 'private challenge socket path too long')
    with gate._namespace_lock():
        _remove_stale_endpoints(gate,endpoints,leases)
        require(not os.path.lexists(path),'challenge endpoint already exists; no takeover')
        lease_fd=os.open(leases/(path.name+'.lock'),
                         os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        server=None
        try:
            gate._private_file(lease_fd)
            fcntl.flock(lease_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            os.write(lease_fd,canonical({'schema':'apatch.passport-endpoint-lease.v1',
                                       'endpoint':path.name,'policy_digest':gate.policy_digest}))
            os.fsync(lease_fd)
            gate._sync_directory(leases)
            # Startup precedes tool dispatch and creation of our handler thread.
            # The socket is private from the instant bind creates its inode.
            previous_umask=os.umask(0o177)
            try:
                server=ChallengeServer(str(path),gate)
            finally:
                os.umask(previous_umask)
            item=path.lstat()
            require(stat.S_IMODE(item.st_mode)==0o600,'challenge endpoint custody refused')
            server.endpoint_identity=(item.st_dev,item.st_ino)
            server.endpoint_lease_fd=lease_fd
        except Exception:
            lease_transferred=server is not None and server.endpoint_lease_fd==lease_fd
            if server is not None:server.server_close()
            if not lease_transferred:
                # Before transfer to the server, only this startup owns the fd.
                os.close(lease_fd)
            if os.path.lexists(path):
                item=path.lstat()
                if stat.S_ISSOCK(item.st_mode) and item.st_uid==os.getuid():path.unlink()
            raise
    thread=threading.Thread(target=server.serve_forever,name='apatch-passport-challenges',daemon=True)
    thread.start()
    return server,path


def main():
    from apatch.mcp.stdio_guard import activate_stdio_guard
    bound=os.environ.get('APATCH_MCP_BOUND')
    activate_stdio_guard(workspace=bound)
    path=os.environ.get('APATCH_CALL_ADMISSION_CONFIG','')
    pin=os.environ.get('APATCH_CALL_ADMISSION_CONFIG_SHA256','')
    require(path and pin,'mandatory passport entrypoint requires a pinned operator configuration')
    config=read_config(path,pin)
    from apatch.mcp.bound_workspace import bind_mcp_workspace
    bind_mcp_workspace(bound)
    from apatch.mcp.lifecycle import on_mcp_startup
    on_mcp_startup()
    from apatch.mcp import server as native
    from apatch.mcp.resources import register_mcp_resources
    from apatch.mcp.profiles import apply_tool_profile
    native._ensure_mcp()
    register_mcp_resources(native.mcp)
    apply_tool_profile(native.mcp)
    native._wrap_mcp_tools()
    native._forbid_unknown_arguments()
    gate=CallAdmission(config,native.mcp)
    install_admission(native.mcp,gate)
    native._forbid_unknown_arguments()
    native._apply_param_docs()
    server,socket_path=start_challenge_server(gate)
    try:
        native.mcp.run(transport='stdio')
    finally:
        server.shutdown();server.server_close()
        if os.path.lexists(socket_path):
            item=socket_path.lstat()
            if (stat.S_ISSOCK(item.st_mode) and item.st_uid==os.getuid()
                    and (item.st_dev,item.st_ino)==server.endpoint_identity):
                socket_path.unlink()


if __name__=='__main__':main()
