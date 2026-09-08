# Public-source portability amendment

Approved by the APatch owner on 2026-09-08 for the new OSS source snapshot only.

The original RFP-044 freeze names private historical commit
`381f9acb402af188636db631e9a8e4d701f208ca`.
The public snapshot intentionally does not include that commit or its ancestors.

The owner approved one change in `tests/test_sdd_integrity_contract.py`:
read `tests/fixtures/public_contracts/RFP-044-owner-frozen.md` instead of running
`git show` against the old private commit.
The fixture is a byte-identical export of that historical RFP, not a fresh copy
substituted whenever the live document changes.

The expected SHA-256 remains:

```text
deb673de57fef35d0f2369581641164c11645bc8d9a8232dab3abf172ce743ae
```

All original assertions remain, including current-document hash, freeze metadata,
upstream hash, acceptance IDs, exact requirement mappings and verification-command
checks. Missing or altered frozen bytes must fail. No skip, permissive fallback or
private-repository access replaces the check.

Historical files under `docs/contracts/` are preserved unchanged. Their old
protected-test hash describes the historical test, not the amended public test.
`public-source-portability.json` records the exact old/new hashes and the scope
of this approval. It is a publication amendment record, not a fabricated
cryptographic owner signature or a new historical acceptance event.

Runtime modules and the other frozen judge assets remain unchanged.
