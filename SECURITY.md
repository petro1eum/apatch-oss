# Security

APatch controls supported effects mediated through its governed runtime.
Installing the package does not enable every optional control, and an agent with
independent unrestricted shell/network access is outside that containment claim.
See [the security model](docs/security-one-pager.md) and
[runtime invariants](docs/governed-runtime-invariants.md).

## Reporting

Do not publish credentials, private source, exploit material or personal data in
a public issue. Use GitHub private vulnerability reporting if it has been enabled
for the eventual public repository. Otherwise contact the maintainer through an
already established private channel and agree a confidential delivery method.
No unverified reporting address or response-time commitment is advertised here.

## Maintainer publication checks

Review the exact source and built distributions for secrets and private material.
Preserve the OSS boundary, frozen judge integrity and explicit amendment history.
Never merge private repository history into the clean public history.
Generated keys, local identity, environment files and operational ledgers must
remain outside source control.

An automated secret scan is one check, not proof that all disclosure risks are absent.
