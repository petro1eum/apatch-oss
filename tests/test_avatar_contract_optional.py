"""Regression: apatch must import and read without the avatar-contract package.

The shared schema is the `avatar` extra — bare installs (patching, timesheet
reading) never require it. A top-level `import avatar_contract` anywhere in
apatch broke `pip install apatch` once (#50) and CI collection twice (#51);
this test pins the contract: import lazily, degrade honestly.
"""
import importlib
import subprocess
import sys


_PROBE = r"""
import sys

class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == "avatar_contract" or name.startswith("avatar_contract."):
            raise ImportError("blocked: avatar_contract unavailable")
        return None

sys.meta_path.insert(0, _Block())

# Reading surfaces must import fine without the shared package...
import apatch.contribution as C
import apatch.timesheet as T
import apatch.work_assets  # noqa: F401
import apatch.work_asset_lifecycle  # noqa: F401
import apatch.work_asset_suggest  # noqa: F401

# ...the allowlist falls back to the frozen mirror (both proof keys present)...
assert {"attestation", "proof_ref", "methodology_tags"} <= T._ALLOWED_EVENT_KEYS

# ...and only EMISSION raises, with ImportError, not a silent local schema.
try:
    C.build_event({"session_id": "probe"}, ledger_rows=[])
except ImportError:
    pass
else:
    raise AssertionError("build_event must require avatar_contract")
print("OK")
"""


def test_apatch_imports_without_avatar_contract():
    """Run the probe in a clean interpreter so this venv's installed
    avatar_contract (and pytest's already-imported modules) can't leak in."""
    res = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout


def test_no_top_level_avatar_contract_imports():
    """Static guard: no module in apatch/ may import avatar_contract at top level."""
    import os

    import apatch

    root = os.path.dirname(apatch.__file__)
    offenders = []
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith(("from avatar_contract", "import avatar_contract")) \
                            and not line.startswith((" ", "\t")):
                        # top-level (unindented) import outside a try/except guard
                        offenders.append(os.path.relpath(path, root))
                        break
    # timesheet.py's guarded try-import is unindented but wrapped in try/except —
    # allow it explicitly by checking the guard.
    allowed = set()
    for rel in list(offenders):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
        if "except ImportError" in text.split("avatar_contract", 1)[1][:400]:
            allowed.add(rel)
    offenders = [o for o in offenders if o not in allowed]
    assert not offenders, f"unguarded top-level avatar_contract imports: {offenders}"
