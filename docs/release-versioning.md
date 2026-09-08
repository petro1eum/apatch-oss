# Release & versioning policy

**Canonical version:** `pyproject.toml` → `[project].version` (semver).  
`apatch --version`, `apatch_doctor.version`, and MCP health read `apatch.__version__`.

**Loader:** in a source checkout, `__version__` reads `pyproject.toml` first (so dev bumps work before `pip install -e`); installed wheels use `importlib.metadata`.

**Not the same as:** RFP-005 monitor maturity labels («0.2 / 0.3») — those describe the reference monitor, not the pip package.

---

## Semver rules (pre-1.0)

| Bump | When | Examples |
|------|------|----------|
| **PATCH** `0.x.Y` | Bugfix only; no new MCP tools or RFP tranche | MCP param fix, hook false positive |
| **MINOR** `0.Y.0` | Completed RFP phase, new MCP tool family, new governed workflow | RFP-007…014 wave, 53→70 MCP tools |
| **MAJOR** `1.0.0` | Stable MCP API promise; intentional breaking tool renames | Future consumer freeze |

Until **1.0.0**, treat **minor** as a feature wave, **patch** as maintenance.

---

## Cadence

1. **Every user-visible change** → one line under `CHANGELOG.md` → `[Unreleased]`.
2. **When `mcp_health.tool_count` changes** → update `docs/README.md` and `docs/mcp_setup.md` tool table note.
3. **Cut a release** when either:
   - an RFP tranche is attested (e.g. SPEC-INTERFERENCE-* done), or
   - `[Unreleased]` spans ≥2 weeks or ≥1 minor feature area, or
   - MCP tool count grew by **≥3** since last tag.

Do not leave `[Unreleased]` growing for months while `pip` still shows an old minor.

---

## Release checklist

```text
1. CHANGELOG: move [Unreleased] → [X.Y.Z] — YYYY-MM-DD; leave empty [Unreleased]
2. pyproject.toml version bump (single source of truth)
3. docs/README.md «Пакет: X.Y.Z»
4. pytest tests/test_version.py -q
5. git commit + tag vX.Y.Z + push
6. pip install -e ".[mcp,dev,yaml]" + MCP restart (apatch source repo)
```

---

## Changelog grouping

- Group by **RFP / SPEC**, not by every attested `Rk`.
- **Added** — tools, workflows, specs shipped.
- **Changed** — behaviour without breaking contract.
- **Fixed** — bugs.

---

## Anti-patterns

- Duplicating version in `apatch/__init__.py` as a hardcoded string (use loader + test).
- Merging attested features without a CHANGELOG line.
- Updating tool count in docs but not bumping package version.
- Confusing RFP monitor «0.3» with package `0.3.0`.
