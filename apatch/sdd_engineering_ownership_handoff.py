"""Exact ownership-only succession of a SPEC accidentally frozen as judge data.

Executable judges and their commands are never editable here. The old composite
acceptance and all its bytes remain historical; a current runtime obligation
retains the identical command and all executable assets. Owner receipts attest
only this engineering-metadata revision, never a new functional test result.
"""
from __future__ import annotations

import copy
import json
import os
import stat

from apatch import sdd_ownership_handoff as base
from apatch import sdd_contract_intake as intake
from apatch.path_leases import acquire_path_lease, release_path_leases
from apatch.runtime.atomic_io import exclusive_file_lock
from apatch.sdd_integrity import SddContractError, _seal, _verify_seal, _path_matches, canonical_hash, freeze_contract, validate_task_envelope
from apatch.spec import parse_spec
from apatch.spec_ownership import resolve_spec_owned_targets
from apatch.strict_existing_signer import ExistingSignerRefused

io = base.io
SCHEMA = "apatch.sdd.engineering-ownership-succession.v1"
KIND = "frozen_engineering_spec"
PROFILE = ".apatch/sdd_verification_contract.json"
MAX_ASSETS = 64
MAX_BYTES = 8_388_608
MAX_ENVELOPES = 32


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _mode(root, relative):
    parts = io._parts(relative)
    with io._directory(root, parts[:-1]) as parent:
        value = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode):
            raise SddContractError("engineering snapshot requires exact regular files")
        return stat.S_IMODE(value.st_mode)



def _ordinary_owner_binding(root, spec_id, contract, package_path, package, review_hash):
    """Associate an ordinary, completed owner intake with its exact public freeze.

    This is not the cold-publication bootstrap or a new authority. Both existing
    native intake signatures and the configured native certificate are required.
    The certified owner party and native signer are distinct existing SDK roles;
    this reader makes no claim that their separate certificates or keys match.
    No signer, owner, profile, selector or receipt is written by this reader.
    """
    from datetime import datetime, timezone
    from pathlib import Path
    import base64
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from trustchain.v2.chain_store import reconstruct_signed_response
    from trustchain.v2.signer import _canonical_json_from_response

    def require(ok, message):
        if not ok:
            raise SddContractError("ordinary intake owner binding: " + message)

    require(isinstance(review_hash, str) and io._HASH.fullmatch(review_hash), "exact intake review hash required")
    owner = contract["authority"]["actor_id"]
    folder = ".apatch/sdd/contract-intakes/" + review_hash[7:] + "/"
    review = io._json(root, folder + "review.json")
    _verify_seal(review, "ordinary owner intake review")
    require(review.get("document_hash") == review_hash and review.get("schema") == intake.SCHEMA
            and review.get("workspace") == str(root) and review.get("spec_id") == spec_id
            and review.get("rfp_id") == "RFP-" + spec_id[5:] and review.get("authority_id") == owner
            and isinstance(review.get("actor_id"), str) and review["actor_id"] != owner
            and not review.get("preparation_mode")
            and all(review.get(k) is v for k, v in intake.PREPARATION.items()), "ordinary intake identity differs")
    require(io._HASH.fullmatch(str(review.get("active_contract_hash"))), "ordinary predecessor contract required")
    predecessor_path = ".apatch/sdd/frozen/" + review["active_contract_hash"][7:] + ".json"
    predecessor_bytes = io._read(root, predecessor_path)
    predecessor = json.loads(predecessor_bytes)
    require(freeze_contract(predecessor) == predecessor
            and predecessor["document_hash"] == review["active_contract_hash"]
            and predecessor["authority"] == contract["authority"]
            and io._hash(predecessor_bytes) == review["input_hashes"].get(PROFILE), "exact predecessor owner archive differs")
    old_checks = {row["acceptance_id"]: row for row in predecessor["obligations"]}
    current_checks = {row["acceptance_id"]: row for row in contract["obligations"]}
    require(all(current_checks.get(key) == value for key, value in old_checks.items()), "a previous obligation was changed")
    outputs = {}
    for row in review["files"]:
        relative = row["path"]
        require(relative not in outputs and isinstance(row.get("content"), str)
                and io._hash(row["content"].encode()) == row["after_hash"]
                and io._hash(io._read(root, relative)) == row["after_hash"], "approved preparation output differs")
        outputs[relative] = row["after_hash"]
    require(outputs.get(package_path) == io._hash(io._read(root, package_path))
            and outputs.get("docs/specs/" + spec_id + ".md") == contract["spec_hash"]
            and outputs.get("docs/" + review["rfp_id"] + ".md") == contract["rfp_hash"], "signed preparation does not bind current documents")
    # Public freeze replaces the profile and removes obsolete envelopes of only
    # this SPEC. All other original authority inputs remain byte-identical.
    removed = {}
    own_envelopes = ".apatch/sdd/envelopes/" + spec_id + "/"
    for relative, expected in review["input_hashes"].items():
        if relative == PROFILE or relative in outputs:
            continue
        try:
            actual = io._hash(io._read(root, relative))
        except FileNotFoundError:
            require(relative.startswith(own_envelopes) and relative.endswith(".json")
                    and io.re.fullmatch(r"R[0-9]+\.json", relative[len(own_envelopes):]), "preserved authority input is absent")
            removed[relative] = expected
            continue
        require(actual == expected, "preserved authority input drift")

    binding, public = intake._identity(root)
    identity = io.load_local_identity(str(root))
    require(identity is not None and identity.agent_id == binding.agent_id
            and len(public) == 32 and binding.public_key_sha256 == io._hash(public)[7:], "current native signer differs")
    receipts, records, pins = {}, [], {}
    for stage in ("approved", "completed"):
        payload = intake._receipt(root, review, stage)
        require(payload is not None, "both real native intake signatures required")
        locator = io._json(root, folder + stage + ".json")
        record = io._json(root, ".trustchain/" + locator["object_path"])
        record = record.get("value", record)
        require(record.get("algorithm") == "ed25519" and record.get("data") == payload, "native receipt record differs")
        response = reconstruct_signed_response(record)
        require(response is not None, "native owner receipt is not reconstructable")
        signature = base64.b64decode(response.signature, validate=True)
        for include in (True, False):
            try:
                Ed25519PublicKey.from_public_bytes(public).verify(signature,
                    _canonical_json_from_response(response, include_signature_id=include).encode())
                break
            except InvalidSignature:
                pass
        else:
            raise SddContractError("ordinary intake independent Ed25519 verification failed")
        receipts[stage] = locator["document_hash"]
        records.append(record)
    approved, completed = records
    owner_party = approved["data"].get("owner") or {}
    require(owner_party.get("id") == owner and owner_party.get("kind") == "machine"
            and owner_party.get("certified") is True and owner_party.get("id") != review["actor_id"]
            and completed.get("parent_signature") == approved["signature"], "signed certified owner or receipt ancestry differs")
    import math
    timestamps = [row.get("response_timestamp") for row in records]
    require(all(isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and t > 0
                for t in timestamps) and timestamps[0] <= timestamps[1] <= datetime.now(timezone.utc).timestamp(),
            "native owner receipt time order differs")
    require(completed["data"].get("result") == {"ok": True, "review_hash": review_hash,
            "files": outputs, **intake.PREPARATION}, "completed native intake result differs")

    pin = io._WORKSPACE_PIN.get()
    pins = pin.setdefault("owner_public_paths", {}) if pin is not None and pin["root"] == root else {}
    require(identity.cert_path is not None and Path(identity.cert_path).is_absolute(),
            "configured current native signer certificate absent")
    native_certificate = io._owner_public_read(Path(identity.cert_path), pins)
    ca_bytes = io._owner_public_read(Path(identity.cert_path).parent / "ca.pem", pins)
    root_bytes = io._owner_public_read(Path(identity.cert_path).parent / "root-ca.pem", pins)
    leaf, ca, anchor = [x509.load_pem_x509_certificate(v) for v in (native_certificate, ca_bytes, root_bytes)]
    leaf.verify_directly_issued_by(ca)
    ca.verify_directly_issued_by(anchor)
    anchor.verify_directly_issued_by(anchor)
    now = datetime.now(timezone.utc)
    require(all(v.not_valid_before_utc <= now <= v.not_valid_after_utc for v in (leaf, ca, anchor))
            and not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and anchor.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            and leaf.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) == public,
            "current native signer public PKIX binding differs")
    request = copy.deepcopy(package["contract_request"])
    for row in request["obligations"]:
        row["approver"] = owner
    request.update(authority={"actor_id": owner, "role": "authority"}, source_mutation_count=0,
                   frozen_at=contract["frozen_at"])
    require(freeze_contract(request) == contract, "current freeze is not the exact signed approved request")
    io._assert_workspace(root)
    return canonical_hash({"review_hash": review_hash, "receipts": receipts,
        "current_contract": contract["document_hash"], "predecessor": predecessor["document_hash"],
        "removed_superseded_envelopes": removed, "native_signer": {"agent_id": identity.agent_id,
            "public_key_sha256": io._hash(public)[7:]}, "native_certificate_sha256": io._hash(native_certificate),
        "native_ca_sha256": io._hash(ca_bytes), "native_root_sha256": io._hash(root_bytes),
        "public_inputs": {str(k): v for k, v in pins.items()}}), io._hash(public)


def _context(root, spec_id, owner_intake_review_hash=None):
    if owner_intake_review_hash is None:
        return io._context(root, spec_id)
    if not isinstance(spec_id, str) or not io._SPEC.fullmatch(spec_id):
        raise SddContractError("invalid engineering SPEC identity")
    package_path = ".apatch/sdd/prepared/" + spec_id + ".json"
    io._check_paths(root, [PROFILE, package_path, ".apatch/sdd/frozen", ".apatch/sdd/envelopes/" + spec_id])
    contract = io.load_profile_contract(root)
    if contract is None:
        raise SddContractError("an active implementation-ready frozen contract is required")
    if io._json(root, ".apatch/sdd/frozen/" + contract["document_hash"][7:] + ".json") != contract:
        raise SddContractError("active contract differs from its immutable frozen archive")
    package_bytes = io._read(root, package_path)
    package = json.loads(package_bytes)
    request = package.get("contract_request", {})
    if (package.get("schema") != "apatch.studio.sdd-preparation.v1" or package.get("spec_id") != spec_id
            or any(request.get(field) != contract.get(field) for field in ("rfp_hash", "spec_hash"))):
        raise SddContractError("canonical prepared SPEC does not bind the active document hashes")
    if not isinstance(package.get("task_envelopes"), dict) or not package["task_envelopes"]:
        raise SddContractError("canonical preparation has no requirement identity map")
    requirements = set(package["task_envelopes"])
    if any(not io.re.fullmatch(r"R[0-9]+", key) for key in requirements):
        raise SddContractError("invalid prepared requirement identity")
    assets = {}
    for obligation in contract["obligations"]:
        if obligation.get("command_hash") != canonical_hash(obligation.get("command")):
            raise SddContractError("frozen judge command hash differs")
        for asset in obligation.get("judge_assets", []):
            path = asset["path"]
            actual = io._hash(io._read(root, path))
            if actual != asset["sha256"] or (path in assets and assets[path] != actual):
                raise SddContractError("frozen judge asset drift")
            assets[path] = actual
    if not assets:
        raise SddContractError("frozen contract has no pinned judge assets")
    envelopes = {}
    for path in sorted(root.glob(".apatch/sdd/envelopes/" + spec_id + "/*.json")):
        relative = path.relative_to(root).as_posix()
        raw = io._json(root, relative)
        if raw.get("contract_hash") != contract["document_hash"]:
            continue
        checked = validate_task_envelope(raw)
        if (checked != raw or raw.get("requirement") != spec_id + "#" + path.stem or path.stem not in requirements
                or validate_task_envelope({**package["task_envelopes"][path.stem], "contract_hash": contract["document_hash"]}) != raw):
            raise SddContractError("active envelope differs from the signed prepared source policy")
        envelopes[relative] = io._hash(io._read(root, relative))
    if not envelopes:
        raise SddContractError("selected SPEC has no envelope of the active frozen contract")
    proof, key_hash = _ordinary_owner_binding(root, spec_id, contract, package_path, package,
                                            owner_intake_review_hash)
    return {"native_owner_binding_hash": proof, "contract_hash": contract["document_hash"],
        # Legacy field name: this pins the actual native signer key, not the machine-party certificate.
        "authority_id": contract["authority"]["actor_id"], "owner_key_hash": key_hash,
        "spec_hash": contract["spec_hash"], "rfp_hash": contract["rfp_hash"],
        "prepared_hash": io._hash(package_bytes), "judge_assets": assets, "envelopes": envelopes}


def _inputs(root, source_spec, destination_spec, source_document_hash, paths, source_contract_hash,
            owner_intake_review_hash=None):
    source_path, target_path = base._spec_path(source_spec), base._spec_path(destination_spec)
    if (source_spec == destination_spec or not isinstance(paths, list) or not 1 <= len(paths) <= 32
            or any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths)):
        raise SddContractError("one to 32 exact predecessor implementation paths are required")
    paths = sorted(paths)
    for path in paths:
        if ("/".join(io._parts(path)) != path or any(c in path for c in "*?[]")
                or path.startswith((".apatch/", ".trustchain/", ".git/", "docs/", "tests/"))):
            raise SddContractError("engineering succession cannot edit foreign or administrative scope")
    context = _context(root, destination_spec, owner_intake_review_hash)
    if (source_contract_hash != context["contract_hash"] or not io._HASH.fullmatch(str(source_document_hash))
            or context["judge_assets"].get(source_path) != source_document_hash):
        raise SddContractError("the predecessor SPEC must be an exact asset of this active owner snapshot")
    before, target = io._read(root, source_path), io._read(root, target_path)
    if io._hash(before) != source_document_hash or io._hash(target) != context["spec_hash"]:
        raise SddContractError("engineering document differs from its reviewed snapshot")
    destination = parse_spec(target.decode("utf-8"))
    if destination.id != destination_spec or not destination.strict_ownership:
        raise SddContractError("destination must already be the exact strict frozen SPEC")
    after, affected, administrative = base._revision(before.decode("utf-8"), source_spec, target.decode("utf-8"), paths)
    contract = io._json(root, ".apatch/sdd/frozen/" + context["contract_hash"][7:] + ".json")
    tests = {row["test_id"] for row in contract["obligations"]}
    for path in paths:
        owners = [row for row in destination.requirements if path in row.owns]
        if len(owners) != 1:
            raise SddContractError("each transferred path needs exactly one frozen destination requirement")
        requirement = destination_spec + "#" + owners[0].id
        relative = ".apatch/sdd/envelopes/" + destination_spec + "/" + owners[0].id + ".json"
        if requirement not in tests or relative not in context["envelopes"]:
            raise SddContractError("transferred scope has no existing frozen obligation and envelope")
        envelope = io._json(root, relative)
        if any(_path_matches(path, p) for p in envelope["forbidden_paths"]) or not any(
                _path_matches(path, p) for p in envelope["allowed_writes"]):
            raise SddContractError("transferred scope is outside the current source policy")
    assets = context["judge_assets"]
    if len(assets) > MAX_ASSETS or sum(len(io._read(root, path)) for path in assets) > MAX_BYTES:
        raise SddContractError("engineering succession exceeds its immutable history budget")
    prepared_path = ".apatch/sdd/prepared/" + destination_spec + ".json"
    before_files = {source_path: before.decode("utf-8"), prepared_path: io._read(root, prepared_path).decode("utf-8"),
                    PROFILE: io._read(root, PROFILE).decode("utf-8")}
    # Preserve/rebind every envelope of the active snapshot, including other SPECs.
    envelope_inventory = {}
    entries = sorted(root.glob(".apatch/sdd/envelopes/*/*.json"))
    if len(entries) > MAX_ENVELOPES:
        raise SddContractError("engineering succession exceeds its envelope budget")
    for path in entries:
        relative = path.relative_to(root).as_posix()
        raw = io._read(root, relative)
        checked = validate_task_envelope(json.loads(raw))
        if checked != json.loads(raw) or checked["requirement"] != path.parent.name + "#" + path.stem:
            raise SddContractError("invalid archived envelope identity")
        envelope_inventory[relative] = io._hash(raw)
        if checked["contract_hash"] == context["contract_hash"]:
            before_files[relative] = raw.decode("utf-8")
    association = ({"owner_intake_review_hash": owner_intake_review_hash}
                   if owner_intake_review_hash is not None else {})
    return {**association, "source_kind": KIND, "source_spec": source_spec, "destination_spec": destination_spec,
        "source_path": source_path, "source_document_hash": source_document_hash,
        "source_contract_hash": source_contract_hash, "destination_context": context,
        "document_hashes": {source_path: source_document_hash, target_path: context["spec_hash"]},
        "paths": paths, "before": before.decode("utf-8"), "after": after,
        "before_hash": io._hash(before), "after_hash": io._hash(after.encode("utf-8")),
        "affected_requirements": affected, "administrative_requirements": administrative,
        "implementation_hashes": {path: io._hash(io._read(root, path)) for path in paths},
        "judge_assets": assets, "envelope_inventory": envelope_inventory,
        "before_files": before_files, "prepared_path": prepared_path,
        "file_modes": {path: _mode(root, path) for path in set(before_files) | set(assets) | set(paths)}}


def _successor(review):
    old = json.loads(review["before_files"][PROFILE])
    body = copy.deepcopy(old)
    body.pop("document_hash")
    retired, remap = [], {}
    for item in body["obligations"]:
        if any(asset["path"] == review["source_path"] for asset in item["judge_assets"]):
            original = copy.deepcopy(item)
            retired.append(original)
            replacement = original["acceptance_id"] + ".RUNTIME"
            if any(row["acceptance_id"] == replacement for row in old["obligations"]):
                raise SddContractError("runtime replacement identity already exists")
            remap[original["acceptance_id"]] = replacement
            item["acceptance_id"] = replacement
            item["oracle"] = ("The original runtime and business assertions remain mandatory: " + original["oracle"]
                + "\nEngineering scope correction: solely the predecessor SPEC byte-identity statement in that historical "
                "composite is superseded by exact owner decision " + review["decision_hash"]
                + ". All runtime/business statements, original commands and executable judges remain current. "
                "No new runtime GREEN or implementation acceptance is asserted by this metadata decision.")
            item["engineering_predecessor_acceptance_id"] = original["acceptance_id"]
            for asset in item["judge_assets"]:
                if asset["path"] == review["source_path"]:
                    asset["sha256"] = review["after_hash"]
            item["asset_hashes"] = [asset["sha256"] for asset in item["judge_assets"]]
    if not retired:
        raise SddContractError("there is no exact engineering-document acceptance to succeed")
    body["supersedes_hash"] = old["document_hash"]
    body["engineering_succession"] = {"schema": SCHEMA, "decision_hash": review["decision_hash"],
        "source_path": review["source_path"], "before_hash": review["before_hash"], "after_hash": review["after_hash"],
        "historical_obligations": retired, "runtime_replacements": remap, "functional_acceptance": False}
    package = json.loads(review["before_files"][review["prepared_path"]])
    package["contract_request"]["obligations"] = copy.deepcopy(body["obligations"])
    package["contract_request"]["engineering_succession"] = copy.deepcopy(body["engineering_succession"])
    package["contract_request"]["supersedes_hash"] = old["document_hash"]
    pins = {asset["path"] for item in body["obligations"] for asset in item["judge_assets"]}
    for envelope in package["task_envelopes"].values():
        envelope["checks"] = [remap.get(check, check) for check in envelope["checks"]]
        envelope["forbidden_paths"] = sorted(set(envelope["forbidden_paths"]) | pins | {".apatch/**", ".trustchain/**", ".git/**"})
    body["candidate_path"] = review["prepared_path"]
    body["candidate_hash"] = io._hash(_bytes(package))
    successor = freeze_contract({**body, "source_mutation_count": 0})
    files = {review["source_path"]: review["after"], review["prepared_path"]: _bytes(package).decode("utf-8"),
             PROFILE: _bytes(successor).decode("utf-8")}
    for relative, text in review["before_files"].items():
        if relative.startswith(".apatch/sdd/envelopes/"):
            envelope = json.loads(text)
            envelope.pop("document_hash")
            envelope["contract_hash"] = successor["document_hash"]
            envelope["checks"] = [remap.get(check, check) for check in envelope["checks"]]
            envelope["forbidden_paths"] = sorted(set(envelope["forbidden_paths"]) | pins | {".apatch/**", ".trustchain/**", ".git/**"})
            files[relative] = _bytes(validate_task_envelope(envelope)).decode("utf-8")
    return successor, files


@io._pinned_workspace
def prepare(root, *, source_spec, destination_spec, source_kind, source_document_hash, paths, reason, source_contract_hash, actor_id,
            owner_intake_review_hash=None):
    io._idle(root)
    if source_kind != KIND or not isinstance(reason, str) or not 8 <= len(reason) <= 4096:
        raise SddContractError("an explicit bounded engineering ownership succession is required")
    inputs = _inputs(root, source_spec, destination_spec, source_document_hash, paths, source_contract_hash,
                     owner_intake_review_hash)
    if not isinstance(actor_id, str) or not 3 <= len(actor_id) <= 128 or actor_id == inputs["destination_context"]["authority_id"]:
        raise SddContractError("the engineering requester must be a named actor distinct from the frozen owner")
    inputs["actor_id"] = actor_id
    decision = canonical_hash({"schema": SCHEMA, **inputs, "reason": reason, "functional_acceptance": False})
    review = {"schema": SCHEMA, **inputs, "reason": reason, "decision_hash": decision, "functional_acceptance": False}
    successor, outputs = _successor(review)
    return _seal({**review, "successor_contract": successor, "after_files": outputs})


def _validate(review):
    _verify_seal(review, "engineering succession review")
    if review.get("schema") != SCHEMA or review.get("source_kind") != KIND or review.get("functional_acceptance") is not False:
        raise SddContractError("invalid engineering succession review")
    inputs = {key: value for key, value in review.items() if key not in {
        "decision_hash", "successor_contract", "after_files", "document_hash"}}
    if canonical_hash(inputs) != review["decision_hash"] or _successor(review) != (review["successor_contract"], review["after_files"]):
        raise SddContractError("engineering decision or exact successor snapshot differs")


def _check(root, review, *, after=False):
    for path, mode in review["file_modes"].items():
        if _mode(root, path) != mode:
            raise SddContractError("reviewed engineering file mode drift: " + path)
    expected = review["after_files"] if after else review["before_files"]
    for path, text in expected.items():
        if io._read(root, path) != text.encode("utf-8"):
            raise SddContractError("reviewed engineering snapshot drift: " + path)
    for path, digest in review["judge_assets"].items():
        expected_hash = review["after_hash"] if after and path == review["source_path"] else digest
        if io._hash(io._read(root, path)) != expected_hash:
            raise SddContractError("original frozen judge drift: " + path)
    for path, digest in review["document_hashes"].items():
        expected_hash = review["after_hash"] if after and path == review["source_path"] else digest
        if io._hash(io._read(root, path)) != expected_hash:
            raise SddContractError("reviewed engineering document drift: " + path)
    for path, digest in review["implementation_hashes"].items():
        if io._hash(io._read(root, path)) != digest:
            raise SddContractError("implementation changed during metadata-only succession")
    inventory = {p.relative_to(root).as_posix(): io._hash(io._read(root, p.relative_to(root).as_posix()))
                 for p in root.glob(".apatch/sdd/envelopes/*/*.json")}
    expected_inventory = dict(review["envelope_inventory"])
    if after:
        expected_inventory.update({path: io._hash(text.encode("utf-8")) for path, text in expected.items()
                                   if path.startswith(".apatch/sdd/envelopes/")})
    if inventory != expected_inventory:
        raise SddContractError("envelope inventory changed after owner review")


def _native_review(root, decision):
    """SDK-generated projection into the existing typed owner-intake commit.

    The signed hash is the *sealed engineering review*, not a caller-selected
    authority object. _commit/_receipt keep their existing native signer,
    Ed25519 verification, audit locators and reconciliation journal unchanged.
    """
    pins = {path: io._hash(text.encode("utf-8")) for path, text in decision["before_files"].items()}
    pins.update(decision["judge_assets"])
    pins.update(decision["implementation_hashes"])
    pins.update(decision["document_hashes"])
    pins.update(decision["envelope_inventory"])
    return {"document_hash": decision["document_hash"], "workspace": str(root),
        "spec_id": decision["destination_spec"], "rfp_id": "RFP-" + decision["destination_spec"][5:],
        "authority_id": decision["destination_context"]["authority_id"], "actor_id": decision["actor_id"],
        "active_contract_hash": decision["source_contract_hash"], "input_hashes": pins,
        "files": [{"path": path, "after_hash": io._hash(text.encode("utf-8"))}
                  for path, text in sorted(decision["after_files"].items())]}


def _extra(decision):
    return {"operation": "engineering-owner-succession", "engineering_decision_hash": decision["decision_hash"],
        "before_contract_hash": decision["source_contract_hash"],
        "after_contract_hash": decision["successor_contract"]["document_hash"], "source_mutations": 0}


def _result(decision):
    return {"ok": True, **_extra(decision), "review_hash": decision["document_hash"],
        "files": {path: io._hash(text.encode("utf-8")) for path, text in decision["after_files"].items()},
        **intake.PREPARATION}


def _native_receipt(root, decision, stage):
    _binding, public = intake._identity(root)
    if io._hash(public) != decision["destination_context"]["owner_key_hash"]:
        raise SddContractError("native intake signer differs from the reviewed owner key")
    native = _native_review(root, decision)
    payload = intake._receipt(root, native, stage)
    if payload is None:
        return None
    expected = _extra(decision)
    if stage == "approved":
        owner = payload.get("owner")
        if (not isinstance(owner, dict) or owner.get("id") != native["authority_id"] or owner.get("certified") is not True
                or owner.get("id") == native["actor_id"]):
            raise SddContractError("the actual certified intake owner differs from the engineering decision")
    else:
        expected["result"] = _result(decision)
    if any(payload.get(key) != value for key, value in expected.items()):
        raise SddContractError("typed native intake receipt differs from the engineering operation")
    return payload


def _receipt(root, review, folder):
    receipt = io._json(root, folder + "/receipt.json")
    _verify_seal(receipt, "engineering succession receipt")
    if (receipt.get("schema") != SCHEMA or receipt.get("review") != review or receipt.get("functional_acceptance") is not False
            or receipt.get("status") != "completed"):
        raise SddContractError("engineering receipt differs from the immutable owner decision")
    for stage in ("approved", "completed"):
        if _native_receipt(root, review, stage) is None:
            raise SddContractError("engineering completion has no genuine typed intake signatures")
    for path, digest in review["judge_assets"].items():
        if io._hash(io._read(root, folder + "/history/assets/" + canonical_hash(path)[7:])) != digest:
            raise SddContractError("historical original judge bytes changed")
    if io._json(root, ".apatch/sdd/frozen/" + review["source_contract_hash"][7:] + ".json") != json.loads(review["before_files"][PROFILE]):
        raise SddContractError("historical original contract changed")
    for path, text in review["before_files"].items():
        if io._read(root, folder + "/history/control/" + canonical_hash(path)[7:]) != text.encode("utf-8"):
            raise SddContractError("historical original control bytes changed")
    if io._json(root, ".apatch/sdd/frozen/" + review["successor_contract"]["document_hash"][7:] + ".json") != review["successor_contract"]:
        raise SddContractError("successor immutable contract archive differs")
    _check(root, review, after=True)
    return receipt



@io._pinned_workspace
def execute(root, review, *, owner, expected_snapshot):
    _validate(review)
    authority = review["destination_context"]["authority_id"]
    if (expected_snapshot != review["document_hash"] or not isinstance(owner, dict)
            or owner.get("id") != authority or owner.get("certified") is not True
            or owner.get("id") == review["actor_id"]):
        raise SddContractError("the current certified owner must approve the exact engineering snapshot")
    folder = ".apatch/sdd/engineering-successions/" + review["document_hash"][7:]
    transaction = "authority-intake:" + review["document_hash"]
    native = _native_review(root, review)
    lock = ".apatch/sdd/successor-activation"
    io._check_paths(root, [lock, lock + ".lock", folder, *review["after_files"], intake.PENDING, intake._folder(native)])
    with exclusive_file_lock(str(root / lock)):
        try:
            return _receipt(root, review, folder)
        except FileNotFoundError:
            pass
        io._idle(root, own_transaction=transaction)
        pending = intake._pending(root, native)
        approval = _native_receipt(root, review, "approved")
        completion = _native_receipt(root, review, "completed")
        if completion is not None:
            _check(root, review, after=True)
            receipt = _seal({"schema": SCHEMA, "status": "completed", "review": review,
                "native_intake": {stage: intake._folder(native) + stage + ".json" for stage in ("approved", "completed")},
                "functional_acceptance": False})
            io._save(root, folder + "/receipt.json", receipt)
            if pending is not None:
                (root / intake.PENDING).unlink()
            return _receipt(root, review, folder)
        if pending is not None and pending["stage"].startswith("signing_"):
            raise ExistingSignerRefused("EXISTING_SIGNER_RECONCILIATION_REQUIRED", reconciliation_required=True)
        _check(root, review)
        rebuilt = prepare(root, source_spec=review["source_spec"], destination_spec=review["destination_spec"], source_kind=KIND,
            source_document_hash=review["source_document_hash"], paths=review["paths"], reason=review["reason"],
            source_contract_hash=review["source_contract_hash"], actor_id=review["actor_id"],
            **{key: review[key] for key in ("owner_intake_review_hash",) if key in review})
        if rebuilt != review:
            raise SddContractError("engineering scope changed after owner review")
        binding, public = intake._identity(root)
        if io._hash(public) != review["destination_context"]["owner_key_hash"]:
            raise SddContractError("existing intake signer differs from the reviewed owner key")
        lease = acquire_path_lease(str(root), [*review["after_files"], folder], tool=intake.TOOL, governed_session_id=transaction, max_seconds=300)
        completed, changed = False, []
        try:
            _check(root, review)
            io._save(root, intake.PENDING, _seal({"review_hash": review["document_hash"], "stage": "prepared"}))
            intake._commit(root, native, "approved", {**_extra(review), "owner": owner})
            _native_receipt(root, review, "approved")
            _check(root, review)
            io._idle(root, own_transaction=transaction)
            io._save(root, folder + "/review.json", review)
            for path in review["judge_assets"]:
                io._immutable(root, folder + "/history/assets/" + canonical_hash(path)[7:], io._read(root, path))
            for path, text in review["before_files"].items():
                io._immutable(root, folder + "/history/control/" + canonical_hash(path)[7:], text.encode("utf-8"))
            if io._json(root, ".apatch/sdd/frozen/" + review["source_contract_hash"][7:] + ".json") != json.loads(review["before_files"][PROFILE]):
                raise SddContractError("original immutable contract archive differs")
            io._immutable(root, ".apatch/sdd/frozen/" + review["successor_contract"]["document_hash"][7:] + ".json", review["after_files"][PROFILE].encode("utf-8"))
            for path in sorted(review["after_files"], key=lambda p: p == PROFILE):
                base._replace(root, path, review["before_files"][path].encode("utf-8"), review["after_files"][path].encode("utf-8"))
                changed.append(path)
            _check(root, review, after=True)
            resolved = resolve_spec_owned_targets(str(root), [{"action": "replace", "target_file": path} for path in review["paths"]])
            if not resolved.get("ok") or {row["spec"] for row in resolved["owned"]} != {review["destination_spec"]}:
                raise SddContractError("engineering succession did not produce exact unique destination ownership")
            intake._commit(root, native, "completed", {**_extra(review), "result": _result(review)})
            _native_receipt(root, review, "completed")
            completed = True
            _check(root, review, after=True)
            receipt = _seal({"schema": SCHEMA, "status": "completed", "review": review,
                "native_intake": {stage: intake._folder(native) + stage + ".json" for stage in ("approved", "completed")},
                "functional_acceptance": False})
            io._save(root, folder + "/receipt.json", receipt)
            (root / intake.PENDING).unlink()
            return _receipt(root, review, folder)
        except BaseException:
            if not completed:
                for path in reversed(changed):
                    current = io._read(root, path)
                    if current == review["after_files"][path].encode("utf-8"):
                        base._replace(root, path, current, review["before_files"][path].encode("utf-8"))
                    elif current != review["before_files"][path].encode("utf-8"):
                        raise SddContractError("foreign engineering drift requires native reconciliation")
                state = intake._pending(root, native)
                # Existing _commit retains an ambiguous signing stage. Never
                # clear it, fake a completion, or retry a signature blindly.
                if state is not None and not state["stage"].startswith("signing_"):
                    (root / intake.PENDING).unlink()
            raise
        finally:
            io._assert_workspace(root)
            release_path_leases(str(root), lease_id=lease["lease_id"], governed_session_id=transaction)


@io._pinned_workspace
def review(root, decision):
    _validate(decision)
    folder = ".apatch/sdd/engineering-successions/" + decision["document_hash"][7:]
    try:
        result = _receipt(root, decision, folder)
    except FileNotFoundError:
        _check(root, decision)
        return {"status": "awaiting_approval", "review": decision, "functional_acceptance": False}
    return {"status": "completed", "review": decision, "receipt": result, "functional_acceptance": False}



def _cancellation_inputs(root, decision, *, own_transaction=None):
    """Authenticate this exact engineering frame without pretending it is ordinary.

    Only the existing typed native-intake projection is passed to recovery's
    ledger/incarnation readers. The original sealed engineering decision and all
    current before bytes, modes, scope and owner receipts remain mandatory.
    """
    from apatch import sdd_intake_recovery as recovery
    _validate(decision)
    io._idle(root, own_transaction=own_transaction)
    recovery.assert_can_freeze(root)
    _check(root, decision)
    reason, actor = decision["reason"], decision["actor_id"]
    if (not isinstance(reason, str) or not 8 <= len(reason) <= 4096
            or not isinstance(actor, str) or not 3 <= len(actor) <= 128):
        raise SddContractError("invalid original engineering cancellation identity")
    inputs = _inputs(root, decision["source_spec"], decision["destination_spec"],
        decision["source_document_hash"], decision["paths"], decision["source_contract_hash"],
        decision.get("owner_intake_review_hash"))
    if actor == inputs["destination_context"]["authority_id"]:
        raise SddContractError("engineering requester cannot approve its cancellation")
    inputs["actor_id"] = actor
    base_review = {"schema": SCHEMA, **inputs, "reason": reason, "functional_acceptance": False}
    base_review["decision_hash"] = canonical_hash(base_review)
    successor, outputs = _successor(base_review)
    rebuilt = _seal({**base_review, "successor_contract": successor, "after_files": outputs})
    if rebuilt != decision:
        raise SddContractError("engineering cancellation no longer matches the exact approved preparation")
    return _native_review(root, decision)


def _cancellation_review(root, decision, native, incarnation):
    from apatch import sdd_intake_recovery as recovery
    return _seal({**recovery._base(root, native, incarnation),
        "operation": "engineering-owner-succession-cancellation",
        "engineering_decision_hash": decision["decision_hash"]})


@io._pinned_workspace
def review_cancellation(root, decision):
    """Read-only proof that this original engineering attempt is uncommitted."""
    from apatch import sdd_intake_recovery as recovery
    native = _cancellation_inputs(root, decision)
    incarnation = recovery._incarnation(root, native)
    recovery._require(incarnation is not None, "no exact engineering pending incarnation to cancel")
    binding, public = intake._identity(root)
    recovery._require(io._hash(public) == decision["destination_context"]["owner_key_hash"],
                      "engineering cancellation signer differs from reviewed native key")
    recovery._scan(root, native, binding, public)
    return _cancellation_review(root, decision, native, incarnation)


@io._pinned_workspace
def cancel_uncommitted_engineering(root, decision, *, owner, expected_snapshot):
    """One genuine owner cancellation; never erase an uncertain native approval."""
    from apatch import sdd_intake_recovery as recovery
    from apatch.path_leases import find_covering_lease
    from apatch.trustchain_helper import TrustChainHelper
    native = _cancellation_inputs(root, decision)
    recovery._require(isinstance(owner, dict) and owner.get("certified") is True
        and owner.get("id") == native["authority_id"] and owner.get("id") != native["actor_id"],
        "current certified owner must approve engineering cancellation")
    folder = recovery._folder(native, expected_snapshot)
    binding, public = intake._identity(root)
    recovery._require(io._hash(public) == decision["destination_context"]["owner_key_hash"],
                      "engineering cancellation signer differs from reviewed native key")
    current = recovery._incarnation(root, native)
    if current is None:
        proposed = intake._read(root, folder + "review.json")
        _verify_seal(proposed, "saved engineering cancellation review")
        recovery._require(io._read(root, folder + "engineering-review.json") == _bytes(decision),
                          "original engineering cancellation history differs")
        recovery._require(proposed["document_hash"] == expected_snapshot
            and proposed == _cancellation_review(root, decision, native, proposed["pending_incarnation"]),
            "saved engineering cancellation differs")
    else:
        proposed = _cancellation_review(root, decision, native, current)
        recovery._require(proposed["document_hash"] == expected_snapshot,
                          "engineering pending incarnation or cancellation snapshot changed")
    expected = {**recovery._payload(root, native, proposed, owner, binding),
        "operation": "engineering-owner-succession-cancellation",
        "engineering_decision_hash": decision["decision_hash"]}
    prior = recovery._matching(recovery._scan(root, native, binding, public), proposed, expected)
    if current is None:
        recovery._require(prior is not None, "engineering pending vanished without native cancellation")
        selector = intake._read(root, folder + "cancelled.json")
        _verify_seal(selector, "engineering cancellation receipt selector")
        location, row = prior
        recovery._require(selector == _seal({"object_path": location, "signature": row["signature"],
            "payload_hash": canonical_hash(row["data"])}), "engineering cancellation selector differs")
        return expected["result"]
    lease_owner = "authority-intake-cancel:" + expected_snapshot
    paths = [row["path"] for row in native["files"]] + [intake.PENDING]
    io._check_paths(root, [folder, folder + "review.json", folder + "engineering-review.json",
                           folder + "cancelled.json", *paths])
    for path in paths:
        lease = find_covering_lease(str(root), path)
        recovery._require(lease is None or lease.get("governed_session_id") == lease_owner,
                          "engineering cancellation path lease conflict")
    with recovery.freeze_lock(root):
        recovery._require(_cancellation_inputs(root, decision, own_transaction=lease_owner) == native,
                          "engineering cancellation context changed")
        recovery._require(recovery._incarnation(root, native) == current,
                          "engineering pending changed before cancellation")
        lease = acquire_path_lease(str(root), paths, tool=intake.TOOL,
                                  governed_session_id=lease_owner, max_seconds=300)
        try:
            recovery._require(_cancellation_inputs(root, decision, own_transaction=lease_owner) == native,
                              "engineering cancellation context changed after lease")
            recovery._require(recovery._incarnation(root, native) == current,
                              "engineering pending changed after path lease")
            with exclusive_file_lock(str(root / ".apatch/trustchain_ledger")):
                prior = recovery._matching(recovery._scan(root, native, binding, public), proposed, expected)
            io._immutable(root, folder + "engineering-review.json", _bytes(decision))
            recovery._immutable(root, folder + "review.json", proposed)
            if prior is None:
                chain = TrustChainHelper(str(root), auto_init=False, existing_signer=binding)
                if not chain.commit_action(intake.TOOL, expected):
                    raise ExistingSignerRefused("EXISTING_SIGNER_RECEIPT_INVALID", reconciliation_required=True)
            with exclusive_file_lock(str(root / ".apatch/trustchain_ledger")):
                found = recovery._matching(recovery._scan(root, native, binding, public), proposed, expected)
                recovery._require(found is not None, "native engineering cancellation receipt is absent")
                recovery._require(_cancellation_inputs(root, decision, own_transaction=lease_owner) == native,
                                  "engineering cancellation inputs changed during signing")
                recovery._require(recovery._incarnation(root, native) == current,
                                  "engineering pending changed during signing")
                location, row = found
                recovery._immutable(root, folder + "cancelled.json", _seal({"object_path": location,
                    "signature": row["signature"], "payload_hash": canonical_hash(row["data"])}))
                intake._path(root, intake.PENDING).unlink()
                descriptor = os.open(intake._path(root, intake.PENDING).parent,
                                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return expected["result"]
        finally:
            io._assert_workspace(root)
            release_path_leases(str(root), lease_id=lease["lease_id"], governed_session_id=lease_owner)
