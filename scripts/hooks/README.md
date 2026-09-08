# apatch git hooks (agnostic, opt-in)

These hooks are committed but **not active until you enable them per machine**
(git never auto-runs hooks from a clone, by design).

## What they do

- **post-merge / post-checkout** → run `apatch mcp sync` so the MCP config is
  regenerated locally after every pull/checkout. The MCP config carries an
  **absolute, machine-specific interpreter path**, so it is generated per machine
  and never committed (`.apatch/mcp.json` and `.cursor/mcp.json` are gitignored).
- **pre-commit-trustchain** → blocks a commit whose staged source lacks an
  Ed25519 TrustChain proof (Ring-1 enforcement; only when `.apatch/enforcement.json`
  is present).

## One-time setup per machine

```sh
git config core.hooksPath scripts/hooks   # enable the hooks above
apatch mcp sync                            # generate the local MCP config now
```

After that, the MCP config self-refreshes on every pull/checkout, and a fresh
clone needs only these two commands — no machine paths live in the repo.

> `apatch doctor` also keeps `.apatch/mcp.json` healthy on demand.
