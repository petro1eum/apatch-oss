import os
import re
import importlib
from dataclasses import dataclass, field
from typing import Tuple, Optional, List, Dict
from tree_sitter import Parser, Node, Language

from apatch.ast_window import extract_parse_slice, find_parse_anchor
from apatch.scale_config import get_ast_window_config


# Map of language name -> (pip module, candidate factory functions). The first
# six are guaranteed core dependencies; the rest are optional (extra
# "languages") and loaded lazily so a missing grammar never breaks import.
_LANG_MODULES = {
    'cpp': ('tree_sitter_cpp', ['language']),
    'python': ('tree_sitter_python', ['language']),
    'javascript': ('tree_sitter_javascript', ['language']),
    'jsx': ('tree_sitter_javascript', ['language_jsx', 'language']),
    'typescript': ('tree_sitter_typescript', ['language_typescript', 'language']),
    'tsx': ('tree_sitter_typescript', ['language_tsx', 'language_typescript', 'language']),
    'rust': ('tree_sitter_rust', ['language']),
    'go': ('tree_sitter_go', ['language']),
    'java': ('tree_sitter_java', ['language']),
    'c_sharp': ('tree_sitter_c_sharp', ['language']),
    'ruby': ('tree_sitter_ruby', ['language']),
    'php': ('tree_sitter_php', ['language_php', 'language']),
    'kotlin': ('tree_sitter_kotlin', ['language']),
    'swift': ('tree_sitter_swift', ['language']),
}

# Cache resolved Language objects (None means "grammar unavailable").
_LANG_CACHE: Dict[str, Optional[Language]] = {}


def load_language(name: str) -> Optional[Language]:
    """Resiliently resolve a tree-sitter Language by logical name.

    Returns None when the grammar package is not installed, so callers can
    gracefully fall back to whitespace-fuzzy matching instead of crashing.
    """
    if name in _LANG_CACHE:
        return _LANG_CACHE[name]
    lang: Optional[Language] = None
    spec = _LANG_MODULES.get(name)
    if spec:
        mod_name, factories = spec
        try:
            mod = importlib.import_module(mod_name)
            for fn in factories:
                if hasattr(mod, fn):
                    lang = Language(getattr(mod, fn)())
                    break
        except Exception:
            lang = None
    _LANG_CACHE[name] = lang
    return lang


@dataclass
class MatchResult:
    """Outcome of attempting to align a single proposed patch to a file."""
    success: bool
    content: str
    strategy: str
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)


# Baseline confidence per strategy. AST-fuzzy is computed dynamically.
_STRATEGY_CONFIDENCE = {
    "exact": 1.0,
    "exact-all": 1.0,
    "create": 1.0,
    "delete": 1.0,
    "chmod": 1.0,
    "whitespace-fuzzy": 0.85,
    "whitespace-fuzzy-all": 0.85,
    "semantic-sid": 0.95,
    "document-fuzzy": 0.7,
    "ast-fuzzy": 0.8,
    "json-semantic": 0.9,
    "json-semantic-all": 0.9,
}


def read_file_robust(path: str) -> Tuple[str, str]:
    encodings = ["utf-8", "utf-8-sig", "cp1251", "cp1252", "latin-1"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                content = f.read()
            return content, enc
        except UnicodeDecodeError:
            continue
    # Absolute fallback
    with open(path, "r", encoding="latin-1", errors="replace", newline="") as f:
        return f.read(), "latin-1"

class ASTMatcher:
    """
    ASTMatcher handles the alignment and application of proposed patches
    to target source code files, supporting:
    - Level 1: Exact matching
    - Level 2: Whitespace-fuzzy matching
    - Level 3: AST-fuzzy function body and structure matching
    """

    def __init__(self, target_file_path: str):
        self.target_file_path = target_file_path
        self._load_target_content()

    def _load_target_content(self):
        if not os.path.exists(self.target_file_path):
            self.content = ""
            self.encoding = "utf-8"
            self.newline_format = "\n"
        else:
            self.content, self.encoding = read_file_robust(self.target_file_path)
            self.newline_format = "\r\n" if "\r\n" in self.content else "\n"
            self.content = self.content.replace("\r\n", "\n")

    def restore_formatting(self, text: str) -> str:
        if self.newline_format == "\r\n":
            return text.replace("\n", "\r\n")
        return text

    def get_language_name(self) -> Optional[str]:
        ext = os.path.splitext(self.target_file_path)[1].lower()
        if ext in ['.cpp', '.hpp', '.cc', '.h', '.cxx', '.inc']:
            return 'cpp'
        elif ext in ['.py']:
            return 'python'
        elif ext in ['.js', '.jsx']:
            return 'javascript'
        elif ext in ['.ts', '.tsx']:
            return 'typescript'
        elif ext in ['.rs']:
            return 'rust'
        elif ext in ['.go']:
            return 'go'
        elif ext in ['.java']:
            return 'java'
        elif ext in ['.cs']:
            return 'c_sharp'
        elif ext in ['.rb']:
            return 'ruby'
        elif ext in ['.php']:
            return 'php'
        elif ext in ['.kt', '.kts']:
            return 'kotlin'
        elif ext in ['.swift']:
            return 'swift'
        elif ext in ['.omega']:
            # O-Lang structural AST-fuzzy mapping:
            # O-Lang subgraphs, alpha schemas, test and main blocks
            # align beautifully with C++ C-family brackets and statement shapes,
            # allowing high-fidelity structural AST matching.
            return 'cpp'
        return None

    def apply_patch(self, old_str: str, new_str: str, action_type: str = "REPLACE",
                    replace_all: bool = False) -> Tuple[bool, str, str]:
        """Backwards-compatible wrapper over :meth:`evaluate`.

        Returns:
            Tuple[success: bool, applied_code: str, strategy_used: str]
        """
        result = self.evaluate(old_str, new_str, action_type, replace_all)
        return result.success, result.content, result.strategy

    def evaluate(self, old_str: str, new_str: str, action_type: str = "REPLACE",
                 replace_all: bool = False) -> MatchResult:
        """
        Attempts to align a patch using the layered matching strategies and
        reports the outcome (strategy, confidence 0..1, and any warnings) without
        deciding whether to persist it.

        When ``replace_all`` is True, the exact (Level 1) and whitespace-fuzzy
        (Level 2) strategies rewrite *every* occurrence instead of just the
        first one. AST-fuzzy (Level 3) remains single-node by design.
        """
        old_str = old_str.replace("\r\n", "\n")
        new_str = new_str.replace("\r\n", "\n")

        if action_type == "CHMOD":
            from apatch.file_modes import normalize_file_mode, stat_mode

            try:
                target_mode = normalize_file_mode(new_str)
                current_mode = stat_mode(self.target_file_path)
            except Exception as exc:
                return MatchResult(False, self.content, "chmod-invalid", 0.0, [str(exc)])
            return MatchResult(
                True,
                target_mode,
                "chmod",
                1.0,
                [f"mode {current_mode} -> {target_mode}"],
            )

        if action_type == "CREATE" or not old_str.strip():
            return MatchResult(True, new_str, "create", 1.0, [])

        if action_type == "DELETE":
            return MatchResult(True, "", "delete", 1.0, [])

        # Idempotency guard (REC-6a1d): an anchor-preserving replacement (new_str \u2287
        # old_str) leaves old_str present INSIDE new_str after the first apply, so a naive
        # re-match would re-insert and DUPLICATE. If the post-replacement block is already
        # in the file, treat the re-apply as a no-op skip instead of corrupting the file.
        if (action_type == "REPLACE" and old_str != new_str and old_str in new_str
                and new_str in self.content):
            return MatchResult(False, self.content, "already_applied", 1.0,
                               ["replacement already applied \u2014 skipped to avoid duplication"])

        # Check if file is Markdown / Documentation
        ext = os.path.splitext(self.target_file_path)[1].lower()
        if ext in ('.md', '.markdown'):
            success, replaced, strategy = self._apply_markdown_patch(old_str, new_str, replace_all)
            if success:
                return MatchResult(True, replaced, strategy, _STRATEGY_CONFIDENCE.get(strategy, 0.7), [])
            return MatchResult(False, self.content, "failed", 0.0, [])

        # Level 1: Exact Matching
        if old_str in self.content:
            if replace_all:
                replaced = self.content.replace(old_str, new_str)
                return MatchResult(True, replaced, "exact-all", 1.0, [])
            replaced = self.content.replace(old_str, new_str, 1)
            return MatchResult(True, replaced, "exact", 1.0, [])

        # Level 2: Whitespace-Fuzzy Matching
        success, replaced = self._apply_whitespace_fuzzy(old_str, new_str, replace_all)
        if success:
            strategy = "whitespace-fuzzy-all" if replace_all else "whitespace-fuzzy"
            return MatchResult(True, replaced, strategy, _STRATEGY_CONFIDENCE[strategy], [])

        # Level 2b: JSON/YAML mapping-semantic (Elasticsearch/OpenSearch)
        if ext in (".json", ".yaml", ".yml"):
            from apatch.json_mapping import apply_mapping_semantic_patch

            ok, replaced = apply_mapping_semantic_patch(
                self.content,
                old_str,
                new_str,
                file_ext=ext,
                replace_all=replace_all,
            )
            if ok:
                strategy = "json-semantic-all" if replace_all else "json-semantic"
                return MatchResult(
                    True,
                    replaced,
                    strategy,
                    _STRATEGY_CONFIDENCE[strategy],
                    ["Mapping structure match (whitespace-insensitive)"],
                )

        # Level 3: AST-Fuzzy Matching (Function Body / Method alignment)
        if ext in ('.omega', '.omg', '.olang'):
            success, replaced = self._apply_ast_fuzzy_olang(old_str, new_str)
            if success:
                return MatchResult(True, replaced, "ast-fuzzy", 0.8, [])
        else:
            lang_name = self.get_language_name()
            if lang_name:
                success, replaced, confidence, warnings = self._apply_ast_fuzzy(old_str, new_str, lang_name)
                if success:
                    return MatchResult(True, replaced, "ast-fuzzy", confidence, warnings)

        return MatchResult(False, self.content, "failed", 0.0, [])

    def _apply_markdown_patch(self, old_str: str, new_str: str, replace_all: bool = False) -> Tuple[bool, str, str]:
        """
        Applies non-positional re-anchoring matching for Markdown documentation:
        1. Exact match
        2. Whitespace-fuzzy match
        3. Semantic ID (sid) comment match
        4. Jaccard paragraph-similarity match
        """
        # 1. Level 1: Exact Match
        if old_str in self.content:
            if replace_all:
                replaced = self.content.replace(old_str, new_str)
                return True, replaced, "exact-all"
            replaced = self.content.replace(old_str, new_str, 1)
            return True, replaced, "exact"

        # 2. Level 2: Whitespace-Fuzzy Match
        success, replaced = self._apply_whitespace_fuzzy(old_str, new_str, replace_all)
        if success:
            return True, replaced, "whitespace-fuzzy-all" if replace_all else "whitespace-fuzzy"

        # 3. Level 3: Semantic ID Anchored Match
        from apatch.semantic_parser import extract_blocks_by_sid
        
        sid_match = re.search(r'<!--\s*@sid:([a-zA-Z0-9_-]+)\s*-->', old_str)
        if not sid_match:
            sid_match = re.search(r'<!--\s*@sid:([a-zA-Z0-9_-]+)\s*-->', new_str)
            
        if sid_match:
            sid = sid_match.group(1)
            blocks_map = extract_blocks_by_sid(self.content)
            if sid in blocks_map:
                block_text, start_pos, end_pos = blocks_map[sid]
                replaced = self.content[:start_pos] + new_str + self.content[end_pos:]
                return True, replaced, "semantic-sid"

        # 4. Level 4: Fuzzy Document Match (Jaccard similarity fallback)
        from apatch.semantic_parser import fuzzy_match_paragraphs
        success, best_match = fuzzy_match_paragraphs(old_str, self.content)
        if success and best_match:
            # We locate and replace the best matched paragraph
            replaced = self.content.replace(best_match, new_str, 1)
            return True, replaced, "document-fuzzy"

        return False, self.content, "failed"

    def _apply_whitespace_fuzzy(self, old_str: str, new_str: str, replace_all: bool = False) -> Tuple[bool, str]:
        """
        Normalizes whitespaces and attempts to find a matching block.

        With ``replace_all`` set, every whitespace-insensitive occurrence is
        rewritten; otherwise only the first match is substituted.
        """
        def normalize(s: str) -> str:
            return re.sub(r'\s+', '', s)

        target_normalized = normalize(self.content)
        old_normalized = normalize(old_str)

        if old_normalized not in target_normalized:
            return False, self.content

        # Create a regex to match the old_str with arbitrary spaces/newlines
        pattern_parts = []
        for char in old_str:
            if char.isspace():
                if not pattern_parts or pattern_parts[-1] != r'\s*':
                    pattern_parts.append(r'\s*')
            else:
                pattern_parts.append(re.escape(char))

        pattern = "".join(pattern_parts)
        # A plain-function replacement avoids re interpreting backslashes/group
        # references inside new_str.
        if replace_all:
            replaced, n = re.subn(pattern, lambda _m: new_str, self.content, flags=re.DOTALL)
            if n > 0:
                return True, replaced
            return False, self.content

        # Ensure we match across line boundaries (first occurrence only)
        match = re.search(pattern, self.content, re.DOTALL)
        if match:
            start, end = match.span()
            replaced = self.content[:start] + new_str + self.content[end:]
            return True, replaced

        return False, self.content

    def _apply_ast_fuzzy(self, old_str: str, new_str: str, lang_name: str) -> Tuple[bool, str, float, List[str]]:
        """
        Leverages tree-sitter to find a structurally matching function block or class method,
        even if signatures or formatting have drifted.

        Returns ``(success, replaced_content, confidence, warnings)``.
        """
        language = load_language(lang_name)
        if language is None:
            return False, self.content, 0.0, []
        try:
            parser = Parser(language)
        except Exception:
            return False, self.content, 0.0, []

        def find_structural_nodes(node: Node) -> List[Node]:
            nodes = []
            if node.type in (
                'function_definition', 'method_definition', 'function_declaration',
                'arrow_function', 'function', 'function_item', 'method_declaration',
                'constructor_declaration', 'interface_declaration', 'singleton_method',
                'method',
                'class_definition', 'class_declaration', 'struct_specifier',
                'if_statement', 'for_statement', 'while_statement', 'try_statement', 'switch_statement'
            ):
                nodes.append(node)
            for child in node.children:
                nodes.extend(find_structural_nodes(child))
            return nodes

        def get_node_name(node: Node, code: str) -> str:
            if node.type in ('class_definition', 'class_declaration', 'struct_specifier'):
                for child in node.children:
                    if child.type in ('identifier', 'type_identifier'):
                        return code[child.start_byte:child.end_byte]

            if node.type in ('if_statement', 'for_statement', 'while_statement', 'try_statement', 'switch_statement'):
                header = code[node.start_byte:node.end_byte].split('{')[0].split('\n')[0].strip()
                return re.sub(r'\s+', '', header)

            if node.type == 'arrow_function' and node.parent and node.parent.type == 'variable_declarator':
                for c in node.parent.children:
                    if c.type == 'identifier':
                        return code[c.start_byte:c.end_byte]

            for child in node.children:
                if child.type == 'declarator':
                    sub = child
                    while sub.children:
                        ids = [c for c in sub.children if c.type == 'identifier']
                        if ids:
                            return code[ids[0].start_byte:ids[0].end_byte]
                        sub = sub.children[0]
                elif child.type == 'identifier':
                    return code[child.start_byte:child.end_byte]

            sig = code[node.start_byte:node.end_byte].split('{')[0]
            names = re.findall(r'\b\w+\b', sig)
            return names[-1] if names else ""

        def get_enclosing_scope_name(node: Node, source: str, byte_offset: int) -> str:
            parent = node.parent
            while parent is not None:
                if parent.type in ('class_definition', 'class_declaration', 'struct_specifier', 'namespace_definition'):
                    for child in parent.children:
                        if child.type in ('identifier', 'type_identifier'):
                            start = byte_offset + child.start_byte
                            end = byte_offset + child.end_byte
                            return source[start:end]
                parent = parent.parent
            return ""

        def get_body_node(node: Node) -> Optional[Node]:
            for child in node.children:
                if child.type in (
                    'compound_statement', 'block', 'statement_block', 'block_statement',
                    'block_body', 'class_body', 'declaration_list', 'function_body',
                    'body', 'body_statement', 'enum_body', 'interface_body',
                ):
                    return child
            return None

        def get_signature(node: Node, code: str, body: Optional[Node]) -> str:
            end = body.start_byte if body is not None else node.end_byte
            header = code[node.start_byte:end]
            return re.sub(r'\s+', ' ', header).strip()

        old_tree = parser.parse(bytes(old_str, "utf-8"))
        old_nodes = find_structural_nodes(old_tree.root_node)
        if not old_nodes:
            return False, self.content, 0.0, []

        old_node = old_nodes[0]
        old_node_name = get_node_name(old_node, old_str)
        if not old_node_name:
            return False, self.content, 0.0, []

        old_scope = get_enclosing_scope_name(old_node, old_str, 0)
        old_body = get_body_node(old_node)
        old_body_text = old_str[old_body.start_byte:old_body.end_byte] if old_body else ""

        import difflib as _difflib

        def attempt(parse_text: str, byte_offset: int) -> Optional[Tuple[bool, str, float, List[str]]]:
            target_tree = parser.parse(bytes(parse_text, "utf-8"))
            target_nodes = find_structural_nodes(target_tree.root_node)
            if not target_nodes:
                return None

            name_matches = [
                t for t in target_nodes
                if get_node_name(t, parse_text) == old_node_name
            ]
            if not name_matches:
                return None

            def body_similarity(t_node: Node) -> float:
                t_body = get_body_node(t_node)
                if t_body is not None and old_body_text:
                    start = byte_offset + t_body.start_byte
                    end = byte_offset + t_body.end_byte
                    t_body_text = self.content[start:end]
                    return _difflib.SequenceMatcher(None, old_body_text, t_body_text).ratio()
                return 0.0

            if len(name_matches) == 1:
                matched_target_node = name_matches[0]
            else:
                best_node = None
                best_score = -1.0
                for t_node in name_matches:
                    score = body_similarity(t_node)
                    if old_scope and get_enclosing_scope_name(t_node, self.content, byte_offset) == old_scope:
                        score += 1.0
                    if score > best_score:
                        best_score = score
                        best_node = t_node
                matched_target_node = best_node

            if not matched_target_node:
                return None

            target_body = get_body_node(matched_target_node)
            new_tree = parser.parse(bytes(new_str, "utf-8"))
            new_nodes = find_structural_nodes(new_tree.root_node)
            new_node = new_nodes[0] if new_nodes else None
            new_body = get_body_node(new_node) if new_node else None

            if not (target_body and old_body and new_body):
                return None

            start_byte = byte_offset + target_body.start_byte
            end_byte = byte_offset + target_body.end_byte
            new_body_text = new_str[new_body.start_byte:new_body.end_byte]
            replaced = self.content[:start_byte] + new_body_text + self.content[end_byte:]

            confidence = body_similarity(matched_target_node)
            if confidence <= 0.0:
                confidence = 0.8 if len(name_matches) == 1 else 0.6

            warnings: List[str] = []
            target_sig = get_signature(
                matched_target_node, parse_text, target_body
            )
            new_sig = get_signature(new_node, new_str, new_body)
            if target_sig and new_sig and target_sig != new_sig:
                warnings.append(
                    "signature change ignored (AST-fuzzy replaces body only): "
                    f"target keeps `{target_sig}`, proposed `{new_sig}`"
                )

            return True, replaced, round(confidence, 3), warnings

        window_bytes, full_max = get_ast_window_config()
        if window_bytes > 0 and len(self.content) > full_max:
            anchor = find_parse_anchor(self.content, old_str)
            slice_text, offset = extract_parse_slice(self.content, anchor, window_bytes)
            window_result = attempt(slice_text, offset)
            if window_result is not None:
                return window_result

        full_result = attempt(self.content, 0)
        if full_result is not None:
            return full_result
        return False, self.content, 0.0, []

    def _apply_ast_fuzzy_olang(self, old_str: str, new_str: str) -> Tuple[bool, str]:
        """
        Custom structural block alignment engine for native O-Lang (.omega) files.
        Safely matches and replaces bodies of main, subgraph, on-handlers, and alpha_schema.
        """
        import re
        from typing import Dict

        def extract_blocks(code: str) -> Dict[str, Tuple[str, int, int]]:
            blocks = {}
            pos = 0
            while True:
                # Find a keyword followed by braces
                match = re.search(r'\b(main|subgraph|on|alpha_schema|test|node|morphism)\b[^{]*\{', code[pos:])
                if not match:
                    break
                start_idx = pos + match.start()
                brace_idx = pos + match.end() - 1
                
                # Recursive search for matching brace
                depth = 1
                end_idx = -1
                for i in range(brace_idx + 1, len(code)):
                    if code[i] == '{':
                        depth += 1
                    elif code[i] == '}':
                        depth -= 1
                        if depth == 0:
                            end_idx = i
                            break
                if end_idx == -1:
                    break
                    
                header = code[start_idx:brace_idx].strip()
                words = re.findall(r'\b\w+\b', header)
                # create block lookup key: "main" or "subgraph board" or "on click"
                block_key = " ".join(words[:2]) if words else header
                
                body = code[brace_idx:end_idx + 1]
                blocks[block_key] = (body, start_idx, end_idx + 1)
                pos = brace_idx + 1
            return blocks

        target_blocks = extract_blocks(self.content)
        old_blocks = extract_blocks(old_str)

        if not old_blocks or not target_blocks:
            return False, self.content

        # Match block key
        old_key = list(old_blocks.keys())[0]
        if old_key not in target_blocks:
            # Fuzzy match keys on first word
            matched_key = None
            for k in target_blocks:
                if old_key.split()[0] == k.split()[0]:
                    matched_key = k
                    break
            if not matched_key:
                return False, self.content
            old_key = matched_key

        target_body, start_byte, end_byte = target_blocks[old_key]

        new_blocks = extract_blocks(new_str)
        if not new_blocks:
            return False, self.content
        new_key = list(new_blocks.keys())[0]
        new_body = new_blocks[new_key][0]

        # Splice new body content in place
        brace_offset = self.content[start_byte:].find('{')
        if brace_offset == -1:
            return False, self.content

        replaced = (
            self.content[:start_byte + brace_offset + 1] +
            new_body[1:-1] +
            self.content[end_byte - 1:]
        )
        return True, replaced
