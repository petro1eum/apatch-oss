from apatch.strip import StripSpec
from apatch.wiring_profiles import build_integration_hints, build_typescript_wiring_hints


def test_route_redirect_in_wiring():
    spec = StripSpec(
        start="// a",
        until="// b",
        route_from="/legacy/planning",
        route_to="/account-planning",
        module_kind="hook",
    )
    hints = build_typescript_wiring_hints(
        spec=spec,
        filename="x.fragment.txt",
        module_out_path="src/hooks/useX.ts",
        label="handlers",
    )
    assert hints["route_redirect"] == "/legacy/planning → /account-planning"

    ih = build_integration_hints(
        profile="typescript",
        spec=spec,
        out_dir="extracted",
        verify_command=None,
        export_paths=[],
    )
    assert ih["route_from"] == "/legacy/planning"
    assert ih["route_to"] == "/account-planning"
