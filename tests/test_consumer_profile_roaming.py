"""Generated consumer agent sections include local roaming safety."""

from apatch.consumer_profiles import get_profile


def test_all_consumer_profiles_include_roaming_contract():
    for profile in ("default", "frontend", "sqlalchemy", "elastic", "prisma", "django", "cosmos"):
        section = get_profile(profile)["agents_section"]
        assert 'target_dir="@alias"' in section
        assert "apatch_workspace_inspect" in section
        assert "raw absolute cross-workspace path" in section
