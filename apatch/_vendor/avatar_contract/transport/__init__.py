"""Machine-readable transport contracts for the Avatar owner lane.

The event contracts in this package say what a signed record *is*. These
transport contracts say *where it travels*: the routes, methods, request shapes
and response keys of the two HTTP surfaces every consumer of the Avatar lane
already agrees on.

- ``avatar_bff`` -- the trust-chain.ai Avatar BFF that a local apatch and the
  browser client call.
- ``hc_tracker_internal`` -- the HC Tracker service-to-service API the BFF and
  apatch's internal lane call.

The JSON documents shipped next to this module are the contract; this module
is only their reader. Nothing here performs a network operation.
"""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any, Dict, List, Tuple

TRANSPORT_SCHEMA = "avatar_contract.transport.v1"
TRANSPORT_CONTRACTS: Tuple[str, ...] = ("avatar_bff", "hc_tracker_internal")
TRANSPORT_OUTCOMES: Tuple[str, ...] = ("success", "not_ready")


class TransportContractError(ValueError):
    """Raised when a transport contract, route or response shape is not as declared."""


def _contract_filename(name: str) -> str:
    if name not in TRANSPORT_CONTRACTS:
        raise TransportContractError(
            f"unknown transport contract {name!r}; known contracts: "
            f"{', '.join(TRANSPORT_CONTRACTS)}"
        )
    return f"{name}.v1.json"


def transport_contract_bytes(name: str) -> bytes:
    """The exact bytes of the shipped contract document for ``name``."""
    return files(__name__).joinpath(_contract_filename(name)).read_bytes()


def contract_sha256(name: str) -> str:
    """SHA-256 hex digest of the raw contract JSON bytes shipped for ``name``."""
    return hashlib.sha256(transport_contract_bytes(name)).hexdigest()


def load_transport_contract(name: str) -> Dict[str, Any]:
    """Load one shipped transport contract by name (``avatar_bff`` or
    ``hc_tracker_internal``).

    Raises :class:`TransportContractError` for an unknown name, or when the
    shipped document is not the v1 contract it claims to be.
    """
    raw = transport_contract_bytes(name)
    try:
        contract = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TransportContractError(
            f"transport contract {name!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(contract, dict):
        raise TransportContractError(
            f"transport contract {name!r} must be a JSON object, "
            f"got {type(contract).__name__}"
        )
    if contract.get("schema") != TRANSPORT_SCHEMA:
        raise TransportContractError(
            f"transport contract {name!r} declares schema "
            f"{contract.get('schema')!r}, expected {TRANSPORT_SCHEMA!r}"
        )
    if contract.get("name") != name:
        raise TransportContractError(
            f"transport contract file for {name!r} names itself "
            f"{contract.get('name')!r}"
        )
    if not isinstance(contract.get("routes"), list):
        raise TransportContractError(
            f"transport contract {name!r} declares no routes list"
        )
    return contract


def _contract_name(contract: Dict[str, Any]) -> str:
    name = contract.get("name") if isinstance(contract, dict) else None
    return str(name) if name else "<unnamed>"


def transport_route(contract: Dict[str, Any], route_id: str) -> Dict[str, Any]:
    """The route block with ``id == route_id``; raises when it is missing."""
    name = _contract_name(contract)
    routes = contract.get("routes") if isinstance(contract, dict) else None
    if not isinstance(routes, list):
        raise TransportContractError(
            f"transport contract {name!r} declares no routes list"
        )
    for route in routes:
        if isinstance(route, dict) and route.get("id") == route_id:
            return route
    known: List[str] = [
        str(route.get("id"))
        for route in routes
        if isinstance(route, dict) and route.get("id")
    ]
    raise TransportContractError(
        f"transport contract {name!r} has no route {route_id!r}; "
        f"known routes: {', '.join(known)}"
    )


def _route_field(contract: Dict[str, Any], route_id: str, field: str) -> str:
    value = transport_route(contract, route_id).get(field)
    if not isinstance(value, str) or not value:
        raise TransportContractError(
            f"route {route_id!r} of transport contract "
            f"{_contract_name(contract)!r} declares no {field}"
        )
    return value


def route_path(contract: Dict[str, Any], route_id: str) -> str:
    """The declared path template of one route, e.g. ``/api/avatar/home``."""
    return _route_field(contract, route_id, "path")


def route_method(contract: Dict[str, Any], route_id: str) -> str:
    """The declared HTTP method of one route, e.g. ``GET``."""
    return _route_field(contract, route_id, "method")


def assert_response_shape(
    contract: Dict[str, Any],
    route_id: str,
    payload: Dict[str, Any],
    outcome: str = "success",
) -> None:
    """Check ``payload`` against the ``success`` or ``not_ready`` block of a route.

    Every key listed in the block's ``required_keys`` must be present. When the
    block's ``status_values`` is a list, ``payload["status"]`` must be one of
    them; any other ``status_values`` shape (a per-field mapping, or none at
    all) leaves the status unchecked. Raises :class:`TransportContractError`
    naming the contract, route, outcome and exact deviation otherwise.
    """
    name = _contract_name(contract)
    route = transport_route(contract, route_id)
    where = f"{name}.{route_id} {outcome} response"
    if outcome not in TRANSPORT_OUTCOMES:
        raise TransportContractError(
            f"unknown response outcome {outcome!r} for {name}.{route_id}; "
            f"expected one of: {', '.join(TRANSPORT_OUTCOMES)}"
        )
    block = route.get(outcome)
    if not isinstance(block, dict):
        raise TransportContractError(
            f"route {route_id!r} of transport contract {name!r} declares no "
            f"{outcome!r} response block"
        )
    if not isinstance(payload, dict):
        raise TransportContractError(
            f"{where} must be a JSON object, got {type(payload).__name__}"
        )
    required = block.get("required_keys", [])
    if not isinstance(required, list):
        raise TransportContractError(
            f"{where} block declares malformed required_keys"
        )
    missing = [str(key) for key in required if key not in payload]
    if missing:
        raise TransportContractError(
            f"{where} is missing required keys: {', '.join(missing)}"
        )
    status_values = block.get("status_values")
    if isinstance(status_values, list):
        if "status" not in payload:
            raise TransportContractError(
                f"{where} declares status_values {status_values!r} "
                "but the payload carries no 'status'"
            )
        if payload["status"] not in status_values:
            raise TransportContractError(
                f"{where} status {payload['status']!r} is not one of "
                f"{status_values!r}"
            )


__all__ = [
    "TRANSPORT_CONTRACTS",
    "TRANSPORT_OUTCOMES",
    "TRANSPORT_SCHEMA",
    "TransportContractError",
    "assert_response_shape",
    "contract_sha256",
    "load_transport_contract",
    "route_method",
    "route_path",
    "transport_contract_bytes",
    "transport_route",
]
