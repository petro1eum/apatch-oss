"""Fail-closed Linux SDD judge launcher; executes only in a fresh child process."""
import ctypes
import errno
import os
from pathlib import Path
import platform
import sys


class SandboxUnavailable(RuntimeError):
    pass


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class _Compare(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


def _libraries():
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise SandboxUnavailable("unsupported_linux_architecture")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        sec = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError:
        raise SandboxUnavailable("linux_sandbox_library_unavailable") from None
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)
    # ABI3 adds TRUNCATE; older kernels do not provide this write boundary.
    if abi < 3:
        raise SandboxUnavailable("landlock_abi3_required")
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sec.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Compare)]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    return libc, sec, abi


def capability():
    """Read-only kernel/library check; does not restrict the calling process."""
    _, _, abi = _libraries()
    return {"backend": "landlock-seccomp", "landlock_abi": abi}


def confine(workspace):
    """Install irreversible restrictions before exec; never call in the host."""
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir() or root == Path("/"):
        raise SandboxUnavailable("invalid_workspace")
    libc, sec, abi = _libraries()
    capabilities = {}
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        if line.split(":", 1)[0] in {"CapEff", "CapPrm", "CapAmb"}:
            key, value = line.split(":", 1)
            capabilities[key] = int(value.strip(), 16)
    if set(capabilities) != {"CapEff", "CapPrm", "CapAmb"} or any(capabilities.values()):
        raise SandboxUnavailable("nonzero_process_capabilities")
    # All mutation rights through ABI3, plus device ioctl on kernels supporting it.
    rights = (1 << 1) | sum(1 << n for n in range(4, 15))
    if abi >= 5:
        rights |= 1 << 15
    ruleset = libc.syscall(444, ctypes.byref(_Ruleset(rights)), ctypes.sizeof(_Ruleset), 0)
    if ruleset < 0:
        raise SandboxUnavailable("landlock_create_failed")
    try:
        for path, allowed in ((root, rights), (Path("/dev/null"), (1 << 1) | (1 << 14))):
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _PathRule(allowed, fd)
                if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) != 0:
                    raise SandboxUnavailable("landlock_rule_failed")
            finally:
                os.close(fd)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise SandboxUnavailable("no_new_privileges_failed")
        if libc.syscall(446, ruleset, 0) != 0:
            raise SandboxUnavailable("landlock_restrict_failed")
    finally:
        os.close(ruleset)
    ctx = sec.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise SandboxUnavailable("seccomp_init_failed")
    try:
        # Landlock does not mediate metadata writes. Deny those globally, plus
        # networking (including Unix sockets), cross-process writes and escapes.
        denied = ("socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
                  "sendto", "sendmsg", "sendmmsg", "recvmsg", "recvmmsg", "socketcall",
                  "chmod", "fchmod", "fchmodat", "fchmodat2", "chown", "fchown", "lchown",
                  "fchownat", "utime", "utimes", "futimesat", "utimensat", "setxattr",
                  "lsetxattr", "fsetxattr", "removexattr", "lremovexattr", "fremovexattr",
                  "setxattrat", "removexattrat", "file_setattr",
                  "ioctl", "mknod", "mknodat", "ptrace", "process_vm_writev", "pidfd_getfd", "mount", "umount2",
                  "move_mount", "open_tree", "fsopen", "fsmount", "fsconfig", "mount_setattr",
                  "setns", "unshare", "setsid", "setpgid", "kill", "tkill", "tgkill",
                  "pidfd_send_signal", "rt_sigqueueinfo", "rt_tgsigqueueinfo",
                  "io_uring_setup", "io_uring_enter", "io_uring_register", "clone3")
        for name in denied:
            number = sec.seccomp_syscall_resolve_name(name.encode("ascii"))
            # These numbers are shared by the two explicitly supported native
            # ABIs (Linux scripts/syscall.tbl and x86 syscall_64.tbl). Older
            # libseccomp may not know a metadata syscall a newer kernel exposes.
            numeric = {"fchmodat2": 452, "setxattrat": 463,
                       "removexattrat": 466, "file_setattr": 469}
            if name in numeric and number < 0:
                number = numeric[name]
            if number == -1:
                raise SandboxUnavailable("seccomp_syscall_unknown")
            # ioctl remains denied. EACCES permits CPython's documented
            # FIOCLEX -> fcntl fallback when opening an ordinary script file.
            denied_errno = errno.EACCES if name == "ioctl" else errno.EPERM
            if number >= 0 and sec.seccomp_rule_add_array(ctx, 0x50000 | denied_errno, number, 0, None) != 0:
                raise SandboxUnavailable("seccomp_rule_failed")
        # Ordinary fork/threads remain possible; namespace creation is forbidden.
        clone = sec.seccomp_syscall_resolve_name(b"clone")
        if clone < 0:
            raise SandboxUnavailable("seccomp_clone_unknown")
        if clone >= 0:
            for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                         0x10000000, 0x20000000, 0x40000000):
                cmp = _Compare(0, 7, flag, flag)  # SCMP_CMP_MASKED_EQ
                if sec.seccomp_rule_add_array(ctx, 0x50000 | errno.EPERM, clone, 1, ctypes.byref(cmp)) != 0:
                    raise SandboxUnavailable("seccomp_clone_rule_failed")
        if sec.seccomp_load(ctx) != 0:
            raise SandboxUnavailable("seccomp_load_failed")
    finally:
        sec.seccomp_release(ctx)
    os.chdir(root)
    os.environ.update(HOME=str(root), TMPDIR=str(root), PYTHONDONTWRITEBYTECODE="1")


def main():
    try:
        if len(sys.argv) < 4 or sys.argv[2] != "--":
            raise SandboxUnavailable("invalid_launcher_arguments")
        confine(sys.argv[1])
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    except (SandboxUnavailable, OSError) as exc:
        # No raw argv, environment, path, or subprocess output in setup errors.
        code = str(exc) if isinstance(exc, SandboxUnavailable) else "sandbox_exec_failed"
        print("SDD_SANDBOX_UNAVAILABLE:" + code, file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
