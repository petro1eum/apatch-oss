"""Identity stopgap — `key_id` is the avatar's primary key; everything else is an alias.

Avatar Architecture Canon §8 / Rule 3. Full IRS (HC ADR-004) is tech debt; this is the
trivial, deterministic alias table that unblocks the HC consumer's `avatar_id` binding
TODAY: `avatar_id ≡ contributor_id ≡ key_id`.

ADR-004 semantic namespaces are PRESERVED, not collapsed: `key_id` is the identity of
ACTION (who signed). This resolver only maps external handles (github_login, org
user_id, professional_uuid, contributor_id, enrolled_cn, legacy agent_id) TO the
canonical `key_id`. It does not merge their meanings and it does not assign weight,
price, or attribution — that is Layer 3 (HC).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

# Alias namespaces that resolve TO a key_id (ADR-004 aligned).
ALIAS_NAMESPACES = (
    "github_login",
    "org_user_id",
    "professional_uuid",
    "contributor_id",   # hc.economics — attribution subject, resolved to key_id here
    "enrolled_cn",      # certificate common name
    "legacy_agent_id",  # APATCH_AGENT_ID fallback for un-enrolled events
)


class IdentityError(ValueError):
    """Raised on namespace / alias-conflict violations."""


class AliasResolver:
    """A key_id-primary alias table. Forward map: key_id -> {namespace: [aliases]}.

    An (namespace, alias) pair must map to at most ONE key_id — one handle cannot
    belong to two people. Registering a conflicting binding raises.
    """

    def __init__(self, table: Optional[Dict[str, Dict[str, List[str]]]] = None) -> None:
        self._fwd: Dict[str, Dict[str, List[str]]] = {}
        self._rev: Dict[tuple, str] = {}  # (namespace, alias) -> key_id
        if table:
            for key_id, by_ns in table.items():
                for ns, aliases in (by_ns or {}).items():
                    for alias in aliases:
                        self.register(alias, ns, key_id)

    @staticmethod
    def _check_ns(namespace: str) -> None:
        if namespace not in ALIAS_NAMESPACES:
            raise IdentityError(
                f"unknown namespace {namespace!r}; allowed: {ALIAS_NAMESPACES}")

    def register(self, alias: str, namespace: str, key_id: str) -> None:
        """Bind an external handle to a key_id. Idempotent; conflicting bind raises."""
        self._check_ns(namespace)
        if not alias or not key_id:
            raise IdentityError("alias and key_id must be non-empty")
        existing = self._rev.get((namespace, alias))
        if existing is not None and existing != key_id:
            raise IdentityError(
                f"alias conflict: {namespace}:{alias} already bound to {existing!r}, "
                f"cannot rebind to {key_id!r}")
        self._rev[(namespace, alias)] = key_id
        ns_map = self._fwd.setdefault(key_id, {})
        bucket = ns_map.setdefault(namespace, [])
        if alias not in bucket:
            bucket.append(alias)

    def resolve(self, alias: str, namespace: str) -> Optional[str]:
        """Return the key_id for an external handle, or None if unknown."""
        self._check_ns(namespace)
        return self._rev.get((namespace, alias))

    def avatar_id_for(self, alias: str, namespace: str) -> Optional[str]:
        """Alias for `resolve` — avatar_id == key_id (canon §7.3 #4)."""
        return self.resolve(alias, namespace)

    def aliases_of(self, key_id: str) -> Dict[str, List[str]]:
        """All known external handles for a key_id, grouped by namespace."""
        return {ns: list(aliases) for ns, aliases in self._fwd.get(key_id, {}).items()}

    def to_dict(self) -> Dict[str, Dict[str, List[str]]]:
        return {k: {ns: list(a) for ns, a in v.items()} for k, v in self._fwd.items()}

    @classmethod
    def from_dict(cls, table: Dict[str, Dict[str, List[str]]]) -> "AliasResolver":
        return cls(table)

    @classmethod
    def load(cls, path: str) -> "AliasResolver":
        """Load a resolver from JSON, or an empty one if the file is missing."""
        if not os.path.isfile(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, sort_keys=True, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
