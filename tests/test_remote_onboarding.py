"""Product-neutral remote onboarding and release handoff regressions."""

import json

from click.testing import CliRunner

from apatch.cli import cli
from apatch.remote.errors import RemoteTaskError
from apatch.remote.handoff import (
    SshArchiveTransport,
    execute_source_handoff,
    plan_release_bundle_handoff,
    plan_source_handoff,
)
from apatch.remote.onboarding import build_remote_config
from apatch.remote.target import build_remote_target
from apatch.remote.policy import resolve_remote_target
from apatch.remote.services import execute_service_action, plan_service_action


def test_build_remote_config_includes_health_service():
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        health_url="http://127.0.0.1:8080/health",
        service="api",
        python="/opt/remote/bin/python",
        ssh_args=["-J", "bastion"],
        runtime_path="/home/ubuntu/.local/src/apatch_runtime",
    )

    target = config["targets"]["search-example"]
    assert config["allowed_hosts"] == ["example-search-host"]
    assert config["allowed_roots"] == ["/srv/example/search-workspace"]
    assert target["redact"] is True
    assert target["python"] == "/opt/remote/bin/python"
    assert target["ssh_args"] == ["-J", "bastion"]
    assert target["apatch_runtime"]["path"] == "/home/ubuntu/.local/src/apatch_runtime"
    assert target["services"]["api"]["healthcheck"]["url"] == "http://127.0.0.1:8080/health"
    assert target["services"]["api"]["allowed"] == ["healthcheck"]

    resolved = resolve_remote_target("search-example", policy=config)
    assert resolved.as_dict()["redacted"] is True


def test_remote_init_cli_writes_policy_and_validate(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "remote",
            "init",
            "--target-dir",
            str(tmp_path),
            "--alias",
            "search-example",
            "--host",
            "example-search-host",
            "--path",
            "/srv/example/search-workspace",
            "--health-url",
            "http://127.0.0.1:8080/health",
            "--service",
            "api",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    policy = json.loads((tmp_path / ".apatch" / "remote.json").read_text(encoding="utf-8"))
    assert "search-example" in policy["targets"]
    assert policy["targets"]["search-example"]["services"]["api"]["healthcheck"]["url"] == "http://127.0.0.1:8080/health"

    validate = runner.invoke(
        cli,
        ["remote", "validate", "--target-dir", str(tmp_path), "--alias", "search-example", "--json"],
    )
    assert validate.exit_code == 0, validate.output
    validated = json.loads(validate.output)
    assert validated["ok"] is True
    assert validated["target"]["alias"] == "search-example"
    assert validated["target"]["redacted"] is True


def test_systemd_service_restart_plan_is_policy_generated():
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        health_url="http://127.0.0.1:8080/health",
        service="api",
        service_kind="systemd",
        service_unit="search-api.service",
    )

    plan = plan_service_action(
        alias="search-example",
        service="api",
        action="restart",
        policy=config,
    )

    assert plan["ok"] is True
    assert plan["dry_run"] is True
    assert plan["kind"] == "systemd"
    assert plan["operation"] == "command"
    assert plan["argv"] == ["systemctl", "restart", "search-api.service"]
    serialized = json.dumps(plan, ensure_ascii=False)
    assert "example-search-host" not in serialized
    assert "/srv/example/search-workspace" not in serialized


def test_systemd_service_can_be_configured_without_health_url():
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        service="api",
        service_kind="systemd",
        service_unit="search-api.service",
    )

    service = config["targets"]["search-example"]["services"]["api"]
    assert service["allowed"] == ["status", "restart", "logs"]
    assert "healthcheck" not in service


def test_http_service_restart_is_denied_by_default():
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        health_url="http://127.0.0.1:8080/health",
        service="api",
    )

    try:
        plan_service_action(
            alias="search-example",
            service="api",
            action="restart",
            policy=config,
        )
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_SERVICE_ACTION_DENIED"
    else:
        raise AssertionError("HTTP health-only service should not allow restart")


def test_remote_service_cli_plans_healthcheck(tmp_path):
    runner = CliRunner()
    init = runner.invoke(
        cli,
        [
            "remote",
            "init",
            "--target-dir",
            str(tmp_path),
            "--alias",
            "search-example",
            "--host",
            "example-search-host",
            "--path",
            "/srv/example/search-workspace",
            "--health-url",
            "http://127.0.0.1:8080/health",
            "--service",
            "api",
            "--json",
        ],
    )
    assert init.exit_code == 0, init.output

    planned = runner.invoke(
        cli,
        [
            "remote",
            "service",
            "--target-dir",
            str(tmp_path),
            "--alias",
            "search-example",
            "--service",
            "api",
            "--action",
            "healthcheck",
            "--json",
        ],
    )
    assert planned.exit_code == 0, planned.output
    payload = json.loads(planned.output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["operation"] == "http_healthcheck"
    assert payload["url"] == "http://127.0.0.1:8080/health"


def test_source_handoff_disabled_by_default(tmp_path):
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
    )

    try:
        plan_source_handoff(alias="search-example", source_dir=str(tmp_path), policy=config)
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_SOURCE_HANDOFF_DISABLED"
    else:
        raise AssertionError("source handoff should require explicit policy")


def test_source_handoff_plan_redacts_remote_and_local_paths(tmp_path):
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        source_handoff=True,
        source_roots=[str(tmp_path)],
    )

    plan = plan_source_handoff(alias="search-example", source_dir=str(tmp_path), policy=config)

    assert plan["ok"] is True
    assert plan["operation"] == "source_archive_push"
    assert plan["source"]["redacted"] is True
    serialized = json.dumps(plan, ensure_ascii=False)
    assert "example-search-host" not in serialized
    assert "/srv/example/search-workspace" not in serialized
    assert str(tmp_path) not in serialized
    assert "ssh" not in serialized.lower()
    assert "github" not in serialized.lower()
    assert "credential" not in serialized.lower()
    assert "scp" not in serialized.lower()
    assert "pipe" not in serialized.lower()
    assert "tar | ssh" not in serialized.lower()


def test_source_handoff_keeps_safety_excludes_with_custom_policy(tmp_path):
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        source_handoff=True,
        source_roots=[str(tmp_path)],
    )
    config["targets"]["search-example"]["source_handoff"]["exclude"] = ["custom-cache"]

    plan = plan_source_handoff(alias="search-example", source_dir=str(tmp_path), policy=config)

    assert "custom-cache" in plan["exclude"]
    assert ".DS_Store" in plan["exclude"]
    assert "*/.DS_Store" in plan["exclude"]
    assert "._*" in plan["exclude"]
    assert "*/._*" in plan["exclude"]
    assert ".AppleDouble" in plan["exclude"]
    assert "*/.AppleDouble" in plan["exclude"]
    assert "__MACOSX" in plan["exclude"]
    assert "*/__MACOSX" in plan["exclude"]


def test_source_handoff_tar_disables_macos_copyfile_metadata(tmp_path, monkeypatch):
    calls = []

    class Pipe:
        def close(self):
            pass

        def read(self):
            return b""

    class Proc:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.kwargs = kwargs
            self.stdout = Pipe()
            self.stderr = Pipe()
            self.returncode = 0
            calls.append(self)

        def communicate(self, timeout=None):
            return b"", b""

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("apatch.remote.handoff.subprocess.Popen", Proc)
    target = build_remote_target("example-search-host", "/home/ubuntu/tools/apatch", alias="apatch-prod")
    transport = SshArchiveTransport(ssh_args=["-o", "BatchMode=yes"], timeout_sec=10)

    result = transport.push(target, {"source_dir": str(tmp_path), "exclude": [".DS_Store"]})

    assert result["ok"] is True
    assert calls[0].cmd[:4] == ["tar", "--no-xattrs", "-czf", "-"]
    assert calls[0].kwargs["env"]["COPYFILE_DISABLE"] == "1"
    assert "--exclude=.DS_Store" in calls[0].cmd


def test_source_handoff_execute_passes_unredacted_plan_to_transport(tmp_path):
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        source_handoff=True,
        source_roots=[str(tmp_path)],
    )

    class Transport:
        def __init__(self):
            self.plan = None

        def push(self, target, plan):
            self.plan = plan
            return {"ok": True, "echo": plan, "message": "pushed to example-search-host:/srv/example/search-workspace"}

    transport = Transport()
    result = execute_source_handoff(
        alias="search-example",
        source_dir=str(tmp_path),
        policy=config,
        transport=transport,
    )

    assert transport.plan["source_dir"] == str(tmp_path)
    assert result["ok"] is True
    serialized = json.dumps(result, ensure_ascii=False)
    assert "example-search-host" not in serialized
    assert "/srv/example/search-workspace" not in serialized
    assert str(tmp_path) not in serialized
    assert "credential" not in serialized.lower()
    assert "github" not in serialized.lower()


def test_remote_handoff_cli_plans_redacted_handoff(tmp_path):
    runner = CliRunner()
    init = runner.invoke(
        cli,
        [
            "remote",
            "init",
            "--target-dir",
            str(tmp_path),
            "--alias",
            "search-example",
            "--host",
            "example-search-host",
            "--path",
            "/srv/example/search-workspace",
            "--source-handoff",
            "--json",
        ],
    )
    assert init.exit_code == 0, init.output

    planned = runner.invoke(
        cli,
        [
            "remote",
            "handoff",
            "--target-dir",
            str(tmp_path),
            "--source",
            str(tmp_path),
            "--alias",
            "search-example",
            "--json",
        ],
    )
    assert planned.exit_code == 0, planned.output
    payload = json.loads(planned.output)
    assert payload["ok"] is True
    assert payload["source"]["redacted"] is True




DEFAULT_RELEASE_IMAGES = {
    "API_IMAGE": "ghcr.io/example/application-api",
    "WORKER_IMAGE": "ghcr.io/example/application-worker",
}


def _write_verified_release_bundle(tmp_path, *, release_sha, env_lines=None):
    import hashlib
    import io
    import tarfile

    env = "\n".join(
        env_lines
        or [
            "{}={}:sha-{}".format(key, repository, release_sha)
            for key, repository in DEFAULT_RELEASE_IMAGES.items()
        ]
    )
    bundle = tmp_path / "application-release.tar.gz"
    release_root = "application-release"
    with tarfile.open(bundle, "w:gz") as archive:
        directory = tarfile.TarInfo(release_root)
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        data = env.encode("utf-8")
        info = tarfile.TarInfo("{}/.env.release".format(release_root))
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        compose = b"services: {}\n"
        compose_info = tarfile.TarInfo("{}/docker-compose.yml".format(release_root))
        compose_info.size = len(compose)
        archive.addfile(compose_info, io.BytesIO(compose))
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    checksum = tmp_path / "application-release.tar.gz.sha256"
    checksum.write_text("{}  {}\n".format(digest, bundle.name), encoding="utf-8")
    return bundle, checksum


def _release_bundle_policy(tmp_path):
    config = build_remote_config(
        alias="application-prod",
        host="prod",
        path="/srv/application",
    )
    config["targets"]["application-prod"]["source_handoff"] = {
        "enabled": True,
        "allowed_modes": ["release_bundle"],
        "release_bundle": {
            "enabled": True,
            "required_image_keys": list(DEFAULT_RELEASE_IMAGES),
            "allowed_image_repositories": dict(DEFAULT_RELEASE_IMAGES),
        },
    }
    return config


def test_release_bundle_plan_verifies_checksum_images_and_redacts_paths(tmp_path):
    release_sha = "a" * 40
    bundle, checksum = _write_verified_release_bundle(tmp_path, release_sha=release_sha)

    plan = plan_release_bundle_handoff(
        alias="application-prod",
        bundle_path=str(bundle),
        checksum_path=str(checksum),
        release_sha=release_sha,
        policy=_release_bundle_policy(tmp_path),
    )

    assert plan["ok"] is True
    assert plan["operation"] == "verified_release_bundle_push"
    assert plan["release_sha"] == release_sha
    assert plan["bundle_sha256"]
    assert plan["bundle"]["redacted"] is True
    assert str(bundle) not in json.dumps(plan)


def test_release_bundle_rejects_checksum_mismatch_and_wrong_image_sha(tmp_path):
    release_sha = "b" * 40
    bundle, checksum = _write_verified_release_bundle(tmp_path, release_sha=release_sha)
    checksum.write_text("{}  {}\n".format("0" * 64, bundle.name), encoding="utf-8")

    try:
        plan_release_bundle_handoff(
            alias="application-prod",
            bundle_path=str(bundle),
            checksum_path=str(checksum),
            release_sha=release_sha,
            policy=_release_bundle_policy(tmp_path),
        )
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_RELEASE_CHECKSUM_MISMATCH"
    else:
        raise AssertionError("checksum mismatch must fail closed")

    bundle, checksum = _write_verified_release_bundle(
        tmp_path,
        release_sha=release_sha,
        env_lines=[
            "API_IMAGE=ghcr.io/example/application-api:sha-{}".format(release_sha),
            "WORKER_IMAGE=ghcr.io/example/application-worker:sha-{}".format("c" * 40),
        ],
    )
    try:
        plan_release_bundle_handoff(
            alias="application-prod",
            bundle_path=str(bundle),
            checksum_path=str(checksum),
            release_sha=release_sha,
            policy=_release_bundle_policy(tmp_path),
        )
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_RELEASE_BUNDLE_DENIED"
    else:
        raise AssertionError("mixed image SHAs must fail closed")


def test_release_bundle_rejects_missing_declared_root_directory(tmp_path):
    import hashlib
    import io
    import tarfile

    release_sha = "d" * 40
    bundle = tmp_path / "missing-root.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        data = "\n".join(
            [
                "{}={}:sha-{}".format(key, repository, release_sha)
                for key, repository in DEFAULT_RELEASE_IMAGES.items()
            ]
        ).encode("utf-8")
        info = tarfile.TarInfo("release/.env.release")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    checksum = tmp_path / "missing-root.tar.gz.sha256"
    checksum.write_text("{}  {}\n".format(hashlib.sha256(bundle.read_bytes()).hexdigest(), bundle.name), encoding="utf-8")

    try:
        plan_release_bundle_handoff(
            alias="application-prod",
            bundle_path=str(bundle),
            checksum_path=str(checksum),
            release_sha=release_sha,
            policy=_release_bundle_policy(tmp_path),
        )
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_RELEASE_BUNDLE_DENIED"
    else:
        raise AssertionError("bundle without its declared root directory must fail closed")


def test_release_bundle_requires_explicit_policy_image_keys(tmp_path):
    release_sha = "e" * 40
    bundle, checksum = _write_verified_release_bundle(tmp_path, release_sha=release_sha)
    policy = _release_bundle_policy(tmp_path)
    del policy["targets"]["application-prod"]["source_handoff"]["release_bundle"][
        "required_image_keys"
    ]

    try:
        plan_release_bundle_handoff(
            alias="application-prod",
            bundle_path=str(bundle),
            checksum_path=str(checksum),
            release_sha=release_sha,
            policy=policy,
        )
    except RemoteTaskError as exc:
        assert exc.error_type == "REMOTE_RELEASE_BUNDLE_POLICY_INVALID"
    else:
        raise AssertionError("release image keys must come from alias policy")


def test_release_bundle_runtime_has_no_product_specific_image_defaults():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "apatch/remote/handoff.py").read_text(encoding="utf-8")
    product_prefix = "".join(("COMMUNICATIONS", "_"))
    assert product_prefix not in source
    assert "DEFAULT_RELEASE_IMAGE_KEYS" not in source


def test_execute_service_action_passes_unredacted_plan_to_transport():
    config = build_remote_config(
        alias="search-example",
        host="example-search-host",
        path="/srv/example/search-workspace",
        health_url="http://example-search-host.local/health",
        service="api",
        service_kind="docker_compose",
        compose_file="/srv/example/search-workspace/docker-compose.yml",
        compose_service="api",
    )

    class Transport:
        def __init__(self):
            self.plan = None

        def call(self, target, plan):
            self.plan = plan
            return {"ok": True, "echo": plan}

    transport = Transport()
    result = execute_service_action(
        alias="search-example",
        service="api",
        action="status",
        policy=config,
        transport=transport,
    )

    assert transport.plan["argv"] == [
        "docker",
        "compose",
        "-f",
        "/srv/example/search-workspace/docker-compose.yml",
        "ps",
        "api",
    ]
    assert result["ok"] is True
    serialized = json.dumps(result, ensure_ascii=False)
    assert "/srv/example/search-workspace" not in serialized
    assert "example-search-host.local" not in serialized
