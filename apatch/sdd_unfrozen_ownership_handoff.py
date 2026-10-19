"""Ownership-only amendment of an unfrozen predecessor, never acceptance.

The implementation stays untouched. A raw predecessor-document pin is not a
contract: only the destination's real frozen owner authorizes the amendment.
The frozen-to-frozen implementation and its tests remain separate and intact.
"""
# Only the frozen destination owner authorizes this metadata-only handoff.
from __future__ import annotations

import json
import os
import re
import stat

from apatch import sdd_ownership_handoff as frozen
from apatch.enforcement import load_notarized_index
from apatch.path_leases import acquire_path_lease, release_path_leases
from apatch.runtime.atomic_io import exclusive_file_lock
from apatch.sdd_integrity import (SddContractError, _seal, _verify_seal, _path_matches,
                                  canonical_hash, freeze_contract)
from apatch.spec import parse_spec
from apatch.spec_ownership import resolve_spec_owned_targets
from apatch.trustchain_helper import TrustChainHelper

io = frozen.io
_notarize = frozen._notarize
SCHEMA = "apatch.sdd.unfrozen-ownership-handoff.v1"
UNFROZEN_PREDECESSOR_VERSION = 1
MAX_ARCHIVES = 512
MAX_ARCHIVE_BYTES = 67_108_864


def _absence(root, source_spec, source_document_hash):
    """Bounded, inode-pinned inventory; unreadable history is not absence."""
    inventory, total = {}, 0
    source_hashes = {source_document_hash}
    prepared_path = ".apatch/sdd/prepared/" + source_spec + ".json"
    try:
        prepared_bytes = io._read(root, prepared_path)
    except FileNotFoundError:
        pass
    else:
        prepared = json.loads(prepared_bytes.decode("utf-8"))
        request = prepared.get("contract_request") if isinstance(prepared, dict) else None
        prepared_hash = request.get("spec_hash") if isinstance(request, dict) else None
        if (not isinstance(prepared, dict)
                or prepared.get("schema") != "apatch.studio.sdd-preparation.v1"
                or prepared.get("spec_id") != source_spec
                or not isinstance(prepared_hash, str) or not io._HASH.fullmatch(prepared_hash)):
            raise SddContractError("source preparation is not a qualified historical document link")
        source_hashes.add(prepared_hash)
        inventory[prepared_path] = io._hash(prepared_bytes)
        total += len(prepared_bytes)
    with io._directory(root, (".apatch", "sdd", "frozen")) as directory:
        entries = os.listdir(directory)
        if len(entries) > MAX_ARCHIVES:
            raise SddContractError("frozen history exceeds the bounded ownership review")
        for name in sorted(entries):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not re.fullmatch(r"[0-9a-f]{64}\.json", name) or not stat.S_ISREG(info.st_mode):
                raise SddContractError("frozen history must contain only exact regular archives")
            relative = ".apatch/sdd/frozen/" + name
            data = io._read(root, relative)
            total += len(data)
            if total > MAX_ARCHIVE_BYTES:
                raise SddContractError("frozen history exceeds the bounded ownership review")
            record = json.loads(data.decode("utf-8"))
            if not isinstance(record, dict):
                raise SddContractError("frozen history must contain sealed contract objects")
            _verify_seal(record, "unfrozen predecessor archive inventory")
            obligations = record.get("obligations")
            if (record.get("document_hash") != "sha256:" + name[:-5]
                    or record.get("schema") != "apatch.sdd.verification-contract.v1"
                    or record.get("status") != "frozen" or record.get("implementation_allowed") is not True
                    or not isinstance(obligations, list)
                    or not obligations or any(not isinstance(row, dict)
                    or not isinstance(row.get("test_id"), str) for row in obligations)):
                raise SddContractError("frozen history contains an invalid archived contract")
            checked = freeze_contract({key: value for key, value in record.items() if key != "document_hash"})
            if checked != record or any(row.get("command_hash") != canonical_hash(row.get("command")) for row in obligations):
                raise SddContractError("frozen history contains a malformed native verification contract")
            if (record.get("spec_hash") in source_hashes or
                    any(row["test_id"].startswith(source_spec + "#") for row in obligations)):
                raise SddContractError("a predecessor with frozen history cannot use the unfrozen handoff")
            inventory[relative] = io._hash(data)
    # A source envelope is evidence of bound source authority, not a raw draft.
    try:
        with io._directory(root, (".apatch", "sdd", "envelopes", source_spec)) as directory:
            if os.listdir(directory):
                raise SddContractError("a predecessor with task envelopes cannot use the unfrozen handoff")
    except FileNotFoundError:
        pass
    return inventory


def _inputs(root, source_spec, destination_spec, source_document_hash, paths, *, before=None):
    source_path = frozen._spec_path(source_spec)
    destination_path = frozen._spec_path(destination_spec)
    if (source_spec == destination_spec or not isinstance(paths, list)
            or not 1 <= len(paths) <= 32 or any(not isinstance(path, str) for path in paths)
            or len(set(paths)) != len(paths)):
        raise SddContractError("one to 32 unique exact overlapping paths are required")
    paths = sorted(paths)
    for path in paths:
        parts = io._parts(path)
        if ("/".join(parts) != path or any(c in path for c in "*?[]")
                or path.startswith((".apatch/", ".trustchain/", ".git/", "docs/specs/", "tests/"))):
            raise SddContractError("handoff supports exact implementation files only")
    if not isinstance(source_document_hash, str) or not io._HASH.fullmatch(source_document_hash):
        raise SddContractError("an exact raw predecessor-document pin is required")
    context = io._context(root, destination_spec)
    source_bytes = io._read(root, source_path) if before is None else before.encode("utf-8")
    target_bytes = io._read(root, destination_path)
    if io._hash(source_bytes) != source_document_hash or io._hash(target_bytes) != context["spec_hash"]:
        raise SddContractError("canonical ownership document bytes differ from their exact pins")
    destination = parse_spec(target_bytes.decode("utf-8"))
    if destination.id != destination_spec or not destination.strict_ownership:
        raise SddContractError("destination document must be the exact strict frozen SPEC")
    after, affected, administrative = frozen._revision(source_bytes.decode("utf-8"), source_spec,
        target_bytes.decode("utf-8"), paths)
    contract = io._json(root, ".apatch/sdd/frozen/" + context["contract_hash"][7:] + ".json")
    obligations = {row["test_id"] for row in contract["obligations"]}
    for path in paths:
        owners = [row for row in destination.requirements if path in row.owns]
        if len(owners) != 1:
            raise SddContractError("each transferred path needs one destination requirement")
        requirement = destination_spec + "#" + owners[0].id
        envelope_path = ".apatch/sdd/envelopes/" + destination_spec + "/" + owners[0].id + ".json"
        if requirement not in obligations or envelope_path not in context["envelopes"]:
            raise SddContractError("transferred ownership has no exact frozen obligation and envelope")
        envelope = io._json(root, envelope_path)
        if any(_path_matches(path, pattern) for pattern in envelope["forbidden_paths"]) or not any(
                _path_matches(path, pattern) for pattern in envelope["allowed_writes"]):
            raise SddContractError("transferred ownership is outside the frozen write envelope")
    inventory = _absence(root, source_spec, source_document_hash)
    return {"source_kind": "unfrozen_spec", "source_spec": source_spec,
        "destination_spec": destination_spec, "source_path": source_path,
        "source_contract_hash": None, "source_document_hash": source_document_hash,
        "destination_context": context, "paths": paths, "judge_assets": context["judge_assets"],
        "frozen_inventory": inventory,
        "implementation_hashes": {path: io._hash(io._read(root, path)) for path in paths},
        "before": source_bytes.decode("utf-8"), "after": after,
        "before_hash": io._hash(source_bytes), "after_hash": io._hash(after.encode("utf-8")),
        "affected_requirements": affected, "administrative_requirements": administrative}


@io._pinned_workspace
def prepare_ownership_handoff(root, *, source_spec, destination_spec, source_kind,
        source_document_hash, paths, reason, source_contract_hash=None):
    io._idle(root)
    if source_kind != "unfrozen_spec" or source_contract_hash is not None:
        raise SddContractError("this additive path accepts only an explicitly unfrozen predecessor")
    if not isinstance(reason, str) or not 8 <= len(reason) <= 4096:
        raise SddContractError("an explicit bounded ownership-amendment reason is required")
    return _seal({"schema": SCHEMA,
        **_inputs(root, source_spec, destination_spec, source_document_hash, paths),
        "reason": reason, "functional_acceptance": False})


def _payload(review, stage):
    value = frozen._payload(review, stage)
    value.update(schema=SCHEMA, source_kind="unfrozen_spec", source_contract_hash=None,
                 source_document_hash=review["source_document_hash"])
    return value


def _validate_review(root, review):
    _verify_seal(review, "unfrozen ownership handoff review")
    if (review.get("schema") != SCHEMA or review.get("functional_acceptance") is not False
            or review.get("source_kind") != "unfrozen_spec" or review.get("source_contract_hash") is not None):
        raise SddContractError("invalid explicit unfrozen ownership handoff review")
    current = io._read(root, review["source_path"])
    if current not in (review["before"].encode("utf-8"), review["after"].encode("utf-8")):
        raise SddContractError("predecessor changed after the exact owner review")
    inputs = _inputs(root, review["source_spec"], review["destination_spec"],
        review["source_document_hash"], review["paths"], before=review["before"])
    expected = _seal({"schema": SCHEMA, **inputs, "reason": review["reason"], "functional_acceptance": False})
    if expected != review:
        raise SddContractError("reviewed ownership or frozen destination inputs changed")


def _completed(root, review, receipt):
    """Validate signed history, not a new authorization to mutate again."""
    _verify_seal(review, "unfrozen ownership handoff review")
    _verify_seal(receipt, "unfrozen ownership handoff receipt")
    if (receipt.get("review") != review or receipt.get("schema") != SCHEMA
            or receipt.get("functional_acceptance") is not False or receipt.get("status") != "completed"
            or review.get("source_kind") != "unfrozen_spec" or review.get("source_contract_hash") is not None):
        raise SddContractError("ownership receipt differs from the immutable unfrozen owner review")
    context = review["destination_context"]
    digest = context["contract_hash"]
    if not isinstance(digest, str) or not io._HASH.fullmatch(digest):
        raise SddContractError("invalid archived destination contract")
    contract = io._json(root, ".apatch/sdd/frozen/" + digest[7:] + ".json")
    _verify_seal(contract, "archived ownership destination")
    if (contract["document_hash"] != digest or contract["spec_hash"] != context["spec_hash"]
            or contract["authority"]["actor_id"] != context["authority_id"]):
        raise SddContractError("archived ownership authority differs")
    identity, pin = io._identity(root, context["authority_id"])
    if pin != context["owner_key_hash"]:
        raise SddContractError("ownership authority key changed")
    for stage, field in (("approved", "approval"), ("written", "mutation"), ("completed", "completion")):
        io._verified_record(root, receipt[field], identity, _payload(review, stage))
    if io._read(root, review["source_path"]) != review["after"].encode("utf-8"):
        raise SddContractError("ownership document differs from its completed amendment")
    return receipt


@io._pinned_workspace
def execute_ownership_handoff(root, review, *, authority_id, expected_snapshot, confirmed):
    _verify_seal(review, "unfrozen ownership handoff review")
    if (confirmed is not True or expected_snapshot != review["document_hash"]
            or authority_id != review["destination_context"]["authority_id"]):
        raise SddContractError("explicit approval by the exact frozen destination owner is required")
    folder = ".apatch/sdd/ownership-handoffs/" + review["document_hash"][7:]
    transaction = "ownership-handoff:" + review["document_hash"][7:]
    lock = ".apatch/sdd/successor-activation"
    io._check_paths(root, [lock, lock + ".lock", folder, ".apatch/notarized_index.json", ".apatch/notarized_index.json.lock"])
    with exclusive_file_lock(str(root / lock)):
        io._idle(root, own_transaction=transaction)
        try:
            receipt = io._json(root, folder + "/receipt.json")
        except FileNotFoundError:
            receipt = None
        if receipt is not None:
            result = _completed(root, review, receipt)
            _notarize(root, review, receipt)
            return result
        _validate_review(root, review)
        identity, pin = io._identity(root, authority_id)
        if pin != review["destination_context"]["owner_key_hash"]:
            raise SddContractError("ownership authority key changed")
        helper = TrustChainHelper(str(root), auto_init=False)
        try:
            approval = io._json(root, folder + "/approval.json")
        except FileNotFoundError:
            if io._read(root, review["source_path"]) != review["before"].encode("utf-8"):
                raise SddContractError("predecessor changed after owner review")
            approval = io._sign(root, helper, identity, _payload(review, "approved"))
        io._verified_record(root, approval, identity, _payload(review, "approved"))
        lease = acquire_path_lease(str(root), [review["source_path"], folder], tool="apatch_sdd_ownership_handoff",
                                  governed_session_id=transaction, max_seconds=300)
        before_index = load_notarized_index(str(root))
        completed = False
        try:
            io._save(root, folder + "/approval.json", approval)
            io._save(root, folder + "/review.json", review)
            io._immutable(root, folder + "/before.md", review["before"].encode("utf-8"))
            io._immutable(root, folder + "/after.md", review["after"].encode("utf-8"))
            _validate_review(root, review)
            io._idle(root, own_transaction=transaction)
            if io._read(root, review["source_path"]) != review["after"].encode("utf-8"):
                frozen._replace(root, review["source_path"], review["before"].encode("utf-8"), review["after"].encode("utf-8"))
            resolved = resolve_spec_owned_targets(str(root), [{"action": "replace", "target_file": path} for path in review["paths"]])
            if not resolved.get("ok") or {row["spec"] for row in resolved["owned"]} != {review["destination_spec"]}:
                raise SddContractError("ownership handoff did not resolve every transferred path")
            _validate_review(root, review)
            records = {"approval": approval}
            for stage, field in (("written", "mutation"), ("completed", "completion")):
                try:
                    record = io._json(root, folder + "/" + field + ".json")
                except FileNotFoundError:
                    record = io._sign(root, helper, identity, _payload(review, stage))
                    io._save(root, folder + "/" + field + ".json", record)
                records[field] = io._verified_record(root, record, identity, _payload(review, stage))
                _validate_review(root, review)  # Source may freeze during signer latency.
                io._idle(root, own_transaction=transaction)
            receipt = _seal({"schema": SCHEMA, "status": "completed", "review": review,
                             **records, "functional_acceptance": False})
            io._save(root, folder + "/receipt.json", receipt)
            completed = True
            _notarize(root, review, receipt)
            return _completed(root, review, receipt)
        except BaseException:
            if not completed:
                if io._read(root, review["source_path"]) == review["after"].encode("utf-8"):
                    frozen._replace(root, review["source_path"], review["after"].encode("utf-8"), review["before"].encode("utf-8"))
                io._rollback_notarization(root, [review["source_path"]], transaction, before_index)
            raise
        finally:
            io._assert_workspace(root)
            release_path_leases(str(root), lease_id=lease["lease_id"], governed_session_id=transaction)


@io._pinned_workspace
def review_ownership_handoff(root, review):
    _verify_seal(review, "unfrozen ownership handoff review")
    try:
        receipt = io._json(root, ".apatch/sdd/ownership-handoffs/" + review["document_hash"][7:] + "/receipt.json")
    except FileNotFoundError:
        _validate_review(root, review)
        return {"status": "awaiting_approval", "review": review, "snapshot": review["document_hash"], "functional_acceptance": False}
    return {"status": "completed", "review": review, "snapshot": review["document_hash"],
            "receipt": _completed(root, review, receipt), "functional_acceptance": False}
