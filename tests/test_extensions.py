from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from apatch.extensions.schema import (
    EXTENSION_PROTOCOL,
    ExtensionContractError,
    package_digest,
    validate_lock,
    validate_manifest,
    verify_lock_entry,
)


def _write_valid_extension(root: Path):
    extension_dir = root / "extensions" / "example"
    extension_dir.mkdir(parents=True)
    runner = extension_dir / "runner.py"
    runner.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "json.dump({'protocol': 'apatch.extension.v1', "
        "'request_id': request['request_id'], 'ok': True, "
        "'result': {'echo': request['arguments']['value']}}, sys.stdout)\n",
        encoding="utf-8",
    )
    runner_sha = hashlib.sha256(runner.read_bytes()).hexdigest()
    artifacts = [{"path": "runner.py", "sha256": runner_sha}]
    manifest = {
        "schema_version": 1,
        "id": "dev.example.echo",
        "version": "1.0.0",
        "api_range": {"min": "0.8.28", "max": "0.9.99"},
        "owner": {"name": "Example User"},
        "license": "LicenseRef-Example-Private",
        "exportable": False,
        "notices": ["Owner retains all rights."],
        "capabilities": [],
        "artifacts": artifacts,
        "package_sha256": package_digest(artifacts),
        "tools": [
            {
                "id": "echo",
                "title": "Echo",
                "description": "Return one schema-checked value.",
                "authority": "read_only",
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "maxLength": 64}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"echo": {"type": "string", "maxLength": 64}},
                    "required": ["echo"],
                    "additionalProperties": False,
                },
                "runtime": "python",
                "entrypoint": "runner.py",
                "argv": [],
                "timeout_sec": 5,
                "max_output_bytes": 4096,
                "pass_env": [],
                "work_asset_ref": "work_asset:echo-method",
            }
        ],
    }
    manifest_path = extension_dir / "apatch-extension.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    entry = {
        "id": manifest["id"],
        "version": manifest["version"],
        "source": "workspace",
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "manifest_sha256": manifest_sha,
        "package_sha256": manifest["package_sha256"],
        "license": manifest["license"],
        "enabled": True,
        "grants": [],
    }
    return manifest, entry, runner, manifest_path


def test_r1_manifest_and_lock_are_explicit_and_digest_pinned(tmp_path):
    manifest, entry, runner, manifest_path = _write_valid_extension(tmp_path)

    normalized = validate_manifest(manifest)
    assert normalized["tools"][0]["full_id"] == "dev.example.echo/echo"
    assert normalized["tools"][0]["authority"] == "read_only"
    assert EXTENSION_PROTOCOL == "apatch.extension.v1"

    schema_root = Path(__file__).resolve().parents[1] / "apatch/extensions/schemas"
    manifest_schema = json.loads((schema_root / "manifest-v1.json").read_text())
    lock_schema = json.loads((schema_root / "lock-v1.json").read_text())
    manifest_tool = manifest_schema["properties"]["tools"]["items"]
    assert set(manifest_tool["required"]) == {
        "id", "title", "description", "authority", "input_schema", "output_schema",
        "runtime", "entrypoint", "argv", "timeout_sec", "max_output_bytes", "pass_env",
    }
    assert manifest_tool["properties"]["authority"]["enum"] == ["read_only", "proposal"]
    lock_entry = lock_schema["properties"]["extensions"]["items"]
    assert set(lock_entry["required"]) == set(entry)
    assert lock_entry["properties"]["source"]["enum"] == [
        "workspace", "bundled", "personal"
    ]
    strict_id_pattern = manifest_schema["properties"]["id"]["pattern"]
    assert manifest_schema["properties"]["id"]["maxLength"] == 253
    assert lock_entry["properties"]["id"]["pattern"] == strict_id_pattern
    assert lock_entry["properties"]["id"]["maxLength"] == 253
    assert re.fullmatch(strict_id_pattern, "dev.example.echo")
    for invalid_id in (
        "dev-example-echo",
        "dev_example_echo",
        "dev.example",
        "Dev.example.echo",
        "dev.-example.echo",
        "dev.example-.echo",
        "apatch.example.echo",
        " dev.example.echo",
        "dev.example.echo ",
    ):
        assert re.fullmatch(strict_id_pattern, invalid_id) is None
        bad_manifest = copy.deepcopy(manifest)
        bad_manifest["id"] = invalid_id
        with pytest.raises(ExtensionContractError) as exc:
            validate_manifest(bad_manifest)
        assert exc.value.code == "EXTENSION_ID_INVALID"
        bad_lock_entry = copy.deepcopy(entry)
        bad_lock_entry["id"] = invalid_id
        with pytest.raises(ExtensionContractError) as exc:
            validate_lock({"schema_version": 1, "extensions": [bad_lock_entry]})
        assert exc.value.code == "EXTENSION_ID_INVALID"

    too_long_id = ".".join(["a" * 63] * 4)
    assert len(too_long_id) > 253
    assert re.fullmatch(strict_id_pattern, too_long_id)
    for target in ("manifest", "lock"):
        bad_manifest = copy.deepcopy(manifest)
        bad_entry = copy.deepcopy(entry)
        bad_manifest["id"] = too_long_id
        bad_entry["id"] = too_long_id
        with pytest.raises(ExtensionContractError) as exc:
            if target == "manifest":
                validate_manifest(bad_manifest)
            else:
                validate_lock({"schema_version": 1, "extensions": [bad_entry]})
        assert exc.value.code == "EXTENSION_ID_INVALID"

    lock = validate_lock({"schema_version": 1, "extensions": [entry]})
    assert lock["extensions"][0]["manifest_path"] == "extensions/example/apatch-extension.json"
    verified = verify_lock_entry(tmp_path, entry)
    assert verified["manifest_sha256"] == entry["manifest_sha256"]
    assert verified["package_sha256"] == entry["package_sha256"]
    assert verified["manifest"]["license"] == "LicenseRef-Example-Private"

    for location, field, value, expected_code in (
        ("manifest", "title", "", "EXTENSION_TITLE_INVALID"),
        ("manifest", "description", 7, "EXTENSION_DESCRIPTION_INVALID"),
        ("tool", "title", "", "EXTENSION_TOOL_TITLE_INVALID"),
        ("tool", "description", None, "EXTENSION_TOOL_DESCRIPTION_INVALID"),
    ):
        bad = copy.deepcopy(manifest)
        target = bad if location == "manifest" else bad["tools"][0]
        target[field] = value
        with pytest.raises(ExtensionContractError) as exc:
            validate_manifest(bad)
        assert exc.value.code == expected_code

    bad = copy.deepcopy(manifest)
    bad["tools"][0]["authority"] = "mutation"
    with pytest.raises(ExtensionContractError, match="read_only or proposal") as exc:
        validate_manifest(bad)
    assert exc.value.code == "EXTENSION_AUTHORITY_INVALID"

    bad = copy.deepcopy(manifest)
    bad["tools"].append(copy.deepcopy(bad["tools"][0]))
    with pytest.raises(ExtensionContractError) as exc:
        validate_manifest(bad)
    assert exc.value.code == "EXTENSION_TOOL_DUPLICATE"

    bad = copy.deepcopy(manifest)
    bad["artifacts"][0]["path"] = "../runner.py"
    with pytest.raises(ExtensionContractError) as exc:
        validate_manifest(bad)
    assert exc.value.code == "EXTENSION_ARTIFACT_PATH_INVALID"

    bad = copy.deepcopy(manifest)
    bad["unexpected_execution_field"] = True
    with pytest.raises(ExtensionContractError) as exc:
        validate_manifest(bad)
    assert exc.value.code == "EXTENSION_MANIFEST_INVALID"

    incompatible = copy.deepcopy(manifest)
    incompatible["api_range"] = {"min": "1.0.0", "max": "1.9.99"}
    manifest_path.write_text(
        json.dumps(incompatible, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    incompatible_entry = dict(entry)
    incompatible_entry["manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    with pytest.raises(ExtensionContractError) as exc:
        verify_lock_entry(tmp_path, incompatible_entry)
    assert exc.value.code == "EXTENSION_API_VERSION_UNSUPPORTED"

    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    entry["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    runner.write_text("print('drift')\n", encoding="utf-8")
    with pytest.raises(ExtensionContractError) as exc:
        verify_lock_entry(tmp_path, entry)
    assert exc.value.code == "EXTENSION_ARTIFACT_DIGEST_MISMATCH"

    runner.unlink()
    target = tmp_path / "outside.py"
    target.write_text("print('outside')\n", encoding="utf-8")
    try:
        runner.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    entry["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    with pytest.raises(ExtensionContractError) as exc:
        verify_lock_entry(tmp_path, entry)
    assert exc.value.code == "EXTENSION_ARTIFACT_PATH_INVALID"


def test_r2_discovery_is_explicit_and_never_auto_updates(tmp_path):
    from apatch.extensions.catalog import ExtensionCatalog, load_explicit_lock

    manifest, entry, _runner, _manifest_path = _write_valid_extension(tmp_path)
    lock_path = tmp_path / ".apatch" / "extensions.lock.json"
    lock_path.parent.mkdir()
    lock_bytes = json.dumps(
        {"schema_version": 1, "extensions": [entry]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    lock_path.write_bytes(lock_bytes)

    rogue_dir = tmp_path / "unlisted" / "rogue"
    rogue_dir.mkdir(parents=True)
    (rogue_dir / "apatch-extension.json").write_text(
        json.dumps({**manifest, "id": "dev.example.rogue"}),
        encoding="utf-8",
    )

    catalog = ExtensionCatalog(tmp_path)
    listing = catalog.list()
    assert listing["count"] == 1
    assert [row["id"] for row in listing["extensions"]] == ["dev.example.echo"]
    assert catalog.inspect("dev.example.echo")["extension"]["tools"][0]["full_id"] == (
        "dev.example.echo/echo"
    )
    assert catalog.validate()["extensions"][0]["valid"] is True
    assert lock_path.read_bytes() == lock_bytes

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty = load_explicit_lock(empty_root)
    assert empty["extensions"] == []
    assert empty["_lock_present"] is False
    assert not (empty_root / ".apatch" / "extensions.lock.json").exists()

    with pytest.raises(ExtensionContractError) as exc:
        catalog.inspect("dev.example.rogue")
    assert exc.value.code == "EXTENSION_NOT_FOUND"

    original = json.loads(lock_bytes)
    original["extensions"][0]["version"] = "9.9.9"
    lock_path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ExtensionContractError) as exc:
        ExtensionCatalog(tmp_path).list()
    assert exc.value.code == "EXTENSION_LOCK_MANIFEST_MISMATCH"


def _write_lock(root: Path, entry):
    lock_path = root / ".apatch" / "extensions.lock.json"
    lock_path.parent.mkdir(exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {"schema_version": 1, "extensions": [entry]},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return lock_path


def _rewrite_extension(manifest_path: Path, runner: Path, manifest, entry):
    runner_sha = hashlib.sha256(runner.read_bytes()).hexdigest()
    manifest["artifacts"] = [{"path": "runner.py", "sha256": runner_sha}]
    manifest["package_sha256"] = package_digest(manifest["artifacts"])
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    entry["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    entry["package_sha256"] = manifest["package_sha256"]
    entry["version"] = manifest["version"]
    entry["license"] = manifest["license"]


def test_r3_runner_is_bounded_and_proposal_never_applies(tmp_path, monkeypatch):
    from apatch.extensions.catalog import ExtensionCatalog
    from apatch.extensions.runner import run_verified_tool

    manifest, entry, runner, manifest_path = _write_valid_extension(tmp_path)
    _write_lock(tmp_path, entry)
    resolved = ExtensionCatalog(tmp_path).resolve_tool("dev.example.echo/echo")
    result = run_verified_tool(resolved, {"value": "hello"}, workspace=tmp_path)
    assert result["result"] == {"echo": "hello"}
    assert result["authority"] == "read_only"
    assert set(result["evidence"]) == {
        "manifest_sha256",
        "package_sha256",
        "request_sha256",
        "response_sha256",
    }

    with pytest.raises(ExtensionContractError) as exc:
        run_verified_tool(resolved, {"value": "hello", "argv": ["sh"]}, workspace=tmp_path)
    assert exc.value.code == "EXTENSION_VALUE_SCHEMA_MISMATCH"

    manifest["tools"][0]["authority"] = "proposal"
    runner.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "json.dump({'protocol': 'apatch.extension.v1', "
        "'request_id': request['request_id'], 'ok': True, "
        "'result': {'echo': request['arguments']['value']}, "
        "'proposed_needles': [{'action': 'create', "
        "'target_file': 'proposal-only.txt', 'content': 'not applied'}]}, sys.stdout)\n",
        encoding="utf-8",
    )
    _rewrite_extension(manifest_path, runner, manifest, entry)
    _write_lock(tmp_path, entry)
    before = {path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    proposal = run_verified_tool(
        ExtensionCatalog(tmp_path).resolve_tool("dev.example.echo/echo"),
        {"value": "proposal"},
        workspace=tmp_path,
    )
    after = {path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert proposal["proposed_needles"][0]["target_file"] == "proposal-only.txt"
    assert before == after
    assert not (tmp_path / "proposal-only.txt").exists()

    runner.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "json.dump({'protocol': 'apatch.extension.v1', "
        "'request_id': request['request_id'], 'ok': True, "
        "'result': {'echo': 'x'}, 'session_token': 'forged'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _rewrite_extension(manifest_path, runner, manifest, entry)
    _write_lock(tmp_path, entry)
    with pytest.raises(ExtensionContractError) as exc:
        run_verified_tool(
            ExtensionCatalog(tmp_path).resolve_tool("dev.example.echo/echo"),
            {"value": "reserved"},
            workspace=tmp_path,
        )
    assert exc.value.code == "EXTENSION_RESPONSE_RESERVED_FIELD"

    def assert_runner_error(source, expected_code, *, max_output_bytes=4096):
        runner.write_text(source, encoding="utf-8")
        manifest["tools"][0]["max_output_bytes"] = max_output_bytes
        _rewrite_extension(manifest_path, runner, manifest, entry)
        _write_lock(tmp_path, entry)
        with pytest.raises(ExtensionContractError) as caught:
            run_verified_tool(
                ExtensionCatalog(tmp_path).resolve_tool("dev.example.echo/echo"),
                {"value": expected_code},
                workspace=tmp_path,
            )
        assert caught.value.code == expected_code

    assert_runner_error(
        "import sys\nsys.stdout.write('{')\n",
        "EXTENSION_RESPONSE_JSON_INVALID",
    )
    assert_runner_error(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "response = {'protocol': 'apatch.extension.v1', "
        "'request_id': request['request_id'], 'ok': True, "
        "'result': {'echo': 'one'}}\n"
        "json.dump(response, sys.stdout)\n"
        "json.dump(response, sys.stdout)\n",
        "EXTENSION_RESPONSE_JSON_INVALID",
    )
    assert_runner_error(
        "import sys\nsys.stdout.write('x' * 257)\n",
        "EXTENSION_OUTPUT_LIMIT",
        max_output_bytes=256,
    )
    assert_runner_error(
        "import sys\nsys.stderr.write('x' * 65537)\n",
        "EXTENSION_STDERR_LIMIT",
    )

    runner.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    manifest["tools"][0]["timeout_sec"] = 1
    _rewrite_extension(manifest_path, runner, manifest, entry)
    _write_lock(tmp_path, entry)
    started = __import__("time").monotonic()
    with pytest.raises(ExtensionContractError) as exc:
        run_verified_tool(
            ExtensionCatalog(tmp_path).resolve_tool("dev.example.echo/echo"),
            {"value": "timeout"},
            workspace=tmp_path,
        )
    assert exc.value.code == "EXTENSION_PROCESS_TIMEOUT"
    assert __import__("time").monotonic() - started < 3

    secret = "owner-secret-value"
    monkeypatch.setenv("OWNER_TEST_SECRET", secret)
    manifest["capabilities"] = ["env:OWNER_TEST_SECRET"]
    manifest["tools"][0]["pass_env"] = ["OWNER_TEST_SECRET"]
    manifest["tools"][0]["timeout_sec"] = 5
    entry["grants"] = ["env:OWNER_TEST_SECRET"]
    runner.write_text(
        "import os, sys\n"
        "sys.stderr.write('token=' + os.environ['OWNER_TEST_SECRET'])\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    _rewrite_extension(manifest_path, runner, manifest, entry)
    _write_lock(tmp_path, entry)
    with pytest.raises(ExtensionContractError) as exc:
        run_verified_tool(
            ExtensionCatalog(tmp_path).resolve_tool("dev.example.echo/echo"),
            {"value": "redact"},
            workspace=tmp_path,
        )
    assert exc.value.code == "EXTENSION_PROCESS_FAILED"
    assert secret not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


def test_r5_local_scaffold_pin_and_run_need_no_account_or_network(tmp_path, monkeypatch):
    import socket
    from click.testing import CliRunner
    from apatch.cli import cli

    for name in (
        "APATCH_PLATFORM_URL", "APATCH_AGENT_ID", "APATCH_AGENT_KEY",
        "APATCH_ENTITLEMENT_TOKEN", "TRUSTCHAIN_API_KEY", "TRUSTCHAIN_SSO_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("local extension authoring attempted a network call")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    runner = CliRunner()
    initialized = runner.invoke(
        cli, ["extension", "init", "dev.example.local", "--tool-id", "echo",
              "--owner", "Local User", "--target-dir", str(tmp_path), "--json"],
    )
    assert initialized.exit_code == 0, initialized.output
    init_result = json.loads(initialized.output)
    assert init_result["pinned"] is False
    assert init_result["manifest_path"] == "extensions/dev.example.local/apatch-extension.json"
    scaffolded_manifest = json.loads(
        (tmp_path / init_result["manifest_path"]).read_text(encoding="utf-8")
    )
    scaffolded_tool = scaffolded_manifest["tools"][0]
    closed_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert scaffolded_tool["input_schema"] == closed_schema
    assert scaffolded_tool["output_schema"] == closed_schema

    pinned = runner.invoke(
        cli, ["extension", "pin", init_result["manifest_path"],
              "--target-dir", str(tmp_path), "--json"],
    )
    assert pinned.exit_code == 0, pinned.output
    pin_result = json.loads(pinned.output)
    assert pin_result["source"] == "personal"
    assert pin_result["grants"] == []

    validated = runner.invoke(
        cli, ["extension", "validate", "dev.example.local",
              "--target-dir", str(tmp_path), "--json"],
    )
    assert validated.exit_code == 0, validated.output
    validate_result = json.loads(validated.output)
    assert validate_result["count"] == 1
    assert validate_result["extensions"][0]["id"] == "dev.example.local"

    inspected = runner.invoke(
        cli, ["extension", "inspect", "dev.example.local",
              "--target-dir", str(tmp_path), "--json"],
    )
    assert inspected.exit_code == 0, inspected.output
    extension = json.loads(inspected.output)["extension"]
    assert extension["owner"] == {"name": "Local User"}
    assert extension["license"] == "LicenseRef-Private"
    assert extension["exportable"] is False

    executed = runner.invoke(
        cli, ["extension", "run", "dev.example.local/echo",
              "--arguments-json", "{}",
              "--target-dir", str(tmp_path), "--json"],
    )
    assert executed.exit_code == 0, executed.output
    assert json.loads(executed.output)["result"] == {}

    for forbidden_arguments in (
        {"argv": ["sh"]},
        {"executable": "/bin/sh"},
        {"upstream_url": "https://example.invalid"},
        {"secret_name": "OWNER_KEY"},
    ):
        refused_run = runner.invoke(
            cli,
            [
                "extension", "run", "dev.example.local/echo",
                "--arguments-json", json.dumps(forbidden_arguments),
                "--target-dir", str(tmp_path), "--json",
            ],
        )
        assert refused_run.exit_code != 0
        assert "unknown properties" in refused_run.output

    manifest_path = tmp_path / init_result["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["description"] = "Explicitly changed local manifest."
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    refused = runner.invoke(
        cli, ["extension", "pin", str(manifest_path),
              "--target-dir", str(tmp_path), "--json"],
    )
    assert refused.exit_code != 0
    assert "explicit replace" in refused.output
    replaced = runner.invoke(
        cli, ["extension", "pin", str(manifest_path), "--replace",
              "--target-dir", str(tmp_path), "--json"],
    )
    assert replaced.exit_code == 0, replaced.output
    assert json.loads(replaced.output)["changed"] is True


def test_r7_extension_rights_are_pinned_and_reference_only(tmp_path):
    from apatch.extensions.catalog import ExtensionCatalog

    manifest, entry, _runner, manifest_path = _write_valid_extension(tmp_path)
    _write_lock(tmp_path, entry)
    record = ExtensionCatalog(tmp_path).inspect("dev.example.echo")["extension"]
    assert record["owner"] == {"name": "Example User"}
    assert record["license"] == "LicenseRef-Example-Private"
    assert record["exportable"] is False
    assert record["notices"] == ["Owner retains all rights."]
    assert record["manifest_sha256"] == entry["manifest_sha256"]
    assert record["package_sha256"] == entry["package_sha256"]
    assert record["tools"][0]["work_asset_ref"] == "work_asset:echo-method"
    public_blob = json.dumps(record, ensure_ascii=False)
    assert "request = json.load" not in public_blob
    assert "runner.py" not in public_blob

    bad = copy.deepcopy(manifest)
    bad["tools"][0]["work_asset_ref"] = '{"inline":"code"}\n'
    with pytest.raises(ExtensionContractError) as exc:
        validate_manifest(bad)
    assert exc.value.code == "EXTENSION_WORK_ASSET_REF_INVALID"

    manifest["owner"]["name"] = "Changed Owner"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ExtensionContractError) as exc:
        verify_lock_entry(tmp_path, entry)
    assert exc.value.code == "EXTENSION_MANIFEST_DIGEST_MISMATCH"


def test_r4_cli_workflow_and_mcp_parity(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from apatch.cli import cli
    from apatch.mcp import server as mcp_server
    from apatch.mcp.profiles import PROFILE_CORE
    from apatch.workflows import (
        extension_inspect_workspace, extension_list_workspace,
        extension_run_workspace, extension_validate_workspace,
    )
    from apatch.extensions import host as extension_host

    for name in (
        "list_extensions", "inspect_extension",
        "validate_extensions", "run_extension_tool",
    ):
        assert callable(getattr(extension_host, name))

    schema_root = Path(__file__).resolve().parents[1] / "apatch/extensions/schemas"
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in schema_root.glob("*-v1.json")
    }
    assert set(schemas) == {
        "lock-v1.json", "manifest-v1.json",
        "request-v1.json", "response-v1.json",
    }
    assert schemas["request-v1.json"]["properties"]["protocol"]["const"] == EXTENSION_PROTOCOL
    response_schema = schemas["response-v1.json"]
    assert response_schema["properties"]["protocol"]["const"] == EXTENSION_PROTOCOL
    assert "session_token" in response_schema[
        "x-apatch-reserved-fields-forbidden-recursively"
    ]

    _manifest, entry, _runner, _manifest_path = _write_valid_extension(tmp_path)
    _write_lock(tmp_path, entry)
    assert extension_list_workspace(str(tmp_path))["count"] == 1
    assert extension_inspect_workspace(
        str(tmp_path), extension_id="dev.example.echo"
    )["extension"]["id"] == "dev.example.echo"
    assert extension_validate_workspace(str(tmp_path))["count"] == 1
    assert extension_run_workspace(
        str(tmp_path), tool_id="dev.example.echo/echo", arguments={"value": "workflow"}
    )["result"] == {"echo": "workflow"}
    runner = CliRunner()
    listed = runner.invoke(cli, ["extension", "list", "--target-dir", str(tmp_path), "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["extensions"][0]["id"] == "dev.example.echo"
    executed = runner.invoke(
        cli, ["extension", "run", "dev.example.echo/echo", "--arguments-json",
              '{"value":"cli"}', "--target-dir", str(tmp_path), "--json"]
    )
    assert executed.exit_code == 0, executed.output
    assert json.loads(executed.output)["result"] == {"echo": "cli"}
    manager = getattr(mcp_server.mcp, "_tool_manager", None)
    assert manager is not None
    names = set(manager._tools)
    expected = {
        "apatch_extension_list", "apatch_extension_inspect",
        "apatch_extension_validate", "apatch_extension_run",
    }
    assert expected <= names
    assert expected <= PROFILE_CORE
    assert not any(name.startswith("dev.example.echo") for name in names)
    assert mcp_server.apatch_extension_run(
        tool_id="dev.example.echo/echo", arguments={"value": "mcp"},
        target_dir=str(tmp_path)
    )["result"] == {"echo": "mcp"}

    extension_run_tool = manager._tools["apatch_extension_run"]
    arguments_schema = extension_run_tool.parameters["properties"]["arguments"]
    assert "type" not in arguments_schema
    assert "anyOf" not in arguments_schema

    from apatch import cli_extension, workflows as extension_workflows

    mcp_arguments = []
    cli_arguments = []

    def capture_mcp(target_dir, *, tool_id, arguments, lock_path):
        mcp_arguments.append(arguments)
        return {"ok": True, "arguments": arguments}

    def capture_cli(target_dir, tool_id, arguments, *, lock_path):
        cli_arguments.append(arguments)
        return {"ok": True, "arguments": arguments}

    monkeypatch.setattr(
        extension_workflows, "extension_run_workspace", capture_mcp
    )
    monkeypatch.setattr(cli_extension, "run_extension_tool", capture_cli)

    for arguments in (False, 0, [], {}, ["one"], "scalar"):
        mcp_result = mcp_server.apatch_extension_run(
            tool_id="dev.example.echo/echo",
            arguments=arguments,
            target_dir=str(tmp_path),
        )
        assert type(mcp_arguments[-1]) is type(arguments)
        assert mcp_arguments[-1] == arguments
        assert mcp_result["arguments"] == arguments

        cli_result = runner.invoke(
            cli,
            [
                "extension", "run", "dev.example.echo/echo",
                "--arguments-json", json.dumps(arguments),
                "--target-dir", str(tmp_path), "--json",
            ],
        )
        assert cli_result.exit_code == 0, cli_result.output
        assert type(cli_arguments[-1]) is type(arguments)
        assert cli_arguments[-1] == arguments

    defaulted = mcp_server.apatch_extension_run(
        tool_id="dev.example.echo/echo",
        target_dir=str(tmp_path),
    )
    assert defaulted["arguments"] == {}
    assert mcp_arguments[-1] == {}
