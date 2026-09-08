from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_runtime_and_studio_delivery_boundaries():
    matrix = (ROOT / "docs/product-matrix.md").read_text(encoding="utf-8")
    studio = (ROOT / "docs/apatch-studio.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "APatch OSS runtime" in matrix
    assert "| **APatch Studio OSS** |" in matrix
    assert "| **APatch Studio Pro** |" in matrix
    assert "full professional MCP API" in matrix
    assert "APatch Studio OSS" in studio
    assert "APatch Studio Pro" in studio
    assert "TrustChain Cowork" in studio
    assert "APatch Studio OSS" in readme
    assert "APatch Studio Pro" in readme
    assert "Standalone operation" in matrix
    assert "does not require an account or subscription" in matrix
    assert "No hosted Pro implementation" in matrix
    assert "technical attestation is not business acceptance" in matrix
