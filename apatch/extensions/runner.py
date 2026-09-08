"""Bounded out-of-process execution for verified local extensions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Sequence

from apatch.extensions.schema import (
    EXTENSION_PROTOCOL,
    ExtensionContractError,
    validate_json_value,
)

MAX_INPUT_BYTES = 256 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_PROPOSAL_BYTES = 256 * 1024
MAX_PROPOSAL_COUNT = 100

_RESERVED_RESPONSE_KEYS = frozenset(
    {
        "session_token",
        "session_capability",
        "governed_session_id",
        "attestation",
        "checkpoint",
        "trustchain",
        "apply",
        "applied",
    }
)
_PROPOSAL_KEYS = {
    "replace": frozenset({"action", "target_file", "find_text", "replace_text", "match_mode", "replace_all"}),
    "create": frozenset({"action", "target_file", "content"}),
    "delete": frozenset({"action", "target_file"}),
    "rename": frozenset({"action", "source_file", "target_file"}),
    "chmod": frozenset({"action", "target_file", "mode", "executable"}),
}
_SECRET_PATTERN = re.compile(r"(?i)(key|token|secret|password)(\s*[:=]\s*)([^\s,;]+)")


def _fail(code: str, message: str, *, path: str = "") -> None:
    raise ExtensionContractError(code, message, path=path)


def _json_bytes(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("EXTENSION_JSON_INVALID", "value is not canonical JSON")
    return raw


def _relative_proposal_path(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("EXTENSION_PROPOSAL_INVALID", "proposal path must be a POSIX relative path", path=path)
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("EXTENSION_PROPOSAL_INVALID", "proposal path escapes workspace", path=path)
    return pure.as_posix()


def validate_proposed_needles(value: Any) -> list[Dict[str, Any]]:
    """Pure validation only; never writes patch logs or workspace files."""

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_PROPOSAL_COUNT:
        _fail("EXTENSION_PROPOSAL_INVALID", "proposed_needles must be a bounded list")
    if len(_json_bytes(value)) > MAX_PROPOSAL_BYTES:
        _fail("EXTENSION_PROPOSAL_INVALID", "proposed_needles exceed byte limit")
    normalized: list[Dict[str, Any]] = []
    for index, raw in enumerate(value):
        path = f"proposed_needles[{index}]"
        if not isinstance(raw, Mapping):
            _fail("EXTENSION_PROPOSAL_INVALID", "needle must be an object", path=path)
        action = raw.get("action")
        if action not in _PROPOSAL_KEYS:
            _fail("EXTENSION_PROPOSAL_INVALID", "unsupported proposal action", path=path)
        unknown = sorted(set(raw) - set(_PROPOSAL_KEYS[action]))
        if unknown:
            _fail("EXTENSION_PROPOSAL_INVALID", "unknown proposal fields: " + ", ".join(unknown), path=path)
        item = dict(raw)
        if action == "rename":
            item["source_file"] = _relative_proposal_path(item.get("source_file"), path=path + ".source_file")
        if "target_file" in item or action != "rename":
            item["target_file"] = _relative_proposal_path(item.get("target_file"), path=path + ".target_file")
        if action == "replace":
            if not isinstance(item.get("find_text"), str) or not item["find_text"]:
                _fail("EXTENSION_PROPOSAL_INVALID", "replace requires non-empty find_text", path=path)
            if not isinstance(item.get("replace_text"), str):
                _fail("EXTENSION_PROPOSAL_INVALID", "replace_text must be a string", path=path)
            if item.get("match_mode", "literal") not in {"literal", "whitespace", "regex", "json"}:
                _fail("EXTENSION_PROPOSAL_INVALID", "unsupported match_mode", path=path)
            if "replace_all" in item and not isinstance(item["replace_all"], bool):
                _fail("EXTENSION_PROPOSAL_INVALID", "replace_all must be boolean", path=path)
        elif action == "create" and not isinstance(item.get("content"), str):
            _fail("EXTENSION_PROPOSAL_INVALID", "create content must be a string", path=path)
        elif action == "chmod":
            has_mode = "mode" in item
            has_exec = "executable" in item
            if has_mode == has_exec:
                _fail("EXTENSION_PROPOSAL_INVALID", "chmod requires exactly one of mode/executable", path=path)
            if has_exec and not isinstance(item["executable"], bool):
                _fail("EXTENSION_PROPOSAL_INVALID", "executable must be boolean", path=path)
            if has_mode and str(item["mode"]) not in {"644", "0644", "0o644", "755", "0755", "0o755"}:
                _fail("EXTENSION_PROPOSAL_INVALID", "unsupported chmod mode", path=path)
        normalized.append(item)
    return normalized


def _contains_reserved_key(value: Any) -> str:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _RESERVED_RESPONSE_KEYS:
                return str(key)
            found = _contains_reserved_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_reserved_key(child)
            if found:
                return found
    return ""


def _redact(text: str, secret_values: Sequence[str]) -> str:
    redacted = text
    for value in sorted({item for item in secret_values if len(item) >= 4}, key=len, reverse=True):
        redacted = redacted.replace(value, "[REDACTED]")
    redacted = _SECRET_PATTERN.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", redacted)
    return redacted[:1024]


def _kill_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()


def _bounded_process(
    argv: Sequence[str],
    request: bytes,
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_sec: int,
    max_stdout_bytes: int,
    secret_values: Sequence[str],
) -> tuple[bytes, str]:
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        env=dict(env),
        shell=False,
        start_new_session=True,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        os.set_blocking(stream.fileno(), False)
    selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    offset = 0
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout_sec
    failure: ExtensionContractError | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = ExtensionContractError("EXTENSION_PROCESS_TIMEOUT", "extension process exceeded deadline")
                _kill_process(proc)
                break
            events = selector.select(min(remaining, 0.1))
            if not events and proc.poll() is not None:
                continue
            for key, _mask in events:
                stream = key.fileobj
                kind = key.data
                if kind == "stdin":
                    try:
                        count = os.write(stream.fileno(), request[offset : offset + 65536])
                    except BrokenPipeError:
                        count = 0
                        offset = len(request)
                    offset += count
                    if offset >= len(request):
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                target = stdout if kind == "stdout" else stderr
                target.extend(chunk)
                bound = max_stdout_bytes if kind == "stdout" else MAX_STDERR_BYTES
                if len(target) > bound:
                    failure = ExtensionContractError(
                        "EXTENSION_OUTPUT_LIMIT" if kind == "stdout" else "EXTENSION_STDERR_LIMIT",
                        "extension process exceeded " + kind + " byte limit",
                    )
                    _kill_process(proc)
                    break
            if failure:
                break
    finally:
        selector.close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    if failure:
        raise failure
    try:
        return_code = proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _kill_process(proc)
        _fail("EXTENSION_PROCESS_TIMEOUT", "extension process did not terminate")
    stderr_text = _redact(stderr.decode("utf-8", errors="replace"), secret_values)
    if return_code != 0:
        _fail(
            "EXTENSION_PROCESS_FAILED",
            "extension process exited non-zero" + (": " + stderr_text if stderr_text else ""),
        )
    return bytes(stdout), stderr_text


def _stage_verified(verified: Mapping[str, Any], destination: Path) -> Dict[str, Path]:
    staged: Dict[str, Path] = {}
    for artifact in verified["artifacts"]:
        source = Path(artifact["absolute_path"])
        try:
            raw = source.read_bytes()
        except OSError as exc:
            _fail("EXTENSION_ARTIFACT_READ_FAILED", "cannot stage artifact: " + str(exc), path=artifact["path"])
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            _fail("EXTENSION_ARTIFACT_DIGEST_MISMATCH", "artifact changed before execution", path=artifact["path"])
        target = destination.joinpath(*PurePosixPath(artifact["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o700 if artifact["path"] == verified["tool"]["entrypoint"] else 0o600)
        staged[artifact["path"]] = target
    return staged


def run_verified_tool(
    verified: Mapping[str, Any],
    arguments: Any,
    *,
    workspace: str | os.PathLike[str],
) -> Dict[str, Any]:
    """Execute one catalog-resolved tool without invoking APatch mutation APIs."""

    tool = verified["tool"]
    manifest = verified["manifest"]
    lock_entry = verified["lock_entry"]
    validate_json_value(arguments, tool["input_schema"], path="arguments")
    request_id = uuid.uuid4().hex
    request_obj = {
        "protocol": EXTENSION_PROTOCOL,
        "request_id": request_id,
        "tool_id": tool["full_id"],
        "workspace": {
            "root": str(Path(workspace).resolve()) if "workspace.read" in lock_entry["grants"] else None,
            "read_only": True,
        },
        "arguments": arguments,
    }
    request = _json_bytes(request_obj)
    if len(request) > MAX_INPUT_BYTES:
        _fail("EXTENSION_INPUT_LIMIT", "extension request exceeds input byte limit")

    missing_env_grants = [
        name for name in tool["pass_env"] if "env:" + name not in lock_entry["grants"]
    ]
    if missing_env_grants:
        _fail("EXTENSION_GRANT_REQUIRED", "tool environment capability is not granted")
    passed_env = {
        name: os.environ[name] for name in tool["pass_env"] if name in os.environ
    }
    secret_values = list(passed_env.values())

    with tempfile.TemporaryDirectory(prefix="apatch-extension-") as temp:
        stage = Path(temp)
        (stage / "home").mkdir()
        (stage / "tmp").mkdir()
        staged = _stage_verified(verified, stage)
        entrypoint = staged[tool["entrypoint"]]
        if tool["runtime"] == "python":
            argv = [sys.executable, str(entrypoint), *tool["argv"]]
        else:
            argv = [str(entrypoint), *tool["argv"]]
        env = {
            "PATH": os.defpath,
            "HOME": str(stage / "home"),
            "TMPDIR": str(stage / "tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **passed_env,
        }
        stdout, _stderr = _bounded_process(
            argv,
            request,
            cwd=stage,
            env=env,
            timeout_sec=tool["timeout_sec"],
            max_stdout_bytes=tool["max_output_bytes"],
            secret_values=secret_values,
        )

    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("EXTENSION_RESPONSE_JSON_INVALID", "extension must emit exactly one UTF-8 JSON object")
    if not isinstance(response, Mapping):
        _fail("EXTENSION_RESPONSE_INVALID", "extension response must be an object")
    if response.get("protocol") != EXTENSION_PROTOCOL or response.get("request_id") != request_id:
        _fail("EXTENSION_RESPONSE_BINDING_MISMATCH", "extension response protocol/request_id mismatch")
    reserved = _contains_reserved_key(response)
    if reserved:
        _fail("EXTENSION_RESPONSE_RESERVED_FIELD", "extension returned reserved field: " + reserved)
    allowed = {"protocol", "request_id", "ok", "result", "proposed_needles", "error"}
    unknown = sorted(set(response) - allowed)
    if unknown:
        _fail("EXTENSION_RESPONSE_INVALID", "unknown response fields: " + ", ".join(unknown))
    if not isinstance(response.get("ok"), bool):
        _fail("EXTENSION_RESPONSE_INVALID", "response ok must be boolean")
    if not response["ok"]:
        detail = _redact(str(response.get("error") or "extension rejected request"), secret_values)
        _fail("EXTENSION_PROCESS_REJECTED", detail)

    result = response.get("result", {})
    validate_json_value(result, tool["output_schema"], path="result")
    proposed = validate_proposed_needles(response.get("proposed_needles"))
    if tool["authority"] == "read_only" and proposed:
        _fail("EXTENSION_AUTHORITY_VIOLATION", "read_only tool returned proposed_needles")

    response_hash = hashlib.sha256(stdout).hexdigest()
    return {
        "ok": True,
        "extension": manifest["id"],
        "version": manifest["version"],
        "tool_id": tool["full_id"],
        "authority": tool["authority"],
        "result": result,
        "proposed_needles": proposed,
        "evidence": {
            "manifest_sha256": verified["manifest_sha256"],
            "package_sha256": verified["package_sha256"],
            "request_sha256": hashlib.sha256(request).hexdigest(),
            "response_sha256": response_hash,
        },
    }
