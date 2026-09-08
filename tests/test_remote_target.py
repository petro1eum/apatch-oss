import json

from apatch.remote.errors import RemoteTaskError
from apatch.remote.policy import resolve_remote_target
from apatch.remote.target import is_remote_target, parse_remote_target


def test_parse_remote_target_accepts_ssh_uri():
    target = parse_remote_target("ssh://example-search-host/srv/example/search-workspace")

    assert target.host == "example-search-host"
    assert target.path == "/srv/example/search-workspace"
    assert target.uri == "ssh://example-search-host/srv/example/search-workspace"
    assert len(target.workspace_id) == 16


def test_parse_remote_target_accepts_scp_style_absolute_path():
    target = parse_remote_target("ubuntu@10.0.0.4:/srv/repo/")

    assert target.host == "ubuntu@10.0.0.4"
    assert target.path == "/srv/repo"
    assert target.uri == "ssh://ubuntu@10.0.0.4/srv/repo"


def test_parse_remote_target_rejects_local_paths():
    try:
        parse_remote_target("/Users/edcher/Documents/GitHub/apatch")
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_TARGET_INVALID"
    else:
        raise AssertionError("local paths must not parse as remote targets")

    assert is_remote_target("/Users/edcher/Documents/GitHub/apatch") is False


def test_remote_policy_resolves_alias_without_leaking_host_path(tmp_path):
    policy_dir = tmp_path / ".apatch"
    policy_dir.mkdir()
    (policy_dir / "remote.json").write_text(
        json.dumps(
            {
                "allowed_hosts": ["example-*"],
                "allowed_roots": ["/srv/example/*"],
                "targets": {
                    "search-example": {
                        "host": "example-search-host",
                        "path": "/srv/example/search-workspace",
                        "display": "Search main",
                        "python": "/opt/py/bin/python3",
                        "ssh_args": ["-J", "bastion"],
                        "timeout_sec": 42,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    target = resolve_remote_target("search-example", policy_root=str(tmp_path))

    assert target.host == "example-search-host"
    assert target.path == "/srv/example/search-workspace"
    assert target.redact is True
    assert target.python == "/opt/py/bin/python3"
    assert target.ssh_args == ("-J", "bastion")
    assert target.timeout_sec == 42
    exposed = target.as_dict()
    assert exposed["alias"] == "search-example"
    assert exposed["display"] == "Search main"
    assert exposed["redacted"] is True
    assert "host" not in exposed
    assert "path" not in exposed
    assert "uri" not in exposed


def test_remote_policy_rejects_unknown_alias(tmp_path):
    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text('{"targets": {}}', encoding="utf-8")

    try:
        resolve_remote_target("missing-prod", policy_root=str(tmp_path))
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_ALIAS_NOT_FOUND"
    else:
        raise AssertionError("unknown aliases must fail closed")


def test_remote_policy_enforces_host_and_root_allowlists(tmp_path):
    (tmp_path / ".apatch").mkdir()
    (tmp_path / ".apatch" / "remote.json").write_text(
        json.dumps(
            {
                "allowed_hosts": ["example-search-host"],
                "allowed_roots": ["/srv/allowed/*"],
                "targets": {"bad": {"host": "example-search-host", "path": "/home/ubuntu/project"}},
            }
        ),
        encoding="utf-8",
    )

    try:
        resolve_remote_target("bad", policy_root=str(tmp_path))
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_ROOT_DENIED"
    else:
        raise AssertionError("root allowlist must be enforced")


def test_r5_typed_remote_errors():
    from apatch.remote import errors as E

    required = {
        "REMOTE_UNREACHABLE",
        "REMOTE_APATCH_MISSING",
        "REMOTE_VERSION_SKEW",
        "REMOTE_WORKSPACE_NOT_GIT",
        "REMOTE_ROOT_DENIED",
        "REMOTE_PROTOCOL_ERROR",
        "WORKSPACE_NOT_LOCAL",
    }
    assert required <= set(E.REMOTE_ERROR_TYPES)
    for name in required:
        assert getattr(E, name) == name

    result = RemoteTaskError(
        E.REMOTE_UNREACHABLE,
        "ssh dial failed",
        recoverable=True,
        recommended_action="Check SSH reachability and retry.",
    ).to_result()
    assert result["ok"] is False
    assert result["error_type"] == "REMOTE_UNREACHABLE"
    assert result["recoverable"] is True
    assert result["recommended_action"] == "Check SSH reachability and retry."
    state = result["state_update"]
    assert state["phase"] == "failed"
    assert state["next_action"]
    assert state["risk_level"] in {"medium", "high"}


def test_r3_workspace_not_local_guard(tmp_path):
    from apatch.remote.target import guard_local_workspace

    missing = "/srv/example/search-workspace"
    try:
        guard_local_workspace(missing, local_exists=lambda _p: False)
    except RemoteTaskError as exc:
        assert exc.error_type == "WORKSPACE_NOT_LOCAL"
        assert "ssh://" in (exc.recommended_action or "")
    else:
        raise AssertionError("remote-looking missing path must not pass as local")

    guard_local_workspace(
        "ssh://example-search-host/srv/example/x", local_exists=lambda _p: False
    )
    guard_local_workspace(str(tmp_path), local_exists=lambda _p: True)


def test_r1_parse_remote_targets():
    ssh = parse_remote_target("ssh://example-search-host/srv/example/X")
    assert ssh.host == "example-search-host"
    assert ssh.path == "/srv/example/X"
    assert ssh.uri == "ssh://example-search-host/srv/example/X"
    assert len(ssh.workspace_id) == 16

    scp = parse_remote_target("ubuntu@10.0.0.4:/srv/repo/")
    assert scp.host == "ubuntu@10.0.0.4"
    assert scp.path == "/srv/repo"

    for bad in ("host:relative/path", "just-a-name", "/Users/edcher/local"):
        try:
            parse_remote_target(bad)
        except RemoteTaskError as exc:
            assert exc.error_type == "REMOTE_TARGET_INVALID"
        else:
            raise AssertionError("%r must not parse as a remote target" % bad)

    assert is_remote_target("/Users/edcher/Documents/GitHub/apatch") is False


def test_r2_remote_allowlist_policy(tmp_path):
    from apatch.remote.policy import load_remote_policy, resolve_remote_target

    policy_dir = tmp_path / ".apatch"
    policy_dir.mkdir()
    (policy_dir / "remote.json").write_text(
        json.dumps(
            {
                "allowed_hosts": ["example-*"],
                "allowed_roots": ["/srv/example/*"],
                "targets": {
                    "ok": {"host": "example-search-host", "path": "/srv/example/X"},
                    "bad-host": {"host": "evil", "path": "/srv/example/X"},
                    "bad-root": {"host": "example-search-host", "path": "/etc/secret"},
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = load_remote_policy(str(tmp_path))
    assert cfg["allowed_hosts"] == ["example-*"]
    assert "/srv/example/*" in cfg["allowed_roots"]

    target = resolve_remote_target("ok", policy_root=str(tmp_path))
    assert target.host == "example-search-host"

    try:
        resolve_remote_target("bad-host", policy_root=str(tmp_path))
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_HOST_DENIED"
    else:
        raise AssertionError("host allowlist must be enforced")

    try:
        resolve_remote_target("bad-root", policy_root=str(tmp_path))
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_ROOT_DENIED"
    else:
        raise AssertionError("root allowlist must be enforced")


def test_r4_no_shell_interpolation_helpers():
    from apatch.remote.target import build_remote_target
    from apatch.remote.transport import FakeRemoteTransport

    injection = "/srv/repo; rm -rf / #"
    target = build_remote_target("example-search-host", "/srv/repo", alias="x")
    transport = FakeRemoteTransport()
    transport.call(
        target,
        "apatch_verify_run",
        {"verify": ["pytest", injection], "needle": injection},
    )

    assert len(transport.calls) == 1
    recorded = transport.calls[0]
    assert recorded["operation"] == "apatch_verify_run"
    # argv stays a list and the dangerous payload is preserved verbatim as data,
    # never concatenated into a shell command by core helpers.
    assert recorded["arguments"]["verify"] == ["pytest", injection]
    assert recorded["arguments"]["needle"] == injection
    assert isinstance(recorded["arguments"], dict)


def test_r0_remote_core_rfp_coverage():
    import os
    import pytest

    pytest.importorskip("apatch.rfp_coverage")
    from apatch.rfp_coverage import rfp_spec_coverage

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(
        os.path.join(root, "docs", "RFP-029-remote-ssh-workspaces.md"),
        encoding="utf-8",
    ) as f:
        rfp = f.read()
    with open(
        os.path.join(root, "docs", "specs", "SPEC-REMOTE-SSH-CORE-1.md"),
        encoding="utf-8",
    ) as f:
        spec = f.read()
    out = rfp_spec_coverage(
        rfp, spec, rfp_id="RFP-029", spec_id="SPEC-REMOTE-SSH-CORE-1"
    )
    assert out["passed"] is True
    assert not out.get("gaps")
