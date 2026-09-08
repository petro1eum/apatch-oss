import re
import os
import hashlib
from typing import Tuple, List, Dict, Optional

class SemanticBlock:
    def __init__(self, block_type: str, content: str, sid: Optional[str] = None):
        self.block_type = block_type  # 'header', 'code_block', 'table', 'list', 'paragraph', 'blank_line'
        self.content = content  # Raw lines including newlines
        self.sid = sid

    def __repr__(self):
        return f"SemanticBlock(type={self.block_type}, sid={self.sid}, lines={len(self.content.splitlines())})"

def parse_markdown_blocks(content: str) -> List[SemanticBlock]:
    """
    Parses a Markdown string into structural blocks, preserving formatting,
    whitespaces, blank lines, and existing <!-- @sid:claim_N --> comments.
    """
    # Normalize line endings to \n internally
    content = content.replace("\r\n", "\n")
    lines = content.split("\n")
    
    blocks: List[SemanticBlock] = []
    
    i = 0
    num_lines = len(lines)
    pending_sid: Optional[str] = None
    
    sid_pattern = re.compile(r'^\s*<!--\s*@sid:([a-zA-Z0-9_-]+)\s*-->\s*$')
    
    while i < num_lines:
        line = lines[i]
        
        # 1. Check for SID comment
        match = sid_pattern.match(line)
        if match:
            pending_sid = match.group(1)
            i += 1
            continue
            
        # 2. Check for blank line
        if not line.strip():
            # If there was a pending SID, it shouldn't attach to a blank line.
            # However, to be robust, we preserve pending_sid and attach it to the next logical block.
            blocks.append(SemanticBlock('blank_line', line))
            i += 1
            continue
            
        # 3. Check for Header
        if line.strip().startswith("#"):
            blocks.append(SemanticBlock('header', line, pending_sid))
            pending_sid = None
            i += 1
            continue
            
        # 4. Check for Code Block
        if line.strip().startswith("```"):
            code_lines = [line]
            i += 1
            # Consume until closing ```
            while i < num_lines:
                c_line = lines[i]
                code_lines.append(c_line)
                i += 1
                if c_line.strip().startswith("```"):
                    break
            blocks.append(SemanticBlock('code_block', "\n".join(code_lines), pending_sid))
            pending_sid = None
            continue
            
        # 5. Check for Table
        if line.strip().startswith("|"):
            table_lines = [line]
            i += 1
            while i < num_lines and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            blocks.append(SemanticBlock('table', "\n".join(table_lines), pending_sid))
            pending_sid = None
            continue
            
        # 6. Check for List block
        # We group contiguous list items into a single list block
        list_item_pattern = re.compile(r'^\s*([-*+]|\d+\.)\s+')
        if list_item_pattern.match(line):
            list_lines = [line]
            i += 1
            while i < num_lines:
                next_line = lines[i]
                # Keep accumulating list items or indented content
                if list_item_pattern.match(next_line) or (next_line.strip() and next_line.startswith(" ")):
                    list_lines.append(next_line)
                    i += 1
                else:
                    break
            blocks.append(SemanticBlock('list', "\n".join(list_lines), pending_sid))
            pending_sid = None
            continue
            
        # 7. Standard Paragraph (accumulate until next block boundary or blank line)
        para_lines = [line]
        i += 1
        while i < num_lines:
            next_line = lines[i]
            # Stop paragraph on blank line, header, code block, table, list item, or another sid comment
            if not next_line.strip():
                break
            if next_line.strip().startswith("#") or next_line.strip().startswith("```") or next_line.strip().startswith("|"):
                break
            if list_item_pattern.match(next_line):
                break
            if sid_pattern.match(next_line):
                break
            
            para_lines.append(next_line)
            i += 1
            
        blocks.append(SemanticBlock('paragraph', "\n".join(para_lines), pending_sid))
        pending_sid = None
        
    return blocks

def compile_markdown(content: str) -> Tuple[str, Dict]:
    """
    Parses Markdown, assigns incremental 'claim_N' stable IDs to blocks without them,
    and returns the compiled Markdown string and the knowledge map dictionary.
    """
    blocks = parse_markdown_blocks(content)
    
    # Scan for existing claim numbers to avoid collisions
    max_claim_num = 0
    claim_num_pattern = re.compile(r'^claim_(\d+)$')
    
    for b in blocks:
        if b.sid:
            match = claim_num_pattern.match(b.sid)
            if match:
                max_claim_num = max(max_claim_num, int(match.group(1)))
                
    # Assign unique IDs to blocks that require them
    # Note: 'blank_line' does NOT get a sid.
    for b in blocks:
        if not b.sid and b.block_type != 'blank_line':
            max_claim_num += 1
            b.sid = f"claim_{max_claim_num}"
            
    # Reassemble content
    compiled_parts = []
    knowledge_map = {}
    
    for b in blocks:
        if b.block_type == 'blank_line':
            compiled_parts.append(b.content)
        else:
            # Format with the inline HTML comment anchor
            compiled_parts.append(f"<!-- @sid:{b.sid} -->\n{b.content}")
            
            # Add to knowledge map
            sha256 = hashlib.sha256(b.content.encode("utf-8")).hexdigest()
            knowledge_map[b.sid] = {
                "type": b.block_type,
                "content": b.content,
                "sha256": sha256,
                "line_count": len(b.content.splitlines())
            }
            
    # Keep final newline structure consistent
    compiled_text = "\n".join(compiled_parts)
    if content.endswith("\n") and not compiled_text.endswith("\n"):
        compiled_text += "\n"
        
    return compiled_text, knowledge_map

def extract_blocks_by_sid(content: str) -> Dict[str, Tuple[str, int, int]]:
    """
    Extracts a dictionary mapping sid to (raw_content_including_sid_and_block, start_char_index, end_char_index)
    from a compiled Markdown text.
    """
    content = content.replace("\r\n", "\n")
    
    sid_pattern = re.compile(r'<!--\s*@sid:([a-zA-Z0-9_-]+)\s*-->')
    
    blocks_map = {}
    matches = list(sid_pattern.finditer(content))
    
    for idx, match in enumerate(matches):
        sid = match.group(1)
        start_pos = match.start()
        
        # End position is either the start of the next sid comment or the end of the document
        if idx + 1 < len(matches):
            end_pos = matches[idx + 1].start()
        else:
            end_pos = len(content)
            
        block_text = content[start_pos:end_pos]
        blocks_map[sid] = (block_text, start_pos, end_pos)
        
    return blocks_map

def fuzzy_match_paragraphs(old_block: str, target_content: str, threshold: float = 0.7) -> Tuple[bool, str]:
    """
    Fuzzy text similarity matcher. Checks all logical paragraphs in target_content
    for the best match with old_block. Used as Level 3 fallback for document patching.
    """
    old_clean = re.sub(r'<!--.*?-->', '', old_block).strip()
    old_words = set(re.findall(r'\b\w+\b', old_clean.lower()))
    
    if not old_words:
        return False, target_content
        
    target_clean = target_content.replace("\r\n", "\n")
    # Split by double-newlines to get paragraphs
    paragraphs = [p.strip() for p in target_clean.split("\n\n") if p.strip()]
    
    best_match = None
    best_score = 0.0
    
    for para in paragraphs:
        para_clean = re.sub(r'<!--.*?-->', '', para).strip()
        para_words = re.findall(r'\b\w+\b', para_clean.lower())
        if not para_words:
            continue
            
        para_word_set = set(para_words)
        
        # Calculate Jaccard similarity
        intersection = old_words.intersection(para_word_set)
        union = old_words.union(para_word_set)
        score = len(intersection) / len(union) if union else 0.0
        
        if score > best_score:
            best_score = score
            best_match = para
            
    if best_score >= threshold and best_match and best_match in target_clean:
        # Return the matched paragraph so the caller can splice the replacement.
        return True, best_match

    return False, ""
