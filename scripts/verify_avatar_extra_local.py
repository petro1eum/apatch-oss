#!/usr/bin/env python3
"""Build and exercise the exact local Apatch + Avatar contract wheel pair."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
PIN_PREFIX = "avatar-contract @ git+https://github.com/petro1eum/avatar-contract.git@"
IGNORED = shutil.ignore_patterns(
    ".git", ".apatch", ".pytest_cache", "__pycache__", ".venv",
    "dist", "build", "*.egg-info", "*.pyc",
)


def run_checked(argv, *, cwd=None, env=None):
    result = subprocess.run(
        [str(value) for value in argv],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if result.returncode:
        raise RuntimeError(
            "command failed: {!r}\n{}\n{}".format(argv, result.stdout, result.stderr)
        )
    return result


def pinned_requirement(source):
    project = tomllib.loads(
        (source / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    values = project["optional-dependencies"].get("avatar")
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("avatar extra must contain exactly one requirement")
    requirement = values[0]
    if not requirement.startswith(PIN_PREFIX):
        raise RuntimeError("avatar extra is not pinned to the canonical repository")
    commit = requirement[len(PIN_PREFIX):]
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise RuntimeError("avatar extra pin must be a lowercase 40-hex commit")
    return requirement, commit


def contract_snapshot(source, commit, destination):
    run_checked(["git", "-C", source, "cat-file", "-e", commit + "^{commit}"])
    archive = destination.parent / "avatar-contract.tar"
    run_checked([
        "git", "-C", source, "archive", "--format=tar",
        "--output=" + str(archive), commit,
    ])
    destination.mkdir()
    with tarfile.open(archive, "r:") as bundle:
        root = destination.resolve()
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("unsafe archive member: " + member.name)
        bundle.extractall(destination)


def build_wheel(source, out_dir):
    out_dir.mkdir()
    run_checked([
        sys.executable, "-m", "build", "--no-isolation", "--wheel",
        "--outdir", out_dir, source,
    ])
    wheels = list(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one wheel")
    return wheels[0]


def wheel_metadata(wheel):
    with zipfile.ZipFile(wheel) as archive:
        name = next(
            item for item in archive.namelist()
            if item.endswith(".dist-info/METADATA")
        )
        return archive.read(name).decode("utf-8")


PROBE = r"""
import json
import os
from pathlib import Path
from apatch import contribution as C
from apatch import episode as E
from apatch.spec import resolve_requirement
from avatar_contract import ContributionEvent

spec_dir = Path("docs/specs")
spec_dir.mkdir(parents=True)
(spec_dir / "SPEC-AVATAR-RELEASE-1.md").write_text(
    "# SPEC-AVATAR-RELEASE-1 - Public release result\n\n"
    "## R1 Deliver review package\n\n"
    "(verify: true)\n",
    encoding="utf-8",
)
resolved = resolve_requirement(".", "SPEC-AVATAR-RELEASE-1#R1")
assert resolved["ok"] is True
key_id = "e2e-avatar-key"
session_id = "apatch_sess_avatar_release_probe"
C.resolve_identity = lambda *_a, **_k: {
    "key_id": key_id, "cert_fingerprint": None,
    "agent_id": "release-probe", "ca": "legacy", "trust_level": "claimed",
}
C.project_identity = lambda *_a, **_k: {
    "id": "avatar-release-probe", "name": "apatch", "remote": None,
}
C._ledger_rows_for_session = lambda *_a, **_k: [{
    "id": "op_avatar_release_probe",
    "timestamp": "2026-08-13T00:00:01+00:00",
    "payload": {
        "governed_session_id": session_id,
        "files": {"probe.txt": {}}, "insertions": 1, "deletions": 0,
    },
}, {
    "id": "op_avatar_release_gate",
    "tool_id": "apatch_probe",
    "timestamp": "2026-08-13T00:00:02+00:00",
    "payload": {
        "action": "probe_falsify",
        "gate_quality": "falsified",
        "verify_sha256": "a" * 64,
    },
}]
session = {
    "session_id": session_id,
    "intent": "private release command and internal prompt",
    "artifacts": [resolved["artifact"]],
    "started_at": "2026-08-13T00:00:00+00:00",
    "ended_at": "2026-08-13T00:01:00+00:00",
}
store = Path(os.environ["AVATAR_PROBE_DIR"])
event = C.emit_contribution(
    ".",
    session,
    store_dir=str(store),
    completion_summary="Delivered the public Avatar release result.",
)
assert event is not None
written = list((store / key_id).glob("*.json"))
assert len(written) == 1
wire = json.loads(written[0].read_text(encoding="utf-8"))
ContributionEvent.from_wire(wire).validate()
assert wire["event_id"] == event["event_id"]
assert wire["avatar_id"] == key_id
serialized = json.dumps(wire)
assert "private release command" not in serialized
assert wire["session"]["intent"] == "SPEC-AVATAR-RELEASE-1 - Public release result"
submission = wire["payload"]["review_submission"]
assert submission["delivery_summary"] == "Delivered the public Avatar release result."
assert submission["acceptance_criteria"] == ["Deliver review package"]
E._signature_status = lambda *_a, **_k: "verified"
episode = E.episode_from_event(wire, ".")
package = episode["review_package"]
assert package["status"] == "ready"
assert package["review_package_id"]
assert "private release command" not in json.dumps(package)
print("avatar_extra_emission=PASS")
print("avatar_review_package=PASS")
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apatch-source", type=Path, default=ROOT)
    parser.add_argument(
        "--avatar-contract-source", type=Path,
        default=ROOT.parent / "avatar-contract",
    )
    args = parser.parse_args()
    apatch_source = args.apatch_source.resolve()
    contract_source = args.avatar_contract_source.resolve()
    requirement, commit = pinned_requirement(apatch_source)

    with tempfile.TemporaryDirectory(prefix="apatch-avatar-extra-") as temp:
        work = Path(temp)
        apatch_copy = work / "apatch"
        shutil.copytree(apatch_source, apatch_copy, ignore=IGNORED)
        contract_copy = work / "avatar-contract"
        contract_snapshot(contract_source, commit, contract_copy)

        contract_project = tomllib.loads(
            (contract_copy / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        if contract_project.get("license") != {"text": "MIT"}:
            raise RuntimeError("pinned avatar-contract is not MIT-declared")

        contract_wheel = build_wheel(contract_copy, work / "contract-dist")
        apatch_wheel = build_wheel(apatch_copy, work / "apatch-dist")
        metadata = wheel_metadata(apatch_wheel)
        requires = [
            line for line in metadata.splitlines()
            if line.startswith("Requires-Dist: avatar-contract")
        ]
        if "Provides-Extra: avatar" not in metadata or len(requires) != 1:
            raise RuntimeError("Apatch wheel lost the avatar extra")
        if commit not in requires[0]:
            raise RuntimeError("Apatch wheel lost the immutable Avatar pin")

        venv = work / "venv"
        run_checked([sys.executable, "-m", "venv", venv])
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run_checked([
            python, "-m", "pip", "install", "--no-index", "--no-deps",
            contract_wheel, apatch_wheel,
        ])
        env = os.environ.copy()
        env["AVATAR_PROBE_DIR"] = str(work / "receipts")
        result = run_checked([python, "-I", "-c", PROBE], cwd=work, env=env)
        if "avatar_extra_emission=PASS" not in result.stdout:
            raise RuntimeError("ContributionEvent emission probe did not pass")

    print("avatar_extra_local=PASS contract_commit=" + commit)
    print("avatar_requirement=" + requirement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
