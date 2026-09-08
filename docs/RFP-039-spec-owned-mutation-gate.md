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
agent contract when initialized with sandbox and enforcement.

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
