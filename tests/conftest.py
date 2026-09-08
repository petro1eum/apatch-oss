"""Pytest configuration and shared fixtures for the apatch test suite."""
from __future__ import annotations

import os
import shutil

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: large monorepo / perf regression tests (ADR-001 §7)",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("APATCH_SKIP_SLOW", "").strip().lower() in ("1", "true", "yes"):
        skip = pytest.mark.skip(reason="APATCH_SKIP_SLOW is set")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def require_tc():
    """Skip a test unless the real ``tc`` CLI and ``trustchain`` library exist."""
    if shutil.which("tc") is None:
        pytest.skip("requires the real `tc` CLI (TrustChain not installed)")
    try:
        import trustchain  # noqa: F401
    except Exception:
        pytest.skip("requires the `trustchain` Python library (not on PyPI)")
