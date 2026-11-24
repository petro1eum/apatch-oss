"""Versioned 0.8.55 export: immutable public Git inputs and the shipped 0.8.53 baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path


SCHEMA = "apatch.public-source.v6"
BASE = "d64fbc965d9fe130259cdf434403a696c67d4616"
PRODUCT = "41f00cb82771a34bcc46730cb79acd046c843f73"
MANIFEST = "PUBLIC-SOURCE-MANIFEST.json"
BASE_MANIFEST = "5ab8fa224fd8d3aa990cd15f9af654a98260b9ecfd5563ce5c82e6a329a23ae6"
WHEEL = "3fcdad914741fac3e2a7ce05998a03ed30831c75a5e8bf530f0e00a980c8a818"
SDIST = "2144fce1c1d53246c3db58baaff436a280c768eecb45b9ce37704ff76a43d42a"
ROOTS = ("apatch/", "apatch_search_workflows/")
# Already present in both 0.8.53 PyPI archives, absent from its Git manifest.
SHIPPED_ONLY = {
    "apatch/mcp/project_admission.py": "e89652f1b2156c49f7a7d73e4b33f79a6f2fda7218c179171ee0df12443b134b",
    "apatch/sdd_engineering_ownership_handoff.py": "d0ce38dade1d3026e4ab1d2cff2576491bd815bf5f16e4e160e9f0d6f4e7ca98",
}
RELEASE_FILES = {
    "README.pypi.md",
    "scripts/check_public_release_055.py", "scripts/qualify_oss.py",
    "docs/oss-verification-profiles.json", "docs/oss-verification.md",
    "tests/test_public_source_boundary.py", "tests/test_public_stabilization_boundary.py",
    "tests/test_oss_verification_profiles.py", "tests/portable_intake/test_portable_intake.py",
}


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def git(root, *args):
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                            capture_output=True, timeout=45)
    require(result.returncode == 0, "missing public Git input: " + str(args[:2]))
    return result.stdout


def tree(root, commit):
    entries = {}
    for line in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if not line:
            continue
        head, raw_path = line.split(b"\t", 1)
        mode, kind, oid = head.split()
        name = raw_path.decode()
        require(kind == b"blob" and mode in {b"100644", b"100755"}, name)
        entries[name] = mode, oid
    return entries


def document(root, source_commit):
    root = Path(root).resolve(strict=True)
    require(isinstance(source_commit, str) and re.fullmatch("[0-9a-f]{40}", source_commit),
            "expected an exact public source commit")
    git(root, "merge-base", "--is-ancestor", PRODUCT, source_commit)
    git(root, "merge-base", "--is-ancestor", source_commit, "HEAD")
    old_bytes = git(root, "show", BASE + ":" + MANIFEST)
    require(hashlib.sha256(old_bytes).hexdigest() == BASE_MANIFEST, "historical manifest")
    historical = json.loads(old_bytes)
    previous = {r["path"]: r["sha256"] for r in historical["files"]}
    shipped = {p: h for p, h in previous.items() if p.startswith(ROOTS)} | SHIPPED_ONLY
    product, source = tree(root, PRODUCT), tree(root, source_commit)
    require(set(product) <= set(source), "reviewed source file removed")
    changed = {p for p in source if p != MANIFEST and source[p] != product.get(p)}
    require(changed <= RELEASE_FILES, "changes outside release tooling: " + str(sorted(changed - RELEASE_FILES)))
    require(MANIFEST in source, "missing baseline")
    source.pop(MANIFEST)
    runtime = {p for p in source if p.startswith(ROOTS)}
    actual = set()
    for prefix in ROOTS:
        for path in (root / prefix).rglob("*"):
            require(not path.is_symlink(), "linked runtime member")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                actual.add(path.relative_to(root).as_posix())
    require(actual == runtime and len(runtime) == 284, "unexpected runtime members")
    rows, delta, modified = [], [], []
    for name, (mode, oid) in sorted(source.items()):
        path = root / name
        require(path.resolve() == path and path.is_file(), name)
        st = path.stat()
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, "linked source: " + name)
        data = path.read_bytes()
        git_blob = b"blob " + str(len(data)).encode() + b"\0" + data
        require(hashlib.sha1(git_blob, usedforsecurity=False).hexdigest().encode() == oid, "unreviewed bytes: " + name)
        executable = bool(st.st_mode & 0o111)
        require(executable == (mode == b"100755"), "source mode: " + name)
        digest = hashlib.sha256(data).hexdigest()
        rows.append(dict(path=name, sha256=digest, bytes=len(data),
                         mode="0o755" if executable else "0o644",
                         runtime_baseline_unchanged=shipped.get(name) == digest))
        if name in shipped and digest != shipped[name]:
            modified.append(name)
        if previous.get(name) != digest:
            delta.append(dict(path=name, before_sha256=previous.get(name), after_sha256=digest))
    require(set(shipped) <= runtime and not (set(previous) - set(source)), "missing baseline file")
    require(set(modified) == {"apatch/governed_work_mcp.py", "apatch/mcp/sanitize.py",
                              "apatch/mcp/server.py", "apatch/sdd_integrity.py", "apatch/tool_paths.py"}, "runtime delta")
    require(runtime - set(shipped) == {"apatch/task_budget.py"}, "runtime additions")
    return dict(schema=SCHEMA, version="0.8.55", baseline_commit=BASE,
                reviewed_source_commit=source_commit, product_commit=PRODUCT,
                historical_public_source=dict(public_commit=BASE, manifest_sha256=BASE_MANIFEST, manifest=historical),
                published_baseline=dict(version="0.8.53", wheel_sha256=WHEEL, sdist_sha256=SDIST,
                                        additional_runtime=SHIPPED_ONLY),
                private_git_history_included=False, generated_artifacts_excluded=True, self_excluded=MANIFEST,
                runtime_files_total=len(runtime), runtime_files_unchanged=len(shipped) - len(modified),
                runtime_files_added=sorted(runtime - set(shipped)), runtime_files_modified=modified,
                release_delta=delta, files=rows)


def validate(manifest, root):
    require(manifest == document(root, manifest.get("reviewed_source_commit")), "release manifest differs from reviewed inputs")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-from", metavar="EXACT_COMMIT")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path = root / MANIFEST
    if args.write_from:
        path.write_text(json.dumps(document(root, args.write_from), indent=2) + "\n")
    else:
        validate(json.loads(path.read_bytes()), root)
