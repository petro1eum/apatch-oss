"""Fixed-ID, owner-reviewed preparation; never an implementation grant.

Uses the existing owner, signer, transaction lock and path-lease primitives.
Preparation and replay do not switch the active profile or enrol an identity.
"""
from __future__ import annotations

import ast
import base64
import difflib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from apatch.sdd_preparation_io import _path, _read, _capture, _hash, _identity, assert_can_freeze, freeze_lock, _immutable
from apatch.sdd_integrity import SddContractError, _seal, _verify_seal, canonical_hash, freeze_contract
from apatch.strict_existing_signer import ExistingSignerRefused
from apatch.runtime.atomic_io import atomic_write_json

SCHEMA = "apatch.sdd.contract-intake-review.v1"
TOOL = "apatch_sdd_contract_intake"
PROFILE = ".apatch/sdd_verification_contract.json"
PENDING = ".apatch/sdd/contract-intake-pending.json"
MAX_FILES = 64
MAX_BYTES = 1_048_576
PREPARATION = {"functional_acceptance": False, "implementation_allowed": False, "owner_freeze_required": True}


def _root(root):
    selected = Path(root).absolute()
    if not selected.is_dir() or selected != selected.resolve():
        raise SddContractError("intake requires a canonical existing workspace")
    return selected


def _identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"-[A-Z0-9][A-Z0-9-]{1,95}", value):
        raise SddContractError("intake identifiers must retain their exact safe identity")
    return value


def _text(value, label):
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 4096:
        raise SddContractError(label + " must be bounded exact text")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SddContractError(label + " contains a control character")
    return value


def _json(data, label):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise SddContractError(label + " is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SddContractError(label + " must be an object")
    return value


def _context(root):
    raw = _capture(root, PROFILE)
    contract = _json(raw, "active intake profile")
    _verify_seal(contract, "active intake profile")
    if freeze_contract(contract) != contract or contract.get("implementation_allowed") is not True:
        raise SddContractError("intake requires an existing frozen owner profile")
    owner = _text((contract.get("authority") or {}).get("actor_id"), "active owner")
    pins = {PROFILE: _hash(raw)}
    for obligation in contract["obligations"]:
        for asset in obligation.get("judge_assets", []):
            relative, expected = asset["path"], asset["sha256"]
            actual = _hash(_capture(root, relative))
            if actual != expected:
                raise SddContractError("existing frozen intake judge drift")
            pins[relative] = actual
    # Read only bounded authority inputs, never the large TrustChain ledger.
    for relative in (".apatch/sdd/frozen", ".apatch/sdd/envelopes", ".apatch/locks"):
        directory = _path(root, relative)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise SddContractError("intake authority store is not a directory")
        for path in sorted(directory.rglob("*")):
            name = path.relative_to(root).as_posix()
            _path(root, name)
            if path.is_file():
                pins[name] = _hash(_capture(root, name))
            elif not path.is_dir():
                raise SddContractError("unsupported intake authority input")
    return {"active_contract_hash": contract["document_hash"], "authority_id": owner, "input_hashes": pins}


def _candidate(text, spec_id):
    value = _json(text, "own preparation candidate")
    allowed = {"schema", "spec_id", "source_mutation_count", "contract_request", "task_envelopes",
               "objective", "prepared_at"}
    if (set(value) - allowed or value.get("schema") != "apatch.studio.sdd-preparation.v1"
            or value.get("spec_id") != spec_id or type(value.get("source_mutation_count")) is not int
            or value["source_mutation_count"] != 0):
        raise SddContractError("candidate is not its own preparation-only scope")
    request, envelopes = value.get("contract_request"), value.get("task_envelopes")
    if not isinstance(request, dict) or not isinstance(envelopes, dict):
        raise SddContractError("preparation candidate requires typed request and envelopes")
    forbidden = {"authority", "status", "frozen_at", "document_hash", "implementation_allowed",
                 "source_mutation_count", "contract_hash", "owner", "certified", "role"}
    if forbidden & set(request):
        raise SddContractError("preparation candidate cannot preselect owner or frozen authority")
    for requirement, envelope in envelopes.items():
        if (not re.fullmatch(r"R[0-9]+", str(requirement)) or not isinstance(envelope, dict)
                or envelope.get("requirement") != spec_id + "#" + requirement
                or {"contract_hash", "document_hash", "authority", "implementation_allowed"} & set(envelope)):
            raise SddContractError("candidate cannot prebind another task or implementation authority")


def _package(root, spec_id, rfp_id, files, protected):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise SddContractError("intake requires one to 64 exact files")
    documents = {"docs/" + rfp_id + ".md", "docs/specs/" + spec_id + ".md"}
    candidate = ".apatch/sdd/prepared/" + spec_id + ".json"
    seen, rows, total, judges = set(), [], 0, 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise SddContractError("intake files accept only path and content")
        relative, text = item["path"], item["content"]
        target = _path(root, relative)
        if relative in seen or relative in protected:
            raise SddContractError("duplicate or already frozen intake path")
        seen.add(relative)
        if not isinstance(text, str):
            raise SddContractError("intake content must be exact UTF-8 text")
        try:
            data = text.encode("utf-8")
        except UnicodeError as exc:
            raise SddContractError("intake content is not UTF-8") from exc
        total += len(data)
        if not data or len(data) > MAX_BYTES or total > MAX_BYTES:
            raise SddContractError("intake exceeds its exact byte budget")
        parts = Path(relative).parts
        is_test = ("tests" in parts[:-1] and target.suffix in {".py", ".json"}
                   and not any(part.startswith(".") for part in parts)
                   and parts[0] not in {"apatch", "apatch_studio", "src", "services", "frontend"})
        if relative not in documents and relative != candidate and not is_test:
            raise SddContractError("intake may prepare only its documents, tests and own candidate")
        try:
            if relative == candidate:
                _candidate(text, spec_id)
            elif is_test and target.suffix == ".py":
                ast.parse(text, filename=relative)
                judges += 1
            elif is_test:
                json.loads(text)
            else:
                expected = rfp_id if relative == "docs/" + rfp_id + ".md" else spec_id
                if not re.match(r"^# " + re.escape(expected) + r"(?:\s|$)", text):
                    raise SddContractError("document heading must retain its fixed identifier")
                if expected == spec_id and not re.search(r"(?<![A-Z0-9-])" + re.escape(rfp_id) + r"(?![A-Z0-9-])", text):
                    raise SddContractError("SPEC must link its exact RFP")
        except (SyntaxError, ValueError, UnicodeError) as exc:
            raise SddContractError("invalid preparation document: " + relative) from exc
        before = _capture(root, relative, optional=True)
        rows.append({"path": relative, "content": text, "operation": "CREATE" if before is None else "REPLACE",
                     "before_hash": _hash(before) if before is not None else None,
                     "after_hash": _hash(data), "bytes": len(data),
                     "mode": stat.S_IMODE(target.stat().st_mode) if before is not None else 0o644,
                     "diff": "".join(difflib.unified_diff((before or b"").decode("utf-8").splitlines(True),
                                    text.splitlines(True), fromfile="a/" + relative, tofile="b/" + relative))})
    if not documents <= seen or not judges:
        raise SddContractError("intake needs its two exact documents and a Python judge")
    return sorted(rows, key=lambda row: row["path"]), total


def prepare_contract_intake(root, *, spec_id, rfp_id, actor_id, files, reason):
    root = _root(root)
    spec_id, rfp_id = _identifier(spec_id, "SPEC"), _identifier(rfp_id, "RFP")
    actor_id, reason = _text(actor_id, "preparation actor"), _text(reason, "preparation reason")
    context = _context(root)
    if actor_id == context["authority_id"]:
        raise SddContractError("the preparation requester cannot approve itself")
    rows, total = _package(root, spec_id, rfp_id, files, context["input_hashes"])
    return _seal({"schema": SCHEMA, "workspace": str(root), "spec_id": spec_id, "rfp_id": rfp_id,
                  "actor_id": actor_id, "reason": reason, **context, "files": rows,
                  "max_files": MAX_FILES, "max_bytes": MAX_BYTES, "total_bytes": total, **PREPARATION})


def _review(root, review):
    if not isinstance(review, dict):
        raise SddContractError("intake review must be one sealed object")
    _verify_seal(review, "intake review")
    if (review.get("schema") != SCHEMA or review.get("workspace") != str(root)
            or any(review.get(key) is not value for key, value in PREPARATION.items())):
        raise SddContractError("intake review is not this workspace's exact preparation")
    _identifier(review.get("spec_id"), "SPEC")
    _identifier(review.get("rfp_id"), "RFP")
    _text(review.get("actor_id"), "preparation actor")
    current = _context(root)
    if any(review.get(key) != value for key, value in current.items()):
        raise SddContractError("intake profile, frozen judges or authority inputs drifted")
    if review["actor_id"] == current["authority_id"]:
        raise SddContractError("intake requester and owner must remain distinct")


def _folder(review):
    return ".apatch/sdd/contract-intakes/" + review["document_hash"][7:] + "/"


def _receipt(root, review, stage):
    relative = _folder(review) + stage + ".json"
    if _capture(root, relative, optional=True) is None:
        return None
    saved = _read(root, relative)
    _verify_seal(saved, "intake receipt selector")
    binding, public = _identity(root)
    object_path = saved.get("object_path")
    if not isinstance(object_path, str) or not re.fullmatch(r"objects/op_[0-9]+\.json", object_path):
        raise SddContractError("intake receipt object locator refused")
    raw = _json(_capture(root, ".trustchain/" + object_path), "native intake receipt")
    record = raw.get("value") if isinstance(raw.get("value"), dict) else raw
    from trustchain.v2.chain_store import verify_record_signature
    from trustchain.v2.verifier import TrustChainVerifier
    verifier = TrustChainVerifier(base64.b64encode(public).decode(), binding.agent_id, max_age_seconds=None)
    payload = record.get("data") or {}
    expected = {"schema": SCHEMA, "review_hash": review["document_hash"], "stage": stage,
                "workspace": str(root), "spec_id": review["spec_id"], "rfp_id": review["rfp_id"],
                "authority_id": review["authority_id"], "actor_id": review["actor_id"],
                "active_contract_hash": review["active_contract_hash"],
                "input_hashes": review["input_hashes"],
                "files": {item["path"]: item["after_hash"] for item in review["files"]}, **PREPARATION,
                "native_signer": {"agent_id": binding.agent_id, "public_key_sha256": binding.public_key_sha256}}
    if (verify_record_signature(record, verifier) is not True or record.get("tool") != TOOL
            or record.get("key_id") != binding.agent_id or record.get("signature") != saved.get("signature")
            or canonical_hash(payload) != saved.get("payload_hash")
            or any(payload.get(key) != value for key, value in expected.items())):
        raise SddContractError("intake native receipt binding or Ed25519 signature differs")
    return payload


def _pending(root, review):
    path = _path(root, PENDING)
    if not path.exists():
        return None
    pending = _read(root, PENDING)
    _verify_seal(pending, "intake pending transaction")
    if pending.get("review_hash") != review["document_hash"]:
        raise SddContractError("another intake preparation transaction is incomplete")
    if pending.get("stage") not in {"prepared", "signing_approved", "approved", "signing_completed", "completed"}:
        raise SddContractError("intake preparation transaction requires reconciliation")
    return pending


def _commit(root, review, stage, extra):
    prior = _receipt(root, review, stage)
    if prior is not None:
        if any(prior.get(key) != value for key, value in extra.items()):
            raise SddContractError("native intake replay differs")
        return prior
    pending = _pending(root, review)
    if pending is None or pending["stage"] == "signing_" + stage:
        raise ExistingSignerRefused("EXISTING_SIGNER_RECONCILIATION_REQUIRED", reconciliation_required=True)
    binding, _ = _identity(root)
    from apatch.trustchain_helper import TrustChainHelper
    tc = TrustChainHelper(str(root), auto_init=False, existing_signer=binding)
    body = {"schema": SCHEMA, "review_hash": review["document_hash"], "stage": stage,
            "workspace": str(root), "spec_id": review["spec_id"], "rfp_id": review["rfp_id"],
            "authority_id": review["authority_id"], "actor_id": review["actor_id"],
            "active_contract_hash": review["active_contract_hash"], "input_hashes": review["input_hashes"],
            "files": {item["path"]: item["after_hash"] for item in review["files"]}, **PREPARATION,
            "native_signer": {"agent_id": binding.agent_id, "public_key_sha256": binding.public_key_sha256}, **extra}
    atomic_write_json(str(_path(root, PENDING)),
                      _seal({"review_hash": review["document_hash"], "stage": "signing_" + stage}))
    if not tc.commit_action(TOOL, body):
        raise ExistingSignerRefused("EXISTING_SIGNER_RECEIPT_INVALID", reconciliation_required=True)
    evidence = tc.last_commit_evidence
    object_path = evidence.get("object_path")
    if not isinstance(object_path, str) or not re.fullmatch(r"objects/op_[0-9]+\.json", object_path):
        raise ExistingSignerRefused("EXISTING_SIGNER_RECEIPT_INVALID", reconciliation_required=True)
    raw = _json(_capture(root, ".trustchain/" + object_path), "persisted native intake receipt")
    record = raw.get("value") if isinstance(raw.get("value"), dict) else raw
    _immutable(root, _folder(review) + stage + ".json", _seal({"object_path": object_path,
                   "signature": evidence.get("signature"), "payload_hash": canonical_hash(record.get("data"))}))
    result = _receipt(root, review, stage)
    if result is None:
        raise ExistingSignerRefused("EXISTING_SIGNER_RECEIPT_INVALID", reconciliation_required=True)
    atomic_write_json(str(_path(root, PENDING)), _seal({"review_hash": review["document_hash"], "stage": stage}))
    return result


def _outputs(root, review):
    expected = {item["path"]: item["after_hash"] for item in review["files"]}
    for relative, digest in expected.items():
        data = _capture(root, relative)
        if _hash(data) != digest:
            raise SddContractError("completed intake output bytes drifted")
    return expected


def review_contract_intake(root, review):
    root = _root(root)
    _review(root, review)
    pending = _pending(root, review)
    result = _receipt(root, review, "completed")
    projection = {"status": "awaiting_approval", "snapshot": review["document_hash"], "review": review, **PREPARATION}
    if result is not None:
        _receipt(root, review, "approved") or _missing_approval()
        outputs = _outputs(root, review)
        expected = {"ok": True, "review_hash": review["document_hash"], "files": outputs, **PREPARATION}
        if result.get("result") != expected:
            raise SddContractError("completed intake receipt result differs")
        return {**projection, "status": "prepared", "result": expected}
    if pending is not None:
        raise SddContractError("intake preparation transaction remains incomplete")
    return projection


def _missing_approval():
    raise SddContractError("intake completion has no native approval")


def _install(root, item):
    """Create never overwrites. Replacements require observed bytes under leases."""
    target = _path(root, item["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    _path(root, item["path"])
    parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".intake-" + secrets.token_hex(16)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, item["mode"], dir_fd=parent_fd)
        try:
            data = item["content"].encode("utf-8")
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            current = _capture(root, item["path"], optional=True)
            if (_hash(current) if current is not None else None) != item["before_hash"]:
                raise SddContractError("intake input changed immediately before install")
            if item["operation"] == "CREATE":
                try:
                    os.link(temporary, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                except FileExistsError as exc:
                    raise SddContractError("intake CREATE cannot overwrite a new file") from exc
            else:
                os.replace(temporary, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(parent_fd)


def approve_contract_intake(root, review, *, owner, expected_snapshot):
    root = _root(root)
    _review(root, review)
    if (expected_snapshot != review["document_hash"] or not isinstance(owner, dict)
            or owner.get("id") != review["authority_id"] or owner.get("certified") is not True
            or owner.get("id") == review["actor_id"]):
        raise SddContractError("the current certified owner must approve the exact intake snapshot")
    assert_can_freeze(root)
    pending = _pending(root, review)
    completed = _receipt(root, review, "completed")
    if completed is not None:
        return review_contract_intake(root, review)["result"]
    if pending is not None:
        # A crash during signing is ambiguous. It is not a fresh owner decision.
        raise SddContractError("intake preparation transaction requires native reconciliation")
    files = [{"path": item["path"], "content": item["content"]} for item in review["files"]]
    if prepare_contract_intake(root, spec_id=review["spec_id"], rfp_id=review["rfp_id"],
                             actor_id=review["actor_id"], files=files, reason=review["reason"]) != review:
        raise SddContractError("intake inputs changed after review")
    # Resolving a missing signer must not even create transaction metadata.
    _identity(root)
    from apatch.path_leases import PathLeaseConflict, acquire_path_lease, release_path_leases
    with freeze_lock(root):
        _review(root, review)
        assert_can_freeze(root)
        if _pending(root, review) is not None:
            raise SddContractError("another intake transaction appeared")
        completed = _receipt(root, review, "completed")
        if completed is not None:
            return review_contract_intake(root, review)["result"]
        if prepare_contract_intake(root, spec_id=review["spec_id"], rfp_id=review["rfp_id"],
                                 actor_id=review["actor_id"], files=files, reason=review["reason"]) != review:
            raise SddContractError("intake input drift during owner approval")
        lease_owner = "authority-intake:" + review["document_hash"]
        try:
            acquire_path_lease(str(root), [item["path"] for item in review["files"]],
                               tool=TOOL, governed_session_id=lease_owner, max_seconds=300)
        except PathLeaseConflict as exc:
            raise SddContractError("intake path lease conflict") from exc
        try:
            # Recheck after acquiring leases, before the first signature.
            _review(root, review)
            if prepare_contract_intake(root, spec_id=review["spec_id"], rfp_id=review["rfp_id"],
                                     actor_id=review["actor_id"], files=files, reason=review["reason"]) != review:
                raise SddContractError("intake input drift after lease acquisition")
            _immutable(root, _folder(review) + "review.json", review)
            atomic_write_json(str(_path(root, PENDING)), _seal({"review_hash": review["document_hash"], "stage": "prepared"}))
            _commit(root, review, "approved", {"owner": owner})
            _review(root, review)
            for item in review["files"]:
                current = _capture(root, item["path"], optional=True)
                if (_hash(current) if current is not None else None) != item["before_hash"]:
                    raise SddContractError("intake input drift before installation")
            for item in review["files"]:
                _install(root, item)
            result = {"ok": True, "review_hash": review["document_hash"], "files": _outputs(root, review), **PREPARATION}
            _commit(root, review, "completed", {"result": result})
            _path(root, PENDING).unlink()
            return result
        finally:
            release_path_leases(str(root), governed_session_id=lease_owner)
