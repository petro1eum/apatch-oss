"""Source-bound OSS verification inventory (RFP-046).

Collect complete verification evidence and classify its explicitly named scope.
Existing tests and native conformance remain authoritative; a scoped result never
grants release approval or external-service acceptance.
"""
from __future__ import annotations

import ast
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET


SCHEMA = "apatch.oss-verification-inventory.v1"
RUNTIME_ROOTS = ("apatch", "apatch_search_workflows")
DEPENDENCIES = {"avatar_contract", "tree_sitter_java"}
COLLECTION_SKIPS = {"tests/test_contribution_event.py", "tests/test_edge_lockstep.py"}
FIELDS = {
    "schema", "qualification_scope", "provenance", "runtime_files", "source_files",
    "absent_peer_failures", "existing_optional_skips", "peer_requirements",
    "unproven_external_specs",
}


class InventoryError(ValueError):
    """Missing, ambiguous, changed or unsupported verification input."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact_fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise InventoryError("unexpected fields: " + label)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InventoryError("missing text: " + label)


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise InventoryError("invalid SHA-256")


def _relative_path(value):
    _text(value, "path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or ".." in path.parts
            or any(character in value for character in "\\:*?[]\n\r\0")):
        raise InventoryError("not an exact repository path: " + value)
    return path


def _unique_rows(rows, key, fields, label):
    if not isinstance(rows, list) or not rows:
        raise InventoryError("empty or malformed declaration: " + label)
    seen = set()
    for row in rows:
        _exact_fields(row, fields, label)
        _text(row[key], key)
        if row[key] in seen:
            raise InventoryError("duplicate declaration: " + row[key])
        seen.add(row[key])
    return rows


def _node_path(node, *, collection=False):
    parts = node.split("::")
    path = _relative_path(parts[0]).as_posix()
    if not path.startswith("tests/") or not path.endswith(".py"):
        raise InventoryError("not a test path: " + node)
    if collection:
        if len(parts) != 1 or path not in COLLECTION_SKIPS:
            raise InventoryError("unreviewed collection-level skip: " + node)
    elif len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z_]\w*", part) for part in parts[1:]):
        raise InventoryError("not an exact test identity: " + node)
    return path


def _dependency(value):
    if value not in DEPENDENCIES:
        raise InventoryError("unknown optional dependency")


def validate_inventory(inventory):
    """Validate the closed schema without importing or installing any peer."""
    _exact_fields(inventory, FIELDS, "inventory")
    if (inventory["schema"] != SCHEMA
            or inventory["qualification_scope"] != "declarations_only_not_profile_acceptance"):
        raise InventoryError("unsupported inventory schema or acceptance claim")
    provenance = inventory["provenance"]
    _exact_fields(provenance, {"public_commit", "full_junit_sha256", "conformance_sha256"}, "provenance")
    if not isinstance(provenance["public_commit"], str) or re.fullmatch(r"[0-9a-f]{40}", provenance["public_commit"]) is None:
        raise InventoryError("invalid public source commit")
    _digest(provenance["full_junit_sha256"])
    _digest(provenance["conformance_sha256"])
    for group in ("runtime_files", "source_files"):
        if not isinstance(inventory[group], dict) or not inventory[group]:
            raise InventoryError("empty source hash map: " + group)
        for name, digest in inventory[group].items():
            _relative_path(name)
            _digest(digest)
            if group == "runtime_files" and not any(name.startswith(root + "/") for root in RUNTIME_ROOTS):
                raise InventoryError("invalid runtime path: " + name)
    failures = _unique_rows(inventory["absent_peer_failures"], "node",
                            {"node", "dependency", "reason", "observed_message"}, "failure")
    skips = _unique_rows(inventory["existing_optional_skips"], "node",
                         {"node", "dependency", "reason", "collection_skip"}, "skip")
    if {row["node"] for row in failures} & {row["node"] for row in skips}:
        raise InventoryError("ambiguous failure/skip declaration")
    for row in failures:
        _text(row["observed_message"], "observed failure message")
    referenced = set()
    for row in failures + skips:
        _dependency(row["dependency"])
        _text(row["reason"], "dependency reason")
        collection = row.get("collection_skip", False)
        if not isinstance(collection, bool):
            raise InventoryError("collection_skip must be boolean")
        if collection and row["dependency"] != "avatar_contract":
            raise InventoryError("invalid collection dependency")
        if row in failures and row["dependency"] != "avatar_contract":
            raise InventoryError("unreviewed failure dependency")
        referenced.add(_node_path(row["node"], collection=collection))
    requirements = _unique_rows(inventory["peer_requirements"], "requirement",
                                {"requirement", "command", "observed_exit_code", "dependency", "kind", "reason"},
                                "requirement")
    for row in requirements:
        if re.fullmatch(r"SPEC-[A-Z0-9-]+#R\d+", row["requirement"]) is None:
            raise InventoryError("not an exact requirement identity")
        _text(row["command"], "verify command")
        _text(row["reason"], "requirement reason")
        if (row["dependency"] != "avatar_contract"
                or type(row["observed_exit_code"]) is not int
                or (row["kind"], row["observed_exit_code"]) not in {("failure", 1), ("broken", 4)}):
            raise InventoryError("unreviewed requirement observation")
        referenced.add("docs/specs/" + row["requirement"].split("#")[0] + ".md")
    unproven = _unique_rows(inventory["unproven_external_specs"], "spec", {"spec", "reason"}, "unproven")
    if len(unproven) != 1 or unproven[0]["spec"] != "SPEC-AVATAR-CONTRACT-1":
        raise InventoryError("unreviewed external specification")
    _text(unproven[0]["reason"], "external specification reason")
    referenced.add("docs/specs/SPEC-AVATAR-CONTRACT-1.md")
    if set(inventory["source_files"]) != referenced:
        raise InventoryError("missing or unexplained classified source hashes")
    return inventory


def _read_source(source, name):
    path = source.joinpath(*_relative_path(name).parts)
    # No symlink, including a parent directory, may supply reviewed source bytes.
    current = source
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink():
            raise InventoryError("symlinked verification input: " + name)
    if not path.is_file():
        raise InventoryError("missing verification input: " + name)
    return path.read_bytes()


def _test_exists(content, node):
    parts = node.split("::")[1:]
    body = ast.parse(content).body
    for part in parts:
        matches = [item for item in body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   and item.name == part]
        if len(matches) != 1:
            raise InventoryError("missing or ambiguous test identity: " + node)
        body = matches[0].body


def verify_inventory_source(inventory, source):
    """Reject changed bytes, missing/new runtime files and stale selectors."""
    validate_inventory(inventory)
    source = Path(source).resolve(strict=True)
    runtime = set()
    for root in RUNTIME_ROOTS:
        directory = source / root
        if not directory.is_dir() or directory.is_symlink():
            raise InventoryError("missing or substituted runtime root: " + root)
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise InventoryError("symlinked runtime member")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                runtime.add(path.relative_to(source).as_posix())
    if runtime != set(inventory["runtime_files"]):
        raise InventoryError("runtime inventory changed")
    contents = {}
    for group in ("runtime_files", "source_files"):
        for name, digest in inventory[group].items():
            content = _read_source(source, name)
            if sha256(content) != digest:
                raise InventoryError("changed verification input: " + name)
            contents[name] = content
    for row in inventory["absent_peer_failures"] + inventory["existing_optional_skips"]:
        _test_exists(contents[row["node"].split("::")[0]], row["node"])
    # Use the same parser as native conformance, after verifying its bytes.
    from apatch.spec import parse_spec

    for row in inventory["peer_requirements"]:
        spec_id, requirement = row["requirement"].split("#")
        parsed = parse_spec(contents["docs/specs/" + spec_id + ".md"].decode("utf-8"), spec_id=spec_id)
        matches = [item for item in parsed.requirements if item.id == requirement]
        if len(matches) != 1 or matches[0].verify != row["command"]:
            raise InventoryError("changed requirement command: " + row["requirement"])
    return {"inventory_validated": True, "profile_passed": False,
            "qualification_scope": "declarations_only_not_profile_acceptance",
            "release_authorized": False, "external_acceptance": "not_checked",
            "pinned_runtime_files": len(runtime), "pinned_classified_files": len(inventory["source_files"])}


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_inventory(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_json)
    except (OSError, ValueError) as exc:
        raise InventoryError("cannot read inventory: " + str(exc)) from exc
    return validate_inventory(value)


OBSERVER = r'''
"""Observe the complete pytest run without deselecting or modifying any test."""
import importlib.util
import json
import os
from pathlib import Path
import pytest

data = {"run_id": os.environ["APATCH_OSS_RUN_ID"], "collected": [], "deselected": [],
        "collection_skips": {}, "collection_errors": [], "outcomes": {}, "finished": False}

def pytest_collection_finish(session):
    data["collected"] = [item.nodeid for item in session.items]
    option = session.config.option
    data["selection"] = {"args": list(session.config.args), "keyword": option.keyword,
                         "markexpr": option.markexpr, "maxfail": option.maxfail,
                         "collectonly": option.collectonly, "ignore": option.ignore,
                         "ignore_glob": option.ignore_glob, "deselect": option.deselect}
    data["dependencies"] = {name: importlib.util.find_spec(name) is not None
                            for name in ("avatar_contract", "tree_sitter_java", "trustchain", "mcp", "cryptography")}

def pytest_deselected(items):
    data["deselected"].extend(item.nodeid for item in items)

def pytest_collectreport(report):
    if report.skipped:
        data["collection_skips"][report.nodeid] = str(report.longrepr)
    elif report.failed:
        data["collection_errors"].append(report.nodeid)

def pytest_runtest_logreport(report):
    node = report.nodeid
    if report.failed:
        data["outcomes"][node] = {"outcome": "failed", "phase": report.when,
                                  "message": str(report.longrepr.reprcrash.message),
                                  "output": str(report.longrepr)}
    elif report.skipped and data["outcomes"].get(node, {}).get("outcome") != "failed":
        data["outcomes"][node] = {"outcome": "skipped", "phase": report.when,
                                  "message": str(report.longrepr)}
    elif report.when == "call" and node not in data["outcomes"]:
        data["outcomes"][node] = {"outcome": "passed", "phase": "call"}

@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    data.update(finished=True, exit_code=int(exitstatus))
    Path(os.environ["APATCH_OSS_OBSERVATION"]).write_text(json.dumps(data, indent=2) + "\n")
'''


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_json)
    except (OSError, ValueError) as exc:
        raise InventoryError("missing or malformed evidence: " + str(path)) from exc


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def public_files(source):
    manifest_path = source / "PUBLIC-SOURCE-MANIFEST.json"
    manifest = read_json(manifest_path)
    if manifest.get("private_git_history_included") is not False:
        raise InventoryError("requires a reviewed public source manifest")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise InventoryError("empty public source manifest")
    files = {}
    for row in rows:
        name = _relative_path(row["path"]).as_posix()
        if name in files or name == "PUBLIC-SOURCE-MANIFEST.json" or name.startswith((".git/", ".trustchain/", "apatch_pro/")):
            raise InventoryError("unsafe or duplicate public source member")
        _digest(row["sha256"])
        content = _read_source(source, name)
        if sha256(content) != row["sha256"]:
            raise InventoryError("public source manifest drift: " + name)
        files[name] = row["sha256"]
    files["PUBLIC-SOURCE-MANIFEST.json"] = sha256(manifest_path.read_bytes())
    return files


def snapshot_source(source, destination):
    """Copy only reviewed bytes; never clone ignored ledger, credentials or history."""
    files = public_files(source)
    destination.mkdir(parents=True, exist_ok=False)
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _read_source(source, name)
        if sha256(content) != files[name]:
            raise InventoryError("source changed while snapshotting: " + name)
        target.write_bytes(content)
        target.chmod((source / name).stat().st_mode & 0o777)
    subprocess.run(["git", "init", "-b", "main", str(destination)], check=True,
                   capture_output=True, timeout=30)
    return files


def source_unchanged(source, files):
    for name, digest in files.items():
        if sha256(_read_source(source, name)) != digest:
            raise InventoryError("verification modified reviewed source: " + name)


def verification_environment(work):
    if os.environ.get("PYTEST_ADDOPTS") or os.environ.get("PYTEST_PLUGINS"):
        raise InventoryError("caller-provided pytest options/plugins are not a qualified full run")
    # Keep secrets, service destinations and user workspace registries out of QA.
    env = {key: os.environ[key] for key in ("LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    private_home = work / "home"
    private_home.mkdir(exist_ok=True)
    env.update(HOME=str(private_home), USERPROFILE=str(private_home),
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", os.defpath),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               APATCH_WORKSPACE_REGISTRY=str(work / "workspaces.json"),
               APATCH_AVATAR_TRUST_POLICY=str(work / "absent-avatar-trust-policy.json"),
               PYTHONHASHSEED="0")
    return env


def _terminate_group(process):
    """Terminate only the process group created by run_capture."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    process.wait(timeout=5)


def run_capture(command, *, cwd, env, output, label, timeout=1800):
    if os.name != "posix":
        raise InventoryError("qualification process-group isolation requires POSIX")
    if not 0 < timeout <= 7200:
        raise InventoryError("invalid verification timeout")
    started = time.time()
    with (output / (label + ".stdout")).open("xb") as stdout, (output / (label + ".stderr")).open("xb") as stderr:
        process = subprocess.Popen([str(arg) for arg in command], cwd=cwd, env=env,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(process)
    result = {"command": [str(arg) for arg in command], "cwd": str(cwd),
              "exit_code": process.returncode, "timed_out": timed_out,
              "started_at": started, "finished_at": time.time()}
    write_json(output / (label + "-process.json"), result)
    return result


def suite_command(output):
    return [sys.executable, "-B", "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider",
            "-p", "oss_profile_observer", "--junitxml=" + str(output / "suite.xml")]


def contract_command():
    return [sys.executable, "-B", "-m", "apatch.cli", "conformance", "gate", "--live", "--ci-safe",
            "--exhaustive", "--jobs", "2", "--requirement-jobs", "1", "--json"]


def collect_suite(source, output, *, timeout=1800):
    output.mkdir(parents=True, exist_ok=False)
    plugin = output / "observer"
    plugin.mkdir()
    (plugin / "oss_profile_observer.py").write_text(OBSERVER, encoding="utf-8")
    env = verification_environment(output)
    run_id = uuid.uuid4().hex
    env.update(PYTHONPATH=str(plugin), APATCH_OSS_RUN_ID=run_id,
               APATCH_OSS_OBSERVATION=str(output / "observation.json"))
    process = run_capture(suite_command(output), cwd=source, env=env, output=output, label="suite", timeout=timeout)
    if process["timed_out"]:
        raise InventoryError("full suite timed out; raw output retained")
    observation = read_json(output / "observation.json")
    validate_suite_observation(observation, output / "suite.xml", process, run_id)
    return observation, process


def validate_suite_observation(observation, junit, process, run_id):
    if (observation.get("run_id") != run_id or observation.get("finished") is not True
            or process.get("timed_out") or process.get("exit_code") not in (0, 1)
            or observation.get("exit_code") != process["exit_code"]):
        raise InventoryError("stale, interrupted or incomplete suite")
    selection = observation.get("selection", {})
    if (selection.get("args") != ["tests/"]
            or any(selection.get(key) for key in ("keyword", "markexpr", "maxfail", "collectonly", "ignore", "ignore_glob", "deselect"))
            or observation.get("deselected") or observation.get("collection_errors")):
        raise InventoryError("filtered or failed collection")
    collected = observation.get("collected", [])
    outcomes = observation.get("outcomes", {})
    if not collected or len(set(collected)) != len(collected) or set(collected) != set(outcomes):
        raise InventoryError("missing or duplicate executed test identities")
    if any(row.get("outcome") not in {"passed", "failed", "skipped"} for row in outcomes.values()):
        raise InventoryError("unknown test outcome")
    failed = sum(row["outcome"] == "failed" for row in outcomes.values())
    skipped = sum(row["outcome"] == "skipped" for row in outcomes.values()) + len(observation.get("collection_skips", {}))
    if process["exit_code"] != (1 if failed else 0):
        raise InventoryError("suite exit code does not match outcomes")
    try:
        suites = list(ET.parse(junit).iter("testsuite"))
        if len(suites) != 1:
            raise InventoryError("unexpected JUnit structure")
        counts = suites[0].attrib
        if (int(counts["errors"]) != 0 or int(counts["failures"]) != failed
                or int(counts["skipped"]) != skipped
                or int(counts["tests"]) != len(collected) + len(observation.get("collection_skips", {}))
                or len(list(suites[0].iter("testcase"))) != int(counts["tests"])):
            raise InventoryError("JUnit and complete-run observation disagree")
        from collections import Counter
        from _pytest.junitxml import mangle_test_address
        expected_cases = Counter()
        for node in collected + list(observation.get("collection_skips", {})):
            parts = mangle_test_address(node)
            expected_cases[(".".join(parts[:-1]), parts[-1])] += 1
        actual_cases = Counter((row.get("classname", ""), row.get("name", ""))
                               for row in suites[0].iter("testcase"))
        if actual_cases != expected_cases:
            raise InventoryError("JUnit test identities disagree with complete-run observation")
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        raise InventoryError("malformed or inconsistent JUnit evidence") from exc


def expected_contract(source):
    from apatch.conformance import gated_specs, is_ci_safe, load_conformance_config
    from apatch.spec import parse_spec_file

    config = load_conformance_config(str(source))
    if not config.get("enabled"):
        raise InventoryError("standing contract must be enabled")
    result = {}
    for spec_id in gated_specs(str(source), config):
        parsed = parse_spec_file(str(source / "docs/specs" / (spec_id + ".md")))
        result[spec_id] = {row.id: row.verify for row in parsed.requirements if row.verify and is_ci_safe(row.verify)}
    if not result:
        raise InventoryError("empty standing contract")
    return result


def collect_contract(source, output, *, timeout=1800):
    output.mkdir(parents=True, exist_ok=False)
    expected = expected_contract(source)
    process = run_capture(contract_command(), cwd=source, env=verification_environment(output),
                          output=output, label="conformance", timeout=timeout)
    if process["timed_out"] or process["exit_code"] not in (0, 1):
        raise InventoryError("standing contract run incomplete; raw output retained")
    contract = read_json(output / "conformance.stdout")
    if type(contract.get("ok")) is not bool or process["exit_code"] != (0 if contract["ok"] else 1):
        raise InventoryError("standing contract exit code disagrees with raw verdict")
    write_json(output / "conformance.json", contract)
    validate_contract_observation(contract, expected)
    validate_contract_policy(source, contract)
    return contract, process


def validate_contract_observation(contract, expected):
    rows = contract.get("per_spec", [])
    if (contract.get("enabled") is not True or contract.get("fail_fast") is not False
            or contract.get("live") is not True or contract.get("ci_safe") is not True
            or contract.get("verified_live") != len(expected)
            or contract.get("gated") != len(expected) or len(rows) != len(expected)
            or len({row.get("spec") for row in rows}) != len(expected)
            or {row.get("spec") for row in rows} != set(expected)
            or type(contract.get("contract_holds")) is not bool):
        raise InventoryError("incomplete or non-exhaustive standing contract")
    for row in rows:
        commands = expected[row["spec"]]
        if row.get("verify_ran") != len(commands):
            raise InventoryError("not every CI-safe requirement ran: " + row["spec"])
        if row.get("conformance") not in {"conformant", "drifted", "broken", "unproven"}:
            raise InventoryError("unqualified contract state")
        details = row.get("verify_details", [])
        if len({d["id"] for d in details}) != len(details):
            raise InventoryError("duplicate requirement outcome")
        if any(d["id"] not in commands or d["cmd"] != commands[d["id"]] for d in details):
            raise InventoryError("unreviewed requirement identity or command")
        if row["conformance"] in {"drifted", "broken"} and not details:
            raise InventoryError("missing requirement failure evidence")
        if row["conformance"] in {"conformant", "unproven"} and details:
            raise InventoryError("contract status hides requirement failures")
        if (row["conformance"] == "unproven") != (len(commands) == 0):
            raise InventoryError("unproven status disagrees with runnable requirements")


def collect_complete_run(source, output, *, timeout=1800):
    """Shared by both profiles: fresh separate trees, full raw suite and gate."""
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if output == source or source in output.parents:
        raise InventoryError("evidence directory must be outside reviewed source")
    output.mkdir(parents=True, exist_ok=False)
    suite_source, contract_source = output / 'suite-source', output / 'contract-source'
    files = snapshot_source(source, suite_source)
    if snapshot_source(source, contract_source) != files:
        raise InventoryError("source changed between verification snapshots")
    suite, suite_process = collect_suite(suite_source, output / 'suite', timeout=timeout)
    contract, contract_process = collect_contract(contract_source, output / 'contract', timeout=timeout)
    for root in (source, suite_source, contract_source):
        source_unchanged(root, files)
    evidence = {'suite': suite, 'suite_process': suite_process,
                'contract': contract, 'contract_process': contract_process,
                'source_files': files, 'complete': True,
                'suite_source': str(suite_source), 'contract_source': str(contract_source)}
    write_json(output / 'complete-run.json', evidence)
    return evidence


def _selected_nodes(command, nodes):
    import fnmatch
    import shlex
    args = shlex.split(command)
    if args[:3] not in (['python3', '-m', 'pytest'], ['python', '-m', 'pytest']):
        raise InventoryError('unreviewed requirement runner')
    selectors = [value for value in args[3:] if value.startswith('tests/')]
    if not selectors or any(value in args[3:] for value in ('-k', '-m', '--deselect', '--ignore', '-x')):
        raise InventoryError('unreviewed requirement selection')
    result = set()
    for node in nodes:
        for selector in selectors:
            file, *parts = selector.split('::')
            if fnmatch.fnmatchcase(node.split('::')[0], file) and (
                    not parts or node == selector or node.startswith(selector + '::')):
                result.add(node)
    return result


def _validate_absent_requirement(detail, declaration, failures, skipped):
    if (detail.get('kind') != declaration['kind']
            or type(detail.get('exit_code')) is not int
            or detail['exit_code'] != declaration['observed_exit_code']
            or detail.get('cmd') != declaration['command']):
        raise InventoryError('changed requirement failure: ' + declaration['requirement'])
    stdout, stderr = detail.get('stdout_tail', ''), detail.get('stderr_tail', '')
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise InventoryError('malformed requirement output')
    if detail['kind'] == 'broken':
        import shlex
        selectors = [value for value in shlex.split(detail['cmd']) if value.startswith('tests/')]
        if (len(selectors) != 1 or selectors[0].split('::')[0] not in COLLECTION_SKIPS
                or re.fullmatch(r'\s*1 skipped in [^\n]+\s*', stdout) is None
                or not stderr.strip().startswith('ERROR: found no collectors for ')
                or not stderr.strip().endswith(selectors[0])
                or len(stderr.strip().splitlines()) != 1):
            raise InventoryError('unexplained uncollectable requirement')
        return
    expected = _selected_nodes(detail['cmd'], failures)
    reported = re.findall(r'^FAILED (tests/\S+)(?:\s+-|\s*$)', stdout, flags=re.MULTILINE)
    counts = re.findall(r'(\d+) failed(?:,| in|\s)', stdout)
    if (not expected or set(reported) != expected or len(reported) != len(expected)
            or not counts or int(counts[-1]) != len(expected)):
        raise InventoryError('requirement output contains unreviewed or incomplete failures')
    skip_counts = re.findall(r'(\d+) skipped(?:,| in|\s)', stdout)
    expected_skips = _selected_nodes(detail['cmd'], skipped)
    if (int(skip_counts[-1]) if skip_counts else 0) != len(expected_skips):
        raise InventoryError('unreviewed requirement skip count')
    if re.search(r'(\d+) error(?:s)?(?:,| in|\s)', stdout) or 'ERROR' in stderr:
        raise InventoryError('requirement has an unrelated collection/runtime error')


def qualify_standalone(inventory, evidence):
    """Classify complete reviewed evidence, never turn raw failures into raw PASS."""
    validate_inventory(inventory)
    if evidence.get('complete') is not True:
        raise InventoryError('incomplete profile evidence')
    files = evidence.get('source_files', {})
    if any(files.get(path) != value for group in ('runtime_files', 'source_files')
           for path, value in inventory[group].items()):
        raise InventoryError('evidence is not bound to reviewed source')
    suite, contract = evidence['suite'], evidence['contract']
    dependencies = suite.get('dependencies', {})
    expected_dependencies = {'avatar_contract', 'tree_sitter_java', 'trustchain', 'mcp', 'cryptography'}
    if (set(dependencies) != expected_dependencies or any(type(v) is not bool for v in dependencies.values())
            or dependencies['avatar_contract'] is not False
            or not all(dependencies[name] for name in ('trustchain', 'mcp', 'cryptography'))):
        raise InventoryError('standalone requires absent Avatar and present public verification dependencies')
    outcomes = suite.get('outcomes', {})
    failures = {node: row for node, row in outcomes.items() if row.get('outcome') == 'failed'}
    declared_failures = {row['node']: row for row in inventory['absent_peer_failures']}
    if set(failures) != set(declared_failures):
        raise InventoryError('unexpected failure or changed absence behavior')
    for node, row in failures.items():
        if row.get('phase') != 'call' or row.get('message') != declared_failures[node]['observed_message']:
            raise InventoryError('changed reviewed failure: ' + node)
    expected_skips = {row['node']: row for row in inventory['existing_optional_skips']
                      if dependencies[row['dependency']] is False}
    actual_skips = {node: row for node, row in outcomes.items() if row.get('outcome') == 'skipped'}
    collected_skips = suite.get('collection_skips', {})
    if (set(actual_skips) != {node for node, row in expected_skips.items() if not row['collection_skip']}
            or set(collected_skips) != {node for node, row in expected_skips.items() if row['collection_skip']}):
        raise InventoryError('unexpected or missing optional skip')
    for node in actual_skips:
        message = actual_skips[node].get('message', '')
        dependency = expected_skips[node]['dependency']
        marker = 'avatar_contract' if dependency == 'avatar_contract' else 'tree-sitter-java'
        if not isinstance(message, str) or marker not in message:
            raise InventoryError('skip does not explain the absent dependency: ' + node)
    for node, message in collected_skips.items():
        if 'avatar_contract' not in message:
            raise InventoryError('unexplained collection skip')
    declared_requirements = {row['requirement']: row for row in inventory['peer_requirements']}
    actual_requirements, unproven = {}, set()
    rows = contract.get('per_spec', [])
    if len({row.get('spec') for row in rows}) != len(rows):
        raise InventoryError('duplicate specification result')
    for row in rows:
        status = row.get('conformance')
        if status not in {'conformant', 'drifted', 'broken', 'unproven'}:
            raise InventoryError('unexpected contract status')
        if status == 'unproven': unproven.add(row['spec'])
        details = row.get('verify_details', [])
        if (status in {'drifted', 'broken'}) != bool(details):
            raise InventoryError('unexplained contract failure')
        for detail in details:
            key = row['spec'] + '#' + detail['id']
            if key in actual_requirements or key not in declared_requirements:
                raise InventoryError('unreviewed or duplicate requirement failure: ' + key)
            _validate_absent_requirement(detail, declared_requirements[key], failures, expected_skips)
            actual_requirements[key] = detail
    if set(actual_requirements) != set(declared_requirements):
        raise InventoryError('changed requirement absence behavior')
    if unproven != {row['spec'] for row in inventory['unproven_external_specs']}:
        raise InventoryError('unexpected unproven contract')
    if type(contract.get('contract_holds')) is not bool:
        raise InventoryError('missing raw standing verdict')
    return {'profile': 'standalone', 'profile_passed': True,
            'raw_suite_passed': not failures, 'raw_contract_holds': contract['contract_holds'],
            'external_acceptance': 'not_checked', 'avatar_acceptance': 'unavailable',
            'release_authorized': False, 'classified_failures': sorted(failures),
            'classified_skips': sorted(expected_skips),
            'classified_requirements': sorted(actual_requirements),
            'unproven_external_specs': sorted(unproven)}


def _git(checkout, *args):
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0')
    proc = subprocess.run(['git', '-C', str(checkout), *args], env=env,
                          capture_output=True, timeout=30)
    if proc.returncode:
        raise InventoryError('canonical peer Git prerequisite failed')
    return proc.stdout


def _toml_version(content):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    return tomllib.loads(content.decode('utf-8'))['project']['version']


def avatar_prerequisite(source, checkout):
    """Require the source-declared pin, a clean exact checkout and matching installed bytes."""
    import io
    import tarfile
    text = _read_source(Path(source), 'pyproject.toml').decode('utf-8')
    pins = re.findall(r'avatar-contract @ git\+https://github\.com/petro1eum/avatar-contract\.git@([0-9a-f]{40})', text)
    if len(pins) != 1 or not checkout:
        raise InventoryError('Avatar requires the one source-declared pin and an explicit canonical checkout')
    pin = pins[0]
    checkout = Path(checkout).resolve(strict=True)
    if (_git(checkout, 'rev-parse', 'HEAD').decode().strip() != pin
            or Path(_git(checkout, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != checkout
            or _git(checkout, 'status', '--porcelain', '--untracked-files=all').strip()):
        raise InventoryError('canonical Avatar checkout is dirty or at the wrong commit')
    raw = _git(checkout, 'archive', '--format=tar', pin)
    files, package = {}, {}
    version = None
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            if member.isdir(): continue
            name = _relative_path(member.name).as_posix()
            if not member.isfile():
                raise InventoryError('canonical peer archive contains a substituted/symlinked member')
            content = archive.extractfile(member).read()
            if _read_source(checkout, name) != content:
                raise InventoryError('canonical checkout bytes differ from declared Git object')
            files[name] = sha256(content)
            if name.startswith('avatar_contract/'): package[name] = files[name]
            if name == 'pyproject.toml': version = _toml_version(content)
    if not version or 'avatar_contract/__init__.py' not in package:
        raise InventoryError('declared peer does not contain the canonical package')
    code = '''
import hashlib,importlib.util,json
from importlib import metadata
from pathlib import Path
try:
    spec=importlib.util.find_spec('avatar_contract')
    if spec is None or not spec.origin: raise ValueError('avatar_contract is absent')
    root=Path(spec.origin).parent
    files={}
    for p in root.rglob('*'):
        if p.is_symlink(): raise ValueError('symlinked installed peer member')
        if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':
            files['avatar_contract/'+p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    print(json.dumps({'ok':True,'version':metadata.version('avatar-contract'),'origin':str(root),'files':files}))
except Exception as exc:
    print(json.dumps({'ok':False,'error':str(exc)}))
'''
    proc = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=30)
    try:
        installed = json.loads(proc.stdout)
    except ValueError as exc:
        raise InventoryError('installed canonical peer is not inspectable') from exc
    if (proc.returncode or installed.get('ok') is not True
            or installed.get('version') != version or installed.get('files') != package):
        raise InventoryError('missing, substituted or incompatible installed Avatar contract')
    return {'pin': pin, 'checkout': str(checkout), 'version': version,
            'source_files': files, 'installed': installed, 'prerequisite_passed': True}


def _copy_checked_files(source, destination, files):
    destination.mkdir(parents=True, exist_ok=False)
    for name, digest in files.items():
        content = _read_source(Path(source), name)
        if sha256(content) != digest:
            raise InventoryError('canonical peer changed before isolated verification')
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod((Path(source) / name).stat().st_mode & 0o777)


def collect_shared_contract(source, output, prerequisite, *, timeout=1800):
    """Private peer copies stay outside the reviewed/public source tree."""
    import shlex
    from apatch.spec import parse_spec_file
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if output == source or source in output.parents:
        raise InventoryError('private shared evidence must stay outside reviewed public source')
    output.mkdir(parents=True, exist_ok=False)
    public, peer = output / 'apatch', output / 'avatar-contract'
    public_files = snapshot_source(source, public)
    _copy_checked_files(prerequisite['checkout'], peer, prerequisite['source_files'])
    suite, process = collect_suite(peer, output / 'canonical-suite', timeout=timeout)
    if process['exit_code'] != 0 or suite['collection_skips'] or any(
            row['outcome'] != 'passed' for row in suite['outcomes'].values()):
        raise InventoryError('canonical shared-contract suite failed or skipped checks')
    parsed = parse_spec_file(str(public / 'docs/specs/SPEC-AVATAR-CONTRACT-1.md'))
    checked = []
    for requirement in parsed.requirements:
        if not requirement.verify:
            raise InventoryError('shared contract contains an unexecutable requirement')
        result = run_capture(shlex.split(requirement.verify), cwd=public,
            env=verification_environment(output), output=output,
            label='shared-' + requirement.id, timeout=timeout)
        if result['timed_out'] or result['exit_code'] != 0:
            raise InventoryError('canonical shared requirement failed: ' + requirement.id)
        checked.append(requirement.id)
    source_unchanged(public, public_files)
    source_unchanged(peer, prerequisite['source_files'])
    refreshed = avatar_prerequisite(source, prerequisite['checkout'])
    if refreshed != prerequisite:
        raise InventoryError('canonical peer changed during qualification')
    return {'shared_contract_passed': True, 'pin': prerequisite['pin'],
            'requirements': checked, 'canonical_suite': suite}


def qualify_avatar(inventory, evidence, shared):
    validate_inventory(inventory)
    if evidence.get('complete') is not True or shared.get('shared_contract_passed') is not True:
        raise InventoryError('Avatar qualification is missing complete shared-contract evidence')
    suite = evidence['suite']
    deps = suite.get('dependencies', {})
    if (deps.get('avatar_contract') is not True
            or not all(deps.get(name) is True for name in ('trustchain', 'mcp', 'cryptography'))
            or type(deps.get('tree_sitter_java')) is not bool):
        raise InventoryError('Avatar was not installed in the actual suite process')
    if (not suite.get('outcomes') or any(row.get('outcome') not in {'passed', 'skipped', 'failed'}
            for row in suite['outcomes'].values())
            or any(evidence.get('source_files', {}).get(path) != digest
                   for group in ('runtime_files', 'source_files') for path, digest in inventory[group].items())):
        raise InventoryError('Avatar evidence is incomplete or not source-bound')
    if (re.fullmatch(r'[0-9a-f]{40}', shared.get('pin', '')) is None
            or not shared.get('requirements') or len(set(shared['requirements'])) != len(shared['requirements'])):
        raise InventoryError('canonical shared-contract evidence is incomplete')
    if suite.get('collection_skips') or any(row.get('outcome') == 'failed' for row in suite['outcomes'].values()):
        raise InventoryError('Avatar integration failures or missing collection coverage cannot be waived')
    skips = {node: row for node, row in suite['outcomes'].items() if row.get('outcome') == 'skipped'}
    expected = {'tests/test_matcher.py::test_evaluate_java_body_match'} if deps.get('tree_sitter_java') is False else set()
    if set(skips) != expected or any('tree-sitter-java' not in row.get('message', '') for row in skips.values()):
        raise InventoryError('Avatar integration skips cannot be waived')
    contract = evidence['contract']
    rows = contract.get('per_spec', [])
    if (not rows or len({row.get('spec') for row in rows}) != len(rows)
            or contract.get('contract_holds') is not True) or any(
            row.get('conformance') != 'conformant' and not (
                row.get('spec') == 'SPEC-AVATAR-CONTRACT-1' and row.get('conformance') == 'unproven')
            for row in rows):
        raise InventoryError('Avatar standing contract is not qualified')
    return {'profile': 'avatar', 'profile_passed': True, 'raw_suite_passed': True,
            'raw_contract_holds': contract['contract_holds'], 'external_acceptance': 'not_checked',
            'release_authorized': False, 'canonical_shared_contract_passed': True,
            'canonical_pin': shared['pin'], 'classified_skips': sorted(skips)}


def validate_contract_policy(source, contract):
    from collections import Counter
    from apatch.conformance import load_conformance_config, resolve_block_on
    config = load_conformance_config(str(source))
    blocking = resolve_block_on(config)
    counts = Counter(row['conformance'] for row in contract['per_spec'])
    holds = not any(counts[name] for name in blocking)
    mode = config.get('mode', 'advisory')
    if (contract.get('mode') != mode or contract.get('block_on') != blocking
            or contract.get('contract_holds') is not holds
            or contract.get('ok') is not (holds or mode != 'blocking')):
        raise InventoryError('raw standing verdict disagrees with the unchanged project policy')
    buckets = contract.get('buckets', {})
    if (not buckets or sum(buckets.values()) != len(contract['per_spec'])
            or any(value != counts[name] for name, value in buckets.items())
            or any(name not in buckets for name in counts)):
        raise InventoryError('raw standing buckets disagree with requirement evidence')


def inspect_installed_runtime():
    """Inspect a fresh isolated interpreter, not an import cached by this runner."""
    code = '''
import hashlib,importlib.util,json,sys
from importlib import metadata
from pathlib import Path
try:
    files={}
    for name in ('apatch','apatch_search_workflows'):
        spec=importlib.util.find_spec(name)
        if spec is None or not spec.origin: raise ValueError('missing runtime: '+name)
        root=Path(spec.origin).parent
        for p in root.rglob('*'):
            if p.is_symlink(): raise ValueError('symlinked installed runtime member')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':
                files[name+'/'+p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    dependencies={name:importlib.util.find_spec(name) is not None for name in
                  ('avatar_contract','tree_sitter_java','trustchain','mcp','cryptography','pytest','build')}
    versions={name:metadata.version(name) for name in ('apatch','trustchain','mcp','cryptography','pytest','build')}
    print(json.dumps({'ok':True,'files':files,'dependencies':dependencies,'versions':versions,
                      'python':sys.version,'executable':sys.executable}))
except Exception as exc:
    print(json.dumps({'ok':False,'error':str(exc)}))
'''
    process = subprocess.run([sys.executable, '-I', '-c', code],
                             capture_output=True, text=True, timeout=30)
    try:
        result = json.loads(process.stdout)
    except ValueError as exc:
        raise InventoryError('cannot inspect installed qualification runtime') from exc
    if process.returncode or result.get('ok') is not True:
        raise InventoryError('installed qualification runtime or public dependencies are missing')
    return result


def qualification_prerequisite(source, inventory, profile):
    files = public_files(source)
    if files.get('scripts/qualify_oss.py') != sha256(Path(__file__).read_bytes()):
        raise InventoryError('runner differs from reviewed public source')
    verify_inventory_source(inventory, source)
    expected = dict(inventory['runtime_files'])
    # The wheel also contains generated, byte-identical canonical consumer assets.
    setup = ast.parse(_read_source(source, 'setup.py'))
    declarations = [node.value for node in setup.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'CONSUMER_ASSETS'
                            for target in node.targets)]
    if len(declarations) != 1:
        raise InventoryError('missing or ambiguous packaged resource inventory')
    assets = ast.literal_eval(declarations[0])
    if not isinstance(assets, tuple) or not assets or len(set(assets)) != len(assets):
        raise InventoryError('invalid packaged resource inventory')
    for name in assets:
        _relative_path(name)
        if name not in files:
            raise InventoryError('unreviewed packaged consumer resource')
        expected['apatch/_consumer_assets/' + name] = files[name]
    installed = inspect_installed_runtime()
    if (installed.get('files') != expected or installed.get('versions', {}).get('apatch')
            != _toml_version(_read_source(source, 'pyproject.toml'))):
        raise InventoryError('installed APatch is not the reviewed source version and bytes')
    deps = installed.get('dependencies', {})
    if (not all(deps.get(name) is True for name in ('trustchain', 'mcp', 'cryptography', 'pytest', 'build'))
            or deps.get('avatar_contract') is not (profile == 'avatar')):
        raise InventoryError('qualification dependencies do not match the named profile')
    console = Path(sys.executable).parent / 'apatch'
    if (not console.is_file() or console.is_symlink()
            or console.read_text().splitlines()[0] != '#!' + sys.executable):
        raise InventoryError('apatch console is not bound to this qualification interpreter')
    return installed


def run_profile(profile, source, output, *, avatar_checkout=None, timeout=1800):
    """The release entrypoint always collects fresh evidence; it cannot import a PASS."""
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if output == source or source in output.parents:
        raise InventoryError('evidence must be outside reviewed public source')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'apatch.oss-qualification-report.v1', 'profile': profile,
              'run_id': uuid.uuid4().hex, 'started_at': time.time(),
              'profile_passed': False, 'raw_suite_passed': None, 'raw_contract_holds': None,
              'external_acceptance': 'not_checked', 'release_authorized': False,
              'complete': False, 'source': str(source), 'evidence': str(output)}
    try:
        if profile not in {'standalone', 'avatar'}:
            raise InventoryError('unknown qualification profile')
        inventory = load_inventory(source / 'docs/oss-verification-profiles.json')
        before = qualification_prerequisite(source, inventory, profile)
        report['runtime'] = {key: value for key, value in before.items() if key != 'files'}
        peer = avatar_prerequisite(source, avatar_checkout) if profile == 'avatar' else None
        if profile == 'standalone' and avatar_checkout is not None:
            raise InventoryError('standalone must not read a private Avatar checkout')
        evidence = collect_complete_run(source, output / 'complete', timeout=timeout)
        report.update(raw_suite_passed=evidence['suite_process']['exit_code'] == 0,
                      raw_contract_holds=evidence['contract']['contract_holds'])
        if profile == 'avatar':
            shared = collect_shared_contract(source, output / 'shared', peer, timeout=timeout)
            write_json(output / 'shared-contract.json', shared)
            result = qualify_avatar(inventory, evidence, shared)
        else:
            result = qualify_standalone(inventory, evidence)
        if qualification_prerequisite(source, inventory, profile) != before:
            raise InventoryError('qualification runtime changed during execution')
        report.update(result, complete=True,
                      source_manifest_sha256=evidence['source_files']['PUBLIC-SOURCE-MANIFEST.json'])
    except (InventoryError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    report['finished_at'] = time.time()
    write_json(output / 'qualification.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('standalone', 'avatar'), required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory outside the public source checkout')
    parser.add_argument('--avatar-checkout', type=Path)
    parser.add_argument('--timeout', type=int, default=1800, help='Seconds per complete run (1..7200)')
    args = parser.parse_args(argv)
    try:
        if not 0 < args.timeout <= 7200:
            raise InventoryError('timeout must be 1..7200 seconds')
        report = run_profile(args.profile, args.source, args.output,
                             avatar_checkout=args.avatar_checkout, timeout=args.timeout)
    except (InventoryError, OSError) as exc:
        print(json.dumps({'profile_passed': False, 'release_authorized': False,
                          'external_acceptance': 'not_checked', 'error': str(exc)}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['profile_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
