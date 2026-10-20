"""Signed project data for one installed host hook; no enrollment.

The fixed hook command pins a separately reviewed enrolled authority document.
The mutable workspace registry is only a locator, never an authority. Existing
policy-lock signatures bind the project files; the embedded signing key alone
is never trusted. Readiness observations must come from trusted local adapters,
not from an HTTP request body or a configured-child probe.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
try:
    import fcntl
except ImportError:
    fcntl = None
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from apatch.mcp.workspace_registry import REGISTRY_VERSION, _entry_status, inspect_local_workspace

PROJECT_CONFIG = ".apatch/passport-hook.json"
ANCHOR_SCHEMA = "apatch.project-authority.v1"
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate JSON field")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def PublicRegistry(*args, **kwargs):
    # Native admission remains POSIX-only; pure readiness must import on Windows.
    from apatch.mcp.call_admission import PublicRegistry as implementation
    return implementation(*args, **kwargs)


def _path(value):
    path = Path(value)
    require(path.is_absolute() and not path.is_symlink() and
            all(not parent.is_symlink() for parent in path.parents), "unsafe project path")
    require(path.resolve(strict=True) == path, "noncanonical project path")
    return path


def _read_owned(value, *, private=False):
    path = _path(value)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        item = os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid == os.getuid() and
                not item.st_mode & (0o077 if private else 0o022) and item.st_size <= 1_048_576,
                "project data custody refused")
        raw = os.read(fd, 1_048_577)
    finally:
        os.close(fd)
    require(len(raw) <= 1_048_576, "project data size refused")
    return raw


def _read_private(value):
    return _read_owned(value, private=True)


def _registered_root(event, registry_path):
    data = decode(_read_owned(registry_path))
    rows = data.get("workspaces")
    require(data.get("version") == REGISTRY_VERSION and isinstance(rows, dict) and
            0 < len(rows) <= 128, "workspace registry refused")
    arguments = event.get("tool_input")
    require(isinstance(arguments, dict), "tool input refused")
    target = arguments.get("target_dir", ".")
    require(isinstance(target, str), "workspace target refused")
    if target.startswith("@"):
        alias = target[1:]
        entry = rows.get(alias)
        require(isinstance(entry, dict), "workspace alias is not registered")
        root = _path(entry.get("path", ""))
        status = _entry_status(alias, entry)
        require(status.get("ready") is True and status.get("path") == str(root),
                "registered project identity or contract changed")
        return root, status
    cwd = _path(event.get("cwd", ""))
    require(cwd.is_dir(), "project cwd is not a directory")
    matches = {}
    for alias, entry in rows.items():
        require(isinstance(entry, dict), "workspace entry refused")
        candidate = Path(entry.get("path", ""))
        if candidate.is_absolute() and (cwd == candidate or candidate in cwd.parents):
            root = _path(candidate)
            status = _entry_status(alias, entry)
            require(status.get("ready") is True, "registered project identity or contract changed")
            identity = (entry.get("root_fingerprint"), entry.get("contract_sha256"))
            if root in matches:
                require(matches[root][0] == identity, "conflicting synonymous project registrations")
            else:
                matches[root] = (identity, status)
    require(matches, "project is not registered")
    root = max(matches, key=lambda value: len(value.parts))
    status = matches[root][1]
    require(target in ("", ".", str(root)), "raw off-project target refused")
    require(status.get("ready") is True and status.get("path") == str(root),
            "registered project identity or contract changed")
    return root, status


def _trusted_authority(anchor_path, anchor_sha256):
    raw = _read_owned(anchor_path)
    require(hashlib.sha256(raw).hexdigest() == anchor_sha256, "project authority anchor changed")
    anchor = decode(raw)
    require(set(anchor) == {"schema", "registry_base", "root_pin", "agent_id", "public_key_b64"} and
            anchor.get("schema") == ANCHOR_SCHEMA and
            isinstance(anchor.get("registry_base"), str) and anchor["registry_base"].startswith("https://") and
            isinstance(anchor.get("agent_id"), str), "project authority anchor refused")
    public = base64.b64decode(anchor["public_key_b64"], validate=True)
    require(len(public) == 32, "project authority key refused")
    enrolled = PublicRegistry(anchor["registry_base"], anchor["root_pin"]).identity(anchor["agent_id"])
    require(enrolled["public_key"] == public, "project authority enrollment changed")
    return anchor, public


def resolve_project_admission(event, registry_path, anchor_path, anchor_sha256):
    """Select an existing registered project and verify its signed data only."""
    require(isinstance(event, dict) and isinstance(event.get("tool_name"), str) and
            event["tool_name"].startswith("mcp__apatch__"), "canonical apatch audience required")
    root, status = _registered_root(event, registry_path)
    anchor, public = _trusted_authority(anchor_path, anchor_sha256)
    return _verified_project_config(root, status, anchor, public), str(root)


def _verified_project_config(root, status, anchor, public):
    from apatch.policy_lock import compute_policy_manifest
    raw = _read_private(root / PROJECT_CONFIG)
    config = decode(raw)
    require(config.get("workspace_root") == str(root) and
            config.get("workspace_fingerprint") == status["root_fingerprint"] and
            config.get("contract_sha256") == status["contract"]["sha256"], "signed project scope refused")
    lock = decode(_read_private(root / ".apatch/policy.lock.json"))
    manifest = lock.get("manifest")
    require(isinstance(manifest, dict) and manifest.get("profile") == "project_admission.v1" and
            manifest == compute_policy_manifest(str(root), project_admission=True) and
            manifest.get("files", {}).get(PROJECT_CONFIG) == hashlib.sha256(raw).hexdigest() and
            ".apatch/mcp.json" in manifest.get("files", {}), "project configuration is not signed or has drifted")
    _verify_intended_lock(lock, manifest, anchor, public)
    return config


def _verify_intended_lock(lock, manifest, anchor, public):
    """Ownership of one signed intended manifest, independent of mutable files."""
    from apatch.policy_lock import _canonical
    require(lock.get("manifest") == manifest and lock.get("version") == 1 and lock.get("algorithm") == "ed25519" and
            lock.get("signer_key_id") == anchor["agent_id"] and
            base64.b64decode(lock.get("signer_public_key", ""), validate=True) == public,
            "project policy signer differs from the pinned authority")
    Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(lock["signature"]), _canonical(manifest))


class ProjectSetupError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _pinned_data(path, digest):
    require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest), "invalid operator data pin")
    raw = _read_private(path)
    require(hashlib.sha256(raw).hexdigest() == digest, "operator admission data changed")
    return decode(raw)


def _signer_capability(metadata, helper_sha256):
    """Inspect only a separately pinned, known broker helper; never its signer path."""
    if metadata.get("key_backend", "pem") == "pem":
        return
    if metadata.get("key_backend") != "command" or metadata.get("broker_adapter") != "apatch.trustchain-ledger-sign.v1":
        raise ProjectSetupError("PROJECT_ADMISSION_SIGNER_UNSUPPORTED")
    argv = shlex.split(metadata.get("sign_cmd", ""))
    if len(argv) != 2 or not UUID.fullmatch(argv[1]) or not re.fullmatch("[0-9a-f]{64}", helper_sha256 or ""):
        raise ProjectSetupError("PROJECT_ADMISSION_SIGNER_UNSUPPORTED")
    helper = _path(argv[0])
    item = helper.stat()
    require(helper.name == "TrustChainSecretsSigner" and stat.S_ISREG(item.st_mode) and
            item.st_uid in (0, os.getuid()) and not item.st_mode & 0o022 and
            item.st_mode & 0o111 and item.st_size <= 67_108_864, "known signer helper custody refused")
    with helper.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1_048_576), b""):
            digest.update(chunk)
        require(digest.hexdigest() == helper_sha256, "known signer helper changed")
    try:
        reply = subprocess.run([str(helper), "--capabilities"], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=5, check=False)
        value = decode(reply.stdout) if reply.returncode == 0 and len(reply.stdout) <= 8192 else {}
        require(set(value) == {"schema", "features", "side_effects"} and
                value["schema"] == "trustchain.signer-capabilities.v1" and
                isinstance(value["features"], list) and len(value["features"]) <= 32 and
                all(isinstance(feature, str) for feature in value["features"]) and
                "policy-project-admission-v1" in value["features"] and
                isinstance(value["side_effects"], dict) and
                set(value["side_effects"]) == {"sign", "keychain", "socket"} and
                all(effect is False for effect in value["side_effects"].values()),
                "known signer does not support this exact policy profile")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ProjectSetupError("PROJECT_ADMISSION_SIGNER_UNSUPPORTED") from exc


def _snapshot(path):
    if not os.path.lexists(path):
        return None
    raw = _read_owned(path, private=path.name != "mcp.json")
    return raw, stat.S_IMODE(path.stat().st_mode)


def _replace_data(path, expected, desired):
    require(_snapshot(path) == expected, "project setup compare-and-swap conflict")
    if desired is None:
        path.unlink()
        return
    raw, mode = desired
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        require(_snapshot(path) == expected, "project setup compare-and-swap conflict")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def install_project_admission(alias, *, registry_path, hook_config_path, hook_config_sha256,
                              admission_policy_path, admission_policy_sha256,
                              anchor_path, anchor_sha256, signer_helper_sha256=None):
    """Operator CLI only: install an already authorized project, not a new mandate.

    No host trust/configuration, enrollment, grant, subject or workspace is added.
    A policy signature is the commit record: staged data cannot pass the stable
    reader until signed. Directory flock serializes cooperating installers; CAS
    never overwrites a concurrent foreign edit. Unsupported old signers fail
    before staging. Successful setup still reports host readiness as unknown.
    """
    if fcntl is None or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "getuid")):
        return {"ok": False, "ready": False, "error_type": "PROJECT_ADMISSION_UNSUPPORTED_PLATFORM",
                "signing_attempted": False, "restored": True,
                "recommended_action": "use_supported_operator_setup_platform"}
    from apatch.mcp.call_admission import CONFIG_SCHEMA
    from apatch.path_leases import load_active_leases
    from apatch.policy_lock import POLICY_FILES, _canonical, compute_policy_manifest, sign_policy
    from apatch.trust_identity import load_local_identity, workspace_identity_scope

    before, staged, directory_fd, signing_started = {}, {}, None, False
    root = None
    try:
        _read_owned(registry_path)
        status = inspect_local_workspace(alias, enforce_pins=True, registry_path=str(registry_path))
        root = _path(status["path"])
        directory = _path(root / ".apatch")
        item = directory.stat()
        require(item.st_uid == os.getuid() and not item.st_mode & 0o022, "project directory custody refused")
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        hook = _pinned_data(hook_config_path, hook_config_sha256)
        policy = _pinned_data(admission_policy_path, admission_policy_sha256)
        roots = policy.get("workspace_roots")
        subjects = hook.get("subjects")
        require(policy.get("schema") == CONFIG_SCHEMA and policy.get("server") == "apatch" and
                isinstance(roots, list) and str(root) in roots and all(str(_path(r)) == r for r in roots) and
                isinstance(subjects, dict) and subjects and subjects == policy.get("subjects") and
                all(UUID.fullmatch(chat) and subject.get("agent_id") == "codex-chat-" + chat and
                    isinstance(subject.get("tenant_id"), str) and subject["tenant_id"]
                    for chat, subject in subjects.items()), "project is outside the pinned mandate or subject scope")
        gateway = hook.get("gateways", {}).get("apatch", {})
        require(hook.get("schema") == "apatch.passport-hook.config.v1" and
                hook.get("authorization_base") == policy.get("authorization_base") and
                isinstance(policy.get("authorization_base"), str) and policy["authorization_base"].startswith("https://") and
                str(_path(gateway.get("namespace_dir", ""))) == str(_path(policy.get("state_dir", ""))) and
                isinstance(hook.get("key_base"), str) and _path(hook["key_base"]).is_dir(),
                "existing admission gateway or holder custody differs")
        anchor, public = _trusted_authority(anchor_path, anchor_sha256)
        require(policy.get("registry_base") == anchor["registry_base"] and policy.get("root_pin") == anchor["root_pin"],
                "operator policy and enrolled authority differ")
        identity_path = root / ".apatch/agent-identity.json"
        identity_bytes = _read_private(identity_path)
        metadata = decode(identity_bytes)
        require(not metadata.get("pending_bootstrap") and metadata.get("agent_id") == anchor["agent_id"],
                "an already enrolled scoped workspace identity is required")
        require(not load_active_leases(str(root)), "project has an active writer lease")
        for relative in POLICY_FILES:
            path = root / relative
            if os.path.lexists(path):
                _read_owned(path)
        require((root / ".apatch/remote.json").is_file(), "existing remote policy is required by this signer profile")
        paths = [root / ".apatch/mcp.json", root / PROJECT_CONFIG, root / ".apatch/policy.lock.json"]
        before = {path: _snapshot(path) for path in paths}
        require(before[paths[0]] is not None, "existing canonical MCP configuration is required")
        canonical = decode(before[paths[0]][0])
        server = canonical.get("mcpServers", {}).get("apatch")
        require(isinstance(server, dict) and isinstance(server.get("command"), str) and server["command"] and
                isinstance(server.get("args"), list) and all(isinstance(a, str) for a in server["args"]) and
                server["args"].count("-m") == 1, "existing canonical Python module entrypoint is required")
        module_index = server["args"].index("-m") + 1
        require(module_index == len(server["args"]) - 1 and server["args"][module_index] in
                ("apatch.mcp.launcher", "apatch.mcp.passport_entrypoint"), "unsupported existing MCP entrypoint")
        server["args"][module_index] = "apatch.mcp.passport_entrypoint"
        environment = server.setdefault("env", {})
        require(isinstance(environment, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()) and
                environment.get("APATCH_MCP_BOUND", str(root)) == str(root), "existing MCP root binding differs")
        environment.update(APATCH_MCP_BOUND=str(root), APATCH_CALL_ADMISSION_CONFIG=str(_path(admission_policy_path)),
                           APATCH_CALL_ADMISSION_CONFIG_SHA256=admission_policy_sha256)
        hook.update(workspace_root=str(root), workspace_fingerprint=status["root_fingerprint"],
                    contract_sha256=status["contract"]["sha256"])
        wanted = {paths[0]: _canonical(canonical) + b"\n", paths[1]: _canonical(hook) + b"\n"}
        with workspace_identity_scope(str(root)):
            identity = load_local_identity(str(root))
            require(identity is not None and identity.agent_id == anchor["agent_id"] and
                    identity.key_provider.get_key_id() == anchor["agent_id"] and
                    identity.key_provider.get_public_key() == public, "existing scoped signing authority differs")
            unchanged = all(before[path] is not None and decode(before[path][0]) == decode(raw)
                            for path, raw in wanted.items())
            if unchanged:
                try:
                    _verified_project_config(root, status, anchor, public)
                    return {"ok": True, "changed": False, "signatures": 0, "ready": False,
                            "workspace": str(root), "status": "configured_signed_host_not_observed"}
                except (OSError, ValueError):
                    pass  # Existing unsigned/drifted data still requires one normal signature.
            _signer_capability(metadata, signer_helper_sha256)
            require(_read_private(identity_path) == identity_bytes and
                    inspect_local_workspace(alias, enforce_pins=True, registry_path=str(registry_path)) == status,
                    "project setup subject changed during preflight")
            _pinned_data(hook_config_path, hook_config_sha256)
            _pinned_data(admission_policy_path, admission_policy_sha256)
            _read_owned(anchor_path)
            require(hashlib.sha256(_read_owned(anchor_path)).hexdigest() == anchor_sha256, "authority anchor changed")
            manifest = compute_policy_manifest(str(root))
            manifest.update(profile="project_admission.v1")
            manifest["files"].update({str(path.relative_to(root)): hashlib.sha256(raw).hexdigest()
                                      for path, raw in wanted.items()})
            require(len(_canonical(manifest)) <= 16384, "project policy payload is too large")
            for path, raw in wanted.items():
                if before[path] is None or before[path][0] != raw:
                    staged[path] = (raw, before[path][1] if before[path] else 0o600)
                    _replace_data(path, before[path], staged[path])
            require(compute_policy_manifest(str(root), project_admission=True) == manifest,
                    "project policy inputs changed before signing")
            signing_started = True
            result = sign_policy(str(root), identity=identity, project_admission=True, _expected_manifest=manifest)
            require(result.get("ok") is True, "project policy signing did not complete")
            _verified_project_config(root, status, anchor, public)
            require(_read_private(identity_path) == identity_bytes and
                    inspect_local_workspace(alias, enforce_pins=True, registry_path=str(registry_path)) == status,
                    "workspace signing identity or contract changed")
            return {"ok": True, "changed": True, "signatures": 1, "ready": False,
                    "workspace": str(root), "status": "configured_signed_host_not_observed"}
    except Exception as exc:
        restored = not staged
        if staged:
            try:
                lock_path = root / ".apatch/policy.lock.json"
                current_lock = _snapshot(lock_path)
                if current_lock != before[lock_path]:
                    require(current_lock is not None, "signed policy lock disappeared")
                    _verify_intended_lock(decode(current_lock[0]), manifest, anchor, public)
                    staged[lock_path] = current_lock
                require(all(_snapshot(path) in (old, staged.get(path)) for path, old in before.items()),
                        "concurrent project edit prevents rollback")
                for path, owned in reversed(list(staged.items())):
                    _replace_data(path, owned, before[path])
                restored = True
            except Exception:
                restored = False
        return {"ok": False, "ready": False, "workspace": str(root) if root else None,
                "error_type": getattr(exc, "code", "PROJECT_ADMISSION_SETUP_REJECTED"),
                "restored": restored, "signing_attempted": signing_started,
                "recommended_action": "reconcile_signing_result" if signing_started else "review_existing_operator_admission"}
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def _observation(kind, value, workspace, config_sha256, now):
    if value is None:
        return {"status": "not_observed", "reason": "no_actual_" + kind + "_observation", "evidence": None}
    if not isinstance(value, dict):
        return {"status": "failed", "reason": "invalid_observation", "evidence": None}
    evidence = value.get("evidence")
    if value.get("workspace") != workspace or value.get("config_sha256") != config_sha256:
        return {"status": "stale", "reason": "observation_subject_changed", "evidence": evidence}
    observed, expires = _timestamp(value.get("observed_at")), _timestamp(value.get("expires_at"))
    if observed is None or expires is None or not observed <= now < expires or expires - observed > 300:
        return {"status": "stale", "reason": "observation_expired_or_unbounded", "evidence": evidence}
    expected_source = {"loaded": "host_mcp_tools_list", "authorized": "validated_call_admission",
                       "verified": "validated_native_lifecycle"}[kind]
    correct = value.get("source") == expected_source and UUID.fullmatch(str(value.get("thread_id", "")))
    if kind == "loaded":
        tools = value.get("tool_names")
        correct = correct and isinstance(tools, list) and "apatch_doctor" in tools and bool(value.get("runtime_id"))
    elif kind == "authorized":
        correct = correct and value.get("allowed") is True and value.get("tool") == "apatch_doctor"
    else:
        correct = correct and value.get("verification_passed") is True and value.get("attested") is True and value.get("ended") is True and bool(value.get("native_session_id"))
    return {"status": "satisfied" if correct else "failed",
            "reason": None if correct else "wrong_observation_source_or_result", "evidence": evidence}


def project_readiness(workspace, *, configured_runtime=None, host_observation=None,
                      authorization_observation=None, verification_observation=None, now=None):
    """Read/projection only. No host reload, permission, key access or signature.

    Observations are internal validated-adapter inputs, never client-supplied
    claims. With no actual host producer, loaded/authorized/verified stay unknown.
    Every observation carries its own bounded subject, time and evidence scope.
    """
    root = str(_path(Path(workspace).expanduser().absolute()))
    config_sha256 = None
    try:
        raw = _path(Path(root) / ".apatch/mcp.json").read_bytes()
        config = decode(raw)
        server = config.get("mcpServers", {}).get("apatch")
        valid = (isinstance(server, dict) and isinstance(server.get("command"), str) and bool(server["command"]) and
                 isinstance(server.get("args", []), list) and all(isinstance(arg, str) for arg in server.get("args", [])) and
                 isinstance(server.get("env", {}), dict))
        config_sha256 = hashlib.sha256(raw).hexdigest()
    except (OSError, TypeError, ValueError):
        valid = False
    states = {"configured": {"status": "satisfied" if valid else "failed",
                              "reason": None if valid else "canonical_configuration_missing_or_invalid",
                              "evidence": {"path": str(Path(root) / ".apatch/mcp.json"), "sha256": config_sha256}}}
    instant = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    values = {"loaded": host_observation, "authorized": authorization_observation, "verified": verification_observation}
    for kind, value in values.items():
        states[kind] = _observation(kind, value, root, config_sha256, instant)
    if host_observation and isinstance(host_observation, dict):
        for kind in ("authorized", "verified"):
            value = values[kind]
            if states[kind]["status"] == "satisfied" and (not isinstance(value, dict) or
                    value.get("thread_id") != host_observation.get("thread_id") or
                    value.get("runtime_id") != host_observation.get("runtime_id")):
                states[kind] = {"status": "stale", "reason": "host_or_runtime_changed", "evidence": value.get("evidence")}
    elif any(states[kind]["status"] == "satisfied" for kind in ("authorized", "verified")):
        for kind in ("authorized", "verified"):
            if states[kind]["status"] == "satisfied":
                states[kind] = {"status": "not_observed", "reason": "actual_host_not_observed", "evidence": values[kind].get("evidence")}
    next_actions = {"configured": "configure_project", "loaded": "observe_host_tools",
                    "authorized": "authorize_exact_operation", "verified": "complete_governed_verification"}
    agent_actions = {
        "configured": "Prepare and review this project's canonical settings; preserve other projects and host preferences.",
        "loaded": "Read tools/list from the current actual host for this project. If unavailable, report not_observable; a disposable child is not a substitute.",
        "authorized": "Through the current actual host and its normal admission hook, run only apatch_doctor(target_dir='.'). This is a read-only probe, not a mutation or a signing test.",
    }
    pending = next((kind for kind in ("configured", "loaded", "authorized") if states[kind]["status"] != "satisfied"), None)
    return {"schema": "apatch.project-readiness.v1", "workspace": root, "config_sha256": config_sha256,
            "states": states, "configured_runtime": configured_runtime,
            "configured_runtime_is_host_observation": False, "ready": pending is None,
            "operation_verified": states["verified"]["status"] == "satisfied",
            "next_action": next_actions.get(pending), "blocked_step": pending,
            "reason": states[pending]["reason"] if pending else None,
            "agent_next": agent_actions.get(pending),
            "scope": "exact_observed_project_host_and_operation"}
