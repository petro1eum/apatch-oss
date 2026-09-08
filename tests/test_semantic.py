import os
import pytest
import json
from click.testing import CliRunner
from apatch.semantic_parser import parse_markdown_blocks, compile_markdown, extract_blocks_by_sid
from apatch.matcher import ASTMatcher
from apatch.cli import cli

def test_parse_markdown_blocks():
    content = """# Header 1

Some text paragraph here.
With two lines.

- Item 1
- Item 2

```python
def foo():
    pass
```

| Col 1 | Col 2 |
|---|---|
| A | B |
"""
    blocks = parse_markdown_blocks(content)
    
    # We expect blocks:
    # 1. header (Header 1)
    # 2. blank_line
    # 3. paragraph (Some text...)
    # 4. blank_line
    # 5. list (- Item 1...)
    # 6. blank_line
    # 7. code_block (```python...)
    # 8. blank_line
    # 9. table (| Col 1...)
    
    non_blank_blocks = [b for b in blocks if b.block_type != 'blank_line']
    assert len(non_blank_blocks) == 5
    
    assert non_blank_blocks[0].block_type == 'header'
    assert "Header 1" in non_blank_blocks[0].content
    
    assert non_blank_blocks[1].block_type == 'paragraph'
    assert "Some text paragraph" in non_blank_blocks[1].content
    
    assert non_blank_blocks[2].block_type == 'list'
    assert "- Item 1" in non_blank_blocks[2].content
    
    assert non_blank_blocks[3].block_type == 'code_block'
    assert "def foo():" in non_blank_blocks[3].content
    
    assert non_blank_blocks[4].block_type == 'table'
    assert "| Col 1 |" in non_blank_blocks[4].content

def test_compile_markdown():
    content = """# Intro

This is first claim.

<!-- @sid:claim_99 -->
This is an existing claim.
"""
    compiled, kmap = compile_markdown(content)
    
    # Intro header and first claim should get new incremental IDs
    # claim_99 should be preserved and max incremented from it.
    assert "<!-- @sid:claim_100 -->" in compiled or "<!-- @sid:claim_101 -->" in compiled
    assert "<!-- @sid:claim_99 -->" in compiled
    
    assert len(kmap) == 3
    assert "claim_99" in kmap
    assert kmap["claim_99"]["type"] == "paragraph"
    assert "This is an existing claim." in kmap["claim_99"]["content"]

def test_matcher_semantic_sid(tmp_path):
    doc_file = tmp_path / "spec.md"
    content = """<!-- @sid:claim_1 -->
# Specification

<!-- @sid:claim_2 -->
Hyperbolic attention scales sublinearly for long sequences under test.
"""
    with open(doc_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    matcher = ASTMatcher(str(doc_file))
    
    # Agent wants to update claim_2 but its target text has slightly drifted in the codebase
    old_str = """<!-- @sid:claim_2 -->
Hyperbolic attention scales sublinearly for long sequences.
"""
    new_str = """<!-- @sid:claim_2 -->
Hyperbolic attention scales sublinearly for long sequences (O(N) vs O(N^2)).
"""
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "semantic-sid"
    assert "O(N) vs O(N^2)" in replaced
    assert "# Specification" in replaced

def test_matcher_document_fuzzy_fallback(tmp_path):
    doc_file = tmp_path / "spec.md"
    content = """# Specification

Hyperbolic attention scales sublinearly for long sequences under test in active evaluation.
"""
    with open(doc_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    matcher = ASTMatcher(str(doc_file))
    
    # AI proposes change without knowing or having SID tags
    old_str = """Hyperbolic attention scales sublinearly for long sequences in active evaluation."""
    new_str = """<!-- @sid:claim_2 -->\nHyperbolic attention scales sublinearly for long sequences (O(N) complexity)."""
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "document-fuzzy"
    assert "O(N) complexity" in replaced
    assert "# Specification" in replaced

def test_cli_compile_command(tmp_path):
    doc_file = tmp_path / "guide.md"
    content = """# Compilation Guide

Step 1 is simple.
"""
    with open(doc_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    runner = CliRunner()
    result = runner.invoke(cli, ["compile", str(doc_file), "--no-trustchain"])
    
    assert result.exit_code == 0
    assert "Compiled" in result.output
    assert "Exported knowledge map" in result.output
    
    # Verify file is updated on disk
    with open(doc_file, "r", encoding="utf-8") as f:
        compiled_text = f.read()
        
    assert "<!-- @sid:claim_1 -->" in compiled_text
    assert "<!-- @sid:claim_2 -->" in compiled_text
    
    # Verify knowledge map JSON exists
    map_file = tmp_path / "knowledge_map.json"
    assert map_file.exists()
    with open(map_file, "r", encoding="utf-8") as f:
        kmap = json.load(f)
        
    assert len(kmap) == 2
    assert "claim_1" in kmap
    assert kmap["claim_1"]["type"] == "header"
