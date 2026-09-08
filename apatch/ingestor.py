import json
import os
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Iterable

@dataclass
class PatchCandidate:
    step_index: int
    tool_name: str
    target_file: str
    old_content: str
    new_content: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_format: str = "unknown"
    action_type: str = "REPLACE" # "REPLACE", "CREATE", "DELETE"
    replace_all: bool = False     # When True, replace every occurrence (token-rename refactors)


def _read_replace_all(args: Dict[str, Any]) -> bool:
    """Detect a global/all-occurrences replacement intent from common arg spellings."""
    for key in ("replace_all", "ReplaceAll", "replaceAll", "AllowMultiple", "all", "global"):
        if key in args:
            return bool(args[key])
    return False

# Tool names that carry search/replace payloads across agent ecosystems.
_EDIT_TOOL_NAMES = frozenset({
    "strreplace", "str_replace", "str_replace_editor", "replace_file_content",
    "multi_replace_file_content", "edit_file", "write_to_file", "write",
    "search_replace", "apply_patch", "patch", "chmod_file", "chmod", "set_file_mode",
})

_CREATE_TOOL_NAMES = frozenset({
    "write_to_file", "write", "create_file", "write_file", "save_file", "new_file"
})

_DELETE_TOOL_NAMES = frozenset({
    "delete_file", "remove_file", "delete", "rm", "rm_file"
})

_CHMOD_TOOL_NAMES = frozenset({
    "chmod_file", "chmod", "set_file_mode", "set_mode"
})


def _strip_ab_prefix(path: str) -> str:
    """Normalize a diff path header (strip a//b/ prefixes, handle /dev/null)."""
    path = path.strip().split("\t")[0].strip()
    if path in ("/dev/null", ""):
        return ""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def looks_like_diff(text: str) -> bool:
    """Heuristic: does this string look like a unified diff or apply_patch envelope?"""
    if not isinstance(text, str) or not text.strip():
        return False
    if "*** Begin Patch" in text or "*** Update File:" in text or "*** Add File:" in text or "*** Delete File:" in text:
        return True
    if re.search(r'(?m)^@@', text) and ("\n+" in text or "\n-" in text):
        return True
    if "\n--- " in text and "\n+++ " in text:
        return True
    if text.startswith("--- ") and "+++ " in text:
        return True
    return False


def _reconstruct_hunk(body_lines: List[str]) -> "tuple[str, str]":
    """Reconstruct (old, new) text from diff hunk body lines (+/-/space prefixed)."""
    old_lines: List[str] = []
    new_lines: List[str] = []
    for hl in body_lines:
        if hl == "":
            old_lines.append("")
            new_lines.append("")
            continue
        tag, content = hl[:1], hl[1:]
        if tag == "-":
            old_lines.append(content)
        elif tag == "+":
            new_lines.append(content)
        elif tag == " ":
            old_lines.append(content)
            new_lines.append(content)
        elif tag == "\\":
            # "\ No newline at end of file" marker; ignore.
            continue
        else:
            # Unprefixed line: treat as shared context.
            old_lines.append(hl)
            new_lines.append(hl)
    return "\n".join(old_lines), "\n".join(new_lines)


def parse_unified_diff(text: str) -> List[Dict[str, Any]]:
    """Parse a unified diff into per-hunk {path, old, new, action} records."""
    results: List[Dict[str, Any]] = []
    lines = text.split("\n")
    i = 0
    cur_path: Optional[str] = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old_p = _strip_ab_prefix(line[4:])
            if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                new_p = _strip_ab_prefix(lines[i + 1][4:])
                cur_path = new_p or old_p
                i += 2
                continue
        if line.startswith("@@"):
            i += 1
            body: List[str] = []
            while i < len(lines):
                hl = lines[i]
                if hl.startswith(("@@", "--- ", "*** ", "diff ", "Index: ")):
                    break
                body.append(hl)
                i += 1
            old, new = _reconstruct_hunk(body)
            results.append({"path": cur_path, "old": old, "new": new, "action": "REPLACE"})
            continue
        i += 1
    return results


def parse_apply_patch(text: str) -> List[Dict[str, Any]]:
    """Parse an OpenAI apply_patch (V4A) envelope into {path, old, new, action} records."""
    results: List[Dict[str, Any]] = []
    lines = text.split("\n")
    i = 0
    header_re = re.compile(r'^\*\*\* (Update|Add|Delete) File: (.+)$')
    while i < len(lines):
        m = header_re.match(lines[i])
        if not m:
            i += 1
            continue
        kind, path = m.group(1), m.group(2).strip()
        i += 1
        if kind == "Delete":
            results.append({"path": path, "old": "", "new": "", "action": "DELETE"})
            continue
        body: List[str] = []
        while i < len(lines) and not lines[i].startswith("*** "):
            body.append(lines[i])
            i += 1
        if kind == "Add":
            # Each "+line" represents a line WITH its newline; the explicit
            # "\ No newline at end of file" marker (unified diff convention)
            # is the only way to say the last line has none. This keeps
            # Add File byte-exact round-trip with the generator.
            no_final_newline = False
            new_lines: List[str] = []
            for l in body:
                if l == "\\ No newline at end of file":
                    no_final_newline = True
                    continue
                if l == "":
                    # envelope padding: real empty content lines arrive as "+"
                    continue
                new_lines.append(l[1:] if l.startswith("+") else l)
            new_text = "\n".join(new_lines)
            if new_lines and not no_final_newline:
                new_text += "\n"
            results.append({"path": path, "old": "", "new": new_text, "action": "CREATE"})
        else:  # Update: split body into hunks on @@ markers
            hunks: List[List[str]] = []
            cur: List[str] = []
            for l in body:
                if l.startswith("@@"):
                    if cur:
                        hunks.append(cur)
                        cur = []
                    continue
                cur.append(l)
            if cur:
                hunks.append(cur)
            for h in hunks:
                old, new = _reconstruct_hunk(h)
                results.append({"path": path, "old": old, "new": new, "action": "REPLACE"})
    return results


def parse_diff_text(text: str) -> List[Dict[str, Any]]:
    """Parse either an apply_patch envelope or a plain unified diff."""
    if "*** Begin Patch" in text or "*** Update File:" in text or "*** Add File:" in text or "*** Delete File:" in text:
        records = parse_apply_patch(text)
        if records:
            return records
    return parse_unified_diff(text)

class LogIngestor:
    """
    Parse agent transcript logs (.jsonl, .gemini, or any JSONL export) from:
    - Cursor / Claude Code: {role, message: {content: [{type: tool_use, ...}]}}
    - Gemini / Antigravity: {tool_calls: [{name, arguments}]}
    - Gemini API parts: {parts: [{functionCall: {name, args}}]}
    - Anthropic flat: {type: assistant, content: [...]}
    - Generic flat: {tool, arguments} or nested old_string/new_string
    """

    def __init__(self, log_path: str):
        self.log_path = log_path

    def parse(self) -> List[PatchCandidate]:
        if not os.path.exists(self.log_path):
            raise FileNotFoundError(f"Transcript log file not found: {self.log_path}")

        candidates: List[PatchCandidate] = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    step_data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                step_index = step_data.get("step_index", line_idx)
                candidates.extend(self._parse_line(step_data, step_index))
        return candidates

    def _parse_line(self, data: Dict[str, Any], step_index: int) -> List[PatchCandidate]:
        found: List[PatchCandidate] = []
        seen: set[tuple] = set()

        def add(cands: Iterable[PatchCandidate]) -> None:
            for c in cands:
                key = (c.tool_name, c.target_file, c.old_content, c.new_content)
                if key in seen:
                    continue
                seen.add(key)
                found.append(c)

        add(self._parse_role_message(data, step_index))
        add(self._parse_anthropic_content(data, step_index))
        add(self._parse_tool_calls(data, step_index))
        add(self._parse_gemini_parts(data, step_index))
        if not found:
            add(self._parse_flat_step(data, step_index))
        return found

    def _parse_role_message(self, data: Dict[str, Any], step_index: int) -> List[PatchCandidate]:
        """Cursor JSONL, Claude Code, and similar {role, message} transcripts."""
        if "message" not in data or "role" not in data:
            return []
        message = data.get("message")
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        out: List[PatchCandidate] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "tool_use":
                name = item.get("name", "")
                inp = item.get("input", item.get("arguments", {}))
                if isinstance(inp, dict):
                    out.extend(self._extract_from_args(name, inp, step_index, "cursor/claude"))
            elif item_type == "tool_result":
                continue
            elif "tool_use" in item:
                tu = item["tool_use"]
                if isinstance(tu, dict):
                    out.extend(self._extract_from_args(
                        tu.get("name", ""), tu.get("input", tu.get("arguments", {})), step_index, "cursor/claude"
                    ))
        return out

    def _parse_anthropic_content(self, data: Dict[str, Any], step_index: int) -> List[PatchCandidate]:
        """Anthropic API dump: top-level content[] with tool_use blocks."""
        content = data.get("content")
        if not isinstance(content, list):
            return []
        out: List[PatchCandidate] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                inp = item.get("input", {})
                if isinstance(inp, dict):
                    out.extend(self._extract_from_args(item.get("name", ""), inp, step_index, "anthropic"))
        return out

    def _parse_tool_calls(self, data: Dict[str, Any], step_index: int) -> List[PatchCandidate]:
        """Gemini / Antigravity / OpenAI-style tool_calls array."""
        tools = data.get("tool_calls")
        if not isinstance(tools, list):
            return []
        out: List[PatchCandidate] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name", tool.get("function", {}).get("name", ""))
            args = tool.get("arguments", tool.get("function", {}).get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if isinstance(args, dict):
                out.extend(self._parse_tool_call({"name": name, "arguments": args}, step_index, "gemini/tool_calls"))
        return out

    def _parse_gemini_parts(self, data: Dict[str, Any], step_index: int) -> List[PatchCandidate]:
        """Gemini .gemini / API exports with parts[].functionCall."""
        parts = data.get("parts")
        if not isinstance(parts, list):
            return []
        out: List[PatchCandidate] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            fc = part.get("functionCall") or part.get("function_call")
            if not isinstance(fc, dict):
                continue
            name = fc.get("name", "")
            args = fc.get("args", fc.get("arguments", {}))
            if isinstance(args, dict):
                out.extend(self._extract_from_args(name, args, step_index, "gemini/parts"))
        return out

    def _parse_tool_call(self, tool: Dict[str, Any], step_index: int, fmt: str) -> List[PatchCandidate]:
        name = tool.get("name", "")
        args = tool.get("arguments", {})
        if not isinstance(args, dict):
            return []
        return self._extract_from_args(name, args, step_index, fmt)

    def _parse_flat_step(self, data: Dict[str, Any], step_index: int) -> List[PatchCandidate]:
        candidates: List[PatchCandidate] = []
        tool_name = data.get("tool", data.get("tool_name", data.get("name", "")))
        args = data.get("arguments", data.get("args", data.get("input", None)))

        if isinstance(args, dict) and tool_name:
            extracted = self._extract_from_args(str(tool_name), args, step_index, "flat")
            if extracted:
                return extracted

        def recurse(node: Any) -> None:
            if isinstance(node, dict):
                if any(k in node for k in ("TargetContent", "old_string", "old_content", "target_content")):
                    extracted = self._extract_from_args("FuzzyExtractor", node, step_index, "flat/recurse")
                    if extracted:
                        candidates.extend(extracted)
                        return
                for k in ("diff", "patch", "code_edit"):
                    val = node.get(k)
                    if isinstance(val, str) and looks_like_diff(val):
                        target = node.get("path") or node.get("file") or node.get("target_file") or ""
                        diff_cands = self._candidates_from_diff(val, str(tool_name or "diff"), target, step_index, "flat/diff")
                        if diff_cands:
                            candidates.extend(diff_cands)
                            return
                for v in node.values():
                    recurse(v)
            elif isinstance(node, list):
                for item in node:
                    recurse(item)

        recurse(data)
        return candidates

    def _extract_from_args(
        self, tool_name: str, args: Dict[str, Any], step_index: int, fmt: str
    ) -> List[PatchCandidate]:
        candidates: List[PatchCandidate] = []
        target_file = args.get(
            "TargetFile",
            args.get("path", args.get("file", args.get("filepath", args.get("target_file", "")))),
        )

        normalized_tool = tool_name.lower().replace("-", "_")
        replace_all = _read_replace_all(args)

        if normalized_tool in _CHMOD_TOOL_NAMES:
            from apatch.file_modes import normalize_file_mode

            mode = (
                args.get("Mode")
                or args.get("mode")
                or args.get("FileMode")
                or args.get("file_mode")
                or args.get("permissions")
            )
            if mode is None and "executable" in args:
                mode = "755" if bool(args.get("executable")) else "644"
            if mode is None:
                return []
            try:
                normalized_mode = normalize_file_mode(mode)
            except ValueError:
                return []
            candidates.append(PatchCandidate(
                step_index=step_index,
                tool_name=tool_name,
                target_file=target_file,
                old_content="",
                new_content=normalized_mode,
                source_format=fmt,
                action_type="CHMOD",
            ))
            return candidates

        if normalized_tool in _DELETE_TOOL_NAMES:
            candidates.append(PatchCandidate(
                step_index=step_index,
                tool_name=tool_name,
                target_file=target_file,
                old_content="",
                new_content="",
                source_format=fmt,
                action_type="DELETE"
            ))
            return candidates

        if "TargetContent" in args and "ReplacementContent" in args:
            candidates.append(PatchCandidate(
                step_index=step_index,
                tool_name=tool_name,
                target_file=target_file,
                old_content=args["TargetContent"],
                new_content=args["ReplacementContent"],
                start_line=args.get("StartLine"),
                end_line=args.get("EndLine"),
                source_format=fmt,
                action_type="REPLACE",
                replace_all=replace_all,
            ))

        elif "ReplacementChunks" in args and isinstance(args["ReplacementChunks"], list):
            for chunk in args["ReplacementChunks"]:
                if isinstance(chunk, dict) and "TargetContent" in chunk and "ReplacementContent" in chunk:
                    candidates.append(PatchCandidate(
                        step_index=step_index,
                        tool_name=tool_name,
                        target_file=target_file,
                        old_content=chunk["TargetContent"],
                        new_content=chunk["ReplacementContent"],
                        start_line=chunk.get("StartLine"),
                        end_line=chunk.get("EndLine"),
                        source_format=fmt,
                        action_type="REPLACE",
                        replace_all=_read_replace_all(chunk) or replace_all,
                    ))

        elif ("old_string" in args or "old_content" in args) and ("new_string" in args or "new_content" in args):
            old_str = args.get("old_string", args.get("old_content", ""))
            new_str = args.get("new_string", args.get("new_content", ""))
            candidates.append(PatchCandidate(
                step_index=step_index,
                tool_name=tool_name,
                target_file=target_file,
                old_content=old_str,
                new_content=new_str,
                start_line=args.get("start_line"),
                end_line=args.get("end_line"),
                source_format=fmt,
                action_type="REPLACE",
                replace_all=replace_all,
            ))

        elif "CodeContent" in args or ("content" in args and self._looks_like_write(tool_name, args)):
            code = args.get("CodeContent", args.get("content", ""))
            candidates.append(PatchCandidate(
                step_index=step_index,
                tool_name=tool_name,
                target_file=target_file,
                old_content="",
                new_content=code if isinstance(code, str) else str(code),
                source_format=fmt,
                action_type="CREATE",
            ))

        # Fallback: unified-diff / apply_patch payloads carried as a raw string.
        if not candidates:
            for key in ("diff", "patch", "code_edit", "input", "content", "CodeContent"):
                val = args.get(key)
                if isinstance(val, str) and looks_like_diff(val):
                    candidates.extend(
                        self._candidates_from_diff(val, tool_name, target_file, step_index, fmt)
                    )
                    if candidates:
                        break

        return candidates

    def _candidates_from_diff(
        self, text: str, tool_name: str, default_target: str, step_index: int, fmt: str
    ) -> List[PatchCandidate]:
        out: List[PatchCandidate] = []
        for rec in parse_diff_text(text):
            target = rec.get("path") or default_target or ""
            out.append(PatchCandidate(
                step_index=step_index,
                tool_name=tool_name,
                target_file=target,
                old_content=rec.get("old", ""),
                new_content=rec.get("new", ""),
                source_format=f"{fmt}/diff",
                action_type=rec.get("action", "REPLACE"),
            ))
        return out

    @staticmethod
    def _looks_like_write(tool_name: str, args: Dict[str, Any]) -> bool:
        normalized = tool_name.lower().replace("-", "_")
        if normalized in _EDIT_TOOL_NAMES or "write" in normalized or "edit" in normalized:
            return True
        return "path" in args or "TargetFile" in args or "file" in args
