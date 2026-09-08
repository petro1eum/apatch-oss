"""Transport abstraction for remote apatch workflows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Protocol, Union

from apatch.remote.target import RemoteTarget


RemoteStepResult = Dict[str, Any]
RemoteResponse = Union[
    RemoteStepResult,
    List[RemoteStepResult],
    Callable[[RemoteTarget, str, Mapping[str, Any]], RemoteStepResult],
]


class RemoteTransport(Protocol):
    """Execution boundary between local orchestration and remote commands."""

    def call(
        self,
        target: RemoteTarget,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> RemoteStepResult:
        ...


class FakeRemoteTransport:
    """Deterministic transport used by tests and dry-run callers."""

    def __init__(
        self,
        responses: Optional[MutableMapping[str, RemoteResponse]] = None,
        default_ok: bool = True,
    ) -> None:
        self.responses: MutableMapping[str, RemoteResponse] = responses or {}
        self.default_ok = default_ok
        self.calls: List[Dict[str, Any]] = []

    def call(
        self,
        target: RemoteTarget,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> RemoteStepResult:
        self.calls.append(
            {
                "target": target.as_dict(),
                "operation": operation,
                "arguments": deepcopy(dict(arguments)),
            }
        )

        response = self.responses.get(operation)
        if callable(response):
            return dict(response(target, operation, arguments))
        if isinstance(response, list):
            if response:
                return dict(response.pop(0))
            return {"ok": self.default_ok}
        if response is not None:
            return dict(response)
        return {"ok": self.default_ok}
