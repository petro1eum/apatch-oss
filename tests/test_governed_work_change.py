from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import governed_work as G


class Provider:
    def __init__(self, private=None):
        self.private = private or Ed25519PrivateKey.generate()

    def get_public_key(self):
        return self.private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, payload):
        return self.private.sign(payload)


def write_spec(root: Path):
    path = root / "docs" / "specs" / "SPEC-WORK-1.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# SPEC-WORK-1 -- Work\n\n"
        "## R1 First\n\n(verify: true)\n\n"
        "## R2 Second\n\n(verify: true)\n",
        encoding="utf-8",
    )
    return path


def prepare(root: Path, provider: Provider, *, purpose="Implement exact work"):
    spec = write_spec(root) if not (root / "docs/specs/SPEC-WORK-1.md").exists() else root / "docs/specs/SPEC-WORK-1.md"
    return G.prepare_change(
        str(root),
        tenant_id="tenant-a",
        project_group_id="tcpg_" + "1" * 32,
        work_program_id="tcwp_" + "2" * 32,
        work_program_hash="sha256:" + "3" * 64,
        spec_id="SPEC-WORK-1",
        spec_path=str(spec),
        requirement_ids=["R2", "R1"],
        purpose=purpose,
        issued_at="2026-08-30T00:00:00Z",
        key_provider=provider,
    )


def test_change_is_signed_deterministic_and_purpose_private(tmp_path):
    provider = Provider()
    first = prepare(tmp_path, provider)
    second = prepare(tmp_path, provider)
    assert first["change"] == second["change"]
    assert first["stored"] is True
    assert second["stored"] is False
    change = first["change"]
    assert change["change_id"].startswith("apchg_")
    assert len(change["spec_hash"]) == len("sha256:") + 64
    assert [r["requirement_id"] for r in change["requirement_refs"]] == ["R1", "R2"]
    wire = json.dumps(change)
    assert "Implement exact work" not in wire
    assert "purpose_hash" in change
    key_id = G.signer_key_id(provider)
    assert G.validate_change(
        change, trusted_actor_keys={key_id: provider.get_public_key()}
    ) == change


def test_change_tamper_and_unknown_field_fail_closed(tmp_path):
    provider = Provider()
    result = prepare(tmp_path, provider)
    tampered = dict(result["change"])
    tampered["work_program_hash"] = "sha256:" + "4" * 64
    with pytest.raises(G.GovernedWorkError, match="signature verification"):
        G.validate_change(
            tampered,
            trusted_actor_keys={G.signer_key_id(provider): provider.get_public_key()},
        )
    unknown = dict(result["change"])
    unknown["prompt"] = "leak"
    with pytest.raises(G.GovernedWorkError, match="keys mismatch"):
        G.validate_change(unknown)


def test_strict_json_duplicate_keys_and_nonfinite_values_are_rejected():
    with pytest.raises(G.GovernedWorkError, match="duplicate JSON key"):
        G.strict_json_loads('{"a":1,"a":2}')
    with pytest.raises(G.GovernedWorkError):
        G.canonical_bytes({"value": float("nan")})


def test_spec_hash_normalizes_trailing_whitespace_only():
    left = "# S\n\n## R1 X   \nbody\n"
    right = "# S\n\n## R1 X\nbody"
    assert G.full_spec_hash(left) == G.full_spec_hash(right)
