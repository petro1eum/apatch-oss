"""Actual stdio, wrapper, SDK records, and governed lifecycle in fixture repos only."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from apatch.enforcement import DEFAULT_ENFORCEMENT
from apatch.mcp.workspace_registry import register_local_workspace
from apatch.mcp_health import recommended_mcp_server_block
from apatch.sandbox import DEFAULT_SANDBOX
from apatch.trust_identity import workspace_identity_scope, load_local_identity
from apatch.trustchain_helper import TrustChainHelper
from tests.test_workspace_signer_scope import workspace_pin, tree_hashes


FIXTURE_SOURCE = "def next_value(value):\n    return value + 1\n"
FIXTURE_TEST = """import importlib.util
from pathlib import Path
import pytest
path = Path(__file__).resolve().parents[1] / 'src' / 'counter.py'
spec = importlib.util.spec_from_file_location('fixture_counter', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
@pytest.mark.parametrize('value', [-10, -1, 0, 8])
def test_next_value(value):
    assert module.next_value(value) == value + 1
"""


def fixture_workspace(root, identity):
    workspace_pin(root, identity=identity)
    (root / "AGENTS.md").write_text("# Fixture consumer\nProtected src changes require governed sessions and Ed25519 notarization.\n")
    (root / ".gitignore").write_text(".apatch/\n.trustchain/\n__pycache__/\n.pytest_cache/\n")
    (root / "README.md").write_text("Disposable signer selection regression fixture.\n")
    enforcement = dict(DEFAULT_ENFORCEMENT, governed_mode="strict")
    (root / ".apatch/enforcement.json").write_text(json.dumps(enforcement))
    (root / ".apatch/sandbox.json").write_text(json.dumps(DEFAULT_SANDBOX))
    block = recommended_mcp_server_block(str(root))
    block["command"] = sys.executable
    block["env"]["APATCH_MCP_PROFILE"] = "full"
    (root / ".apatch/mcp.json").write_text(json.dumps({"mcpServers":{"apatch":block}}))
    subprocess.run(["git", "-C", str(root), "add", "AGENTS.md", ".gitignore", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture bootstrap"], check=True, capture_output=True)
    return block


def pinned_records(root, own, wrong):
    rows = []
    for path in sorted((root / ".trustchain/objects").glob("op_*.json")):
        raw = json.loads(path.read_text())
        value = raw.get("value", raw)
        own_id, own_valid = TrustChainHelper._pinned_record(value, own)
        wrong_id, wrong_valid = TrustChainHelper._pinned_record(value, wrong)
        assert own_id is True and own_valid is True, {"object":path.name,"key_id":value.get("key_id")}
        assert wrong_id is False and wrong_valid is False
        rows.append({"object":path.name,"tool":value.get("tool"),"action":value.get("data",{}).get("action"),
            "key_id":value.get("key_id"),"pinned_signature_valid":own_valid,"other_pin_valid":wrong_valid})
    return rows


def test_actual_roaming_stdio_session_apply_verify_attest_end_two_pins(tmp_path):
    # The child imports the same source package as the test, including source overlays.
    import apatch
    package_roots = [str(Path(path).resolve().parent) for path in apatch.__path__]
    inherited_paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    child_pythonpath = os.pathsep.join(dict.fromkeys(
        path for path in package_roots + inherited_paths if path
    ))
    before_env = dict(os.environ)
    target = tmp_path / "target-project"
    caller = tmp_path / "caller-project"
    target_block = fixture_workspace(target, "target-enrolled-fixture")
    caller_block = fixture_workspace(caller, "caller-enrolled-fixture")
    registry = tmp_path / "fixture-workspaces.json"
    registered = register_local_workspace("target-fixture", str(target), registry_path=str(registry))
    assert registered["ok"] is True and registered["ready"] is True
    with workspace_identity_scope(str(target)): target_identity = load_local_identity(str(target))
    with workspace_identity_scope(str(caller)): caller_identity = load_local_identity(str(caller))
    assert target_identity.key_provider.get_public_key() != caller_identity.key_provider.get_public_key()
    protected_before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        target / ".apatch/agent-identity.json", caller / ".apatch/agent-identity.json",
        target / ".apatch/enforcement.json", target / ".apatch/sandbox.json",
        Path(target_identity.key_path), Path(caller_identity.key_path))}
    caller_ledger_before = tree_hashes(caller)
    # Isolated child env, not os.environ swaps; no real registry/config/key is used.
    env = {k:v for k,v in os.environ.items() if k in ("PATH","HOME","TMPDIR","LANG","LC_ALL")}
    env.update(caller_block["env"])
    env.update({"PYTHONPATH":child_pythonpath, "PYTHONDONTWRITEBYTECODE":"1",
        "APATCH_MCP_BOUND":str(caller), "APATCH_WORKSPACE":str(caller),
        "APATCH_WORKSPACE_REGISTRY":str(registry), "APATCH_MCP_TARGET_POLICY":"alias_only",
        "APATCH_MCP_STDERR_LOG":str(tmp_path / "fixture-server.log"),
        "APATCH_CANONICAL_RUNTIME":"1",
        "APATCH_AGENT_ID":caller_identity.agent_id, "APATCH_AGENT_KEY":caller_identity.key_path,
        "APATCH_KEY_BACKEND":"pem"})
    params = StdioServerParameters(command=caller_block["command"], args=caller_block["args"], cwd=str(caller), env=env)
    proof = {"scope":"disposable stdio governed regression; not source admission", "tools":[],"records":[]}

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listed = await client.list_tools()
                assert len(listed.tools) >= 70
                proof["tool_count"] = len(listed.tools)
                async def call(tool, arguments=None, bound=None):
                    args = {"target_dir":"@target-fixture", **(arguments or {}), **(bound or {})}
                    result = await asyncio.wait_for(client.call_tool(tool, args), timeout=45)
                    if result.isError:
                        raise AssertionError("fixture MCP tool failed: " + tool)
                    value = json.loads(result.content[0].text)
                    if "result" in value and isinstance(value["result"], dict): value = value["result"]
                    proof["tools"].append({"tool":tool,"ok":value.get("ok"),"error_type":value.get("error_type")})
                    if value.get("ok") is False:
                        raise AssertionError(json.dumps({"tool":tool,"error_type":value.get("error_type"),"error":value.get("error"),"recommended_action":value.get("recommended_action")}))
                    return value
                doctor = await call("apatch_doctor")
                assert doctor["writer_protocol"]["ready"] is True
                assert doctor["writer_protocol"]["reload_required"] is False
                proof["writer_ready"] = True
                started = await call("apatch_session_start", {"intent":"implement counter in disposable signer regression",
                    "artifacts":["ticket:fixture-two-identity-counter"], "lane":"fixture-signer-cycle"})
                session_id = started["session"]["session_id"]
                capability = {"governed_session_id":session_id,"session_token":started["session_token"]}
                checkpoint = None
                try:
                    generated = await call("apatch_generate_batch", {"needles":[
                        {"action":"create","target_file":"src/counter.py","content":FIXTURE_SOURCE},
                        {"action":"create","target_file":"tests/test_counter.py","content":FIXTURE_TEST}]}, capability)
                    logs = generated["out_path"]
                    await call("apatch_simulate", {"logs_path":logs})
                    applied = await call("apatch_apply_session", {"logs_path":logs,"verify_deferred":True}, capability)
                    checkpoint = applied.get("checkpoint")
                    while applied.get("continue"):
                        applied = await call("apatch_apply_session", {"logs_path":logs,"verify_deferred":True}, capability)
                        checkpoint = applied.get("checkpoint") or checkpoint
                    mutation_rows = pinned_records(target, target_identity, caller_identity)
                    assert any(row["action"] in ("chunk", "apply", "create", "apply_session", "mutate") or row["tool"] == "apatch_apply" for row in mutation_rows), mutation_rows
                    verify = await call("apatch_verify_run", {"verify":[sys.executable,"-m","pytest","-q","--noconftest","-p","no:cacheprovider","tests/test_counter.py"]}, capability)
                    if verify.get("async") or verify.get("job_id"):
                        job = verify["job_id"]
                        for _ in range(50):
                            verify = await call("apatch_verify_status", {"job_id":job})
                            if verify.get("status") in ("passed","completed","failed") or verify.get("done"): break
                            await asyncio.sleep(0.05)
                    await call("apatch_verify_notarization", {"working_tree":True})
                    await call("apatch_attest", {"message":"fixture counter verified through actual stdio; not production acceptance"}, capability)
                    proof["records"] = pinned_records(target, target_identity, caller_identity)
                    assert any(row["tool"] == "apatch_attest" or row["action"] == "attest" for row in proof["records"])
                    ended = await call("apatch_session_end", bound=capability)
                    proof["session_closed"] = ended["ok"] is True
                except BaseException:
                    if checkpoint:
                        try: await call("apatch_rollback", {"session_id":checkpoint}, capability)
                        except BaseException: pass
                    try: await call("apatch_session_end", bound=capability)
                    except BaseException: pass
                    raise
    asyncio.run(run())
    assert (target / "src/counter.py").read_text() == FIXTURE_SOURCE
    assert (target / "tests/test_counter.py").read_text() == FIXTURE_TEST
    assert tree_hashes(caller) == caller_ledger_before
    assert dict(os.environ) == before_env
    assert not (caller / "src/counter.py").exists()
    assert {path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in protected_before} == protected_before
    proof["caller_ledger_unchanged"] = True
    proof["pins_config_unchanged"] = True
    from apatch.sandbox import load_active_leases
    assert not load_active_leases(str(target))
    proof["leases_released"] = True
    print("STDIO_SIGNER_FIXTURE_PROOF=" + json.dumps(proof, sort_keys=True))
