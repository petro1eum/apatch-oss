"""Ownership / pilot edge — the person↔many-keys binding (Canon §10 #1 tech-debt).

DISTINCT from ``AliasResolver`` (same-identity aliasing: many handles → ONE key_id).
This edge expresses that ONE human principal (itself a ``key_id``, addressable via its
``professional_uuid`` alias) OWNS or PILOTS a set of DISTINCT agent ``key_id`` s. It is
the missing primitive that unlocks two things at once:

  * "agents roll up to one user" — fold every owned agent's avatar into the human's
    portfolio avatar (:func:`rollup_key_ids`);
  * "agent as its own asset" — record WHO owns/pilots each agent key_id, so an agent
    avatar (a fold of ContributionEvents where ``avatar_id == agent key_id``) can be a
    first-class, owned asset.

Money-free by construction (same barrier as the rest of the contract): it records WHO
owns/pilots WHAT — never value, price, or the contribution split. The economic split
(``cv_delta``, ``company_ai_share``) and any valuation stay Layer 3 (HC).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

# Relationship of a human principal to an agent key_id.
#   pilot    — the human actively drives/steers the agent (the owner's framing)
#   owner    — the human holds/controls the agent (may not actively pilot)
#   operator — a delegated runner acting for the owner
OWNERSHIP_ROLES = ("pilot", "owner", "operator")
DEFAULT_ROLE = "pilot"


class OwnershipError(ValueError):
    """Raised on role / self-binding / cross-owner-conflict violations."""


class OwnershipGraph:
    """Human-principal → owned/piloted agent key_ids.

    Forward map: ``principal_key_id -> {agent_key_id: role}``.
    Reverse invariant: an agent key_id belongs to AT MOST ONE principal (one agent
    cannot be owned by two humans); binding a conflicting principal raises. A principal
    may own many agents. Re-binding the same (principal, agent) is idempotent and may
    update the role. A principal may not own itself (ownership is between distinct
    identities; a human's own direct work lives under their own avatar, not an edge).
    """

    def __init__(self, table: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        self._by_principal: Dict[str, Dict[str, str]] = {}
        self._owner: Dict[str, str] = {}  # agent_key_id -> principal_key_id
        if table:
            for principal, agents in table.items():
                for agent, role in (agents or {}).items():
                    self.bind(principal, agent, role)

    @staticmethod
    def _check_role(role: str) -> None:
        if role not in OWNERSHIP_ROLES:
            raise OwnershipError(
                f"unknown role {role!r}; allowed: {OWNERSHIP_ROLES}")

    def bind(self, principal_key_id: str, agent_key_id: str,
             role: str = DEFAULT_ROLE) -> None:
        """Record that ``principal_key_id`` owns/pilots ``agent_key_id``.

        Idempotent for the same principal (role may be updated). Raises if the agent is
        already owned by a DIFFERENT principal, on self-ownership, or an unknown role.
        """
        self._check_role(role)
        if not principal_key_id or not agent_key_id:
            raise OwnershipError("principal_key_id and agent_key_id must be non-empty")
        if principal_key_id == agent_key_id:
            raise OwnershipError(
                f"a principal cannot own itself ({principal_key_id!r})")
        existing = self._owner.get(agent_key_id)
        if existing is not None and existing != principal_key_id:
            raise OwnershipError(
                f"ownership conflict: agent {agent_key_id!r} already owned by "
                f"{existing!r}, cannot rebind to {principal_key_id!r}")
        self._owner[agent_key_id] = principal_key_id
        self._by_principal.setdefault(principal_key_id, {})[agent_key_id] = role

    def unbind(self, agent_key_id: str) -> bool:
        """Remove an agent's ownership edge. Returns True if one was removed."""
        principal = self._owner.pop(agent_key_id, None)
        if principal is None:
            return False
        agents = self._by_principal.get(principal)
        if agents:
            agents.pop(agent_key_id, None)
            if not agents:
                self._by_principal.pop(principal, None)
        return True

    def agents_of(self, principal_key_id: str) -> Dict[str, str]:
        """All agent key_ids owned/piloted by a principal → ``{agent: role}``."""
        return dict(self._by_principal.get(principal_key_id, {}))

    def owner_of(self, agent_key_id: str) -> Optional[str]:
        """The principal key_id that owns an agent, or None."""
        return self._owner.get(agent_key_id)

    def role_of(self, principal_key_id: str, agent_key_id: str) -> Optional[str]:
        return self._by_principal.get(principal_key_id, {}).get(agent_key_id)

    def principals(self) -> List[str]:
        return sorted(self._by_principal.keys())

    def to_dict(self) -> Dict[str, Dict[str, str]]:
        return {p: dict(agents) for p, agents in self._by_principal.items()}

    @classmethod
    def from_dict(cls, table: Dict[str, Dict[str, str]]) -> "OwnershipGraph":
        return cls(table)

    @classmethod
    def load(cls, path: str) -> "OwnershipGraph":
        if not os.path.isfile(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, sort_keys=True, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def rollup_key_ids(ownership: OwnershipGraph, principal_key_id: str,
                   include_self: bool = True) -> List[str]:
    """The full set of key_ids that fold into a human's portfolio avatar.

    = the principal's own key_id (its direct work) + every owned/piloted agent key_id.
    This is the "many agents → one user" primitive: pass these key_ids to the avatar
    fold to aggregate a human's contributions across all their agents. Deterministic,
    de-duplicated, principal-first.
    """
    ordered: List[str] = []
    if include_self and principal_key_id:
        ordered.append(principal_key_id)
    ordered.extend(sorted(ownership.agents_of(principal_key_id).keys()))
    seen = set()
    out: List[str] = []
    for k in ordered:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out
