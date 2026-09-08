from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import governed_work as G


class Provider:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()

    def get_public_key(self):
        return self.private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, payload):
        return self.private.sign(payload)


def change(root: Path, actor: Provider):
    spec = root / "docs/specs/SPEC-WORK-1.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "# SPEC-WORK-1 -- Work\n\n## R1 Exact\n\n(verify: true)\n",
        encoding="utf-8",
    )
    return G.prepare_change(
        str(root),
        tenant_id="tenant-a",
        project_group_id="tcpg_" + "1" * 32,
        work_program_id="tcwp_" + "2" * 32,
        work_program_hash="sha256:" + "3" * 64,
        spec_id="SPEC-WORK-1",
        spec_path=str(spec),
        requirement_ids=["R1"],
        purpose="Implement exact work",
        issued_at="2026-08-30T00:00:00Z",
        key_provider=actor,
    )["change"]


def _platform_sign(body, platform: Provider, purpose: str):
    payload = (
        b"TrustChain-Governed-Work\x00v1\x00"
        + purpose.encode("ascii")
        + b"\x00"
        + G.canonical_bytes(body)
    )
    return {
        **body,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": G.signer_key_id(platform),
            "value": G._b64url_encode(platform.sign(payload)),
        },
    }


def binding(
    local_change,
    platform: Provider,
    *,
    issued_at="2026-08-30T00:01:00Z",
    authority_version=1,
):
    body = {
        "schema": G.SOURCE_BINDING_SCHEMA,
        "binding_id": "tcpsb_" + "4" * 32,
        "tenant_id": local_change["tenant_id"],
        "project_group_id": local_change["project_group_id"],
        "source_kind": local_change["source_kind"],
        "work_program_id": local_change["work_program_id"],
        "work_program_hash": local_change["work_program_hash"],
        "context_release_id": local_change["context_release_id"],
        "context_release_manifest_hash": local_change[
            "context_release_manifest_hash"
        ],
        "execution_system": local_change["execution_system"],
        "change_id": local_change["change_id"],
        "change_hash": G.document_hash(local_change),
        "spec_id": local_change["spec_id"],
        "spec_hash": local_change["spec_hash"],
        "requirement_refs": local_change["requirement_refs"],
        "actor_ref": "agent_binding:tcpgab_" + "5" * 32,
        "authority_version": authority_version,
        "issued_at": issued_at,
    }
    return _platform_sign(
        body,
        platform,
        G.PLATFORM_SOURCE_BINDING_PURPOSE,
    )


def test_platform_binding_is_verified_stored_and_returns_session_artifact(tmp_path):
    actor, platform = Provider(), Provider()
    local_change = change(tmp_path, actor)
    signed = binding(local_change, platform, authority_version=7)
    keys = {G.signer_key_id(platform): platform.get_public_key()}
    assert signed["authority_version"] == 7
    result = G.store_project_source_binding(
        str(tmp_path),
        signed,
        change=local_change,
        trusted_authority_keys=keys,
        status={"source_verified": "verified"},
    )
    assert result["stored"] is True
    assert result["binding_hash"] == G.document_hash(signed)
    assert result["session_artifact"] == (
        f"project-source-binding:{signed['binding_id']}@{G.document_hash(signed)}"
    )
    replay = G.store_project_source_binding(
        str(tmp_path),
        signed,
        change=local_change,
        trusted_authority_keys=keys,
    )
    assert replay["stored"] is False


def test_binding_mismatch_unknown_signer_and_revocation_fail_closed(tmp_path):
    actor, platform = Provider(), Provider()
    local_change = change(tmp_path, actor)
    signed = binding(local_change, platform)
    keys = {G.signer_key_id(platform): platform.get_public_key()}

    mismatch = dict(signed)
    mismatch["spec_hash"] = "sha256:" + "9" * 64
    mismatch = _platform_sign(
        {k: v for k, v in mismatch.items() if k != "signature"},
        platform,
        G.PLATFORM_SOURCE_BINDING_PURPOSE,
    )
    with pytest.raises(G.GovernedWorkError, match="mismatch: spec_hash"):
        G.validate_project_source_binding(
            mismatch, change=local_change, trusted_authority_keys=keys
        )

    with pytest.raises(G.GovernedWorkError, match="untrusted signing key"):
        G.validate_project_source_binding(
            signed, change=local_change, trusted_authority_keys={}
        )

    with pytest.raises(G.GovernedWorkError, match="not active: revoked"):
        G.validate_project_source_binding(
            signed,
            change=local_change,
            trusted_authority_keys=keys,
            status={"source_verified": "revoked"},
        )


def test_same_binding_id_cannot_be_rebound_to_different_envelope(tmp_path):
    actor, platform = Provider(), Provider()
    local_change = change(tmp_path, actor)
    keys = {G.signer_key_id(platform): platform.get_public_key()}
    first = binding(local_change, platform)
    G.store_project_source_binding(
        str(tmp_path),
        first,
        change=local_change,
        trusted_authority_keys=keys,
    )
    second = binding(
        local_change, platform, issued_at="2026-08-30T00:02:00Z"
    )
    with pytest.raises(G.GovernedWorkError, match="immutable id"):
        G.store_project_source_binding(
            str(tmp_path),
            second,
            change=local_change,
            trusted_authority_keys=keys,
        )
