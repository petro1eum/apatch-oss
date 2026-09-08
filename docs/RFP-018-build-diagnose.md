# RFP-018: Build-Aware Diagnostic Loop

> **Status:** MVP shipped (SPEC-BUILD-DIAGNOSE-1, tests green)
> **Depends on:** RFP-004 (verify_run), RFP-016 (artifact lifecycle)
> **Date:** 2026-06-10

---

## 1. Problem

`apatch_verify_run` / `apply_session` treat compiler output as opaque text.
Contract drift (`SparseOmega::get_concept_name` removed) surfaces as `VERIFY_FAILED`
with rollback — the agent must manually interpret stderr.

## 2. Goal

**Compiler output as first-class structured input:**

```text
verify / make → build_log.json
              → diagnostics.json (parsed errors + type context + suggestions)
              → agent crafts governed needles (NOT auto-apply)
```

## 3. MVP scope (SPEC-BUILD-DIAGNOSE-1)

| In | Out (later) |
|----|-------------|
| clang/gcc `file:line: error:` parser | MSVC, rustc, pyright |
| `missing_member` error type | full diagnostic taxonomy |
| C++ regex member extraction | libclang / compile_commands.json |
| fuzzy member suggestions | auto needle synthesis |
| `.apatch/build_log.json` + `diagnostics.json` | AGL registration (RFP-016 Phase 2) |

## 4. API

### Module: `apatch/build_diagnose.py`

- `parse_compiler_output(text) → Diagnostic[]`
- `extract_cpp_class_members(source, class_name) → str[]`
- `run_build_diagnose(target_dir, verify=, log_text=, write_artifacts=)`

### MCP: `apatch_build_diagnose`

```text
apatch_build_diagnose(target_dir=".", verify="make", log_text=None)
```

`log_text` — parse-only mode (tests, replay). Default: run `verify` or doctor toolchain default.

### CLI: `apatch build-diagnose`

```bash
apatch build-diagnose --target-dir . --verify "make" --json
apatch build-diagnose --log-file /path/to/build.log --json
```

## 5. Suggestion strategies (advisory)

| strategy | When |
|----------|------|
| `rename_call_site` | fuzzy match to existing member |
| `restore_api` | add missing method to type header |
| `adapter_or_literal` | no similar member — call-site workaround |

Suggestions are **hints** for `apatch_generate_batch` / `execute_next`, not autonomous patches.

## 6. Closed-loop workflow (agent)

```text
apatch_build_diagnose(verify="make")
  → read diagnostics[].suggestions
apatch_session_start(intent="fix missing_member …")
apatch_generate_batch(needles=[…])
apatch_apply_session → apatch_build_diagnose (rebuild)
```

## 7. Dogfood

ProbStates `SparseOmega::get_concept_name` — fixture in
`tests/fixtures/build_diagnose/`.

## 8. Superseded by RFP-022

Phase 1+ of [RFP-022 — Unified Diagnostics & Knowledge Graph](./RFP-022-unified-diagnostics-knowledge-graph.md)
generalizes this MVP into a unified `Diagnostic[]` schema (clang/pytest/spec/trust adapters).
RFP-018 remains the clang adapter reference implementation until migration is attested.
