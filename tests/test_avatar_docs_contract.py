from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_avatar_canon_keeps_surface_and_authority_boundaries() -> None:
    canon = (ROOT / "docs" / "AVATAR-ARCHITECTURE-CANON.md").read_text(
        encoding="utf-8"
    )
    utility = (ROOT / "docs" / "AVATAR-UTILITY-CONTRACT.md").read_text(
        encoding="utf-8"
    )
    evidence_spec = (
        ROOT / "docs" / "specs" / "SPEC-AVATAR-EVIDENCE-1.md"
    ).read_text(encoding="utf-8")
    combined = "\n".join((canon, utility, evidence_spec))

    for phrase in (
        "Версия 1.3",
        "natural counterparty",
        "purpose-bound",
        "canonical web",
        "native/mobile",
        "Association сертифицирует по стандарту",
    ):
        assert phrase in combined, f"Avatar canon contract is missing: {phrase}"

    for stale in (
        "выпускается ассоциацией",
        "association-signed",
        "signed by the independent association",
        "L4 · HC Tracker |",
    ):
        assert stale not in combined, f"Stale Avatar doctrine remains: {stale}"


def test_avatar_wiring_history_points_to_current_contract() -> None:
    wiring = (ROOT / "docs" / "RFP-028-avatar-wiring.md").read_text(
        encoding="utf-8"
    )
    contract_spec = (
        ROOT / "docs" / "specs" / "SPEC-AVATAR-CONTRACT-1.md"
    ).read_text(encoding="utf-8")

    for phrase in (
        "Reconciliation amendment (2026-08-28)",
        "avatar-contract` is a versioned `0.5.0` dependency",
        "Current ContributionEvent v3",
        "test_round_trip_current_version",
        "live owner/counterparty delivery proof",
    ):
        assert phrase in f"{wiring}\n{contract_spec}"

    assert "test_round_trip_v2" not in contract_spec
