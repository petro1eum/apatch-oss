"""Owner cancellation of one demonstrably uncommitted preparation transaction.

This operation grants no source authority and never repairs or rewrites a signed
object. An uncertain ledger or any approval record keeps the intake blocked.
"""
from __future__ import annotations

import base64
import os
import re

from apatch import sdd_contract_intake as intake
from apatch.runtime.atomic_io import exclusive_file_lock
from apatch.sdd_preparation_io import assert_can_freeze, freeze_lock
from apatch.sdd_integrity import SddContractError, _seal, _verify_seal, canonical_hash
from apatch.sdd_preparation_io import _immutable
from apatch.strict_existing_signer import ExistingSignerRefused

SCHEMA = "apatch.sdd.intake-cancellation.v1"


def _require(condition, message):
    if not condition:
        raise SddContractError(message)


def _validate(root, review):
    intake._review(root, review)
    original = intake._read(root, intake._folder(review) + "review.json")
    _verify_seal(original, "original intake owner review")
    _require(original == review, "original intake review differs")
    assert_can_freeze(root)
    for item in review["files"]:
        current = intake._capture(root, item["path"], optional=True)
        actual = intake._hash(current) if current is not None else None
        _require(actual == item["before_hash"], "intake output already exists or has changed")


def _incarnation(root, review):
    path = intake._path(root, intake.PENDING)
    if not path.exists():
        return None
    before = path.lstat()
    raw = intake._capture(root, intake.PENDING)
    pending = intake._json(raw, "pending intake")
    _verify_seal(pending, "pending intake")
    after = path.lstat()
    fields = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    _require(fields(before) == fields(after), "pending intake changed while reading")
    _require(pending.get("review_hash") == review["document_hash"], "foreign pending intake")
    _require(pending.get("stage") in {"prepared", "signing_approved"}, "intake already reached approval")
    return {"sha256": intake._hash(raw), "device": after.st_dev, "inode": after.st_ino,
            "size": after.st_size, "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns,
            "stage": pending["stage"]}


def _capture_ledger(root, relative):
    """Read a stable ledger object; immutable archive hardlinks are valid."""
    import stat
    MAX_BYTES = 8 * 1024 * 1024  # Ledger records are not preparation documents.
    path = intake._path(root, relative)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_BYTES,
                 "native ledger object role refused")
        data = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        named = path.lstat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                              s.st_mode, s.st_uid, s.st_gid, s.st_nlink)
        _require(len(data) == before.st_size and identity(before) == identity(after)
                 and identity(after) == identity(named), "native ledger object changed while reading")
        return data
    finally:
        os.close(fd)


def _scan(root, review, binding, public):
    """Preflight is read-only; final decision repeats this under the native lock."""
    from trustchain.v2.chain_store import verify_record_signature
    from trustchain.v2.verifier import TrustChainVerifier
    verifier = TrustChainVerifier(base64.b64encode(public).decode(), binding.agent_id, max_age_seconds=None)
    directory = intake._path(root, ".trustchain/objects")
    _require(directory.is_dir(), "native ledger object store is absent")
    cancellations = []
    for path in sorted(directory.glob("op_*.json")):
        relative = path.relative_to(root).as_posix()
        raw = intake._json(_capture_ledger(root, relative), "native ledger object")
        row = raw.get("value", raw)
        _require(isinstance(row, dict) and isinstance(row.get("tool"), str)
                 and isinstance(row.get("data"), dict), "ambiguous native ledger object")
        payload = row["data"]
        if payload.get("review_hash") != review["document_hash"]:
            continue
        _require(row["tool"] == intake.TOOL and row.get("key_id") == binding.agent_id
                 and verify_record_signature(row, verifier) is True, "invalid matching native intake record")
        _require(payload.get("stage") == "cancelled", "native approval or completion already exists")
        cancellations.append((relative[len(".trustchain/"):], row))
    for stage in ("approved", "completed"):
        _require(intake._capture(root, intake._folder(review) + stage + ".json", optional=True) is None,
                 "approval selector exists; cancellation is unavailable")
    return cancellations


def _base(root, review, incarnation):
    return {"schema": SCHEMA, "workspace": str(root), "review_hash": review["document_hash"],
            "authority_id": review["authority_id"], "actor_id": review["actor_id"],
            "spec_id": review["spec_id"], "rfp_id": review["rfp_id"],
            "active_contract_hash": review["active_contract_hash"], "input_hashes": review["input_hashes"],
            "pending_incarnation": incarnation,
            "proposed_files": {item["path"]: item["after_hash"] for item in review["files"]},
            **intake.PREPARATION}


def _folder(review, snapshot):
    _require(isinstance(snapshot, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot),
             "exact cancellation snapshot is required")
    return intake._folder(review) + "cancellations/" + snapshot[7:] + "/"


def _payload(root, review, proposed, owner, binding):
    return {**_base(root, review, proposed["pending_incarnation"]), "stage": "cancelled",
            "cancellation_hash": proposed["document_hash"], "owner": owner,
            "native_signer": {"agent_id": binding.agent_id, "public_key_sha256": binding.public_key_sha256},
            "result": {"ok": True, "status": "cancelled", "review_hash": review["document_hash"],
                       "cancellation_hash": proposed["document_hash"], **intake.PREPARATION}}


def _matching(rows, proposed, expected):
    matches = []
    for location, row in rows:
        payload = row["data"]
        if payload.get("cancellation_hash") != proposed["document_hash"]:
            continue
        _require(not payload.get("files") and all(payload.get(k) == v for k, v in expected.items()),
                 "native cancellation contents differ")
        matches.append((location, row))
    _require(len(matches) <= 1, "ambiguous duplicate cancellation signatures")
    return matches[0] if matches else None


def review_cancellation(root, intake_review):
    root = intake._root(root)
    review = intake_review
    _validate(root, review)
    incarnation = _incarnation(root, review)
    _require(incarnation is not None, "no exact pending intake to cancel")
    binding, public = intake._identity(root)
    _scan(root, review, binding, public)
    return _seal(_base(root, review, incarnation))


def cancel_uncommitted_intake(root, intake_review, *, owner, expected_snapshot):
    root = intake._root(root)
    review = intake_review
    _validate(root, review)
    _require(isinstance(owner, dict) and owner.get("certified") is True
             and owner.get("id") == review["authority_id"] and owner.get("id") != review["actor_id"],
             "current certified owner must approve cancellation")
    folder = _folder(review, expected_snapshot)
    binding, public = intake._identity(root)
    current = _incarnation(root, review)
    if current is None:
        proposed = intake._read(root, folder + "review.json")
        _verify_seal(proposed, "saved cancellation review")
        _require(proposed["document_hash"] == expected_snapshot
                 and proposed == _seal(_base(root, review, proposed["pending_incarnation"])),
                 "saved cancellation review differs")
    else:
        proposed = _seal(_base(root, review, current))
        _require(proposed["document_hash"] == expected_snapshot, "pending incarnation or cancellation review changed")
    expected = _payload(root, review, proposed, owner, binding)
    prior = _matching(_scan(root, review, binding, public), proposed, expected)
    if current is None:
        _require(prior is not None, "pending vanished without a native cancellation")
        selected = intake._read(root, folder + "cancelled.json")
        _verify_seal(selected, "cancellation receipt selector")
        location, row = prior
        _require(selected == _seal({"object_path": location, "signature": row["signature"],
                                   "payload_hash": canonical_hash(row["data"])}), "cancellation selector differs")
        return expected["result"]

    from apatch.path_leases import PathLeaseConflict, acquire_path_lease, release_path_leases, find_covering_lease
    from apatch.trustchain_helper import TrustChainHelper
    lease_owner = "authority-intake-cancel:" + expected_snapshot
    paths = [item["path"] for item in review["files"]] + [intake.PENDING]
    for path in paths:
        lease = find_covering_lease(str(root), path)
        _require(lease is None or lease.get("governed_session_id") == lease_owner,
                 "intake cancellation path lease conflict")
    with freeze_lock(root):
        _validate(root, review)
        _require(_incarnation(root, review) == current, "pending changed before cancellation")
        try:
            acquire_path_lease(str(root), paths,
                               tool=intake.TOOL, governed_session_id=lease_owner, max_seconds=300)
        except PathLeaseConflict as exc:
            raise SddContractError("intake cancellation path lease conflict") from exc
        try:
            _validate(root, review)
            _require(_incarnation(root, review) == current, "pending changed after path lease")
            with exclusive_file_lock(str(root / ".apatch/trustchain_ledger")):
                prior = _matching(_scan(root, review, binding, public), proposed, expected)
            _immutable(root, folder + "review.json", proposed)
            if prior is None:
                chain = TrustChainHelper(str(root), auto_init=False, existing_signer=binding)
                if not chain.commit_action(intake.TOOL, expected):
                    raise ExistingSignerRefused("EXISTING_SIGNER_RECEIPT_INVALID", reconciliation_required=True)
            # Native commit locks the same ledger itself. Reacquire and inspect
            # the actual store after append; a racing approval must keep pending.
            with exclusive_file_lock(str(root / ".apatch/trustchain_ledger")):
                found = _matching(_scan(root, review, binding, public), proposed, expected)
                _require(found is not None, "native cancellation receipt is absent")
                _validate(root, review)
                _require(_incarnation(root, review) == current, "pending changed during signing")
                location, row = found
                _immutable(root, folder + "cancelled.json", _seal({"object_path": location,
                           "signature": row["signature"], "payload_hash": canonical_hash(row["data"])}))
                pending = intake._path(root, intake.PENDING)
                pending.unlink()
                descriptor = os.open(pending.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return expected["result"]
        finally:
            release_path_leases(str(root), governed_session_id=lease_owner)
