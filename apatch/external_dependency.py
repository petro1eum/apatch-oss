"""Read authenticated native lineage without signing or initializing a workspace.

The name preserves the narrow reader imported by Studio. This module does not
grant external dependencies or author authority. PKIX chain trust remains with
the owning authority; a configured local certificate binds this workspace's
subject and public key. Certificate dates do not alter archived byte authenticity.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path

from apatch.sdd_preparation_io import _path
from apatch.sdd_integrity import SddContractError

MAX_BYTES = 1_048_576


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _json_file(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("native lineage input exceeds the byte limit")
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _text(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and value == value.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _public_file(source):
    if not isinstance(source, str) or not source or source != source.strip():
        raise ValueError("exact configured public material is required")
    path = Path(source)
    if not path.is_absolute() or path != path.resolve() or not path.is_file():
        raise ValueError("configured public material path is unavailable or symbolic")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("public material exceeds the byte limit")
    return data


def _public_key(source):
    if not isinstance(source, str) or not source or source != source.strip():
        raise ValueError("configured public key is required")
    # A valid raw key may itself start with '/', which is also an absolute-path
    # prefix. Decode the closed 32-byte literal shape before treating it as a file.
    try:
        public = base64.b64decode(source, validate=True)
        if len(public) == 32:
            return public
    except ValueError:
        pass
    public = base64.b64decode(_public_file(source).strip(), validate=True)
    if len(public) != 32:
        raise ValueError("an exact raw Ed25519 public key is required")
    return public


def _workspace_verifier(root):
    """Resolve public inputs only; never load a PEM key or a signing provider."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.x509.oid import NameOID
    from trustchain.v2.verifier import TrustChainVerifier

    try:
        stored = _json_file(_path(root, ".apatch/agent-identity.json"))
        if not isinstance(stored, dict) or not _text(stored.get("agent_id")):
            raise ValueError("a stored workspace subject is required")
        agent_id = stored["agent_id"]
        backend = stored.get("key_backend", "pem")
        if backend not in {"pem", "command"}:
            raise ValueError("unregistered workspace key backend")
        if backend == "command" and not _text(stored.get("sign_cmd")):
            raise ValueError("incomplete registered command provider")
        if backend == "pem" and not _text(stored.get("key")):
            raise ValueError("incomplete registered PEM provider")
        for variable, expected in (("APATCH_AGENT_ID", agent_id), ("APATCH_KEY_BACKEND", backend)):
            if os.environ.get(variable) and os.environ[variable] != expected:
                raise ValueError("environment does not match stored workspace identity")
        if os.environ.get("APATCH_AGENT_CERT") and os.environ["APATCH_AGENT_CERT"] != stored.get("cert"):
            raise ValueError("environment certificate is not the stored workspace certificate")
        public = _public_key(stored["public_key"]) if stored.get("public_key") is not None else None
        if stored.get("cert") is not None:
            cert = x509.load_pem_x509_certificate(_public_file(stored["cert"]))
            names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            key = cert.public_key()
            if len(names) != 1 or names[0].value != agent_id or not isinstance(key, Ed25519PublicKey):
                raise ValueError("workspace certificate subject or algorithm differs")
            certified = key.public_bytes_raw()
            if public is not None and public != certified:
                raise ValueError("workspace certificate and configured public key differ")
            public = certified
        if backend == "command" and stored.get("public_key") is None:
            raise ValueError("command provider has no published public key")
        if public is None:
            raise ValueError("PEM provider has no public verification material")
        if os.environ.get("APATCH_AGENT_PUBKEY") and _public_key(os.environ["APATCH_AGENT_PUBKEY"]) != public:
            raise ValueError("environment public key differs from stored workspace identity")
        return agent_id, TrustChainVerifier(base64.b64encode(public).decode("ascii"), agent_id,
                                            max_age_seconds=None)
    except Exception as exc:
        raise SddContractError("workspace public verification identity is unavailable or invalid") from exc


def _ledger_paths(root):
    """Inventory only this exact workspace; no helper construction or ancestor scan."""
    objects = _path(root, ".trustchain/objects")
    chain = _path(root, ".trustchain/chain")
    result = []
    if objects.is_dir():
        result.extend(path.relative_to(root).as_posix() for path in objects.rglob("*.json"))
    if chain.is_dir():
        result.extend(path.relative_to(root).as_posix() for path in chain.glob("block_*.json"))
    return sorted(result)


def _signed_rows(root, *, tool_ids=None, session_id=None, snapshot=None):
    """Return only canonical SDK tool/data authenticated by the stored workspace key.

    ``id`` and ``object_path`` are storage locators, not signed authority claims.
    Unsigned aliases never determine the tool, payload, metadata or filters.
    Legacy records without the exact signed timestamp are not evidence.
    """
    from trustchain.v2.chain_store import verify_record_signature

    selected = Path(root).absolute()
    if not selected.is_dir() or selected != selected.resolve():
        raise SddContractError("a canonical existing workspace is required")
    if tool_ids is not None:
        if not isinstance(tool_ids, (set, frozenset, list, tuple)) or any(not _text(item) for item in tool_ids):
            raise SddContractError("tool filters must be an exact collection of tool names")
        tool_ids = frozenset(tool_ids)
    if any(value is not None and not _text(value) for value in (session_id, snapshot)):
        raise SddContractError("lineage filters must be exact text")
    agent_id, verifier = _workspace_verifier(selected)
    result = []
    for relative in _ledger_paths(selected):
        try:
            raw = _json_file(_path(selected, relative))
            if not isinstance(raw, dict):
                continue
            record = raw.get("value") if isinstance(raw.get("value"), dict) else raw
            tool, payload = record.get("tool"), record.get("data")
            metadata = record.get("metadata")
            timestamp = record.get("response_timestamp")
            if (not _text(tool) or not isinstance(payload, dict)
                    or metadata is not None and not isinstance(metadata, dict)
                    or type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                    or record.get("key_id") != agent_id
                    or not isinstance(record.get("signature"), str)
                    or len(base64.b64decode(record["signature"], validate=True)) != 64):
                continue
            if tool_ids is not None and tool not in tool_ids:
                continue
            if session_id is not None and payload.get("governed_session_id") != session_id:
                continue
            if snapshot is not None and payload.get("snapshot") != snapshot:
                continue
            if verify_record_signature(record, verifier) is not True:
                continue
            result.append({"id": Path(relative).stem, "object_path": relative.removeprefix(".trustchain/"),
                           "tool_id": tool, "payload": payload, "metadata": metadata,
                           "signature": record["signature"], "key_id": agent_id,
                           "algorithm": "Ed25519", "timestamp": timestamp})
        except (OSError, ValueError, TypeError, OverflowError, SddContractError):
            continue
    return result
