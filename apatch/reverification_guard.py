"""Live invalidation for ordinary file writes during one verification.

The guard observes local filesystem write/attribute/name events. It is not a
universal mmap or remote-filesystem mutation history service. No persisted token
can recreate an interrupted observation interval. Hash and identity checks
remain mandatory; this guard supplements them through the pre-signing check.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import select
import struct
import sys


class GuardInvalid(ValueError):
    pass


class FileEventGuard:
    def __init__(self, root, files):
        self.pid = os.getpid()
        self.identity = object()
        self.closed = False
        self.invalid = False
        self.fd = -1
        self.queue = None
        self.handles = []
        self.watches = {}
        base = Path(root).resolve(strict=True)
        self.root = str(base)
        self.files = frozenset(files)
        paths = set()
        children = {}
        try:
            for rel in sorted(files):
                path = base / rel
                if path.is_symlink() or not path.resolve(strict=True).is_relative_to(base):
                    raise GuardInvalid("file event scope escapes workspace")
                if not path.is_file():
                    raise GuardInvalid("file event scope is unavailable")
                paths.add(path)
                current = path
                while current != base:
                    parent = current.parent
                    if parent.is_symlink():
                        raise GuardInvalid("symlink in file event scope")
                    children.setdefault(parent, set()).add(current.name)
                    current = parent
            if not paths:
                raise GuardInvalid("empty file event scope")
            if sys.platform == "linux":
                self._linux(paths, children)
            elif sys.platform == "darwin":
                self._darwin(paths, children)
            else:
                raise GuardInvalid("file event backend unavailable")
            self.check()
        except Exception:
            self.close()
            raise

    def _linux(self, paths, children):
        libc = ctypes.CDLL(None, use_errno=True)
        init = libc.inotify_init1
        init.argtypes = [ctypes.c_int]
        init.restype = ctypes.c_int
        add = libc.inotify_add_watch
        add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add.restype = ctypes.c_int
        self.fd = init(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise GuardInvalid("file event backend unavailable")
        # MODIFY, ATTRIB, MOVE_SELF, DELETE_SELF, CLOSE_WRITE. Parent name
        # watches also catch replacement while ignoring unrelated outputs.
        common = 0x2 | 0x4 | 0x8 | 0x400 | 0x800
        for path in sorted(paths | set(children), key=str):
            directory = path in children
            mask = common | 0x02000000  # IN_DONT_FOLLOW
            if directory:
                mask |= 0x01000000 | 0x40 | 0x80 | 0x100 | 0x200
            wd = add(self.fd, os.fsencode(path), mask)
            if wd < 0:
                raise GuardInvalid("cannot establish complete file event scope")
            self.watches[wd] = children[path] if directory else None

    def _darwin(self, paths, children):
        self.queue = select.kqueue()
        self.fd = self.queue.fileno()
        changes = []
        for path in sorted(paths | set(children), key=str):
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            self.handles.append(fd)
            flags = select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME | select.KQ_NOTE_REVOKE
            if path in paths:
                flags |= (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND |
                          select.KQ_NOTE_ATTRIB | select.KQ_NOTE_LINK)
            changes.append(select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                                         flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                         fflags=flags))
        self.queue.control(changes, 0, 0)

    def _consume_linux(self, data):
        offset = 0
        while offset < len(data):
            if len(data) - offset < 16:
                raise GuardInvalid("invalid file event stream")
            wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
            offset += 16
            if offset + length > len(data):
                raise GuardInvalid("invalid file event stream")
            name = os.fsdecode(data[offset:offset + length].split(b"\0", 1)[0])
            offset += length
            # Overflow, unmount or ignored watch always loses continuity.
            if mask & (0x4000 | 0x2000 | 0x8000) or wd not in self.watches:
                raise GuardInvalid("file event observation lost")
            wanted = self.watches[wd]
            if wanted is None or not name or name in wanted:
                raise GuardInvalid("file scope changed during verification")

    def check(self):
        if self.closed or self.invalid or self.pid != os.getpid():
            self.invalid = True
            raise GuardInvalid("file event guard is not live in this process")
        try:
            if self.queue is not None:
                events = self.queue.control(None, max(1, len(self.handles)), 0)
                if events:
                    raise GuardInvalid("file scope changed or observation lost")
            else:
                while True:
                    try:
                        data = os.read(self.fd, 65536)
                    except BlockingIOError:
                        break
                    if not data:
                        raise GuardInvalid("file event observation closed")
                    self._consume_linux(data)
        except (OSError, ValueError) as exc:
            self.invalid = True
            raise GuardInvalid(str(exc)) from exc

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.queue is not None:
            try:
                self.queue.close()
            except OSError:
                pass
        elif self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
        for fd in self.handles:
            try:
                os.close(fd)
            except OSError:
                pass
        self.handles.clear()

    def __del__(self):
        self.close()
