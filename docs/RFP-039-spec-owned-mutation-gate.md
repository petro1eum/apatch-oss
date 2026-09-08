# RFP-039 -- SPEC-owned mutation gate

> **Status:** Proposed (August 25, 2026)
> **Scope:** Exact requirement ownership, write-set enforcement, and ledger-derived status for executable specifications
> **Executable contract:** `docs/specs/SPEC-SPEC-OWNERSHIP-GATE-1.md`

## Context

APatch already requires `spec_run` for SPEC work, but its first ownership gate
recognizes only direct SPEC files and slug/category contracts. Ordinary product
files can therefore remain "unowned" even when an executable SPEC describes
them. A requirement-bound agent can also add an undeclared file, and prose can
claim `IMPLEMENTED` while the TrustChain ledger still reports every requirement
as pending.

A signature proves that a mutation happened. It does not prove that the exact
requirement authorized the target, or that the resulting implementation matches
the contract. Advisory text is not an authorization boundary.

## Decision

A SPEC MAY opt into strict ownership with:

`> **ownership mode:** strict`

Every requirement in a strict SPEC SHALL declare its authorized write-set with
one `owns:` line. Entries are repository-relative exact paths or
segment-preserving directory prefixes ending in `/**`. Absolute paths, parent
traversal, unsupported wildcard forms, duplicate ownership, and ownership
ambiguity fail closed.

Before writing patch JSONL, the common mutation generator SHALL resolve every
target against both legacy slug ownership and strict requirement declarations.
A strict requirement session may mutate only targets declared by that exact
`SPEC-ID#Rk`; an undeclared extra target fails before generation.

The executable ledger remains the only completion authority. SPEC lint SHALL
reject top-level or per-requirement implementation claims that contradict the
derived ledger. Plan scaffolding SHALL use declared ownership as its
authoritative target list instead of guessing from prose.

Consumer profiles MAY provide exact sandbox protected/allow globs. The OLang
profile SHALL protect the complete language surface, governance contracts, and
agent contract when initialized with sandbox and enforcement. Installed-wheel
initialization must provide the same canonical templates and protection hooks as
source-checkout initialization, without relying on repository-relative files.
Missing required resources must fail before any consumer files are created.

### Bounded legacy slug surface

A slug word anywhere in a filename SHALL NOT confer ownership. With an existing
slug contract, compatibility coverage is limited to its exact contract file,
`categories/<slug>/**`, top-level `atomic/<slug>[_.-]*` files,
`config/agent_schemas/<slug>.json`, `config/categories/<slug>.yaml` or
`.yml`, and `test_<slug>[_.-]*` files beneath `tests/`. Legacy matching
remains case-insensitive. Overlapping legacy basename prefixes use the longest
slug; this rule SHALL NOT hide an explicitly declared owner.

Additional exact repository-relative files are read only from these existing
category-surface fields:

- `runtime_pipeline.category_preprocessor`, `category_behavior`, `query_builder`;
- `atomics.category_sources`, `schema_sources`, `guardrail_sources`;
- `spec_generation.live_test_modules`.

`shared_services`, `global_sources`, arbitrary prose, and requirement text
describe dependencies or behavior, not ownership. Surface entries are exact
paths, not globs. Malformed declarations and conflicting SPEC owners fail closed.
A slug may have one `.yaml` or `.yml` contract, never both. Strict `owns:`
declarations take precedence. The existing fix-forward exception is limited to
repairing the malformed contract itself.

Shared maintenance remains atomic: every target must appear in exactly one signed
partition. On an owner mismatch, `rejected_targets` SHALL contain only conflicting
targets and `partition_conflicts` SHALL identify each path, expected SPEC, and
actual requirement. No patch JSONL is generated for any part of the rejected batch.

## Acceptance

| ID | Requirement | Level |
|---|---|---|
| SO-A | Existing slug contracts resolve one exact owning SPEC without fuzzy matching. | MUST |
| SO-B | Generic and unbound mutations of SPEC-owned files fail before JSONL generation. | MUST |
| SO-C | A session bound to the correct SPEC requirement is allowed; a different SPEC is rejected. | MUST |
| SO-D | Remote worker returns the same structured failure and does not hide or bypass the guard. | MUST |
| SO-E | Agent guidance states that signatures do not replace requirement ownership. | MUST |
| SO-F | Strict SPEC requirements declare exact files or bounded `/**` prefixes; malformed or ambiguous declarations fail closed. | MUST |
| SO-G | A strict requirement cannot mutate any target outside its declared write-set, including a newly invented file. | MUST |
| SO-H | SPEC lint rejects manual implementation/completion claims that disagree with ledger-derived status. | MUST |
| SO-I | Needles and plan scaffolds use declared ownership as the authoritative target list. | MUST |
| SO-J | The OLang consumer profile is selectable through the public `init-consumer` command and installs sandbox/enforcement coverage for `o_lang/**`, governance docs, and `AGENTS.md`. | MUST |
| SO-K | A session bound to `spec-bootstrap:<SPEC>#Rk` may create that absent SPEC file through the local mutation channel; existing, foreign, or plain-bound SPEC files stay gated. | MUST |

## Security invariants

- The guard runs before patch JSONL is persisted.
- Ownership is exact and requirement-granular; ambiguity fails closed.
- A bare `spec:SPEC-ID` artifact is insufficient.
- A valid `SPEC-ID#Rk` artifact does not authorize undeclared targets.
- Completion is derived from TrustChain evidence, never from editable prose.
- The SPEC file that defines ownership is part of the attested requirement hash.
- Existing sandbox, lease, TrustChain, verification, and rollback checks remain in force.

## Migration

Legacy SPECs without `ownership mode: strict` retain current slug/direct-SPEC
behavior. They receive no inferred implementation ownership. Migration is
explicit: add strict mode and complete `owns:` declarations, then lint before
mutation.

## Non-goals

- Guessing the authorizing requirement.
- Inferring ownership from filenames, imports, verify commands, or narrative prose.
- Treating a bare SPEC artifact or green test as completion.
- Replacing `spec_run` or `execute_next`.
