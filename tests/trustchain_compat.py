"""Skip guards for trustchain API differences (2.4 vs 3.1+)."""

from __future__ import annotations

import inspect

import pytest


def require_trustchain_key_provider() -> None:
    """Skip when TrustChainConfig lacks key_provider (trustchain < 3.1)."""
    pytest.importorskip("trustchain")
    from trustchain import TrustChainConfig

    if "key_provider" not in inspect.signature(TrustChainConfig.__init__).parameters:
        pytest.skip("trustchain>=3.1 required (TrustChainConfig.key_provider)")


def require_trustchain_public_key_binding() -> None:
    """Skip when issue_agent_cert lacks public_key_b64 (trustchain < 3.1)."""
    pytest.importorskip("trustchain")
    from trustchain.v2.x509_pki import TrustChainCA

    if "public_key_b64" not in inspect.signature(
        TrustChainCA.issue_agent_cert
    ).parameters:
        pytest.skip("trustchain>=3.1 required (issue_agent_cert.public_key_b64)")


def require_trustchain_kms() -> None:
    """Skip when trustchain.kms is unavailable (trustchain < 3.1)."""
    pytest.importorskip("trustchain.kms")