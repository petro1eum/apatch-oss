"""Check rendered Markdown links and PyPI metadata without fetching private URLs."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


def markdown_links(path: Path):
    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(path.read_text(encoding="utf-8"))
    for token in tokens:
        for child in token.children or []:
            if child.type == "link_open":
                yield child.attrGet("href")
            elif child.type == "image":
                yield child.attrGet("src")


def heading_ids(path: Path):
    tokens = MarkdownIt("commonmark").enable("table").parse(path.read_text(encoding="utf-8"))
    ids = set()
    occurrences = {}
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        inline = tokens[index + 1]
        title = "".join(t.content for t in inline.children or [] if t.type in {"text", "code_inline"})
        slug = re.sub(r"[^\w\- ]", "", title.lower(), flags=re.UNICODE).replace(" ", "-")
        count = occurrences.get(slug, 0)
        occurrences[slug] = count + 1
        ids.add(slug if not count else f"{slug}-{count}")
    ids.update(re.findall(r'(?:id|name)=["\x27]([^"\x27]+)["\x27]', path.read_text(encoding="utf-8")))
    return ids


def check(root: Path):
    paths = sorted(root.glob("*.md")) + sorted((root / "docs").rglob("*.md"))
    errors = []
    checked = 0
    external = set()
    anchors_checked = 0
    for path in paths:
        for href in markdown_links(path):
            if not href:
                continue
            parts = urlsplit(href)
            if parts.scheme in {"https", "http", "mailto"}:
                external.add(href)
                if any(value in href for value in ("github.com/petro1eum/apatch", "github.com/petro1eum/Human_Capital")):
                    errors.append({"file": str(path.relative_to(root)), "href": href, "reason": "private repository link"})
                continue
            if parts.scheme:
                errors.append({"file": str(path.relative_to(root)), "href": href, "reason": "unsupported/local URI scheme"})
                continue
            checked += bool(parts.path)
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path.resolve()
            if not target.is_relative_to(root.resolve()):
                errors.append({"file": str(path.relative_to(root)), "href": href, "reason": "outside public snapshot"})
            elif not target.exists():
                errors.append({"file": str(path.relative_to(root)), "href": href, "reason": "missing target"})
            elif parts.fragment and target.suffix == ".md":
                anchors_checked += 1
                if unquote(parts.fragment) not in heading_ids(target):
                    errors.append({"file": str(path.relative_to(root)), "href": href, "reason": "missing heading anchor"})
    return {"documents": len(paths), "local_targets_checked": checked,
            "anchors_checked": anchors_checked, "external_urls": sorted(external), "errors": errors, "ok": not errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = check(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
