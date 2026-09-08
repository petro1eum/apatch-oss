"""Opaque local-source handoff for remote aliases."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from apatch.remote.errors import RemoteTaskError
from apatch.remote.policy import load_remote_policy, resolve_remote_target
from apatch.remote.target import RemoteTarget


DEFAULT_SOURCE_EXCLUDES = [
    ".git",
    ".apatch/tmp",
    ".DS_Store",
    "*/.DS_Store",
    "._*",
    "*/._*",
    ".AppleDouble",
    "*/.AppleDouble",
    "__MACOSX",
    "*/__MACOSX",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "dist",
    "build",
]


def plan_source_handoff(
    *,
    alias: str,
    source_dir: str = ".",
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
    mode: str = "workspace_overlay",
) -> Dict[str, Any]:
    """Return a redacted source handoff plan for a remote alias."""

    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    target = resolve_remote_target(alias, policy_root=policy_root, policy=cfg)
    source_cfg = _source_handoff_config(cfg, alias)
    source = _prepare_source_dir(source_dir)
    _validate_source_policy(source, source_cfg, policy_root)
    _validate_mode(mode, source_cfg)
    plan = _build_plan(target, source, source_cfg, mode)
    return _redact_plan(target, plan, source)


def execute_source_handoff(
    *,
    alias: str,
    source_dir: str = ".",
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
    mode: str = "workspace_overlay",
    transport: Optional["SshArchiveTransport"] = None,
) -> Dict[str, Any]:
    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    target = resolve_remote_target(alias, policy_root=policy_root, policy=cfg)
    source_cfg = _source_handoff_config(cfg, alias)
    source = _prepare_source_dir(source_dir)
    _validate_source_policy(source, source_cfg, policy_root)
    _validate_mode(mode, source_cfg)
    plan = _build_plan(target, source, source_cfg, mode)
    if transport is None:
        transport = SshArchiveTransport(
            ssh_args=list(target.ssh_args) if target.ssh_args else None,
            timeout_sec=target.timeout_sec or 600,
        )
    result = transport.push(target, plan)
    return _redact_plan(target, result, source)


RELEASE_BUNDLE_MODE = "release_bundle"
_RELEASE_IMAGE_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})(?:\s|$)")
_ALLOWED_RELEASE_ENV_FILES = {".env.release", ".env.prod.example"}


def plan_release_bundle_handoff(
    *,
    alias: str,
    bundle_path: str,
    checksum_path: str,
    release_sha: str,
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Plan a checksum-verified immutable release bundle handoff.

    Unlike workspace overlays, release bundles never use a caller-controlled local
    root allowlist. The broker validates the immutable artifact before any remote
    transfer, and the remote transport verifies the same archive digest again.
    """

    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    target = resolve_remote_target(alias, policy_root=policy_root, policy=cfg)
    source_cfg = _source_handoff_config(cfg, alias)
    _validate_mode(RELEASE_BUNDLE_MODE, source_cfg)
    release_cfg = _release_bundle_config(source_cfg)
    bundle = _prepare_bundle_path(bundle_path)
    checksum = _prepare_checksum_path(checksum_path)
    normalized_sha = _normalize_release_sha(release_sha)
    bundle_sha256 = _verify_bundle_checksum(bundle, checksum)
    _validate_release_bundle(
        bundle,
        release_sha=normalized_sha,
        release_cfg=release_cfg,
    )
    plan = {
        "ok": True,
        "dry_run": True,
        "operation": "verified_release_bundle_push",
        "mode": RELEASE_BUNDLE_MODE,
        "archive_transport": "internal",
        "target": target.as_dict(),
        "bundle_path": bundle,
        "checksum_path": checksum,
        "bundle_sha256": bundle_sha256,
        "release_sha": normalized_sha,
        "credential_strategy": "local_broker_only",
        "agent_visible_transport": False,
    }
    return _redact_plan(target, plan, bundle, checksum)


def execute_release_bundle_handoff(
    *,
    alias: str,
    bundle_path: str,
    checksum_path: str,
    release_sha: str,
    policy_root: str = ".",
    policy: Optional[Mapping[str, Any]] = None,
    transport: Optional["SshArchiveTransport"] = None,
) -> Dict[str, Any]:
    cfg = dict(policy) if policy is not None else load_remote_policy(policy_root)
    target = resolve_remote_target(alias, policy_root=policy_root, policy=cfg)
    source_cfg = _source_handoff_config(cfg, alias)
    _validate_mode(RELEASE_BUNDLE_MODE, source_cfg)
    release_cfg = _release_bundle_config(source_cfg)
    bundle = _prepare_bundle_path(bundle_path)
    checksum = _prepare_checksum_path(checksum_path)
    normalized_sha = _normalize_release_sha(release_sha)
    bundle_sha256 = _verify_bundle_checksum(bundle, checksum)
    _validate_release_bundle(
        bundle,
        release_sha=normalized_sha,
        release_cfg=release_cfg,
    )
    plan = {
        "ok": True,
        "dry_run": False,
        "operation": "verified_release_bundle_push",
        "mode": RELEASE_BUNDLE_MODE,
        "archive_transport": "internal",
        "target": target.as_dict(),
        "bundle_path": bundle,
        "checksum_path": checksum,
        "bundle_sha256": bundle_sha256,
        "release_sha": normalized_sha,
        "credential_strategy": "local_broker_only",
        "agent_visible_transport": False,
    }
    if transport is None:
        transport = SshArchiveTransport(
            ssh_args=list(target.ssh_args) if target.ssh_args else None,
            timeout_sec=target.timeout_sec or 600,
        )
    result = transport.push_release_bundle(target, plan)
    return _redact_plan(target, result, bundle, checksum)


def _release_bundle_config(source_cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    release_cfg = source_cfg.get("release_bundle")
    if not isinstance(release_cfg, Mapping) or not release_cfg.get("enabled"):
        raise RemoteTaskError(
            "REMOTE_RELEASE_BUNDLE_DISABLED",
            "Checksum-verified release bundle handoff is not enabled for this alias.",
            recoverable=True,
            recommended_action="Enable source_handoff.release_bundle in the local remote policy.",
        )
    raw_keys = release_cfg.get("required_image_keys")
    if (
        not isinstance(raw_keys, Sequence)
        or isinstance(raw_keys, (str, bytes))
        or not raw_keys
    ):
        raise _release_bundle_policy_invalid()
    required_keys = tuple(str(key) for key in raw_keys)
    if (
        len(set(required_keys)) != len(required_keys)
        or any(not _RELEASE_IMAGE_KEY_RE.fullmatch(key) for key in required_keys)
    ):
        raise _release_bundle_policy_invalid()
    repositories = release_cfg.get("allowed_image_repositories")
    if (
        not isinstance(repositories, Mapping)
        or any(
            not isinstance(repositories.get(key), str) or not repositories[key]
            for key in required_keys
        )
    ):
        raise _release_bundle_policy_invalid()
    normalized = dict(release_cfg)
    normalized["required_image_keys"] = required_keys
    return normalized


def _release_bundle_policy_invalid() -> RemoteTaskError:
    return RemoteTaskError(
        "REMOTE_RELEASE_BUNDLE_POLICY_INVALID",
        "Release bundle policy must declare unique image keys and pin each repository.",
        recoverable=False,
        recommended_action=(
            "Set a non-empty source_handoff.release_bundle.required_image_keys list "
            "and allowed_image_repositories entry for every key."
        ),
    )


def _prepare_bundle_path(bundle_path: str) -> str:
    path = os.path.abspath(os.path.expanduser(bundle_path or ""))
    if not os.path.isfile(path):
        raise RemoteTaskError(
            "REMOTE_RELEASE_BUNDLE_INVALID",
            "Release bundle archive does not exist.",
            recoverable=True,
            recommended_action="Provide the release artifact archive produced by CI.",
        )
    return path


def _prepare_checksum_path(checksum_path: str) -> str:
    path = os.path.abspath(os.path.expanduser(checksum_path or ""))
    if not os.path.isfile(path):
        raise RemoteTaskError(
            "REMOTE_RELEASE_CHECKSUM_INVALID",
            "Release checksum file does not exist.",
            recoverable=True,
            recommended_action="Provide the matching CI-generated .sha256 file.",
        )
    return path


def _normalize_release_sha(release_sha: str) -> str:
    value = str(release_sha or "").lower()
    if not _RELEASE_SHA_RE.fullmatch(value):
        raise RemoteTaskError(
            "REMOTE_RELEASE_SHA_INVALID",
            "Release SHA must be an immutable 40-character lowercase Git commit SHA.",
            recoverable=True,
            recommended_action="Use the exact commit SHA used to build the release images.",
        )
    return value


def _verify_bundle_checksum(bundle_path: str, checksum_path: str) -> str:
    with open(checksum_path, "r", encoding="utf-8") as handle:
        match = _RELEASE_CHECKSUM_RE.match(handle.readline().strip())
    if match is None:
        raise RemoteTaskError(
            "REMOTE_RELEASE_CHECKSUM_INVALID",
            "Release checksum file is malformed.",
            recoverable=True,
            recommended_action="Use the .sha256 sidecar emitted by CI.",
        )
    actual = _sha256_file(bundle_path)
    if actual != match.group(1):
        raise RemoteTaskError(
            "REMOTE_RELEASE_CHECKSUM_MISMATCH",
            "Release bundle checksum does not match its CI sidecar.",
            recoverable=False,
            recommended_action="Download a matching release archive and checksum sidecar again.",
        )
    return actual


def _validate_release_bundle(
    bundle_path: str,
    *,
    release_sha: str,
    release_cfg: Mapping[str, Any],
) -> None:
    max_bytes = int(release_cfg.get("max_bundle_bytes") or 128 * 1024 * 1024)
    max_members = int(release_cfg.get("max_members") or 512)
    if os.path.getsize(bundle_path) > max_bytes:
        raise RemoteTaskError(
            "REMOTE_RELEASE_BUNDLE_TOO_LARGE",
            "Release bundle exceeds the policy size limit.",
            recoverable=False,
            recommended_action="Publish a bounded deployment artifact from CI.",
        )
    try:
        with tarfile.open(bundle_path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > max_members:
                raise _release_bundle_denied("Release bundle exceeds the policy member limit.")
            seen = set()
            top_levels = set()
            root_member = None
            env_member = None
            env_name = None
            total_size = 0
            for member in members:
                name = _safe_archive_member_name(member.name)
                if name in seen or not (member.isfile() or member.isdir()):
                    raise _release_bundle_denied("Release bundle contains unsafe archive members.")
                seen.add(name)
                root = name.split("/", 1)[0]
                top_levels.add(root)
                if member.isdir() and name == root:
                    root_member = member
                total_size += int(member.size or 0)
                if total_size > max_bytes:
                    raise _release_bundle_denied("Release bundle expands beyond the policy size limit.")
                basename = name.rsplit("/", 1)[-1]
                if basename.startswith(".env") and basename not in _ALLOWED_RELEASE_ENV_FILES:
                    raise _release_bundle_denied("Release bundle contains a secret-bearing environment file.")
                if basename == ".env.release":
                    env_member = member
                    env_name = name
            if len(top_levels) != 1:
                raise _release_bundle_denied("Release bundle must contain exactly one top-level directory.")
            root = next(iter(top_levels))
            if root_member is None:
                raise _release_bundle_denied("Release bundle must declare its top-level directory.")
            if env_member is None or env_name != "{}/.env.release".format(root):
                raise _release_bundle_denied("Release bundle is missing .env.release.")
            handle = archive.extractfile(env_member)
            if handle is None:
                raise _release_bundle_denied("Release bundle .env.release is unreadable.")
            env_text = handle.read(64 * 1024 + 1).decode("utf-8")
            if len(env_text) > 64 * 1024:
                raise _release_bundle_denied("Release bundle .env.release is too large.")
    except (tarfile.TarError, OSError, UnicodeDecodeError) as exc:
        raise RemoteTaskError(
            "REMOTE_RELEASE_BUNDLE_INVALID",
            "Release bundle is not a safe gzip tar archive.",
            recoverable=False,
            recommended_action="Use the immutable release artifact emitted by CI.",
        ) from exc
    _validate_release_images(env_text, release_sha=release_sha, release_cfg=release_cfg)


def _safe_archive_member_name(raw_name: str) -> str:
    name = str(raw_name or "")
    while name.startswith("./"):
        name = name[2:]
    if not name or name.startswith("/") or "\\" in name:
        raise _release_bundle_denied("Release bundle contains unsafe archive paths.")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _release_bundle_denied("Release bundle contains unsafe archive paths.")
    return name


def _validate_release_images(
    env_text: str,
    *,
    release_sha: str,
    release_cfg: Mapping[str, Any],
) -> None:
    values = {}
    keys = tuple(str(key) for key in release_cfg["required_image_keys"])
    for line in env_text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            normalized_key = key.strip()
            if normalized_key in keys and normalized_key in values:
                raise _release_bundle_denied("Release bundle declares an image variable more than once.")
            values[normalized_key] = value.strip()
    repositories = release_cfg.get("allowed_image_repositories") or {}
    for key in keys:
        expected = "{}:sha-{}".format(repositories.get(key, ""), release_sha)
        if values.get(key) != expected:
            raise _release_bundle_denied("Release images are not pinned to the requested immutable SHA and repository.")


def _release_bundle_denied(message: str) -> RemoteTaskError:
    return RemoteTaskError(
        "REMOTE_RELEASE_BUNDLE_DENIED",
        message,
        recoverable=False,
        recommended_action="Use a CI-built release artifact that satisfies the alias policy.",
    )


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SshArchiveTransport:
    """Push a local source archive to a remote alias using internal SSH transport."""

    def __init__(self, *, ssh_args: Optional[Sequence[str]] = None, timeout_sec: int = 600) -> None:
        self.ssh_args = list(ssh_args or ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.timeout_sec = timeout_sec

    def push(self, target: RemoteTarget, plan: Mapping[str, Any]) -> Dict[str, Any]:
        source_dir = str(plan.get("source_dir") or "")
        if not source_dir or not os.path.isdir(source_dir):
            return _error("REMOTE_SOURCE_INVALID", "Local source directory is unavailable.")
        excludes = [str(item) for item in plan.get("exclude") or []]
        tar_cmd = ["tar", "--no-xattrs", "-czf", "-"]
        for item in excludes:
            tar_cmd.append("--exclude={}".format(item))
        tar_cmd.extend(["-C", source_dir, "."])

        remote_cmd = "mkdir -p {root} && tar -xzf - -C {root}".format(root=shlex.quote(target.path))
        ssh_cmd = ["ssh", *self.ssh_args, target.host, remote_cmd]
        archive_env = dict(os.environ)
        archive_env["COPYFILE_DISABLE"] = "1"
        try:
            tar_proc = subprocess.Popen(
                tar_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=archive_env,
            )
            ssh_proc = subprocess.Popen(
                ssh_cmd,
                stdin=tar_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if tar_proc.stdout is not None:
                tar_proc.stdout.close()
            ssh_stdout, ssh_stderr = ssh_proc.communicate(timeout=self.timeout_sec)
            tar_stderr = tar_proc.stderr.read() if tar_proc.stderr is not None else b""
            tar_returncode = tar_proc.wait(timeout=5)
        except FileNotFoundError:
            return _error("REMOTE_SOURCE_HANDOFF_CLIENT_MISSING", "Local broker archive transport is unavailable.")
        except subprocess.TimeoutExpired:
            return _error("REMOTE_SOURCE_HANDOFF_TIMEOUT", "Opaque source handoff timed out.")

        ok = tar_returncode == 0 and ssh_proc.returncode == 0
        return {
            "ok": ok,
            "operation": "source_archive_push",
            "mode": plan.get("mode"),
            "archive_transport": "internal",
            "returncode": ssh_proc.returncode,
            "archive_returncode": tar_returncode,
            "stdout": _coerce_output(ssh_stdout),
            "stderr": _coerce_output(ssh_stderr),
            "archive_stderr": _coerce_output(tar_stderr),
            "message": "Opaque source handoff completed." if ok else "Opaque source handoff failed.",
            "error_type": None if ok else "REMOTE_SOURCE_HANDOFF_FAILED",
        }

    def push_release_bundle(self, target: RemoteTarget, plan: Mapping[str, Any]) -> Dict[str, Any]:
        bundle_path = str(plan.get("bundle_path") or "")
        expected_sha256 = str(plan.get("bundle_sha256") or "")
        if not bundle_path or not os.path.isfile(bundle_path) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            return _error("REMOTE_RELEASE_BUNDLE_INVALID", "Verified release bundle is unavailable.")

        root = shlex.quote(target.path)
        expected = shlex.quote(expected_sha256)
        remote_cmd = (
            "set -eu; root={root}; mkdir -p \"$root\"; "
            "archive=$(mktemp \"$root/.apatch-release.XXXXXX.tar.gz\"); "
            "trap 'rm -f \"$archive\"' EXIT HUP INT TERM; "
            "cat > \"$archive\"; "
            "actual=$( (sha256sum \"$archive\" 2>/dev/null || shasum -a 256 \"$archive\") | awk '{{print $1}}'); "
            "[ \"$actual\" = {expected} ] || exit 74; "
            "tar --strip-components=1 -xzf \"$archive\" -C \"$root\""
        ).format(root=root, expected=expected)
        ssh_cmd = ["ssh", *self.ssh_args, target.host, remote_cmd]
        try:
            with open(bundle_path, "rb") as handle:
                ssh_proc = subprocess.Popen(
                    ssh_cmd,
                    stdin=handle,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                ssh_stdout, ssh_stderr = ssh_proc.communicate(timeout=self.timeout_sec)
        except FileNotFoundError:
            return _error("REMOTE_SOURCE_HANDOFF_CLIENT_MISSING", "Local broker archive transport is unavailable.")
        except subprocess.TimeoutExpired:
            return _error("REMOTE_SOURCE_HANDOFF_TIMEOUT", "Checksum-verified release handoff timed out.")

        ok = ssh_proc.returncode == 0
        return {
            "ok": ok,
            "operation": "verified_release_bundle_push",
            "mode": RELEASE_BUNDLE_MODE,
            "archive_transport": "internal",
            "returncode": ssh_proc.returncode,
            "stdout": _coerce_output(ssh_stdout),
            "stderr": _coerce_output(ssh_stderr),
            "message": "Checksum-verified release handoff completed." if ok else "Checksum-verified release handoff failed.",
            "error_type": None if ok else "REMOTE_RELEASE_BUNDLE_HANDOFF_FAILED",
        }



def _source_handoff_config(cfg: Mapping[str, Any], alias: str) -> Mapping[str, Any]:
    targets = cfg.get("targets") or {}
    entry = targets.get(alias) if isinstance(targets, Mapping) else None
    raw = (entry or {}).get("source_handoff") if isinstance(entry, Mapping) else None
    if raw is None:
        raw = cfg.get("source_handoff")
    if not isinstance(raw, Mapping) or not raw.get("enabled"):
        raise RemoteTaskError(
            "REMOTE_SOURCE_HANDOFF_DISABLED",
            "Remote source handoff is not enabled for this alias.",
            recoverable=True,
            recommended_action="Run apatch remote init with --source-handoff or update local remote policy.",
        )
    return raw


def _prepare_source_dir(source_dir: str) -> str:
    path = os.path.abspath(os.path.expanduser(source_dir or "."))
    if not os.path.isdir(path):
        raise RemoteTaskError(
            "REMOTE_SOURCE_INVALID",
            "Local source directory does not exist.",
            recoverable=True,
            recommended_action="Choose a local workspace directory allowed by policy.",
        )
    return path


def _validate_source_policy(source: str, source_cfg: Mapping[str, Any], policy_root: str) -> None:
    roots = source_cfg.get("local_roots")
    if not roots:
        roots = [os.path.abspath(os.path.expanduser(policy_root or "."))]
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, Iterable):
        _deny_source()
    normalized = os.path.realpath(source)
    for root in roots:
        pattern = os.path.realpath(os.path.abspath(os.path.expanduser(str(root))))
        if fnmatch.fnmatch(normalized, pattern) or _is_relative_to(normalized, pattern):
            return
    _deny_source()


def _deny_source() -> None:
    raise RemoteTaskError(
        "REMOTE_SOURCE_DENIED",
        "Local source directory is outside policy allowlist.",
        recoverable=True,
        recommended_action="Use a policy-approved source root.",
    )


def _validate_mode(mode: str, source_cfg: Mapping[str, Any]) -> None:
    allowed = source_cfg.get("allowed_modes") or ["workspace_overlay"]
    if isinstance(allowed, str):
        allowed = [allowed]
    if mode not in {str(item) for item in allowed}:
        raise RemoteTaskError(
            "REMOTE_SOURCE_MODE_DENIED",
            "Remote source handoff mode is outside policy allowlist.",
            recoverable=True,
            recommended_action="Choose a policy-approved handoff mode.",
        )


def _build_plan(target: RemoteTarget, source: str, source_cfg: Mapping[str, Any], mode: str) -> Dict[str, Any]:
    excludes = _build_excludes(source_cfg)
    return {
        "ok": True,
        "dry_run": True,
        "operation": "source_archive_push",
        "mode": mode,
        "archive_transport": "internal",
        "target": target.as_dict(),
        "source_dir": source,
        "exclude": [str(item) for item in excludes],
        "credential_strategy": "local_broker_only",
        "agent_visible_transport": False,
    }


def _build_excludes(source_cfg: Mapping[str, Any]) -> list:
    raw = source_cfg.get("exclude")
    if raw is None:
        configured = []
    elif isinstance(raw, str):
        configured = [raw]
    elif isinstance(raw, Iterable):
        configured = list(raw)
    else:
        configured = []
    return _unique_strings([*DEFAULT_SOURCE_EXCLUDES, *configured])


def _unique_strings(items: Iterable[Any]) -> list:
    out = []
    seen = set()
    for item in items:
        text = str(item)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _redact_plan(target: RemoteTarget, value: Any, source: str, checksum: Optional[str] = None) -> Any:
    if isinstance(value, str):
        out = value
        label = target.display or target.alias or "remote"
        for needle in (target.uri, target.path, target.host, source, checksum):
            if needle:
                out = out.replace(needle, "<local-source>" if needle == source else label)
        return out
    if isinstance(value, Mapping):
        out: Dict[Any, Any] = {}
        for key, item in value.items():
            if str(key) in {"source_dir", "bundle_path", "checksum_path"}:
                label = "release-bundle" if str(key) == "bundle_path" else "local-source"
                out["bundle" if str(key) == "bundle_path" else "source"] = {"redacted": True, "display": label}
            elif str(key) == "credential_strategy":
                out["handoff_boundary"] = "policy_broker"
            elif item is None:
                continue
            else:
                out[key] = _redact_plan(target, item, source)
        return out
    if isinstance(value, list):
        return [_redact_plan(target, item, source) for item in value]
    return value


def _is_relative_to(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _error(error_type: str, message: str, **extra: Any) -> Dict[str, Any]:
    result = {"ok": False, "error_type": error_type, "message": message, "recoverable": True}
    result.update(extra)
    return result


def _coerce_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
