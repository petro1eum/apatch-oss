"""ProxyJump (bastion) routing for remote SSH transports.

A jump host lets a team keep the real workspace hosts unroutable from developer
machines: the policy names an internal host that only the bastion can resolve,
and every SSH transport (worker, service, archive) dials it through ``ssh -J``.
Developers and agents see aliases; the bastion is the only public address.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from apatch.remote.errors import RemoteTaskError


# user@host, host:port, [ipv6]:port — never a leading '-' (argv injection) or whitespace.
_JUMP_HOP_RE = re.compile(r"^[A-Za-z0-9\[][A-Za-z0-9._@:\[\]-]*$")


def normalize_jump_hosts(value: Any) -> Optional[Tuple[str, ...]]:
    """Normalize a policy ``jump_host`` value into an ordered tuple of hops.

    Accepts ``None`` / ``false`` / ``""`` (no jump), one string (comma-separated
    hops allowed, exactly like ``ssh -J``), or a list of strings. Fails closed on
    anything that could not be passed safely as one ``-J`` argument.
    """

    if value is None or value is False:
        return None
    if isinstance(value, str):
        raw_items: List[str] = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw_items = []
        for item in value:
            if not isinstance(item, str):
                raise _invalid("jump_host entries must be strings.")
            raw_items.extend(item.split(","))
    else:
        raise _invalid("jump_host must be a string or an array of strings.")

    hops = tuple(item.strip() for item in raw_items if item.strip())
    if not hops:
        return None
    for hop in hops:
        if not _JUMP_HOP_RE.match(hop):
            raise _invalid("jump_host entry {!r} is not a valid ssh destination.".format(hop))
    return hops


def ssh_args_declare_jump(ssh_args: Optional[Iterable[str]]) -> bool:
    """Return True when raw ssh_args already carry ``-J`` or ``-o ProxyJump``.

    OpenSSH accepts a single ``-J``; a policy that sets both ``jump_host`` and a
    raw ProxyJump flag is ambiguous and must fail closed instead of at dial time.
    """

    items = [str(item) for item in (ssh_args or ())]
    for index, item in enumerate(items):
        if item == "-J" or (item.startswith("-J") and len(item) > 2):
            return True
        option = None
        if item == "-o" and index + 1 < len(items):
            option = items[index + 1]
        elif item.startswith("-o") and len(item) > 2:
            option = item[2:]
        if option and option.strip().lower().replace(" ", "").startswith("proxyjump"):
            return True
    return False


def jump_argv(jump_hosts: Optional[Sequence[str]]) -> List[str]:
    """Build the ``-J hop1,hop2`` argv fragment for the configured hops."""

    if not jump_hosts:
        return []
    return ["-J", ",".join(str(hop) for hop in jump_hosts)]


def _invalid(message: str) -> RemoteTaskError:
    return RemoteTaskError(
        "REMOTE_POLICY_INVALID",
        message,
        recoverable=True,
        recommended_action=(
            "Set jump_host to an ssh destination such as bastion, user@bastion, "
            "or [user@]host:port; use an array or a comma list for multi-hop."
        ),
    )
