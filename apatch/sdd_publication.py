"""Publish two already-frozen SDD documents; never accept implementation work.

This is a named owner control-plane transaction, not an implementation or an
authoring grant. It neither changes the judge nor broadens frozen write policy.
Each canonical CREATE is exclusive. A durable, staged transaction is completed
only after both exact byte strings and their pinned signatures are verified.
Ordinary errors roll back this transaction's own links; an interrupted process
can resume the same sealed review. There is no cross-directory POSIX rename.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat

from apatch.path_leases import acquire_path_lease, load_active_leases, release_path_leases
from apatch.runtime.atomic_io import exclusive_file_lock
from apatch.sdd_integrity import (SddContractError, _seal, _verify_seal,
                                  canonical_hash, canonical_json, load_profile_contract,
                                  validate_task_envelope)
from apatch.trust_identity import load_local_identity
from apatch.trustchain_helper import TrustChainHelper

TOOL = "apatch_sdd_document_publication"
REVIEW_SCHEMA = "apatch.sdd.document-publication-review.v1"
RECEIPT_SCHEMA = "apatch.sdd.document-publication-receipt.v1"
MAX_DOCUMENT_BYTES = 1_048_576
_SPEC = re.compile(r"SPEC-[A-Z0-9][A-Z0-9-]{1,95}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_WORKSPACE_PIN = ContextVar("sdd_document_publication_workspace_pin", default=None)


def _hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _root(value) -> Path:
    path = Path(value).absolute()
    if path.is_symlink() or not path.is_dir():
        raise SddContractError("publication workspace must be a real directory")
    return path


def _assert_workspace(root):
    pin = _WORKSPACE_PIN.get()
    if pin is None or pin["root"] != root:
        return
    for path, expected in pin["ancestry"]:
        try:
            actual = path.lstat()
        except OSError as exc:
            raise SddContractError("publication workspace ancestry changed during the transaction") from exc
        if (actual.st_dev, actual.st_ino, stat.S_IFMT(actual.st_mode)) != expected:
            raise SddContractError("publication workspace ancestry changed during the transaction")
    for parts, descriptor in pin["directories"].items():
        path = root.joinpath(*parts)
        try:
            actual = path.lstat()
        except OSError as exc:
            raise SddContractError("publication directory changed during the transaction") from exc
        held = os.fstat(descriptor)
        if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (held.st_dev, held.st_ino):
            raise SddContractError("publication directory changed during the transaction")


def _pinned_workspace(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        root = _root(root)
        ancestry = []
        for path in [root, *root.parents]:
            value = path.lstat()
            ancestry.append((path, (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))))
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        pin = {"root": root, "ancestry": ancestry, "directories": {(): descriptor}}
        token = _WORKSPACE_PIN.set(pin)
        try:
            _assert_workspace(root)
            result = function(root, *args, **kwargs)
            _assert_workspace(root)
            return result
        finally:
            _WORKSPACE_PIN.reset(token)
            for held in pin["directories"].values():
                os.close(held)
    return wrapped


@contextmanager
def _directory(root: Path, parts, *, create=False):
    """Pin each directory inode; never follow a symlink during actual I/O."""
    _assert_workspace(root)
    pin = _WORKSPACE_PIN.get()
    if pin is not None and pin["root"] == root:
        descriptor = os.dup(pin["directories"][()])
    else:
        pin = None
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        prefix = []
        for part in parts:
            if part in {"", ".", ".."} or "/" in part or "\\" in part:
                raise SddContractError("publication path must remain workspace-relative")
            if create:
                try:
                    os.mkdir(part, 0o700 if parts[0] == ".apatch" else 0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            prefix.append(part)
            if pin is not None:
                key = tuple(prefix)
                if key in pin["directories"]:
                    held, actual = os.fstat(pin["directories"][key]), os.fstat(child)
                    if (held.st_dev, held.st_ino) != (actual.st_dev, actual.st_ino):
                        raise SddContractError("publication directory changed during the transaction")
                else:
                    pin["directories"][key] = os.dup(child)
        _assert_workspace(root)
        yield descriptor
    finally:
        os.close(descriptor)


def _parts(relative: str):
    if not isinstance(relative, str) or "\\" in relative:
        raise SddContractError("invalid publication path")
    value = PurePosixPath(relative)
    if value.is_absolute() or not value.parts or any(part in {".", ".."} for part in value.parts):
        raise SddContractError("invalid publication path")
    return value.parts


def _read(root: Path, relative: str, *, limit=8_388_608) -> bytes:
    parts = _parts(relative)
    with _directory(root, parts[:-1]) as parent:
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SddContractError("publication input must be a regular file")
            content = handle.read(limit + 1)
            if len(content) > limit:
                raise SddContractError("publication input exceeds its explicit size limit")
            return content


def _json(root, relative):
    value = json.loads(_read(root, relative).decode("utf-8"))
    if not isinstance(value, dict):
        raise SddContractError("publication record must be one JSON object")
    return value


def _write_payload(descriptor, content):
    offset = 0
    while offset < len(content):
        count = os.write(descriptor, content[offset:])
        if count <= 0:
            raise OSError("publication write made no progress")
        offset += count
    os.fsync(descriptor)


def _immutable(root, relative, content: bytes):
    parts = _parts(relative)
    with _directory(root, parts[:-1], create=True) as parent:
        temporary = ".publication-" + secrets.token_hex(16) + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        owned = os.fstat(descriptor)
        try:
            _write_payload(descriptor, content)
            _assert_workspace(root)
            try:
                os.link(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            except FileExistsError:
                if _read(root, relative) != content:
                    raise SddContractError("immutable publication history conflicts")
            os.fsync(parent)
        finally:
            os.close(descriptor)
            try:
                present = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                if (present.st_dev, present.st_ino) == (owned.st_dev, owned.st_ino):
                    os.unlink(temporary, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                pass


def _save(root, relative, value):
    _immutable(root, relative, canonical_json(value).encode("utf-8"))


def _check_paths(root, paths):
    """Check missing tails too, before public helpers which use ordinary paths."""
    _assert_workspace(root)
    for relative in paths:
        current = root
        for part in _parts(relative):
            current = current / part
            if current.is_symlink():
                raise SddContractError("publication paths may not contain symlinks")


def _idle(root, *, own_transaction=None):
    _check_paths(root, [".apatch/session_state.json", ".apatch/lanes",
                        ".apatch/finalizations.json", ".apatch/write_lease.json",
                        ".apatch/write_lease.json.lock", ".apatch/write_leases.json",
                        ".apatch/write_leases.json.lock"])
    from apatch.runtime.finalization import load_finalization
    paths = [root / ".apatch/session_state.json", *sorted(root.glob(".apatch/lanes/*/session_state.json"))]
    for path in paths:
        relative = path.relative_to(root).as_posix()
        _check_paths(root, [relative])
        if not path.exists():
            continue
        state = _json(root, relative)
        sid = state.get("session_id") or state.get("governed_session_id")
        complete = sid and (load_finalization(str(root), sid) or {}).get("status") == "complete"
        # A phase-only reader/rejected-start projection is tool telemetry, not
        # a governed session. Preserve fail-closed handling for any actual
        # session context, including incomplete legacy projections without sid.
        has_context = any(state.get(field) for field in (
            "session_id", "governed_session_id", "intent", "checkpoint", "started_at",
            "session_token", "session_capability", "lifecycle", "artifacts"))
        if not state.get("ended_at") and not complete and has_context:
            raise SddContractError("finish all active governed sessions before document publication")
    if any(item.get("governed_session_id") != own_transaction for item in load_active_leases(str(root))):
        raise SddContractError("document publication requires an idle workspace without foreign path leases")
    for relative in (".apatch/sdd/judge-amendment-pending.json", ".apatch/sdd/successor-pending.json"):
        _check_paths(root, [relative])
        if (root / relative).exists():
            raise SddContractError("finish pending contract activation before document publication")


def _owner_public_read(path, pins):
    """Bounded public input only, with noFOLLOW and transaction inode pins."""
    path = Path(path)
    if not path.is_absolute():
        raise SddContractError("native owner public input must be absolute")
    path = path.absolute()
    for item in [path, *path.parents]:
        value = item.lstat()
        expected = (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))
        if stat.S_ISLNK(value.st_mode) or (item in pins and pins[item] != expected):
            raise SddContractError("native owner public input ancestry changed")
        pins[item] = expected
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as handle:
            held = os.fstat(handle.fileno())
            if not stat.S_ISREG(held.st_mode) or (held.st_dev, held.st_ino, stat.S_IFMT(held.st_mode)) != pins[path]:
                raise SddContractError("native owner public input changed while opening")
            raw = handle.read(8_388_609)
            if len(raw) > 8_388_608:
                raise SddContractError("native owner public input exceeds its bound")
    finally:
        os.close(parent)
    for item, expected in pins.items():
        value = item.lstat()
        if (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)) != expected:
            raise SddContractError("native owner public input ancestry changed while reading")
    return raw


def _native_samekey_cold_owner_binding(root, owner, identity, public, *, _history_read=None):
    """Verify the existing signed cold intake; never enrol or relabel a signer.

    A shared CA is not a delegation. Only the SAME native certificate/key,
    authenticated owner decision and exact current frozen request can connect
    the already configured machine name to the already configured SDK signer.
    """
    from copy import deepcopy
    from datetime import datetime, timezone
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from trustchain.v2.chain_store import reconstruct_signed_response, verify_record_signature
    from trustchain.v2.signer import _canonical_json_from_response
    from trustchain.v2.verifier import TrustChainVerifier
    from apatch.sdd_integrity import freeze_contract

    def refuse(message):
        raise SddContractError("native SAME-key owner binding: " + message)
    def require(condition, message):
        if not condition:
            refuse(message)
    def sealed(relative, label):
        value = json.loads(read(relative))
        _verify_seal(value, label)
        return value

    read = (lambda relative: _read(root, relative)) if _history_read is None else _history_read

    contract = sealed(".apatch/sdd_verification_contract.json", "current frozen native owner contract")
    require(contract.get("authority") == {"actor_id": owner, "role": "authority"}
            and freeze_contract(contract) == contract, "current owner contract differs")
    require(sealed(".apatch/sdd/frozen/" + contract["document_hash"][7:] + ".json",
                   "native owner frozen archive") == contract, "current frozen archive differs")
    specs = {str(item.get("test_id", "")).split("#")[0] for item in contract["obligations"]}
    require(len(specs) == 1 and _SPEC.fullmatch(next(iter(specs))) is not None,
            "current frozen contract has no single exact SPEC")
    spec_id = next(iter(specs))
    selected = sealed(".apatch/sdd/contract-intake-reviews/" + spec_id + ".json", "native owner intake selector")
    require(selected.get("schema") == "apatch.studio.contract-intake-selector.v1"
            and selected.get("workspace") == str(root) and selected.get("spec_id") == spec_id
            and selected.get("kind") in ("proposal", "approval"), "intake selector identity differs")
    review = selected.get("review")
    require(isinstance(review, dict), "intake selector has no review")
    _verify_seal(review, "native cold owner review")
    require(sealed(".apatch/sdd/contract-intake-reviews/" + spec_id + "/" + review["document_hash"][7:] + ".json",
                   "native owner intake selector archive") == selected, "intake selector archive differs")
    preparation = {"functional_acceptance": False, "implementation_allowed": False, "owner_freeze_required": True}
    require(review.get("schema") == "apatch.sdd.contract-intake-review.v1"
            and review.get("workspace") == str(root) and review.get("spec_id") == spec_id
            and review.get("rfp_id") == "RFP-" + spec_id[5:]
            and review.get("preparation_mode") == "cold" and review.get("active_contract_hash") is None
            and review.get("authority_id") == owner and review.get("actor_id") != owner
            and isinstance(review.get("actor_id"), str) and bool(review["actor_id"])
            and all(review.get(key) is value for key, value in preparation.items()),
            "cold intake is not this exact owner's preparation")
    binding, signer = review.get("owner_binding") or {}, review.get("authority_binding") or {}
    native_signer = {"agent_id": identity.agent_id, "public_key_sha256": _hash(public)[7:]}
    require(signer == native_signer and binding.get("id") == owner and binding.get("kind") == "machine"
            and binding.get("link") == "certificate_issued_by_existing_signer_ca"
            and binding.get("public_key_sha256") == native_signer["public_key_sha256"]
            and binding.get("certificate_sha256") == binding.get("native_certificate_sha256"),
            "not the current SAME-key native owner association")
    require(isinstance(review.get("input_hashes"), dict) and isinstance(review.get("files"), list)
            and 1 <= len(review["files"]) <= 64, "native preparation inputs are absent")
    outputs = {}
    for item in review["files"]:
        require(isinstance(item, dict) and isinstance(item.get("content"), str)
                and isinstance(item.get("after_hash"), str) and _HASH.fullmatch(item["after_hash"]),
                "native preparation output is invalid")
        require(item["path"] not in outputs and _hash(item["content"].encode()) == item["after_hash"]
                and _hash(read(item["path"])) == item["after_hash"], "signed prepared output bytes differ")
        outputs[item["path"]] = item["after_hash"]
    for relative, expected in review["input_hashes"].items():
        if relative == ".apatch/sdd_verification_contract.json" or relative in outputs:
            continue  # The verified native preparation/freeze changed these exact paths.
        try:
            actual = _hash(read(relative))
        except FileNotFoundError:
            actual = None
        require(actual == expected, "preserved native authority input differs")

    records, locator_hashes = [], {}
    folder = ".apatch/sdd/contract-intakes/" + review["document_hash"][7:] + "/"
    verifier = TrustChainVerifier(base64.b64encode(public).decode(), identity.agent_id, max_age_seconds=None)
    scope_keys = ("preparation_mode", "authority_binding", "authority_stores", "input_bundle",
                  "bundle_hashes", "owner_identity_root", "owner_binding")
    for stage in ("approved", "completed"):
        locator = sealed(folder + stage + ".json", "native cold owner receipt locator")
        relative = locator.get("object_path")
        require(isinstance(relative, str) and re.fullmatch(r"objects/op_[0-9]+\.json", relative),
                "native receipt object locator differs")
        raw = json.loads(read(".trustchain/" + relative))
        record = raw.get("value", raw)
        require(isinstance(record, dict), "native receipt is not an object")
        payload = record.get("data") or {}
        expected = {"schema": review["schema"], "review_hash": review["document_hash"], "stage": stage,
                    "workspace": str(root), "spec_id": spec_id, "rfp_id": review["rfp_id"],
                    "authority_id": owner, "actor_id": review["actor_id"], "active_contract_hash": None,
                    "input_hashes": review["input_hashes"], "files": outputs, **preparation,
                    **{key: review[key] for key in scope_keys if key in review},
                    "native_signer": native_signer}
        require(isinstance(payload, dict) and all(payload.get(key) == value for key, value in expected.items())
                and record.get("tool") == "apatch_sdd_contract_intake" and record.get("key_id") == identity.agent_id
                and record.get("algorithm") == "ed25519" and record.get("signature") == locator.get("signature")
                and canonical_hash(payload) == locator.get("payload_hash")
                and verify_record_signature(record, verifier) is True, "native SDK signed owner receipt differs")
        response = reconstruct_signed_response(record)
        require(response is not None, "native SDK receipt cannot be reconstructed")
        signature = base64.b64decode(response.signature, validate=True)
        for include in (True, False):
            try:
                Ed25519PublicKey.from_public_bytes(public).verify(signature,
                    _canonical_json_from_response(response, include_signature_id=include).encode())
                break
            except InvalidSignature:
                pass
        else:
            refuse("independent Ed25519 owner receipt verification failed")
        records.append(record)
        locator_hashes[stage] = locator["document_hash"]
    approved, completed = records
    require((approved["data"].get("owner") or {}).get("id") == owner
            and approved["data"]["owner"].get("kind") == "machine"
            and approved["data"]["owner"].get("certified") is True
            and completed.get("parent_signature") == approved["signature"]
            and completed.get("response_timestamp", "") >= approved.get("response_timestamp", ""),
            "authenticated native owner decision or receipt parent differs")
    require(completed["data"].get("result") == {"ok": True, "review_hash": review["document_hash"],
                                               "files": outputs, **preparation},
            "native completed preparation result differs")

    pin = _WORKSPACE_PIN.get()
    pins = pin.setdefault("owner_public_paths", {}) if pin is not None and pin["root"] == root else {}
    state = review.get("owner_identity_root")
    require(isinstance(state, str), "signed owner configuration is absent")
    owner_raw = _owner_public_read(Path(state) / "machine-identity.json", pins)
    owner_identity = json.loads(owner_raw)
    require(isinstance(owner_identity, dict) and owner_identity.get("schema") == "apatch.studio.machine-identity.v1"
            and owner_identity.get("machine_name") == owner and owner_identity.get("certified") is True
            and _hash(owner_raw) == binding.get("identity_sha256"), "configured existing owner bytes differ")
    certificate_path = owner_identity.get("certificate_path")
    require(isinstance(certificate_path, str) and identity.cert_path is not None
            and Path(certificate_path).is_absolute() and Path(identity.cert_path).is_absolute(),
            "existing public owner certificate is unavailable")
    owner_certificate = _owner_public_read(Path(certificate_path), pins)
    native_certificate = _owner_public_read(Path(identity.cert_path), pins)
    root_ca = _owner_public_read(Path(identity.cert_path).parent / "root-ca.pem", pins)
    intermediate = _owner_public_read(Path(identity.cert_path).parent / "ca.pem", pins)
    require(_hash(owner_certificate) == _hash(native_certificate) == binding.get("certificate_sha256")
            and _hash(root_ca) == binding.get("root_ca_sha256")
            and _hash(intermediate) == binding.get("intermediate_sha256"), "pinned public certificate chain differs")
    leaf, ca, anchor = [x509.load_pem_x509_certificate(value) for value in (owner_certificate, intermediate, root_ca)]
    leaf.verify_directly_issued_by(ca)
    ca.verify_directly_issued_by(anchor)
    anchor.verify_directly_issued_by(anchor)
    now = datetime.now(timezone.utc)
    require(all(value.not_valid_before_utc <= now <= value.not_valid_after_utc for value in (leaf, ca, anchor))
            and not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and anchor.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and leaf.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) == public,
            "current SAME-key public PKIX binding differs")

    package_path = ".apatch/sdd/prepared/" + spec_id + ".json"
    package = json.loads(read(package_path))
    require(package.get("schema") == "apatch.studio.sdd-preparation.v1" and package.get("spec_id") == spec_id
            and type(package.get("source_mutation_count")) is int and package["source_mutation_count"] == 0
            and package_path in outputs, "approved native prepared request is absent")
    request = deepcopy(package.get("contract_request") or {})
    for item in request.get("obligations") or []:
        item["approver"] = owner
    request.update(authority={"actor_id": owner, "role": "authority"}, source_mutation_count=0,
                   frozen_at=contract.get("frozen_at"))
    require(freeze_contract(request) == contract, "current frozen contract differs from the exact approved request")
    require(outputs.get("docs/specs/" + spec_id + ".md") == contract["spec_hash"]
            and outputs.get("docs/" + review["rfp_id"] + ".md") == contract["rfp_hash"],
            "current SPEC/RFP is not the signed native preparation")
    _assert_workspace(root)
    return canonical_hash({"review_hash": review["document_hash"], "receipts": locator_hashes,
                           "public_inputs": {str(path): value for path, value in pins.items()}})


def _native_samekey_owner_binding(root, owner, identity, public, *, _verified_cold_inputs=None):
    """Current owner association through exact completed native continuations.

    This public-only verifier neither creates authority nor alters frozen
    criteria. A warm frame must have authenticated native intake AND activation
    receipts, preserve its predecessor and end at the existing SAME-key cold
    association. No caller policy, issuer replacement or signing provider is used.
    """
    from copy import deepcopy
    import math
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from trustchain.v2.chain_store import reconstruct_signed_response, verify_record_signature
    from trustchain.v2.signer import _canonical_json_from_response
    from trustchain.v2.verifier import TrustChainVerifier
    from apatch.sdd_integrity import freeze_contract, amend_contract

    def require(ok, message):
        if not ok:
            raise SddContractError("native SAME-key warm owner binding: " + message)
    active = _json(root, ".apatch/sdd_verification_contract.json")
    if not active.get("native_intake_supersession"):
        proof = _native_samekey_cold_owner_binding(root, owner, identity, public)
        if _verified_cold_inputs is not None:
            spec_id = next(iter({str(x.get("test_id", "")).split("#")[0] for x in active["obligations"]}))
            value = _json(root, ".apatch/sdd/contract-intake-reviews/" + spec_id + ".json")
            package_path = ".apatch/sdd/prepared/" + spec_id + ".json"
            package = json.loads(next(x["content"] for x in value["review"]["files"] if x["path"] == package_path))
            _verified_cold_inputs.update(frame=active, selector=value, package=package)
        return proof
    preparation = {"functional_acceptance": False, "implementation_allowed": False, "owner_freeze_required": True}
    pending = (".apatch/sdd/contract-intake-pending.json", ".apatch/sdd/intake-supersession-pending.json",
               ".apatch/agent-identity-bootstrap-pending.json", ".apatch/sdd/successor-pending.json")
    _check_paths(root, pending)
    require(not any(os.path.lexists(root / path) for path in pending), "native activation is incomplete")
    captures = {}
    def read(relative):
        raw = _read(root, relative)
        path = root / relative
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "public workspace input role differs")
        stamp = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                 info.st_uid, info.st_gid, info.st_nlink)
        captures.setdefault(relative, (stamp, raw))
        require(captures[relative] == (stamp, raw), "public workspace input changed")
        return raw
    def obj(relative, label):
        value = json.loads(read(relative))
        require(isinstance(value, dict), "public native document is not an object")
        _verify_seal(value, label)
        return value
    def archive(frame):
        require(freeze_contract(frame) == frame and frame["authority"] == {"actor_id": owner, "role": "authority"},
                "current or predecessor frame has another owner")
        require(obj(".apatch/sdd/frozen/" + frame["document_hash"][7:] + ".json", "native frozen parent") == frame,
                "frozen parent archive differs")
    public_key = Ed25519PublicKey.from_public_bytes(public)
    native_signer = {"agent_id": identity.agent_id, "public_key_sha256": _hash(public)[7:]}
    verifier = TrustChainVerifier(base64.b64encode(public).decode(), identity.agent_id, max_age_seconds=None)
    seen_objects = set()
    def sdk(folder, stage, tool, expected):
        locator = obj(folder + stage + ".json", "native warm receipt locator")
        name = locator.get("object_path")
        require(isinstance(name, str) and re.fullmatch(r"objects/op_[0-9]+\.json", name)
                and name not in seen_objects, "native receipt object role or identity differs")
        seen_objects.add(name)
        wrapped = json.loads(read(".trustchain/" + name)); record = wrapped.get("value", wrapped)
        payload = record.get("data") or {}
        require(record.get("tool") == tool and record.get("key_id") == identity.agent_id
                and record.get("algorithm") == "ed25519" and record.get("signature") == locator.get("signature")
                and canonical_hash(payload) == locator.get("payload_hash")
                and all(payload.get(k) == v for k, v in expected.items())
                and verify_record_signature(record, verifier) is True, "native SDK purpose, signer or body differs")
        response = reconstruct_signed_response(record)
        require(response is not None, "native SDK canonical response is absent")
        signature = base64.b64decode(response.signature, validate=True)
        for include in (True, False):
            try:
                public_key.verify(signature, _canonical_json_from_response(response, include_signature_id=include).encode())
                break
            except InvalidSignature:
                pass
        else:
            require(False, "independent native Ed25519 signature differs")
        stamp = record.get("response_timestamp")
        require(type(stamp) in (int, float) and math.isfinite(stamp) and stamp > 0
                and record.get("parent_signatures") is None, "native SDK chronology or parent cardinality differs")
        return record
    def selector(frame, current=False):
        specs = {str(x.get("test_id", "")).split("#")[0] for x in frame["obligations"]}
        require(len(specs) == 1 and _SPEC.fullmatch(next(iter(specs))), "native frame has no single exact SPEC")
        spec_id = next(iter(specs))
        reference = frame.get("native_intake_supersession")
        if reference:
            digest = reference["intake_snapshot"]
            path = ".apatch/sdd/contract-intake-reviews/" + spec_id + "/" + digest[7:] + ".json"
        else:
            # The terminal original cold selector is identified only through
            # its exact approved package/frame, never a caller-provided alias.
            matches = []
            directory = root / (".apatch/sdd/contract-intake-reviews/" + spec_id)
            _check_paths(root, [directory.relative_to(root).as_posix()])
            paths = list(directory.glob("*.json"))
            require(len(paths) <= 256, "cold parent history exceeds its bound")
            for path in paths:
                value = obj(path.relative_to(root).as_posix(), "native cold parent selector")
                review = value.get("review") or {}
                if review.get("preparation_mode") == "cold" and review.get("active_contract_hash") is None:
                    package_path = ".apatch/sdd/prepared/" + spec_id + ".json"
                    rows = [x for x in review.get("files", []) if x.get("path") == package_path]
                    if len(rows) == 1:
                        request = deepcopy(json.loads(rows[0]["content"])["contract_request"])
                        for x in request["obligations"]: x["approver"] = owner
                        request.update(authority={"actor_id": owner, "role": "authority"}, source_mutation_count=0,
                                       frozen_at=frame["frozen_at"])
                        if freeze_contract(request) == frame: matches.append(value)
            require(len(matches) == 1, "terminal cold parent is not uniquely authenticated")
            value = matches[0]
            path = ".apatch/sdd/contract-intake-reviews/" + spec_id + "/" + value["review"]["document_hash"][7:] + ".json"
        value = obj(path, "native owner selector archive")
        require(value.get("schema") == "apatch.studio.contract-intake-selector.v1"
                and value.get("workspace") == str(root) and value.get("spec_id") == spec_id
                and value.get("kind") in ("proposal", "approval"), "native selector identity differs")
        if current:
            require(obj(".apatch/sdd/contract-intake-reviews/" + spec_id + ".json", "current native selector") == value,
                    "current native selector differs from its immutable archive")
        review = value["review"]; _verify_seal(review, "native intake review")
        require(not reference or review["document_hash"] == reference["intake_snapshot"],
                "native frame intake snapshot differs from its sealed review")
        package_path = ".apatch/sdd/prepared/" + spec_id + ".json"
        rows = review.get("files")
        require(isinstance(rows, list) and 1 <= len(rows) <= 64, "bounded original native preparation is absent")
        outputs = {}
        for row in rows:
            relative = row.get("path"); _parts(relative)
            require(relative not in outputs and isinstance(row.get("content"), str)
                    and _hash(row["content"].encode()) == row.get("after_hash"), "original native output pin differs")
            outputs[relative] = row["after_hash"]
        metadata = {"docs/specs/" + spec_id + ".md", "docs/RFP-" + spec_id[5:] + ".md", package_path}
        require(metadata.issubset(outputs) and all(p in metadata or p.startswith("tests/") for p in outputs),
                "native owner history cannot contain Source or private outputs")
        content = {row["path"]: row["content"].encode() for row in rows}
        package = json.loads(content[package_path])
        require(package.get("schema") == "apatch.studio.sdd-preparation.v1" and package.get("spec_id") == spec_id
                and type(package.get("source_mutation_count")) is int and package["source_mutation_count"] == 0,
                "native prepared request is not metadata only")
        if current:
            require(all(_hash(read(path)) == pin for path, pin in outputs.items()), "current native prepared bytes differ")
        else:
            require(all(path in metadata or _hash(read(path)) == pin for path, pin in outputs.items()),
                    "historical owner proof cannot replace physical frozen judges")
        return spec_id, value, review, package, outputs, content, metadata

    archive(active)
    require(obj(".apatch/sdd_verification_contract.json", "current owner frame") == active, "current owner frame changed")
    chain, visited = [], set()
    frame = active
    for depth in range(9):
        require(frame["document_hash"] not in visited, "native owner history contains a cycle")
        visited.add(frame["document_hash"])
        data = selector(frame, current=depth == 0)
        reference = frame.get("native_intake_supersession")
        if not reference:
            terminal = (frame, data)
            break
        require(depth < 8 and set(reference) == {"snapshot", "intake_snapshot"}
                and all(_HASH.fullmatch(str(x)) for x in reference.values()), "native owner continuation bound differs")
        parent = obj(".apatch/sdd/frozen/" + frame["supersedes_hash"][7:] + ".json", "native predecessor frame")
        archive(parent)
        spec_id, selected, review, package, outputs, content, metadata = data
        require(review.get("schema") == "apatch.sdd.contract-intake-review.v1" and "preparation_mode" not in review
                and review.get("workspace") == str(root) and review.get("spec_id") == spec_id
                and review.get("rfp_id") == "RFP-" + spec_id[5:] and review.get("authority_id") == owner
                and isinstance(review.get("actor_id"), str) and review["actor_id"] != owner
                and review.get("active_contract_hash") == parent["document_hash"]
                and all(review.get(k) is v for k, v in preparation.items()), "warm review has another owner or parent")
        require(frame["authority"] == parent["authority"] and frame["baseline_hash"] == parent["baseline_hash"]
                and frame["obligations"][:-1] == parent["obligations"]
                and len(frame["obligations"]) == len(parent["obligations"]) + 1
                and frame["obligations"][-1].get("material") is True
                and frame["obligations"][-1]["acceptance_id"] not in {x["acceptance_id"] for x in parent["obligations"]},
                "native continuation weakened its exact original owner frame")
        expected = {"schema": review["schema"], "review_hash": review["document_hash"], "workspace": str(root),
                    "spec_id": spec_id, "rfp_id": review["rfp_id"], "authority_id": owner,
                    "actor_id": review["actor_id"], "active_contract_hash": parent["document_hash"],
                    "input_hashes": review["input_hashes"], "files": outputs, **preparation, "native_signer": native_signer}
        folder = ".apatch/sdd/contract-intakes/" + review["document_hash"][7:] + "/"
        approved = sdk(folder, "approved", "apatch_sdd_contract_intake", {**expected, "stage": "approved"})
        completed = sdk(folder, "completed", "apatch_sdd_contract_intake", {**expected, "stage": "completed",
            "result": {"ok": True, "review_hash": review["document_hash"], "files": outputs, **preparation}})
        decision = approved["data"].get("owner") or {}
        require(decision.get("id") == owner and decision.get("kind") == "machine"
                and decision.get("certified") is True and decision.get("is_person", False) is False
                and set(decision).issubset({"id", "kind", "certified", "schema", "is_person"})
                and decision.get("schema", "apatch.studio.lock-party.v1") == "apatch.studio.lock-party.v1"
                and completed.get("parent_signature") == approved["signature"]
                and approved["response_timestamp"] < completed["response_timestamp"], "warm native owner decision or parent differs")
        folder = ".apatch/sdd/intake-supersessions/" + reference["snapshot"][7:] + "/"
        activate = sdk(folder, "owner_approved", "apatch_sdd_contract_intake_supersession", {
            "schema": "apatch.studio.native-intake-supersession.v1", "stage": "owner_approved", "snapshot": reference["snapshot"],
            "intake_snapshot": review["document_hash"], "workspace": str(root), "spec_id": spec_id,
            "authority_id": owner, "predecessor_contract_hash": parent["document_hash"],
            "successor_contract_hash": frame["document_hash"], "files": outputs,
            "functional_acceptance": False, "activation_point": "active_profile_cas"})
        body = activate["data"]; requirement = body.get("requirement_id")
        require(isinstance(requirement, str) and re.fullmatch(r"R[0-9]+", requirement), "native selected requirement differs")
        finish = sdk(folder, "completed", "apatch_sdd_contract_intake_supersession", {**body, "stage": "completed"})
        require(activate.get("parent_signature") == completed["signature"] and finish.get("parent_signature") == activate["signature"]
                and completed["response_timestamp"] < activate["response_timestamp"] < finish["response_timestamp"],
                "native activation SDK parent or chronology differs")
        proposed = deepcopy(package["contract_request"])
        require(proposed.get("supersedes_hash") == parent["document_hash"] and proposed["obligations"][:-1] == parent["obligations"],
                "native approved request lost its predecessor")
        proposed["obligations"][-1]["approver"] = owner
        proposed.update(authority={"actor_id": owner, "role": "authority"}, source_mutation_count=0,
                        frozen_at=frame["frozen_at"], native_intake_supersession={"snapshot": reference["snapshot"],
                            "intake_snapshot": reference["intake_snapshot"]})
        require(freeze_contract(proposed) == frame, "current frozen whole frame is not the native approved request")
        previous = obj(".apatch/sdd/frozen-envelopes/" + body["predecessor_envelope_hash"][7:] + ".json", "native prior envelope")
        envelope = obj(".apatch/sdd/frozen-envelopes/" + body["successor_envelope_hash"][7:] + ".json", "native next envelope")
        require(validate_task_envelope(previous) == previous and validate_task_envelope(envelope) == envelope
                and previous["document_hash"] == body["predecessor_envelope_hash"]
                and envelope["document_hash"] == body["successor_envelope_hash"]
                and previous["contract_hash"] == parent["document_hash"] and envelope["contract_hash"] == frame["document_hash"]
                and previous["requirement"] == envelope["requirement"] == spec_id + "#" + requirement,
                "native continuation envelopes differ")
        new = frame["obligations"][-1]
        judge_paths = {x["path"] for o in frame["obligations"] for x in o.get("judge_assets", [])}
        for o in frame["obligations"]:
            for asset in o.get("judge_assets", []): require(_hash(read(asset["path"])) == asset["sha256"], "physical frozen judge changed")
        new_paths = {x["path"] for x in new.get("judge_assets", []) if outputs.get(x["path"]) == x["sha256"]}
        require(envelope["allowed_writes"] == previous["allowed_writes"]
                and set(envelope["allowed_reads"]).issubset(set(previous["allowed_reads"]) | new_paths)
                and set(previous["forbidden_paths"]).issubset(envelope["forbidden_paths"])
                and envelope["containment"] == previous["containment"] and envelope["rollback_owner"] == previous["rollback_owner"]
                and all(set(envelope[x]).issubset(previous[x]) for x in ("allowed_symbols", "tools", "network", "remote", "services"))
                and all(type(v) is int and v <= previous["budgets"][k] for k, v in envelope["budgets"].items())
                and (new.get("falsification") or {}).get("target_path") in previous["allowed_writes"], "native selected scope widened")
        compiled = validate_task_envelope({**package["task_envelopes"][requirement], "contract_hash": frame["document_hash"],
            "forbidden_paths": sorted(set(package["task_envelopes"][requirement]["forbidden_paths"]) | judge_paths | {".apatch/**", ".trustchain/**", ".git/**"})})
        require(compiled == envelope and envelope["checks"] == [new["acceptance_id"]] and envelope["commands"] == [new["command"]],
                "native selected envelope does not compile its exact approved gate")
        amendment = obj(".apatch/sdd/amendments/" + body["amendment_hash"][7:] + ".json", "native signed amendment")
        require(amendment == amend_contract(parent, reason=review["reason"], authority=frame["authority"],
                changed_acceptance_ids=[new["acceptance_id"]], affected_requirements=[requirement], all_requirements=list(package["task_envelopes"])),
                "native owner amendment differs")
        if depth == 0:
            require(obj(".apatch/sdd/envelopes/" + spec_id + "/" + requirement + ".json", "current canonical scope") == envelope,
                    "current selected envelope changed")
        chain.append((frame, parent, data, body, previous, envelope, proposed, compiled))
        frame = parent
    else:
        require(False, "native owner continuation chain exceeds its bound")

    cold, data = terminal
    spec_id, selected, review, package, outputs, content, metadata = data
    # Only original exact metadata is overlaid. Real Source, tests, all foreign
    # authority paths and receipt objects always come from the physical root.
    historical = {".apatch/sdd_verification_contract.json": canonical_json(cold).encode(),
                  ".apatch/sdd/contract-intake-reviews/" + spec_id + ".json": canonical_json(selected).encode(),
                  **{path: content[path] for path in metadata}}
    external_pins, external_captures = {}, {}
    def external_capture(path):
        path = Path(path); raw = _owner_public_read(path, external_pins); info = path.lstat()
        stamp = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                 info.st_uid, info.st_gid, info.st_nlink)
        external_captures.setdefault(path, (stamp, raw))
        require(external_captures[path] == (stamp, raw), "existing public owner input changed")
        return raw
    owner_identity = json.loads(external_capture(Path(review["owner_identity_root"]) / "machine-identity.json"))
    for path in (owner_identity["certificate_path"], identity.cert_path,
                 Path(identity.cert_path).parent / "root-ca.pem", Path(identity.cert_path).parent / "ca.pem"):
        external_capture(path)
    cold_proof = _native_samekey_cold_owner_binding(root, owner, identity, public,
        _history_read=lambda relative: historical.get(relative) if relative in historical else read(relative))
    cold_ordered = deepcopy(package["contract_request"])
    for x in cold_ordered["obligations"]: x["approver"] = owner
    cold_ordered.update(authority={"actor_id": owner, "role": "authority"}, source_mutation_count=0, frozen_at=cold["frozen_at"])
    ordered = {cold["document_hash"]: freeze_contract(cold_ordered)}
    envelope_order = {}
    for child, parent, data, body, previous, envelope, proposed, compiled in reversed(chain):
        spec_id, _, review, package, outputs, _, _ = data
        ref = previous["requirement"].split("#")[1]
        if parent["document_hash"] == cold["document_hash"]:
            envelope_order[previous["document_hash"]] = validate_task_envelope({**terminal[1][3]["task_envelopes"][ref], "contract_hash": cold["document_hash"]})
        require(previous["document_hash"] in envelope_order
                and envelope_order[previous["document_hash"]] == previous, "original native envelope ordering or branch differs")
        original_parent = None
        require(_HASH.fullmatch(str(review["input_hashes"].get(".apatch/sdd/envelopes/" + spec_id + "/" + ref + ".json"))),
                "native original selected canonical envelope raw pin is absent")
        for relative, expected in review["input_hashes"].items():
            if relative == ".apatch/sdd_verification_contract.json":
                archive_raw = read(".apatch/sdd/frozen/" + parent["document_hash"][7:] + ".json")
                native_raw = (json.dumps(ordered[parent["document_hash"]], ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
                studio_raw = canonical_json(parent).encode()
                require(expected in {_hash(archive_raw), _hash(native_raw), _hash(studio_raw)}, "original native parent raw profile pin differs")
                original_parent = json.loads(next(raw for raw in (archive_raw, native_raw, studio_raw) if _hash(raw) == expected))
            elif relative == ".apatch/sdd/envelopes/" + spec_id + "/" + ref + ".json":
                archive_raw = read(".apatch/sdd/frozen-envelopes/" + previous["document_hash"][7:] + ".json")
                native_raw = (json.dumps(envelope_order[previous["document_hash"]], ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
                require(expected in {_hash(archive_raw), _hash(native_raw), _hash(canonical_json(previous).encode())},
                        "original selected native envelope raw pin differs")
            elif relative in outputs:
                require(relative in metadata, "historical continuation cannot bypass nonmetadata authority input")
            else:
                try: actual = _hash(read(relative))
                except FileNotFoundError: actual = None
                require(actual == expected, "physical original authority input changed")
        require(original_parent is not None, "native predecessor raw profile binding is absent")
        # The native activator preserves dictionaries from its captured current
        # profile, not the differently ordered copies inside the prepared JSON.
        # Recover that exact ordering from the SDK-bound predecessor raw bytes.
        native_request = deepcopy(proposed)
        native_request["obligations"] = [*deepcopy(original_parent["obligations"]), deepcopy(proposed["obligations"][-1])]
        ordered[child["document_hash"]] = freeze_contract(native_request)
        require(ordered[child["document_hash"]] == child, "native ordered successor differs from its sealed whole frame")
        envelope_order[envelope["document_hash"]] = compiled
    for relative in list(captures): read(relative)
    for path in list(external_captures): external_capture(path)
    require(not any(os.path.lexists(root / path) for path in pending), "native activation changed during verification")
    _assert_workspace(root)
    proof = canonical_hash({"cold_owner_binding": cold_proof, "current_contract": active["document_hash"],
                           "native_continuations": [x[0]["document_hash"] for x in chain],
                           "public_input_pins": {path: _hash(value[1]) for path, value in captures.items()}})
    if _verified_cold_inputs is not None:
        _verified_cold_inputs.update(frame=cold, selector=terminal[1][1], package=terminal[1][3])
    return proof


def _native_verified_cold_parent(root, owner, identity, public):
    """Authenticated public history inputs only; never a current execution grant."""
    result = {}
    proof = _native_samekey_owner_binding(root, owner, identity, public, _verified_cold_inputs=result)
    return {"proof": proof, **result, "historical_authority_grant": False}


def _identity(root, owner):
    identity = load_local_identity(str(root))
    if identity is None:
        raise SddContractError("the exact frozen owner's pinned signing identity is required")
    public = identity.key_provider.get_public_key()
    if not isinstance(public, bytes) or len(public) != 32:
        raise SddContractError("publication requires a pinned Ed25519 public key")
    if identity.agent_id != owner:
        _native_samekey_owner_binding(root, owner, identity, public)
    return identity, _hash(public)


def _context(root, spec_id):
    if not isinstance(spec_id, str) or not _SPEC.fullmatch(spec_id):
        raise SddContractError("invalid publication SPEC identity")
    package_path = ".apatch/sdd/prepared/" + spec_id + ".json"
    _check_paths(root, [".apatch/sdd_verification_contract.json", package_path,
                        ".apatch/sdd/frozen", ".apatch/sdd/envelopes/" + spec_id])
    contract = load_profile_contract(root)
    if contract is None:
        raise SddContractError("an active implementation-ready frozen contract is required")
    frozen_path = ".apatch/sdd/frozen/" + contract["document_hash"][7:] + ".json"
    if _json(root, frozen_path) != contract:
        raise SddContractError("active contract differs from its immutable frozen archive")
    package_bytes = _read(root, package_path)
    package = json.loads(package_bytes.decode("utf-8"))
    request = package.get("contract_request", {})
    if (package.get("schema") != "apatch.studio.sdd-preparation.v1" or
            package.get("spec_id") != spec_id or
            any(request.get(field) != contract.get(field) for field in ("rfp_hash", "spec_hash"))):
        raise SddContractError("canonical prepared SPEC does not bind the active document hashes")
    if not isinstance(package.get("task_envelopes"), dict) or not package["task_envelopes"]:
        raise SddContractError("canonical preparation has no requirement identity map")
    requirement_ids = set(package["task_envelopes"])
    if any(not re.fullmatch(r"R[0-9]+", key) for key in requirement_ids):
        raise SddContractError("invalid prepared requirement identity")
    assets = {}
    for obligation in contract["obligations"]:
        if obligation.get("command_hash") != canonical_hash(obligation.get("command")):
            raise SddContractError("frozen judge command hash differs")
        for asset in obligation.get("judge_assets", []):
            path = asset["path"]
            actual = _hash(_read(root, path))
            if actual != asset["sha256"] or (path in assets and assets[path] != actual):
                raise SddContractError("frozen judge asset drift")
            assets[path] = actual
    if not assets:
        raise SddContractError("frozen contract has no pinned judge assets")
    envelopes = {}
    for path in sorted(root.glob(".apatch/sdd/envelopes/" + spec_id + "/*.json")):
        relative = path.relative_to(root).as_posix()
        raw = _json(root, relative)
        if raw.get("contract_hash") != contract["document_hash"]:
            continue
        checked = validate_task_envelope(raw)
        if checked != raw or raw.get("requirement") != spec_id + "#" + path.stem or path.stem not in requirement_ids:
            raise SddContractError("active task envelope differs from the canonical prepared identity")
        envelopes[relative] = _hash(_read(root, relative))
    if not envelopes:
        raise SddContractError("selected SPEC has no envelope of the active frozen contract")
    owner = contract["authority"]["actor_id"]
    _identity_value, key_hash = _identity(root, owner)
    owner_binding = {}
    if _identity_value.agent_id != owner:
        owner_binding["native_owner_binding_hash"] = _native_samekey_owner_binding(
            root, owner, _identity_value, _identity_value.key_provider.get_public_key())
    return {**owner_binding, "contract_hash": contract["document_hash"], "authority_id": owner,
            "owner_key_hash": key_hash, "spec_hash": contract["spec_hash"],
            "rfp_hash": contract["rfp_hash"], "prepared_hash": _hash(package_bytes),
            "judge_assets": assets, "envelopes": envelopes}


def _documents(context, spec_id, spec_markdown, rfp_markdown):
    rfp_id = "RFP-" + spec_id[5:]
    documents = []
    for document_id, relative, text, expected in (
            (rfp_id, "docs/" + rfp_id + ".md", rfp_markdown, context["rfp_hash"]),
            (spec_id, "docs/specs/" + spec_id + ".md", spec_markdown, context["spec_hash"])):
        if not isinstance(text, str):
            raise SddContractError("publication documents must be exact UTF-8 Markdown")
        data = text.encode("utf-8")
        if not 0 < len(data) <= MAX_DOCUMENT_BYTES or _hash(data) != expected:
            raise SddContractError("publication bytes differ from the active frozen document hash")
        if not re.match(r"\A# " + re.escape(document_id) + r"(?:\s|$)", text):
            raise SddContractError("hash-bound document header differs from the canonical prepared identity")
        documents.append({"id": document_id, "path": relative, "sha256": expected, "content": text})
    if not re.search(r"(?m)^> \*\*RFP:\*\* " + re.escape(rfp_id) + r"\s*$", spec_markdown):
        raise SddContractError("exact SPEC does not reference the paired RFP")
    return documents


def _before(root, documents):
    result = {}
    for document in documents:
        relative = document["path"]
        _check_paths(root, [relative])
        try:
            actual = _read(root, relative, limit=MAX_DOCUMENT_BYTES)
        except FileNotFoundError:
            result[relative] = None
        else:
            if actual != document["content"].encode("utf-8"):
                raise SddContractError("existing canonical document conflicts; overwriting is forbidden")
            result[relative] = document["sha256"]
    return result


@_pinned_workspace
def prepare_document_publication(root, *, spec_id, spec_markdown, rfp_markdown):
    """Read-only exact owner review; no path or caller-role input is accepted."""
    root = _root(root)
    _idle(root)
    context = _context(root, spec_id)
    documents = _documents(context, spec_id, spec_markdown, rfp_markdown)
    return _seal({"schema": REVIEW_SCHEMA, "spec_id": spec_id, "context": context,
                  "documents": documents, "before_hashes": _before(root, documents),
                  "functional_acceptance": False})


def _verified_record(root, reference, identity, expected):
    """O(1) actual Ed25519 verification, not the helper's self-verification flag."""
    from trustchain.v2.chain_store import reconstruct_signed_response
    from trustchain.v2.signer import _canonical_json_from_response
    if not isinstance(reference, dict) or not reference.get("persisted"):
        raise SddContractError("publication signature was not durably persisted")
    relative = reference.get("object_path")
    if not isinstance(relative, str) or not re.fullmatch(r"objects/op_[0-9]+\.json", relative):
        raise SddContractError("publication requires a verifiable persisted object reference")
    raw = _json(root, ".trustchain/" + relative)
    record = raw.get("value", raw)
    response = reconstruct_signed_response(record)
    payload = (record.get("data") or {})
    expected_tool = {"written": "apatch", "completed": "apatch_attest"}.get(expected.get("stage"), TOOL)
    if (response is None or record.get("tool") != expected_tool or
            record.get("key_id") != identity.agent_id or record.get("algorithm") != "ed25519" or
            any(payload.get(key) != value for key, value in expected.items()) or
            record.get("signature") != reference.get("signature")):
        raise SddContractError("publication receipt differs from its exact owner and payload")
    signature = base64.b64decode(response.signature, validate=True)
    if not any(identity.key_provider.verify(
            _canonical_json_from_response(response, include_signature_id=include_id).encode("utf-8"), signature)
            for include_id in (True, False)):
        raise SddContractError("publication signature does not verify against the pinned owner key")
    return reference


def _sign(root, helper, identity, payload):
    tool = {"written": "apatch", "completed": "apatch_attest"}.get(payload.get("stage"), TOOL)
    if not helper.has_trustchain():
        raise SddContractError("owner publication could not produce a signed receipt")
    if not helper.commit_action(tool, payload):
        evidence = helper.last_commit_evidence
        if evidence.get("pinned_signature_valid") is False or evidence.get("key_id_matches") is False:
            raise SddContractError("publication signature does not verify against the pinned owner key")
        raise SddContractError("owner publication could not produce a signed receipt")
    return _verified_record(root, helper.last_commit_evidence, identity, payload)


def _payload(review, *, stage, transaction_id, created_paths):
    # An explicit document-only anchor prevents a completed but not yet ended
    # executor session from donating its SPEC artifacts to this owner receipt.
    anchor = {"kind": "document-publication", "id": review["document_hash"][7:]}
    paths = [item["path"] for item in review["documents"]]
    payload = {"schema": RECEIPT_SCHEMA, "review_hash": review["document_hash"],
            "control_plane_transaction_id": transaction_id,
            "governed_session_id": transaction_id,
            "intent": "Publish the exact approved document pair, not implementation acceptance",
            "artifacts": [anchor],
            "artifact_files": {"document-publication:" + anchor["id"]: paths},
            "stage": stage, "authority_id": review["context"]["authority_id"],
            "contract_hash": review["context"]["contract_hash"],
            "owner_key_hash": review["context"]["owner_key_hash"],
            "document_hashes": {item["path"]: item["sha256"] for item in review["documents"]},
            "created_paths": created_paths, "functional_acceptance": False}
    if stage == "written":
        payload["action"] = "document_publication"
        payload["files"] = {item["path"]: {"sha256": item["sha256"][7:]} for item in review["documents"]}
    elif stage == "completed":
        payload["action"] = "attest"
        payload["attestation_scope"] = "document_bytes_only"
    return payload


def _link(root, source, destination):
    src, dst = _parts(source), _parts(destination)
    with _directory(root, src[:-1]) as src_parent, _directory(root, dst[:-1], create=True) as dst_parent:
        os.link(src[-1], dst[-1], src_dir_fd=src_parent, dst_dir_fd=dst_parent, follow_symlinks=False)
        os.fsync(dst_parent)


def _same_inode(root, source, destination):
    src, dst = _parts(source), _parts(destination)
    with _directory(root, src[:-1]) as src_parent, _directory(root, dst[:-1]) as dst_parent:
        left = os.stat(src[-1], dir_fd=src_parent, follow_symlinks=False)
        right = os.stat(dst[-1], dir_fd=dst_parent, follow_symlinks=False)
        return stat.S_ISREG(right.st_mode) and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _rollback_links(root, review, folder):
    for number, document in reversed(list(enumerate(review["documents"]))):
        if review["before_hashes"][document["path"]] is not None:
            continue
        try:
            if (_same_inode(root, folder + "/staged-" + str(number), document["path"]) and
                    _read(root, document["path"]) == document["content"].encode("utf-8")):
                parts = _parts(document["path"])
                with _directory(root, parts[:-1]) as parent:
                    os.unlink(parts[-1], dir_fd=parent)
                    os.fsync(parent)
        except FileNotFoundError:
            pass


def _notarize(root, review, transaction_id, mutation, completed):
    # Both standard ledger rows are pinned-verified. A canonical file mutation
    # followed by document-only attestation survives ordinary index rebuilds.
    from apatch.enforcement import load_notarized_index, record_notarized_files, record_session_attested
    _check_paths(root, [".apatch/notarized_index.json", ".apatch/notarized_index.json.lock"])
    files = {item["path"]: {"sha256": item["sha256"][7:]} for item in review["documents"]}
    index = load_notarized_index(str(root))
    if all((index.get("committed_files", {}).get(path) or {}).get("sha256") == meta["sha256"]
           and (index["committed_files"][path]).get("object_path") == mutation["object_path"]
           and (index["committed_files"][path]).get("signature") == mutation["signature"]
           and (index["committed_files"][path]).get("governed_session_id") == transaction_id
           and (index["committed_files"][path]).get("attestation_object_path") == completed["object_path"]
           and (index["committed_files"][path]).get("attestation_signature") == completed["signature"]
           and (index["committed_files"][path]).get("attested") is True
           for path, meta in files.items()):
        return
    record_notarized_files(str(root), files, signature=mutation["signature"],
                          object_path=mutation["object_path"],
                          governed_session_id=transaction_id)
    if record_session_attested(str(root), transaction_id, signature=completed["signature"],
                              object_path=completed["object_path"]) != 2:
        raise SddContractError("both exact publication documents must be notarized")


def _rollback_notarization(root, paths, transaction_id, before):
    """Undo only this transaction's cache entries after failed pinned admission."""
    from apatch.enforcement import load_notarized_index, notarized_index_path, save_notarized_index
    _assert_workspace(root)
    with exclusive_file_lock(notarized_index_path(str(root))):
        index = load_notarized_index(str(root))
        changed = False
        for field in ("files", "committed_files"):
            entries = index.setdefault(field, {})
            for path in paths:
                if (entries.get(path) or {}).get("governed_session_id") != transaction_id:
                    continue
                original = before.get(field, {}).get(path)
                if original is None:
                    entries.pop(path, None)
                else:
                    entries[path] = original
                changed = True
        if changed:
            save_notarized_index(str(root), index)


def _completed(root, review, receipt):
    """A publication is a historical fact, not today's active-contract pointer."""
    _verify_seal(receipt, "document publication receipt")
    _verify_seal(review, "document publication review")
    if (receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "completed" or
            receipt.get("review") != review or receipt.get("functional_acceptance") is not False or
            review.get("schema") != REVIEW_SCHEMA or review.get("functional_acceptance") is not False):
        raise SddContractError("immutable publication receipt changed")
    context = review["context"]
    digest = context.get("contract_hash")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise SddContractError("invalid archived publication contract identity")
    contract = _json(root, ".apatch/sdd/frozen/" + digest[7:] + ".json")
    _verify_seal(contract, "archived publication contract")
    if (contract.get("document_hash") != digest or
            contract.get("schema") != "apatch.sdd.verification-contract.v1" or
            contract.get("status") != "frozen" or contract.get("implementation_allowed") is not True or
            contract.get("authority", {}).get("actor_id") != context.get("authority_id") or
            any(contract.get(field) != context.get(field) for field in ("rfp_hash", "spec_hash"))):
        raise SddContractError("historical publication no longer binds its archived frozen contract")
    identity, key_hash = _identity(root, context["authority_id"])
    if key_hash != context["owner_key_hash"]:
        raise SddContractError("historical publication owner key differs from the pinned key")
    checked = _documents(context, review["spec_id"],
                         review["documents"][1]["content"], review["documents"][0]["content"])
    if checked != review["documents"] or set(review["before_hashes"]) != {item["path"] for item in checked}:
        raise SddContractError("historical publication document pair changed")
    created = [item["path"] for item in checked if review["before_hashes"][item["path"]] is None]
    if receipt.get("created_paths") != created:
        raise SddContractError("historical publication CREATE scope changed")
    transaction_id = "document-publication:" + review["document_hash"][7:]
    for stage, field in (("approved", "approval"), ("written", "mutation"), ("completed", "completion")):
        _verified_record(root, receipt[field], identity,
                         _payload(review, stage=stage, transaction_id=transaction_id, created_paths=created))
    if _before(root, checked) != {item["path"]: item["sha256"] for item in checked}:
        raise SddContractError("completed publication lost one of its exact documents")
    return receipt


@_pinned_workspace
def publish_documents(root, review, *, authority_id, expected_snapshot, confirmed):
    """Approve/CREATE the exact pair; retries never issue new functional evidence."""
    root = _root(root)
    _verify_seal(review, "document publication review")
    if (review.get("schema") != REVIEW_SCHEMA or review.get("functional_acceptance") is not False or
            confirmed is not True or expected_snapshot != review["document_hash"] or
            authority_id != review.get("context", {}).get("authority_id")):
        raise SddContractError("explicit frozen-owner approval of the exact publication review is required")
    digest = review["document_hash"][7:]
    transaction_id = "document-publication:" + digest
    folder = ".apatch/sdd/document-publications/" + digest
    paths = [item["path"] for item in review["documents"]]
    try:
        completed_receipt = _json(root, folder + "/receipt.json")
    except FileNotFoundError:
        completed_receipt = None
    if completed_receipt is not None:
        # Exact retry is read-only, including after another SPEC becomes active
        # or R0 adds an envelope. It never reassigns an index entry subsequently
        # notarized by genuine executor work.
        return _completed(root, review, completed_receipt)
    lock_path = ".apatch/sdd/successor-activation"
    _check_paths(root, [lock_path, lock_path + ".lock", folder, ".trustchain",
                        ".trustchain/objects", ".trustchain/HEAD", ".trustchain/.ledger.lock",
                        ".apatch/notarized_index.json", ".apatch/notarized_index.json.lock"])
    with _directory(root, _parts(lock_path)[:-1], create=True):
        pass
    with exclusive_file_lock(str(root / lock_path)):
        _idle(root, own_transaction=transaction_id)
        identity, public_hash = _identity(root, authority_id)
        if public_hash != review["context"]["owner_key_hash"]:
            raise SddContractError("pinned owner key changed after publication review")
        if _context(root, review["spec_id"]) != review["context"]:
            raise SddContractError("frozen publication inputs changed after review")
        checked = _documents(review["context"], review["spec_id"],
                             review["documents"][1]["content"], review["documents"][0]["content"])
        if checked != review["documents"] or set(review["before_hashes"]) != set(paths):
            raise SddContractError("publication review does not contain the exact derived document pair")
        if any(value not in {None, item["sha256"]} for item in checked
               for value in [review["before_hashes"][item["path"]]]):
            raise SddContractError("invalid publication before-state")
        created = [path for path in paths if review["before_hashes"][path] is None]
        approval_payload = _payload(review, stage="approved", transaction_id=transaction_id, created_paths=created)
        mutation_payload = _payload(review, stage="written", transaction_id=transaction_id, created_paths=created)
        completion_payload = _payload(review, stage="completed", transaction_id=transaction_id, created_paths=created)
        try:
            receipt = _json(root, folder + "/receipt.json")
        except FileNotFoundError:
            receipt = None
        if receipt is not None:
            return _completed(root, review, receipt)
        pending = (root / folder / "approval.json").exists()
        if pending:
            if ((root / folder / "review.json").exists() and
                    _json(root, folder + "/review.json") != review):
                raise SddContractError("pending publication belongs to another exact review")
            approval = _verified_record(root, _json(root, folder + "/approval.json"), identity, approval_payload)
        else:
            if _before(root, checked) != review["before_hashes"]:
                raise SddContractError("canonical document state changed after publication review")
            helper = TrustChainHelper(str(root), auto_init=False)
            approval = _sign(root, helper, identity, approval_payload)
        lease = acquire_path_lease(str(root), paths + [folder], tool=TOOL,
                                   governed_session_id=transaction_id, max_seconds=300)
        from apatch.enforcement import load_notarized_index
        before_index = load_notarized_index(str(root))
        completed = False
        try:
            _save(root, folder + "/approval.json", approval)
            _save(root, folder + "/review.json", review)
            for number, document in enumerate(checked):
                if review["before_hashes"][document["path"]] is None:
                    _immutable(root, folder + "/staged-" + str(number), document["content"].encode("utf-8"))
            if _context(root, review["spec_id"]) != review["context"]:
                raise SddContractError("frozen publication inputs changed while signing approval")
            _idle(root, own_transaction=transaction_id)
            for number, document in enumerate(checked):
                relative = document["path"]
                source = folder + "/staged-" + str(number)
                try:
                    present = _read(root, relative)
                except FileNotFoundError:
                    if review["before_hashes"][relative] is not None:
                        raise SddContractError("an existing document disappeared during publication")
                    _link(root, source, relative)
                else:
                    if present != document["content"].encode("utf-8"):
                        raise SddContractError("canonical document conflict during publication")
                    if review["before_hashes"][relative] is None and not _same_inode(root, source, relative):
                        raise SddContractError("a foreign CREATE cannot be adopted as this publication")
            if (any(_read(root, item["path"]) != item["content"].encode("utf-8") for item in checked) or
                    _context(root, review["spec_id"]) != review["context"]):
                raise SddContractError("publication did not verify both exact frozen documents")
            helper = TrustChainHelper(str(root), auto_init=False)
            try:
                mutation = _json(root, folder + "/mutation.json")
            except FileNotFoundError:
                mutation = _sign(root, helper, identity, mutation_payload)
                _save(root, folder + "/mutation.json", mutation)
            _verified_record(root, mutation, identity, mutation_payload)
            try:
                completion = _json(root, folder + "/completion.json")
            except FileNotFoundError:
                completion = _sign(root, helper, identity, completion_payload)
                _save(root, folder + "/completion.json", completion)
            _verified_record(root, completion, identity, completion_payload)
            # From this point, the signed exact-byte completion is immutable.
            # Notarization/Studio pointer failures are repaired by exact retry,
            # not by claiming the already-completed CREATE did not happen.
            completed = True
            receipt = _seal({"schema": RECEIPT_SCHEMA, "status": "completed", "review": review,
                             "approval": approval, "mutation": mutation, "completion": completion,
                             "created_paths": created, "functional_acceptance": False})
            _save(root, folder + "/receipt.json", receipt)
            _notarize(root, review, transaction_id, mutation, completion)
            return receipt
        except BaseException:
            if not completed:
                _rollback_links(root, review, folder)
                _rollback_notarization(root, paths, transaction_id, before_index)
            raise
        finally:
            # A renamed/swapped workspace is a containment failure. Never use
            # the stale pathname to release a lease in a replacement workspace;
            # retain the original lease/journal for normal owner recovery.
            try:
                _assert_workspace(root)
            except (OSError, SddContractError):
                pass
            else:
                release_path_leases(str(root), lease_id=lease["lease_id"], governed_session_id=transaction_id)


@_pinned_workspace
def review_document_publication(root, review):
    """Read-only native projection; never infer completion from a latest pointer."""
    root = _root(root)
    _verify_seal(review, "document publication review")
    folder = ".apatch/sdd/document-publications/" + review["document_hash"][7:]
    try:
        receipt = _json(root, folder + "/receipt.json")
    except FileNotFoundError:
        if (review.get("schema") != REVIEW_SCHEMA or review.get("functional_acceptance") is not False or
                _context(root, review["spec_id"]) != review["context"]):
            raise SddContractError("publication review no longer binds the active frozen inputs")
        checked = _documents(review["context"], review["spec_id"],
                             review["documents"][1]["content"], review["documents"][0]["content"])
        if checked != review["documents"]:
            raise SddContractError("publication document pair changed")
        return {"status": "awaiting_approval", "review": review,
                "snapshot": review["document_hash"], "functional_acceptance": False}
    _completed(root, review, receipt)
    return {"status": "completed", "review": review, "snapshot": review["document_hash"],
            "receipt": receipt, "functional_acceptance": False}

