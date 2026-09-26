#!/usr/bin/env python3
"""Vendor the canonical avatar-contract package into ``apatch/_vendor``.

APatch bundles the ONE shared Avatar contract (Avatar Architecture Canon §7,
Rule 1) under the private name ``apatch._vendor.avatar_contract`` so that a
separately installed ``avatar-contract`` distribution can never collide with,
shadow or change it. The vendored bytes are the canonical bytes at an exact Git
commit; the only transformation is a mechanical, reversible import rewrite:

    ^(\\s*)from avatar_contract(\\.| )   ->  \\1from apatch._vendor.avatar_contract\\2
    ^(\\s*)import avatar_contract$       ->  \\1from apatch._vendor import avatar_contract

``UPSTREAM.json`` records the repository, commit, version, license, the rewrite
rule and the SHA-256 of every *upstream* file. ``--check`` proves byte identity by
applying the inverse rewrite to each vendored file and comparing its hash with the
recorded upstream hash; with ``--checkout`` it also proves the recorded hashes are
exactly the canonical Git objects at the recorded commit.

Usage::

    python scripts/vendor_avatar_contract.py --checkout ../avatar-contract --commit <sha>
    python scripts/vendor_avatar_contract.py --check [--checkout ../avatar-contract]

Regeneration reads Git objects (``git archive``), never the working tree, so a dirty
sibling checkout cannot leak into APatch.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = ROOT / "apatch" / "_vendor" / "avatar_contract"
REPOSITORY = "https://github.com/petro1eum/avatar-contract"
PACKAGE = "avatar_contract"
VENDORED_PACKAGE = "apatch._vendor.avatar_contract"
LICENSE_FILE = "LICENSE"
MANIFEST = "UPSTREAM.json"
SCHEMA = "apatch.vendored-upstream.v1"
REWRITE_RULE = {
    "applies_to": "*.py",
    "from_import": {
        "pattern": r"^(\s*)from avatar_contract(\.| )",
        "replacement": r"\1from apatch._vendor.avatar_contract\2",
    },
    "plain_import": {
        "pattern": r"^(\s*)import avatar_contract(\s*)$",
        "replacement": r"\1from apatch._vendor import avatar_contract\2",
    },
    "inverse": "exact: every rewritten line maps back to its upstream line",
}

_FORWARD_FROM = re.compile(rb"^(\s*)from avatar_contract(\.| )")
_FORWARD_IMPORT = re.compile(rb"^(\s*)import avatar_contract(\s*)$")
_UNSUPPORTED_IMPORT = re.compile(rb"^\s*import\s+avatar_contract\b")
_INVERSE_FROM = re.compile(rb"^(\s*)from apatch\._vendor\.avatar_contract(\.| )")
_INVERSE_IMPORT = re.compile(rb"^(\s*)from apatch\._vendor import avatar_contract(\s*)$")
_VENDOR_MARKER = re.compile(rb"apatch\._vendor")


class VendorError(ValueError):
    """The upstream input or the vendored copy is not the declared canonical contract."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _split_line(line: bytes) -> Tuple[bytes, bytes]:
    body = line.rstrip(b"\r\n")
    return body, line[len(body):]


def rewrite_source(data: bytes) -> bytes:
    """Forward rewrite of one upstream ``.py`` file; fails closed if not reversible."""
    out = []
    for line in data.splitlines(keepends=True):
        body, end = _split_line(line)
        if _VENDOR_MARKER.search(body):
            raise VendorError("upstream line already names apatch._vendor: ambiguous rewrite")
        if _FORWARD_FROM.match(body):
            body = _FORWARD_FROM.sub(rb"\1from apatch._vendor.avatar_contract\2", body, count=1)
        elif _FORWARD_IMPORT.match(body):
            body = _FORWARD_IMPORT.sub(rb"\1from apatch._vendor import avatar_contract\2", body, count=1)
        elif _UNSUPPORTED_IMPORT.match(body):
            raise VendorError("unsupported import form for mechanical rewrite: " + body.decode("utf-8", "replace"))
        out.append(body + end)
    result = b"".join(out)
    if inverse_rewrite(result) != data:
        raise VendorError("import rewrite is not exactly reversible")
    return result


def inverse_rewrite(data: bytes) -> bytes:
    """Map vendored ``.py`` bytes back to the exact upstream bytes."""
    out = []
    for line in data.splitlines(keepends=True):
        body, end = _split_line(line)
        if _INVERSE_FROM.match(body):
            body = _INVERSE_FROM.sub(rb"\1from avatar_contract\2", body, count=1)
        elif _INVERSE_IMPORT.match(body):
            body = _INVERSE_IMPORT.sub(rb"\1import avatar_contract\2", body, count=1)
        out.append(body + end)
    return b"".join(out)


def vendored_path(upstream_name: str) -> str:
    """Upstream repository path -> path relative to the vendored package root."""
    if upstream_name == LICENSE_FILE:
        return LICENSE_FILE
    prefix = PACKAGE + "/"
    if not upstream_name.startswith(prefix):
        raise VendorError("not a canonical package member: " + upstream_name)
    return upstream_name[len(prefix):]


def _is_python(name: str) -> bool:
    return name.endswith(".py")


def _git(checkout: Path, *args: str) -> bytes:
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMPDIR", "HOME") if key in os.environ}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(["git", "-C", str(checkout), *args], env=env, capture_output=True, timeout=60)
    if proc.returncode:
        raise VendorError("git failed: " + " ".join(args) + ": " + proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout


def _toml_version(content: bytes) -> str:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        import tomli as tomllib
    return str(tomllib.loads(content.decode("utf-8"))["project"]["version"])


def read_upstream(checkout: Path, commit: str) -> Tuple[Dict[str, bytes], str, str]:
    """Read the canonical package, LICENSE and version from Git objects at ``commit``."""
    if re.fullmatch(r"[0-9a-f]{40}", commit or "") is None:
        raise VendorError("commit must be a full lowercase 40-hex SHA")
    checkout = Path(checkout).resolve(strict=True)
    resolved = _git(checkout, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise VendorError("commit does not resolve to itself in the canonical checkout")
    raw = _git(checkout, "archive", "--format=tar", commit)
    files: Dict[str, bytes] = {}
    version = None
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            if member.isdir():
                continue
            name = PurePosixPath(member.name).as_posix()
            if not member.isfile():
                raise VendorError("canonical archive contains a non-regular member: " + name)
            content = archive.extractfile(member).read()
            if name == "pyproject.toml":
                version = _toml_version(content)
            if name == LICENSE_FILE or name.startswith(PACKAGE + "/"):
                if "__pycache__" in PurePosixPath(name).parts or name.endswith(".pyc"):
                    raise VendorError("canonical archive contains compiled files")
                files[name] = content
    if not version or PACKAGE + "/__init__.py" not in files or LICENSE_FILE not in files:
        raise VendorError("commit does not contain the canonical package, LICENSE and version")
    return files, version, _license_id(files[LICENSE_FILE])


def _license_id(content: bytes) -> str:
    if not content.startswith(b"MIT License"):
        raise VendorError("upstream LICENSE is not the MIT license text")
    return "MIT"


def build_manifest(files: Dict[str, bytes], commit: str, version: str, license_id: str) -> Dict[str, object]:
    return {
        "schema": SCHEMA,
        "name": "avatar-contract",
        "repository": REPOSITORY,
        "commit": commit,
        "version": version,
        "license": license_id,
        "package": PACKAGE,
        "vendored_as": VENDORED_PACKAGE,
        "rewrite": REWRITE_RULE,
        "files": {name: sha256(files[name]) for name in sorted(files)},
    }


def manifest_bytes(manifest: Dict[str, object]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def vendor(checkout: Path, commit: str, destination: Path = VENDOR_ROOT) -> Dict[str, object]:
    """Regenerate ``destination`` from the canonical Git objects at ``commit``."""
    files, version, license_id = read_upstream(checkout, commit)
    rendered = {}
    for name, content in files.items():
        rendered[vendored_path(name)] = rewrite_source(content) if _is_python(name) else content
    manifest = build_manifest(files, commit, version, license_id)
    rendered[MANIFEST] = manifest_bytes(manifest)
    if destination.exists():
        shutil.rmtree(destination)
    for relative, content in sorted(rendered.items()):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return manifest


def load_manifest(root: Path = VENDOR_ROOT) -> Dict[str, object]:
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VendorError("missing or malformed " + MANIFEST) from exc
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
            or manifest.get("license") != "MIT" or manifest.get("rewrite") != REWRITE_RULE
            or re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("commit") or "")) is None
            or not isinstance(manifest.get("files"), dict) or not manifest["files"]):
        raise VendorError("UPSTREAM.json is not a complete vendoring record")
    return manifest


def check(root: Path = VENDOR_ROOT, checkout: Path = None) -> Dict[str, object]:
    """Prove the vendored tree is exactly the recorded upstream bytes (inverse rewrite)."""
    manifest = load_manifest(root)
    expected = {vendored_path(name): (name, digest) for name, digest in manifest["files"].items()}
    present = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise VendorError("symlinked vendored member: " + path.as_posix())
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            present.add(path.relative_to(root).as_posix())
    if present != set(expected) | {MANIFEST}:
        raise VendorError("vendored file set differs from UPSTREAM.json: missing={} extra={}".format(
            sorted(set(expected) - present), sorted(present - set(expected) - {MANIFEST})))
    for relative, (name, digest) in sorted(expected.items()):
        content = (root / relative).read_bytes()
        upstream = inverse_rewrite(content) if _is_python(name) else content
        if sha256(upstream) != digest:
            raise VendorError("vendored bytes differ from upstream: " + relative)
        if _is_python(name) and rewrite_source(upstream) != content:
            raise VendorError("vendored file carries more than the mechanical rewrite: " + relative)
    result = {"ok": True, "commit": manifest["commit"], "version": manifest["version"],
              "files": len(expected), "canonical_checkout_checked": False}
    if checkout is not None:
        files, version, license_id = read_upstream(checkout, str(manifest["commit"]))
        if build_manifest(files, str(manifest["commit"]), version, license_id) != manifest:
            raise VendorError("UPSTREAM.json differs from the canonical checkout at its commit")
        result["canonical_checkout_checked"] = True
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkout", type=Path, help="Canonical avatar-contract Git checkout")
    parser.add_argument("--commit", help="Exact 40-hex commit to vendor")
    parser.add_argument("--check", action="store_true", help="Verify the vendored copy instead of regenerating")
    args = parser.parse_args(argv)
    try:
        if args.check:
            if args.commit:
                parser.error("--check reads the commit from UPSTREAM.json")
            result = check(VENDOR_ROOT, args.checkout)
        else:
            if not args.checkout or not args.commit:
                parser.error("regeneration requires --checkout and --commit")
            manifest = vendor(args.checkout, args.commit)
            result = {"ok": True, "commit": manifest["commit"], "version": manifest["version"],
                      "files": len(manifest["files"])}
    except (VendorError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
