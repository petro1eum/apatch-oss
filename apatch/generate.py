"""Generate patch JSONL from mutations across a codebase."""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, TextIO, Tuple, Union

MatchMode = Literal["literal", "whitespace", "regex", "json", "yaml"]
MutationAction = Literal[
    "replace", "create", "delete", "rename", "chmod", "passthrough",
    "shift_outline", "insert_before", "insert_section",
]


class NeedleOverlapError(ValueError):
    """A batch cannot be generated atomically against one source snapshot."""

    def __init__(
        self,
        message: str,
        *,
        conflicts: List[Dict[str, Any]],
        safe_batches: List[List[int]],
    ) -> None:
        super().__init__(message)
        self.conflicts = conflicts
        self.safe_batches = safe_batches


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def find_literal_spans(text: str, find: str) -> List[str]:
    """Return each exact occurrence of find in text (for TargetContent)."""
    if find not in text:
        return []
    spans: List[str] = []
    start = 0
    while True:
        idx = text.find(find, start)
        if idx < 0:
            break
        spans.append(find)
        start = idx + len(find)
    return spans


def _pattern_tokens(find: str) -> List[str]:
    """Split a find string into tokens for whitespace-tolerant matching."""
    return [t for t in re.split(r"(\s+|[=(),;:\[\]{}])", find) if t and not t.isspace()]


def find_whitespace_spans(text: str, find: str) -> List[str]:
    """Return actual file substrings that match find when whitespace is ignored."""
    target_norm = _normalize_ws(text)
    find_norm = _normalize_ws(find)
    if not find_norm or find_norm not in target_norm:
        return []
    parts = _pattern_tokens(find)
    if not parts:
        return []
    pattern = r"\s*".join(re.escape(p) for p in parts)
    return [m.group(0) for m in re.finditer(pattern, text, flags=re.MULTILINE | re.DOTALL)]


def find_regex_spans(text: str, pattern: str) -> List[str]:
    try:
        rx = re.compile(pattern, flags=re.MULTILINE | re.DOTALL)
    except re.error:
        return []
    return [m.group(0) for m in rx.finditer(text)]


def iter_matching_files(target_dir: str, glob_pattern: str) -> Iterator[str]:
    root = Path(target_dir)
    pattern = glob_pattern.replace("\\", "/")
    if not any(ch in pattern for ch in "*?["):
        direct = root / pattern
        if direct.is_file():
            yield pattern
            return
    # fnmatch treats '**/' as requiring a literal '/', so a leading recursive prefix
    # excludes root-level files (rel == basename). Also match the de-prefixed pattern
    # so '**/*.md' covers root README.md, not only subdir files.
    patterns = [pattern]
    if pattern.startswith("**/"):
        patterns.append(pattern[3:])
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv", "__pycache__"}]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns):
                yield rel


def find_json_semantic_spans(text: str, find: str) -> List[str]:
    """Find mapping JSON spans semantically equal to find fragment."""
    from apatch.json_mapping import find_semantic_json_spans, looks_like_mapping_document, parse_json_fragment

    if not looks_like_mapping_document(text):
        return []
    old_obj = parse_json_fragment(find)
    if old_obj is None:
        return []
    return [frag for _, _, frag in find_semantic_json_spans(text, old_obj)]


def find_mapping_semantic_spans(text: str, find: str, rel_path: str = "") -> List[str]:
    """Find mapping JSON or YAML spans semantically equal to find fragment."""
    from apatch.json_mapping import (
        find_semantic_json_spans,
        find_yaml_semantic_spans,
        looks_like_mapping_document,
        parse_mapping_fragment,
    )

    ext = os.path.splitext(rel_path)[1].lower()
    is_yaml_file = ext in (".yaml", ".yml")
    if not is_yaml_file and not looks_like_mapping_document(text):
        return []
    old_obj = parse_mapping_fragment(find)
    if old_obj is None:
        return []

    if is_yaml_file:
        yaml_spans = find_yaml_semantic_spans(text, old_obj)
        if yaml_spans:
            return yaml_spans
    return [frag for _, _, frag in find_semantic_json_spans(text, old_obj)]


def _find_spans(
    text: str,
    find: str,
    match_mode: MatchMode,
    find_pattern: Optional[str],
    rel_path: str = "",
) -> List[str]:
    if match_mode == "regex":
        return find_regex_spans(text, find_pattern or find)
    if match_mode == "whitespace":
        return find_whitespace_spans(text, find)
    if match_mode in ("json", "yaml"):
        return find_mapping_semantic_spans(text, find, rel_path)
    return find_literal_spans(text, find)


def read_jsonl_patches(path: str) -> List[dict]:
    """Load patch steps from JSONL; returns [] when file is missing."""
    if not os.path.isfile(path):
        return []
    out: List[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if isinstance(data, dict):
                out.append(data)
    return out


def next_step_index(patches: List[dict]) -> int:
    if not patches:
        return 1
    return max(int(p.get("step_index") or 0) for p in patches) + 1


def _rel_path_exists(target_dir: str, rel_path: str) -> bool:
    return os.path.isfile(os.path.join(target_dir, rel_path.replace("\\", "/")))


def _format_apply_patch_add_file(rel_path: str, content: str) -> str:
    rel_path = rel_path.replace("\\", "/")
    if not content:
        body = ""
    else:
        body = "\n".join(f"+{line}" for line in content.splitlines())
        # Byte fidelity: each "+line" carries an implicit newline; when the
        # source content does NOT end with one, say so explicitly (unified
        # diff convention) so the parser can round-trip the exact bytes.
        # Without this marker a trailing newline was silently dropped and
        # follow-up find/replace anchors spanning EOF failed to match.
        if not content.endswith("\n"):
            body += "\n\\ No newline at end of file"
    return f"*** Begin Patch\n*** Add File: {rel_path}\n{body}\n*** End Patch\n"


def _patch_step_apply_patch(step_index: int, patch_input: str) -> dict:
    return {
        "step_index": step_index,
        "tool_calls": [{"name": "apply_patch", "arguments": {"input": patch_input}}],
    }


def _resolve_mutation_action(raw: Dict[str, Any]) -> str:
    if raw.get("tool_calls"):
        return "passthrough"
    action = raw.get("action") or raw.get("kind")
    if action is None:
        if (raw.get("content") is not None or raw.get("replace_text") is not None or raw.get("replace") is not None) and (
            raw.get("target_file") or raw.get("file")
        ) and not (raw.get("find_text") or raw.get("find")):
            return "create"
        return "replace"
    action = str(action).lower()
    if action in ("find_replace", "replace"):
        return "replace"
    if action in ("chmod", "mode", "set_mode", "set_file_mode"):
        return "chmod"
    if action not in (
        "create",
        "delete",
        "rename",
        "chmod",
        "passthrough",
        "shift_outline",
        "insert_before",
        "insert_section",
    ):
        raise ValueError(f"unknown mutation action {action!r}")
    return action


def normalize_needle(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Accept find/replace or find_text/replace_text; optional target_file (legacy replace)."""
    find_text = raw["find_text"] if "find_text" in raw else raw.get("find")
    replace_text = raw["replace_text"] if "replace_text" in raw else raw.get("replace")
    if not find_text or replace_text is None:
        raise ValueError("each replace mutation requires find_text and replace_text")
    glob_pattern = raw.get("glob_pattern")
    target_file = raw.get("target_file") or raw.get("file")
    if not glob_pattern and target_file:
        glob_pattern = str(target_file)
    return {
        "action": "replace",
        "find_text": str(find_text),
        "replace_text": str(replace_text),
        "glob_pattern": glob_pattern,
        "match_mode": raw.get("match_mode"),
        "find_pattern": raw.get("find_pattern"),
        "replace_all": (
            bool(raw["replace_all"]) if "replace_all" in raw else None
        ),
        "label": raw.get("label"),
        "require_match": raw.get("require_match", True),
    }


def normalize_mutation(raw: Dict[str, Any], *, index: int = 1) -> Dict[str, Any]:
    """Normalize a mutation needle: replace | create | delete | rename | passthrough."""
    label = raw.get("label") or f"mutation #{index}"
    action = _resolve_mutation_action(raw)

    if action == "passthrough":
        tool_calls = raw.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValueError(f"{label}: passthrough requires non-empty tool_calls")
        return {"action": "passthrough", "tool_calls": tool_calls, "label": label}

    if action == "replace":
        out = normalize_needle(raw)
        out["label"] = label
        return out

    if action == "create":
        target_file = raw.get("target_file") or raw.get("file")
        content = raw.get("content")
        if content is None:
            content = raw.get("replace_text", raw.get("replace", ""))
        if not target_file:
            raise ValueError(f"{label}: create requires target_file")
        return {
            "action": "create",
            "target_file": str(target_file).replace("\\", "/"),
            "content": str(content),
            "label": label,
            "require_absent": bool(raw.get("require_absent", True)),
        }

    if action == "delete":
        target_file = raw.get("target_file") or raw.get("file")
        if not target_file:
            raise ValueError(f"{label}: delete requires target_file")
        return {
            "action": "delete",
            "target_file": str(target_file).replace("\\", "/"),
            "label": label,
            "require_match": bool(raw.get("require_match", True)),
        }

    if action == "chmod":
        from apatch.file_modes import normalize_file_mode

        target_file = raw.get("target_file") or raw.get("file")
        if not target_file:
            raise ValueError(f"{label}: chmod requires target_file")
        mode = (
            raw.get("mode")
            or raw.get("file_mode")
            or raw.get("new_mode")
            or raw.get("permissions")
        )
        if mode is None and "executable" in raw:
            mode = "755" if bool(raw.get("executable")) else "644"
        if mode is None:
            raise ValueError(f"{label}: chmod requires mode or executable")
        return {
            "action": "chmod",
            "target_file": str(target_file).replace("\\", "/"),
            "mode": normalize_file_mode(mode),
            "label": label,
            "require_match": bool(raw.get("require_match", True)),
        }


    if action == "shift_outline":
        target_file = raw.get("target_file") or raw.get("file")
        after = raw.get("after") or raw.get("before")
        if not target_file or not after:
            raise ValueError(f"{label}: shift_outline requires target_file and after")
        levels = raw.get("levels") or [2, 3]
        return {
            "action": "shift_outline",
            "target_file": str(target_file).replace("\\", "/"),
            "after": str(after),
            "levels": [int(x) for x in levels],
            "delta": int(raw.get("delta", 1)),
            "include_anchor": bool(raw.get("include_anchor", True)),
            "label": label,
        }

    if action == "insert_before":
        target_file = raw.get("target_file") or raw.get("file")
        before = raw.get("before")
        insert = raw.get("content") if raw.get("content") is not None else raw.get("insert")
        if not target_file or not before or insert is None:
            raise ValueError(f"{label}: insert_before requires target_file, before, content")
        return {
            "action": "insert_before",
            "target_file": str(target_file).replace("\\", "/"),
            "before": str(before),
            "content": str(insert),
            "label": label,
        }

    if action == "insert_section":
        target_file = raw.get("target_file") or raw.get("file")
        before = raw.get("before")
        section = raw.get("content") if raw.get("content") is not None else raw.get("section_content")
        if not target_file or not before or section is None:
            raise ValueError(f"{label}: insert_section requires target_file, before, content")
        out = {
            "action": "insert_section",
            "target_file": str(target_file).replace("\\", "/"),
            "before": str(before),
            "content": str(section),
            "label": label,
        }
        sf = raw.get("shift_following")
        if isinstance(sf, dict):
            out["shift_following"] = sf
        return out

    # rename
    source_file = raw.get("source_file") or raw.get("from_file") or raw.get("from")
    target_file = raw.get("target_file") or raw.get("to_file") or raw.get("to")
    if not source_file or not target_file:
        raise ValueError(f"{label}: rename requires source_file and target_file")
    return {
        "action": "rename",
        "source_file": str(source_file).replace("\\", "/"),
        "target_file": str(target_file).replace("\\", "/"),
        "label": label,
        "require_match": bool(raw.get("require_match", True)),
        "require_absent": bool(raw.get("require_absent", True)),
    }



def _patch_step_full_file_replace(step_index: int, rel_path: str, old_text: str, new_text: str) -> dict:
    return {
        "step_index": step_index,
        "tool_calls": [
            {
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": rel_path.replace("\\", "/"),
                    "TargetContent": old_text,
                    "ReplacementContent": new_text,
                    "AllowMultiple": False,
                },
            }
        ],
    }


def _apply_doc_outline_mutation(
    mutation: Dict[str, Any],
    *,
    target_dir: str,
    start_step: int,
) -> List[dict]:
    from apatch.doc_outline import (
        find_anchor_index,
        insert_section_in_text,
        shift_outline_in_text,
    )

    rel = mutation["target_file"]
    path = os.path.join(target_dir, rel.replace("\\", "/"))
    if not os.path.isfile(path):
        raise ValueError(f"{mutation['label']}: target not found: {rel!r}")
    old_text = Path(path).read_text(encoding="utf-8")
    action = mutation["action"]
    if action == "insert_before":
        normalized = old_text.replace("\r\n", "\n")
        lines = normalized.split("\n")
        anchor_line = lines[find_anchor_index(lines, mutation["before"])]
        insert = mutation["content"].replace("\r\n", "\n")
        if insert and not insert.endswith("\n"):
            insert += "\n"
        if not insert:
            raise ValueError(f"{mutation['label']}: outline mutation made no changes")
        return [
            {
                "step_index": start_step,
                "tool_calls": [
                    {
                        "name": "replace_file_content",
                        "arguments": {
                            "TargetFile": rel.replace("\\", "/"),
                            "TargetContent": anchor_line,
                            "ReplacementContent": insert + anchor_line,
                            "AllowMultiple": False,
                        },
                    }
                ],
            }
        ]
    if action == "shift_outline":
        new_text = shift_outline_in_text(
            old_text,
            after=mutation["after"],
            levels=mutation["levels"],
            delta=int(mutation["delta"]),
            include_anchor=bool(mutation.get("include_anchor", True)),
        )
    else:
        new_text = insert_section_in_text(
            old_text,
            before=mutation["before"],
            section_content=mutation["content"],
            shift_following=mutation.get("shift_following"),
        )
    if new_text == old_text:
        raise ValueError(f"{mutation['label']}: outline mutation made no changes")
    return [_patch_step_full_file_replace(start_step, rel, old_text, new_text)]

def _mutation_to_patch_steps(
    mutation: Dict[str, Any],
    *,
    target_dir: str,
    default_glob_pattern: str,
    default_match_mode: MatchMode,
    default_replace_all: bool,
    start_step: int,
) -> List[dict]:
    action = mutation["action"]
    label = mutation["label"]
    step = start_step

    if action == "passthrough":
        return [{"step_index": step, "tool_calls": mutation["tool_calls"]}]

    if action == "create":
        rel = mutation["target_file"]
        if mutation.get("require_absent", True) and _rel_path_exists(target_dir, rel):
            raise ValueError(f"{label}: create target already exists: {rel!r}")
        return [_patch_step_apply_patch(step, _format_apply_patch_add_file(rel, mutation["content"]))]

    if action == "delete":
        rel = mutation["target_file"]
        if mutation.get("require_match", True) and not _rel_path_exists(target_dir, rel):
            raise ValueError(f"{label}: delete target not found: {rel!r}")
        patch_input = f"*** Begin Patch\n*** Delete File: {rel}\n*** End Patch\n"
        return [_patch_step_apply_patch(step, patch_input)]

    if action == "chmod":
        rel = mutation["target_file"]
        if mutation.get("require_match", True) and not _rel_path_exists(target_dir, rel):
            raise ValueError(f"{label}: chmod target not found: {rel!r}")
        return [
            {
                "step_index": step,
                "tool_calls": [
                    {
                        "name": "chmod_file",
                        "arguments": {
                            "TargetFile": rel.replace("\\", "/"),
                            "Mode": mutation["mode"],
                        },
                    }
                ],
            }
        ]

    if action in ("shift_outline", "insert_before", "insert_section"):
        return _apply_doc_outline_mutation(
            mutation, target_dir=target_dir, start_step=step
        )

    if action == "rename":
        src = mutation["source_file"]
        dst = mutation["target_file"]
        if mutation.get("require_match", True) and not _rel_path_exists(target_dir, src):
            raise ValueError(f"{label}: rename source not found: {src!r}")
        if mutation.get("require_absent", True) and _rel_path_exists(target_dir, dst):
            raise ValueError(f"{label}: rename target already exists: {dst!r}")
        content = Path(os.path.join(target_dir, src)).read_text(encoding="utf-8")
        return [
            _patch_step_apply_patch(step, _format_apply_patch_add_file(dst, content)),
            _patch_step_apply_patch(
                step + 1,
                f"*** Begin Patch\n*** Delete File: {src}\n*** End Patch\n",
            ),
        ]

    # replace
    glob_pattern = mutation["glob_pattern"] or default_glob_pattern
    match_mode = mutation["match_mode"] or default_match_mode
    if match_mode not in ("literal", "whitespace", "regex", "json", "yaml"):
        raise ValueError(f"{label}: invalid match_mode {match_mode!r}")
    batch = generate_patches(
        find=mutation["find_text"],
        replace=mutation["replace_text"],
        target_dir=target_dir,
        glob_pattern=glob_pattern,
        replace_all=(
            mutation["replace_all"]
            if mutation.get("replace_all") is not None
            else default_replace_all
        ),
        match_mode=match_mode,  # type: ignore[arg-type]
        find_pattern=mutation.get("find_pattern"),
        start_step=step,
    )
    if not batch and mutation.get("require_match", True):
        raise ValueError(f"{label}: find_text not found under {glob_pattern!r}")
    return batch


def generate_patches(
    *,
    find: str,
    replace: str,
    target_dir: str,
    glob_pattern: str = "**/*",
    replace_all: bool = True,
    match_mode: MatchMode = "literal",
    find_pattern: Optional[str] = None,
    start_step: int = 1,
) -> List[dict]:
    patches: List[dict] = []
    step = start_step
    for rel in sorted(iter_matching_files(target_dir, glob_pattern)):
        abs_path = os.path.join(target_dir, rel)
        try:
            text = Path(abs_path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if match_mode in ("json", "yaml") and os.path.splitext(rel)[1].lower() in (".yaml", ".yml"):
            from apatch.json_mapping import apply_yaml_semantic_patch

            ok, new_text = apply_yaml_semantic_patch(
                text,
                find,
                replace,
                replace_all=replace_all,
            )
            if ok:
                patches.append(_patch_step_full_file_replace(step, rel, text, new_text))
                step += 1
            continue
        spans = _find_spans(text, find, match_mode, find_pattern, rel)
        if not spans:
            continue
        # Use first span as TargetContent anchor (unique per file for matcher)
        target_content = spans[0] if match_mode != "literal" else find
        patches.append(
            {
                "step_index": step,
                "tool_calls": [
                    {
                        "name": "replace_file_content",
                        "arguments": {
                            "TargetFile": rel.replace("\\", "/"),
                            "TargetContent": target_content,
                            "ReplacementContent": replace,
                            "AllowMultiple": replace_all,
                        },
                    }
                ],
            }
        )
        step += 1
    return patches


def _needle_anchor_span(
    mutation: Dict[str, Any], target_dir: str
) -> Optional[Tuple[str, int, int]]:
    """Best-effort (file, start_line, end_line) where a needle anchors in the
    current file, for batch-overlap detection. Returns None when the location is
    not a single locatable point (create/delete/rename/chmod/passthrough/missing)."""
    action = mutation.get("action")
    # replace needles carry the file in glob_pattern (target_file is folded in)
    target_file = mutation.get("target_file") or mutation.get("glob_pattern")
    if not target_file or action in ("create", "delete", "rename", "chmod", "passthrough"):
        return None
    try:
        with open(
            os.path.join(target_dir, str(target_file)), "r", encoding="utf-8"
        ) as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    norm = text.replace("\r\n", "\n")
    if action == "replace":
        find_text = str(mutation.get("find_text") or "").replace("\r\n", "\n")
        if not find_text:
            return None
        pos = norm.find(find_text)
        if pos < 0:
            return None
        start = norm.count("\n", 0, pos)
        return (str(target_file), start, start + find_text.count("\n"))
    anchor = mutation.get("before") or mutation.get("after")
    if not anchor:
        return None
    try:
        from apatch.doc_outline import find_anchor_index

        idx = find_anchor_index(norm.split("\n"), str(anchor))
    except Exception:
        return None
    return (str(target_file), idx, idx)


def _detect_batch_overlap(
    spans: List[Tuple[int, str, int, int]],
    *,
    needle_count: int,
) -> Optional[NeedleOverlapError]:
    """Flag two same-file needles whose anchor line ranges overlap or touch.

    Batched patches are computed against the original file, so applying
    overlapping ones sequentially drifts the later patch's context and can
    silently corrupt the file. Fail loud instead."""
    by_file: Dict[str, List[Tuple[int, int, int]]] = {}
    for idx, f, start, end in spans:
        by_file.setdefault(f, []).append((idx, start, end))
    conflicts: List[Dict[str, Any]] = []
    adjacency: Dict[int, set[int]] = {idx: set() for idx in range(1, needle_count + 1)}
    for f, items in by_file.items():
        items.sort(key=lambda x: x[1])
        for i in range(len(items)):
            a_idx, a_s, a_e = items[i]
            for j in range(i + 1, len(items)):
                b_idx, b_s, b_e = items[j]
                if b_s > a_e:
                    break
                adjacency[a_idx].add(b_idx)
                adjacency[b_idx].add(a_idx)
                conflicts.append(
                    {
                        "needles": [a_idx, b_idx],
                        "target_file": f,
                        "line_ranges": [[a_s + 1, a_e + 1], [b_s + 1, b_e + 1]],
                    }
                )
    if not conflicts:
        return None

    # Deterministic greedy graph coloring: every returned batch is safe to
    # regenerate from the then-current workspace snapshot.
    colors: Dict[int, int] = {}
    for idx in range(1, needle_count + 1):
        unavailable = {colors[other] for other in adjacency[idx] if other in colors}
        color = 0
        while color in unavailable:
            color += 1
        colors[idx] = color
    safe_batches = [
        [idx for idx in range(1, needle_count + 1) if colors[idx] == color]
        for color in range(max(colors.values()) + 1)
    ]
    first = conflicts[0]
    a_idx, b_idx = first["needles"]
    a_range, b_range = first["line_ranges"]
    message = (
        "needles #{} and #{} touch overlapping regions in {} "
        "(lines {}-{} and {}-{}). No patch log or source mutation was written. "
        "Retry the returned safe_batches in order, regenerating each batch "
        "against the updated workspace.".format(
            a_idx,
            b_idx,
            first["target_file"],
            a_range[0],
            a_range[1],
            b_range[0],
            b_range[1],
        )
    )
    return NeedleOverlapError(
        message,
        conflicts=conflicts,
        safe_batches=safe_batches,
    )


def generate_patches_batch(
    needles: List[Union[Dict[str, Any], str]],
    *,
    target_dir: str,
    default_glob_pattern: str = "**/*",
    default_match_mode: MatchMode = "literal",
    default_replace_all: bool = False,
    start_step: int = 1,
) -> List[dict]:
    """Build ordered JSONL steps from mutation needles.

    Legacy needles without ``action`` remain find/replace. Unified format::

        {action: "replace", find_text, replace_text, target_file?, ...}
        {action: "create", target_file, content}
        {action: "delete", target_file}
        {action: "rename", source_file, target_file}
        {action: "chmod", target_file, mode: "755"}  # or executable: true
        {action: "shift_outline", target_file, after, levels: [2,3], delta: 1}
        {action: "insert_before", target_file, before, content}
        {action: "insert_section", target_file, before, content, shift_following: {levels, delta}}
        {tool_calls: [...]}  # passthrough (e.g. apply_patch envelope)
    """
    patches: List[dict] = []
    step = start_step
    _spans: List[Tuple[int, str, int, int]] = []
    for idx, raw in enumerate(needles, start=1):
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise ValueError(f"mutation #{idx} must be an object")
        mutation = normalize_mutation(raw, index=idx)
        _span = _needle_anchor_span(mutation, target_dir)
        if _span is not None:
            _spans.append((idx, _span[0], _span[1], _span[2]))
        batch = _mutation_to_patch_steps(
            mutation,
            target_dir=target_dir,
            default_glob_pattern=default_glob_pattern,
            default_match_mode=default_match_mode,
            default_replace_all=default_replace_all,
            start_step=step,
        )
        patches.extend(batch)
        if batch:
            step = int(batch[-1]["step_index"]) + 1
    _overlap = _detect_batch_overlap(_spans, needle_count=len(needles))
    if _overlap:
        raise _overlap
    return patches


def write_jsonl(patches: List[dict], out: TextIO) -> int:
    for p in patches:
        out.write(json.dumps(p, ensure_ascii=False) + "\n")
    return len(patches)
