"""Owner-only transfer of exact, overlapping strict-SPEC write ownership.

Only owns: lines of the predecessor are revised. Functional text, both frozen
contracts, task envelopes and all judges are preserved byte-for-byte. A
requirement that relinquishes its last code file retains its own SPEC document
as its administrative artifact; its acceptance command remains mandatory.
This transaction is an amendment, never implementation acceptance.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat

from apatch import sdd_publication as io
from apatch.enforcement import load_notarized_index, record_notarized_files, record_session_attested
from apatch.path_leases import acquire_path_lease, release_path_leases
from apatch.runtime.atomic_io import exclusive_file_lock
from apatch.sdd_integrity import SddContractError, _seal, _verify_seal, canonical_hash
from apatch.spec import parse_spec
from apatch.spec_ownership import resolve_spec_owned_targets
from apatch.trustchain_helper import TrustChainHelper

SCHEMA = "apatch.sdd.ownership-handoff.v1"
_OWNS = re.compile(r"^(owns: )[ \t]*(.+?)(\r?\n)?$")


def _spec_path(spec_id):
    if not isinstance(spec_id, str) or not io._SPEC.fullmatch(spec_id):
        raise SddContractError("invalid ownership SPEC identity")
    return "docs/specs/" + spec_id + ".md"


def _revision(before, source_spec, target, paths):
    source = parse_spec(before)
    destination = parse_spec(target)
    if source.id != source_spec or not source.strict_ownership or not destination.strict_ownership:
        raise SddContractError("handoff requires two exact strict SPECs")
    old = {item.id: list(item.owns) for item in source.requirements}
    new = {item.id: list(item.owns) for item in destination.requirements}
    if any(sum(path in values for values in new.values()) != 1 or
           not any(path in values for values in old.values()) for path in paths):
        raise SddContractError("each exact path must be declared by the predecessor and one destination requirement")
    affected = sorted(key for key, values in old.items() if set(values) & set(paths))
    output, found, requirement, administrative = [], set(), None, []
    for line in before.splitlines(keepends=True):
        heading = re.match(r"^## (R[0-9]+)(?:\s|$)", line)
        if heading:
            requirement = heading[1]
        elif line.startswith("## "):
            requirement = None
        match = _OWNS.fullmatch(line)
        if match and requirement in affected:
            values = [value.strip().strip("`") for value in match[2].split(",")]
            if values != old[requirement] or requirement in found:
                raise SddContractError("only one canonical owns: line per affected requirement is supported")
            remaining = [value for value in values if value not in paths]
            if not remaining:
                remaining = [_spec_path(source_spec)]
                administrative.append(requirement)
            output.append("owns: " + ", ".join(remaining) + (match[3] or ""))
            found.add(requirement)
        else:
            output.append(line)
    if found != set(affected):
        raise SddContractError("ownership must be expressed in canonical owns: lines")
    after = "".join(output)
    revised = parse_spec(after)
    if [(x.id, x.title, x.verify) for x in source.requirements] != [(x.id, x.title, x.verify) for x in revised.requirements]:
        raise SddContractError("handoff cannot change functional requirements or acceptance commands")
    return after, affected, administrative


def _inputs(root, source_spec, destination_spec, source_contract_hash, paths, *, before=None):
    source_path, destination_path = _spec_path(source_spec), _spec_path(destination_spec)
    if source_spec == destination_spec or not isinstance(paths, list) or not 1 <= len(paths) <= 32 or len(set(paths)) != len(paths):
        raise SddContractError("one to 32 unique exact overlapping paths are required")
    paths = sorted(paths)
    policy_only = paths == [".apatch/remote.json"]
    for path in paths:
        parts = io._parts(path)
        if ("/".join(parts) != path or any(c in path for c in "*?[]")
                or (not policy_only and path.startswith((".apatch/", ".trustchain/", ".git/", "docs/specs/", "tests/")))):
            raise SddContractError("handoff supports exact implementation files or one exact remote policy")
    if policy_only:
        policy = json.loads(io._read(root, ".apatch/remote.json").decode("utf-8"))
        if not isinstance(policy, dict):
            raise SddContractError("remote policy must be a JSON object")
    if not isinstance(source_contract_hash, str) or not io._HASH.fullmatch(source_contract_hash):
        raise SddContractError("an exact archived predecessor contract is required")
    context = io._context(root, destination_spec)
    predecessor = io._json(root, ".apatch/sdd/frozen/" + source_contract_hash[7:] + ".json")
    _verify_seal(predecessor, "ownership predecessor contract")
    if (predecessor.get("document_hash") != source_contract_hash or predecessor.get("status") != "frozen"
            or predecessor.get("implementation_allowed") is not True
            or predecessor.get("authority", {}).get("actor_id") != context["authority_id"]
            or not predecessor.get("obligations")
            or any(not item["test_id"].startswith(source_spec + "#") for item in predecessor["obligations"])):
        raise SddContractError("both exact frozen contracts must have the same authenticated owner")
    source_bytes = io._read(root, source_path) if before is None else before.encode("utf-8")
    target_bytes = io._read(root, destination_path)
    if io._hash(source_bytes) != predecessor["spec_hash"] or io._hash(target_bytes) != context["spec_hash"]:
        raise SddContractError("canonical SPEC bytes differ from the exact frozen contracts")
    judges = dict(context["judge_assets"])
    for item in predecessor["obligations"]:
        if item["command_hash"] != canonical_hash(item["command"]):
            raise SddContractError("predecessor acceptance command drift")
        for asset in item["judge_assets"]:
            actual = io._hash(io._read(root, asset["path"]))
            if actual != asset["sha256"] or (asset["path"] in judges and judges[asset["path"]] != actual):
                raise SddContractError("preserved frozen judge drift")
            judges[asset["path"]] = actual
    after, affected, administrative = _revision(source_bytes.decode("utf-8"), source_spec, target_bytes.decode("utf-8"), paths)
    return {"source_spec": source_spec, "destination_spec": destination_spec,
        "source_path": source_path, "source_contract_hash": source_contract_hash,
        "destination_context": context, "paths": paths, "judge_assets": judges,
        "implementation_hashes": {path: io._hash(io._read(root, path)) for path in paths},
        "before": source_bytes.decode("utf-8"), "after": after,
        "before_hash": io._hash(source_bytes), "after_hash": io._hash(after.encode("utf-8")),
        "affected_requirements": affected, "administrative_requirements": administrative}


@io._pinned_workspace
def prepare_ownership_handoff(root, *, source_spec, destination_spec, source_contract_hash, paths, reason):
    io._idle(root)
    if not isinstance(reason, str) or not 8 <= len(reason) <= 4096:
        raise SddContractError("an explicit bounded ownership-amendment reason is required")
    return _seal({"schema": SCHEMA, **_inputs(root, source_spec, destination_spec, source_contract_hash, paths),
                  "reason": reason, "functional_acceptance": False})


def _payload(review, stage):
    digest = review["document_hash"]
    transaction = "ownership-handoff:" + digest[7:]
    value = {"schema": SCHEMA, "stage": stage, "review_hash": digest,
        "control_plane_transaction_id": transaction, "governed_session_id": transaction,
        "intent": review["reason"], "artifacts": [{"kind": "ownership-handoff", "id": digest[7:]}],
        "artifact_files": {transaction: [review["source_path"]]},
        "authority_id": review["destination_context"]["authority_id"],
        "owner_key_hash": review["destination_context"]["owner_key_hash"],
        "source_contract_hash": review["source_contract_hash"],
        "destination_contract_hash": review["destination_context"]["contract_hash"],
        "before_hash": review["before_hash"], "after_hash": review["after_hash"],
        "transferred_paths": review["paths"], "invalidated_requirements": review["affected_requirements"],
        "functional_acceptance": False}
    if stage == "written":
        value.update(action="ownership_handoff", files={review["source_path"]: {"sha256": review["after_hash"][7:]}})
    if stage == "completed":
        value.update(action="attest", attestation_scope="ownership_metadata_only")
    return value


def _replace(root, relative, expected, content):
    """Atomic one-file replacement under the exact path lease and inode-pinned parent."""
    parts = io._parts(relative)
    with io._directory(root, parts[:-1]) as parent:
        if io._read(root, relative) != expected:
            raise SddContractError("ownership document changed during its exact transaction")
        mode = stat.S_IMODE(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False).st_mode)
        temporary = ".ownership-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        try:
            io._write_payload(descriptor, content)
            io._assert_workspace(root)
            if io._read(root, relative) != expected:
                raise SddContractError("ownership document changed before atomic replacement")
            os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def _validate_review(root, review):
    _verify_seal(review, "ownership handoff review")
    if review.get("schema") != SCHEMA or review.get("functional_acceptance") is not False:
        raise SddContractError("invalid ownership handoff review")
    inputs = _inputs(root, review["source_spec"], review["destination_spec"], review["source_contract_hash"], review["paths"], before=review["before"])
    expected = _seal({"schema": SCHEMA, **inputs, "reason": review["reason"], "functional_acceptance": False})
    if expected != review:
        raise SddContractError("reviewed ownership or frozen inputs changed")


def _notarize(root, review, receipt):
    transaction = "ownership-handoff:" + review["document_hash"][7:]
    mutation, completion = receipt["mutation"], receipt["completion"]
    existing = load_notarized_index(str(root)).get("committed_files", {}).get(review["source_path"], {})
    if existing.get("sha256") == review["after_hash"][7:] and existing.get("attested") is True:
        return
    record_notarized_files(str(root), {review["source_path"]: {"sha256": review["after_hash"][7:]}},
        signature=mutation["signature"], object_path=mutation["object_path"], governed_session_id=transaction)
    if record_session_attested(str(root), transaction, signature=completion["signature"], object_path=completion["object_path"]) != 1:
        raise SddContractError("exact ownership document was not notarized")


def _completed(root, review, receipt):
    _verify_seal(review, "ownership handoff review")
    _verify_seal(receipt, "ownership handoff receipt")
    if receipt.get("review") != review or receipt.get("schema") != SCHEMA or receipt.get("functional_acceptance") is not False or receipt.get("status") != "completed":
        raise SddContractError("ownership receipt differs from the immutable owner review")
    for digest, expected_spec_hash in ((review["source_contract_hash"], review["before_hash"]),
            (review["destination_context"]["contract_hash"], review["destination_context"]["spec_hash"])):
        if not io._HASH.fullmatch(digest):
            raise SddContractError("invalid archived ownership contract")
        contract = io._json(root, ".apatch/sdd/frozen/" + digest[7:] + ".json")
        _verify_seal(contract, "archived ownership contract")
        if (contract["document_hash"] != digest or contract["spec_hash"] != expected_spec_hash
                or contract["authority"]["actor_id"] != review["destination_context"]["authority_id"]):
            raise SddContractError("archived ownership authority differs")
    identity, pin = io._identity(root, review["destination_context"]["authority_id"])
    if pin != review["destination_context"]["owner_key_hash"]:
        raise SddContractError("ownership authority key changed")
    for stage, field in (("approved", "approval"), ("written", "mutation"), ("completed", "completion")):
        io._verified_record(root, receipt[field], identity, _payload(review, stage))
    if io._read(root, review["source_path"]) != review["after"].encode("utf-8"):
        raise SddContractError("ownership document differs from its completed amendment")
    return receipt


@io._pinned_workspace
def execute_ownership_handoff(root, review, *, authority_id, expected_snapshot, confirmed):
    _verify_seal(review, "ownership handoff review")
    if confirmed is not True or expected_snapshot != review["document_hash"] or authority_id != review["destination_context"]["authority_id"]:
        raise SddContractError("explicit approval by the exact frozen owner is required")
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
        lease_paths = [review["source_path"], folder]
        if review["paths"] == [".apatch/remote.json"]:
            lease_paths.append(".apatch/remote.json")
        lease = acquire_path_lease(str(root), lease_paths, tool="apatch_sdd_ownership_handoff",
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
                _replace(root, review["source_path"], review["before"].encode("utf-8"), review["after"].encode("utf-8"))
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
                if review["paths"] == [".apatch/remote.json"]:
                    _validate_review(root, review)
            receipt = _seal({"schema": SCHEMA, "status": "completed", "review": review,
                             **records, "functional_acceptance": False})
            completed = True
            io._save(root, folder + "/receipt.json", receipt)
            _notarize(root, review, receipt)
            return _completed(root, review, receipt)
        except BaseException:
            if not completed:
                if io._read(root, review["source_path"]) == review["after"].encode("utf-8"):
                    _replace(root, review["source_path"], review["after"].encode("utf-8"), review["before"].encode("utf-8"))
                io._rollback_notarization(root, [review["source_path"]], transaction, before_index)
            raise
        finally:
            io._assert_workspace(root)
            release_path_leases(str(root), lease_id=lease["lease_id"], governed_session_id=transaction)


@io._pinned_workspace
def review_ownership_handoff(root, review):
    _verify_seal(review, "ownership handoff review")
    try:
        receipt = io._json(root, ".apatch/sdd/ownership-handoffs/" + review["document_hash"][7:] + "/receipt.json")
    except FileNotFoundError:
        _validate_review(root, review)
        return {"status": "awaiting_approval", "review": review, "snapshot": review["document_hash"], "functional_acceptance": False}
    return {"status": "completed", "review": review, "snapshot": review["document_hash"],
            "receipt": _completed(root, review, receipt), "functional_acceptance": False}
