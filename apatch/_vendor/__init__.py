"""Third-party code bundled with APatch under a private namespace.

``apatch._vendor.avatar_contract`` is the canonical MIT ``avatar-contract`` package
at the exact commit recorded in ``avatar_contract/UPSTREAM.json``. Only its own
imports are mechanically rewritten to this namespace; ``python
scripts/vendor_avatar_contract.py --check`` proves byte identity with upstream.

APatch imports the Avatar contract only from here and never from a top-level
``avatar_contract`` module, so a separately installed ``avatar-contract``
distribution of any version cannot shadow or change APatch behaviour.
"""
