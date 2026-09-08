# SPEC-DOC-OUTLINE-1 — Markdown outline mutations

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-DOC-OUTLINE-1`  
> **Anchors:** [RFP-021 design partner playbook](../design-partner-playbook.md) · complements `apatch compile` (@sid blocks)

## 0. Motivation

Agents editing numbered vision/spec markdown often insert a new `## N.` section and must
renumber following headings (`3`→`4`, `3.1`→`4.1`, …). N× `replace` needles on full
heading lines are fragile (Cyrillic titles, long lines). One structural mutation replaces
the cascade.

## R1 shift_outline needle

`apatch_generate_batch` action **`shift_outline`**:

```json
{
  "action": "shift_outline",
  "target_file": "docs/VISION.md",
  "after": "## 3.",
  "levels": [2, 3],
  "delta": 1
}
```

Increments the **first** outline segment (`3.1` → `4.1`) for ATX headers at listed levels
from anchor line onward (inclusive by default).

(verify: python3 -m pytest tests/test_doc_outline.py::test_shift_outline_from_section_three -q)

## R2 insert_section needle

Action **`insert_section`**: insert block before anchor; optional `shift_following` shifts
numbered headings at/after anchor **before** insert (typical «new §3» flow).

```json
{
  "action": "insert_section",
  "target_file": "docs/VISION.md",
  "before": "## 3.",
  "content": "## 3. Entity Model\n\n…",
  "shift_following": {"levels": [2, 3], "delta": 1}
}
```

(verify: python3 -m pytest tests/test_doc_outline.py::test_insert_section_shifts_following -q)

## R3 insert_before needle

Action **`insert_before`**: insert content before anchor without renumbering.

(verify: python3 -m pytest tests/test_doc_outline.py::test_insert_before_without_shift -q)

## R4 generate_batch integration

Needles emit one `replace_file_content` step (full-file transform via `apatch/doc_outline.py`).

(verify: python3 -m pytest tests/test_doc_outline.py::test_generate_batch_shift_outline_needle -q)

## Non-goals

- Renumbering prose cross-refs (`see §4`) — headings only
- SEText headings, `{#custom-id}` stripping
- `@sid` anchor mode (future)
