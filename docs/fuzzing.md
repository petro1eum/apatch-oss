# Patch-Drift Co-simulation & Fuzzing (RTG)

This document describes the design, architecture, and usage of the **Multi-lingual Patch-Drift Co-simulation & Fuzzing Suite** implemented in the `apatch` testing framework.

---

## 1. Overview & Problem Statement

In real-world collaborative development, AI agents propose code patches (`old_str` to `new_str` diffs) based on a snapshot of a file. However, developers or other automated processes may modify the target file in the meantime, introducing **context drift** (whitespace differences, newly added comments, or subtle signature changes).

To prevent match failures and ensure robust merging, `apatch` implements layered matching strategies:
1. **Level 1 (Exact Match)**
2. **Level 2 (Whitespace-Fuzzy Match)**
3. **Level 3 (AST-Fuzzy Block Match)**

To guarantee that these algorithms behave correctly under all possible variations of context drift across all supported programming languages, we implement the **Patch-Drift Random Test Generator (RTG)**. It continuously fuzzes the matching engine with hundreds of randomly generated drift scenarios and mathematically validates the resulting outputs.

---

## 2. Core Architecture

The fuzzing suite is implemented in [test_matcher_fuzz_rtg.py](../tests/test_matcher_fuzz_rtg.py) and is built on four core components:

```
┌──────────────────────────────────────────────────────────┐
│             AST-RTG: Structured Code Generator           │
│  (Generates valid syntax trees & mathematical bodies)    │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│          Target Mutator: Context-Drift Generator         │
│  (Applies random whitespace, comments, & signature shifts)│
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│          Matcher Core: apply_patch Execution             │
│  (Layers: Exact -> Whitespace-Fuzzy -> AST-Fuzzy)        │
└────────────────────────────┬─────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────┐
│            Oracle: Syntax & Semantic Checker             │
│  (Tree-sitter AST error verification & variable assertions)│
└──────────────────────────────────────────────────────────┘
```

### 2.1 AST-RTG: Structural Generation
Instead of plain text, the fuzzer uses **structural templates** mapped to each target language:
* Defines standard method signatures, parameters, and variable assignments specific to that language.
* Automatically selects different random mathematical expressions (e.g. `x + y`, `x * y - 10`, conditional ternary ops, absolute values) to represent body operations.

### 2.2 Target Mutator: Context-Drift
The drift mutator simulates developers modifying the code:
* **Whitespace Drift:** Selects random whitespace characters (spaces, tabs, newlines) and expands them within existing spacing to test whitespace-fuzzy resilience.
* **Comment Drift:** Injects random single-line comments before the function declaration (outside comments) or inside the function body (inside comments).
* **Signature Drift:** Replaces function parameters with drifted versions (e.g. renaming parameter `x` to `x_drifted`) to test AST-fuzzy signature preservation.

### 2.3 Oracle: Double-Verification
After a patch is applied, the Oracle runs two sets of checks:
1. **Semantic Verification:** Confirms the new body operation is merged, the drifted signature parameter is preserved (not overwritten by the AI's old signature), and outside comments are fully retained.
2. **AST-Syntax Verification:** Leverages the active tree-sitter parser of the language to verify that the generated code parses perfectly with **zero syntax errors** (checking for the absence of `ERROR` or `MISSING` nodes in the tree-sitter AST).

---

## 3. Self-Adaptive Testing (Resilient Architecture)

Because `apatch` supports 12 languages, some of which require lazy-loaded optional dependencies (e.g. `c_sharp`, `php`, `kotlin`, etc.), the fuzzer is designed to be **fully self-adaptive**:

1. **Active Grammar Detection:** The fuzzer calls `load_language(language)`.
2. **Full AST Fuzzing:** If the tree-sitter grammar package is available, the fuzzer tests the full pipeline including **Signature Drift** (which forces AST-Fuzzy matching) and performs detailed AST-Syntax Verification on the patched code.
3. **Fuzzy Fallback:** If the grammar package is missing in the testing environment, the fuzzer automatically disables Signature Drift and Inner Comments (since whitespace-fuzzy cannot match through modified signatures and inline body comments), and validates that Whitespace-Fuzzy and Outside Comment matching are still 100% successful.

This ensures the test suite runs and passes cleanly out-of-the-box on *any* machine while still maximizing the depth of testing wherever possible.

---

## 4. Running & Parameterization

The suite is parameterized over both language and seed iteration, generating a grid of 60 tests (12 languages x 5 iterations):

To execute the fuzzing suite:
```bash
.venv/bin/pytest tests/test_matcher_fuzz_rtg.py -v
```

Each iteration utilizes its parameterized index as the random seed. If a specific edge-case fails, it can be debugged deterministically by running that exact parameter, printing the generated code, and fixing the matcher logic.
