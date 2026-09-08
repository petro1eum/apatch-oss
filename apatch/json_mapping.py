"""Structure-aware matching for Elasticsearch/OpenSearch mapping files (R31, R36)."""

from __future__ import annotations

import json
from typing import Any, Iterator, List, Optional, Tuple


_JSON_MARKERS = (
    '"mappings"',
    '"properties"',
    '"index_patterns"',
    '"template"',
    '"settings"',
    '"dynamic"',
)

_YAML_MARKERS = (
    "mappings:",
    "properties:",
    "index_patterns:",
    "template:",
    "settings:",
    "dynamic:",
)


def _yaml_available() -> bool:
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


def looks_like_mapping_document(content: str) -> bool:
    sample = content[:8000]
    hits = sum(1 for m in _JSON_MARKERS if m in sample)
    hits += sum(1 for m in _YAML_MARKERS if m in sample)
    return hits >= 2


def canonical_json(obj: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_json_fragment(text: str) -> Optional[Any]:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _iter_json_object_spans(content: str) -> List[Tuple[int, int, str]]:
    """Yield (start, end, substring) for every balanced `{ ... }` span in content."""
    spans: List[Tuple[int, int, str]] = []
    for i, ch in enumerate(content):
        if ch != "{":
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(content)):
            c = content[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    spans.append((i, j + 1, content[i : j + 1]))
                    break
    return spans


def find_semantic_json_spans(content: str, old_obj: Any) -> List[Tuple[int, int, str]]:
    """Find file spans whose parsed JSON equals old_obj (structure, not whitespace)."""
    target = canonical_json(old_obj)
    matches: List[Tuple[int, int, str]] = []
    for start, end, frag in _iter_json_object_spans(content):
        parsed = parse_json_fragment(frag)
        if parsed is None:
            continue
        if canonical_json(parsed) == target:
            matches.append((start, end, frag))
    return matches


def apply_json_semantic_patch(
    content: str,
    old_str: str,
    new_str: str,
    *,
    replace_all: bool = False,
) -> Tuple[bool, str]:
    """
    Replace JSON object(s) in mapping files when whitespace/format differs.

    Returns (success, new_content).
    """
    if not looks_like_mapping_document(content):
        return False, content

    old_obj = parse_json_fragment(old_str)
    new_obj = parse_json_fragment(new_str)
    if old_obj is None or new_obj is None:
        return False, content

    spans = find_semantic_json_spans(content, old_obj)
    if not spans:
        return False, content

    replacement = new_str.strip()
    if parse_json_fragment(replacement) is not None:
        try:
            replacement = json.dumps(new_obj, indent=2, ensure_ascii=False)
            if not replacement.endswith("\n") and "\n" in content[max(0, spans[0][0] - 1) : spans[0][1] + 1]:
                replacement += "\n"
        except Exception:
            replacement = new_str

    if not replace_all:
        spans = spans[:1]

    out = content
    offset = 0
    for start, end, _ in spans:
        s = start + offset
        e = end + offset
        out = out[:s] + replacement + out[e:]
        offset += len(replacement) - (end - start)
    return True, out


def parse_yaml_fragment(text: str) -> Optional[Any]:
    if not _yaml_available():
        return None
    import yaml

    text = text.strip()
    if not text:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def parse_mapping_fragment(text: str) -> Optional[Any]:
    """Parse a JSON or YAML mapping fragment."""
    obj = parse_json_fragment(text)
    if obj is not None:
        return obj
    return parse_yaml_fragment(text)


def _is_yaml_mapping_file(content: str, file_ext: str) -> bool:
    if file_ext in (".yaml", ".yml"):
        return True
    sample = content.lstrip()[:200]
    return not sample.startswith("{") and any(m in content[:4000] for m in _YAML_MARKERS)


def _iter_mapping_paths(obj: Any, path: Tuple[Any, ...] = ()) -> Iterator[Tuple[Tuple[Any, ...], Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_mapping_paths(value, path + (key,))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from _iter_mapping_paths(value, path + (idx,))


def _set_at_path(root: Any, path: Tuple[Any, ...], value: Any) -> None:
    if not path:
        raise ValueError("empty path")
    cur = root
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value


def _dump_yaml_mapping(obj: Any) -> str:
    import yaml

    return yaml.dump(obj, default_flow_style=False, allow_unicode=True, sort_keys=False)


def find_yaml_semantic_spans(content: str, old_obj: Any) -> List[str]:
    """Return YAML dump fragments in file that match old_obj structurally."""
    if not _yaml_available():
        return []
    import yaml

    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    if doc is None:
        return []

    target = canonical_json(old_obj)
    spans: List[str] = []
    for path, node in _iter_mapping_paths(doc):
        if not isinstance(node, (dict, list)):
            continue
        if canonical_json(node) != target:
            continue
        frag = _dump_yaml_mapping(node)
        if frag not in spans:
            spans.append(frag)
    return spans


def apply_yaml_semantic_patch(
    content: str,
    old_str: str,
    new_str: str,
    *,
    replace_all: bool = False,
) -> Tuple[bool, str]:
    if not _yaml_available():
        return False, content

    import yaml

    old_obj = parse_mapping_fragment(old_str)
    new_obj = parse_mapping_fragment(new_str)
    if old_obj is None or new_obj is None:
        return False, content

    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError:
        return False, content
    if doc is None:
        return False, content

    target = canonical_json(old_obj)
    paths = [
        path
        for path, node in _iter_mapping_paths(doc)
        if isinstance(node, (dict, list)) and canonical_json(node) == target
    ]
    if not paths:
        return False, content

    if not replace_all:
        paths = paths[:1]

    for path in paths:
        if not path:
            doc = new_obj
        else:
            _set_at_path(doc, path, new_obj)

    out = _dump_yaml_mapping(doc)
    if content.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return True, out


def apply_mapping_semantic_patch(
    content: str,
    old_str: str,
    new_str: str,
    *,
    file_ext: Optional[str] = None,
    replace_all: bool = False,
) -> Tuple[bool, str]:
    """JSON or YAML structure match (whitespace / key-order insensitive)."""
    ext = (file_ext or "").lower()
    if _is_yaml_mapping_file(content, ext):
        ok, out = apply_yaml_semantic_patch(
            content, old_str, new_str, replace_all=replace_all
        )
        if ok:
            return True, out

    return apply_json_semantic_patch(
        content, old_str, new_str, replace_all=replace_all
    )
